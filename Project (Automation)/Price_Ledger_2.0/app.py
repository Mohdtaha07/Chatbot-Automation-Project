"""
Local backend for the Price Ledger dashboard.

Run this on your own machine (it needs Chrome + real internet access, which
a hosted chat sandbox doesn't have). It exposes:

  GET  /api/ping                    -> {"ok": true}                 health check
  POST /api/run   {query, pages}    -> {"job_id": "..."}             starts a scrape
  GET  /api/status/<job_id>         -> job dict (status/log/data)    poll this
  GET  /api/download/<job_id>       -> the finished CSV as a file
  POST /api/jobs/<job_id>/cancel    -> best-effort cancel of a running job

Setup (VS Code terminal, from this folder):

    python -m venv .venv
    source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
    pip install -r requirements.txt
    python app.py

Then point the dashboard's "API base URL" field at http://localhost:8787

Requires a real Chrome/Chromium install on this machine — Selenium 4.6+
downloads the matching driver automatically, you just need the browser.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import traceback
import uuid

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from automation_core import run_scrape

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("price_ledger.app")

app = Flask(__name__)

# Lock CORS down to an explicit origin in production; default stays open
# (*) for local dev since the dashboard usually runs on a different port.
ALLOWED_ORIGIN = os.environ.get("PRICE_LEDGER_ALLOWED_ORIGIN", "*")
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGIN}})

MAX_PAGES = int(os.environ.get("PRICE_LEDGER_MAX_PAGES", 50))
JOB_TTL_SECONDS = int(os.environ.get("PRICE_LEDGER_JOB_TTL_SECONDS", 3600))

JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


def _cleanup_old_jobs():
    cutoff = time.time() - JOB_TTL_SECONDS
    with LOCK:
        stale = [jid for jid, job in JOBS.items() if job.get("created_at", 0) < cutoff]
        for jid in stale:
            JOBS.pop(jid, None)


def _run_job(job_id: str, query: str, pages: int, headless: bool):
    job = JOBS[job_id]

    def log(msg: str):
        with LOCK:
            if job.get("cancelled"):
                return
            job["log"].append(msg)
            job["log"] = job["log"][-300:]
            if msg.startswith("Page "):
                try:
                    current = int(msg.split("Page ")[1].split("/")[0])
                    job["progress"] = current
                except (IndexError, ValueError):
                    pass

    try:
        df, csv_path = run_scrape(query, pages=pages, headless=headless, log=log)
        with LOCK:
            job["status"] = "done"
            job["csv_path"] = os.path.abspath(csv_path)
            job["data"] = df.to_dict(orient="records")
            job["row_count"] = len(df)
    except Exception as e:  # surfaces driver/network errors to the dashboard
        logger.exception("Job %s failed", job_id)
        with LOCK:
            job["status"] = "error"
            job["error"] = str(e)
            job["log"].append(f"ERROR: {e}")
            job["log"].append(traceback.format_exc())


@app.get("/api/ping")
def ping():
    return jsonify({"ok": True})


@app.post("/api/run")
def run():
    _cleanup_old_jobs()
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    headless = bool(body.get("headless", True))

    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        pages = int(body.get("pages") or 19)
    except (TypeError, ValueError):
        return jsonify({"error": "pages must be an integer"}), 400
    if pages < 1 or pages > MAX_PAGES:
        return jsonify({"error": f"pages must be between 1 and {MAX_PAGES}"}), 400

    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {
        "status": "running",
        "query": query,
        "pages": pages,
        "progress": 0,
        "log": [f"Starting scrape for '{query}' across {pages} page(s)..."],
        "data": None,
        "row_count": None,
        "error": None,
        "csv_path": None,
        "cancelled": False,
        "created_at": time.time(),
    }
    threading.Thread(target=_run_job, args=(job_id, query, pages, headless), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.get("/api/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job_id"}), 404
    return jsonify(job)


@app.get("/api/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job_id"}), 404
    if job["status"] != "done" or not job.get("csv_path"):
        return jsonify({"error": "job has no completed CSV yet"}), 409
    if not os.path.exists(job["csv_path"]):
        return jsonify({"error": "csv file no longer exists on disk"}), 410
    return send_file(job["csv_path"], as_attachment=True)


@app.post("/api/jobs/<job_id>/cancel")
def cancel(job_id):
    # Best-effort: the scrape loop checks this flag between pages via `log`,
    # so cancellation takes effect at the next page boundary, not instantly.
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job_id"}), 404
    with LOCK:
        job["cancelled"] = True
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PRICE_LEDGER_PORT", 8787))
    logger.info("Price Ledger backend starting on http://127.0.0.1:%s", port)
    app.run(host="127.0.0.1", port=port, debug=False)
