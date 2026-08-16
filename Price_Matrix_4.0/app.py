from __future__ import annotations

import logging
import os
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Optional

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from automation_core import run_scrape
from chatbot import chatbot_bp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("price_ledger.app")

app = Flask(__name__)
app.register_blueprint(chatbot_bp)

ALLOWED_ORIGIN = os.environ.get("PRICE_LEDGER_ALLOWED_ORIGIN", "*")
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGIN}})

MAX_PAGES = int(os.environ.get("PRICE_LEDGER_MAX_PAGES", 50))
JOB_TTL_SECONDS = int(os.environ.get("PRICE_LEDGER_JOB_TTL_SECONDS", 3600))
MAX_CONCURRENT_JOBS = int(os.environ.get("PRICE_LEDGER_MAX_CONCURRENT_JOBS", 3))
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("PRICE_LEDGER_CLEANUP_INTERVAL_SECONDS", 300))

JOB_ID_RE = re.compile(r"^[0-9a-f]{10}$")


@dataclass
class Job:
    status: str
    query: str
    pages: int
    log: list = field(default_factory=list)
    data: Optional[list] = None
    row_count: Optional[int] = None
    error: Optional[str] = None
    csv_path: Optional[str] = None
    cancelled: bool = False
    created_at: float = field(default_factory=time.time)
    # Real cancellation signal, threaded through to automation_core so a
    # "cancel" call actually stops the browser instead of only muting logs.
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def to_public_dict(self) -> dict:
        return {
            "status": self.status,
            "query": self.query,
            "pages": self.pages,
            "log": self.log,
            "data": self.data,
            "row_count": self.row_count,
            "error": self.error,
            "csv_path": self.csv_path,
            "cancelled": self.cancelled,
            "created_at": self.created_at,
        }


class JobStore:
    """Thread-safe in-memory job store.

    Centralizing every mutation behind one lock (instead of the original's
    scattered `with LOCK:` blocks in app.py) makes it easy to see that no
    job field is ever touched outside the lock, and gives us one place to
    enforce the concurrent-job cap and TTL cleanup.
    """

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, query: str, pages: int) -> tuple[str, Job]:
        job_id = uuid.uuid4().hex[:10]
        job = Job(
            status="running",
            query=query,
            pages=pages,
            log=[f"Starting comparative search for '{query}' across Amazon & Flipkart..."],
        )
        with self._lock:
            self._jobs[job_id] = job
        return job_id, job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == "running")

    def append_log(self, job_id: str, msg: str):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.cancelled:
                return
            job.log.append(msg)
            job.log = job.log[-300:]

    def finish_ok(self, job_id: str, csv_path: str, records: list, row_count: int):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = "done"
            job.csv_path = csv_path
            job.data = records
            job.row_count = row_count

    def finish_error(self, job_id: str, error_msg: str, tb: str):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = "error"
            job.error = error_msg
            job.log.append(f"ERROR: {error_msg}")
            job.log.append(tb)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job.cancelled = True
            job.cancel_event.set()
            return True

    def cleanup_stale(self):
        cutoff = time.time() - JOB_TTL_SECONDS
        with self._lock:
            stale = [jid for jid, job in self._jobs.items() if job.created_at < cutoff]
            for jid in stale:
                self._jobs.pop(jid, None)
        if stale:
            logger.info("Cleaned up %d stale job(s)", len(stale))


STORE = JobStore()


def _run_job(job_id: str, query: str, pages: int, headless: bool):
    job = STORE.get(job_id)
    if job is None:
        return

    def log(msg: str):
        STORE.append_log(job_id, msg)

    try:
        df, csv_path = run_scrape(
            query, pages=pages, headless=headless, log=log, cancel_event=job.cancel_event
        )
        STORE.finish_ok(job_id, os.path.abspath(csv_path), df.to_dict(orient="records"), len(df))
    except Exception as e:
        logger.exception("Job %s failed", job_id)
        STORE.finish_error(job_id, str(e), traceback.format_exc())


def _cleanup_loop():
    # Runs continuously in the background instead of only cleaning up
    # opportunistically when a new job happens to be created, so stale jobs
    # (and the memory they hold) don't linger indefinitely on a quiet server.
    while True:
        time.sleep(CLEANUP_INTERVAL_SECONDS)
        try:
            STORE.cleanup_stale()
        except Exception:
            logger.exception("Cleanup loop error")


threading.Thread(target=_cleanup_loop, daemon=True).start()


@app.get("/api/ping")
def ping():
    return jsonify({"ok": True})


@app.post("/api/run")
def run():
    STORE.cleanup_stale()
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    headless = bool(body.get("headless", True))

    if not query:
        return jsonify({"error": "query is required"}), 400
    if len(query) > 200:
        return jsonify({"error": "query is too long (max 200 characters)"}), 400

    try:
        pages = int(body.get("pages") or 2)
    except (TypeError, ValueError):
        return jsonify({"error": "pages must be an integer"}), 400
    if pages < 1 or pages > MAX_PAGES:
        return jsonify({"error": f"pages must be between 1 and {MAX_PAGES}"}), 400

    if STORE.running_count() >= MAX_CONCURRENT_JOBS:
        return jsonify(
            {"error": f"too many jobs running (max {MAX_CONCURRENT_JOBS}), try again shortly"}
        ), 429

    job_id, _ = STORE.create(query, pages)
    threading.Thread(target=_run_job, args=(job_id, query, pages, headless), daemon=True).start()
    return jsonify({"job_id": job_id})


def _get_valid_job(job_id: str) -> Optional[Job]:
    if not JOB_ID_RE.match(job_id or ""):
        return None
    return STORE.get(job_id)


@app.get("/api/status/<job_id>")
def status(job_id):
    job = _get_valid_job(job_id)
    if not job:
        return jsonify({"error": "unknown job_id"}), 404
    return jsonify(job.to_public_dict())


@app.get("/api/download/<job_id>")
def download(job_id):
    job = _get_valid_job(job_id)
    if not job:
        return jsonify({"error": "unknown job_id"}), 404
    if job.status != "done" or not job.csv_path:
        return jsonify({"error": "job has no completed CSV yet"}), 409
    if not os.path.exists(job.csv_path):
        return jsonify({"error": "csv file no longer exists on disk"}), 410
    return send_file(job.csv_path, as_attachment=True)


@app.post("/api/jobs/<job_id>/cancel")
def cancel(job_id):
    if not JOB_ID_RE.match(job_id or ""):
        return jsonify({"error": "unknown job_id"}), 404
    if not STORE.cancel(job_id):
        return jsonify({"error": "unknown job_id"}), 404
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PRICE_LEDGER_PORT", 8787))
    logger.info("Price Ledger backend starting on http://127.0.0.1:%s", port)
    app.run(host="127.0.0.1", port=port, debug=False)
