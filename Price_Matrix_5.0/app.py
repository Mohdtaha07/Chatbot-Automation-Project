"""
app.py — Price Matrix backend (v2)

Improvements over v1
--------------------
* Serves index.html itself (`/`), so there's no file:// + CORS dance.
* /api/health reports whether Selenium AND Chrome are actually usable, so the
  UI can tell the user *why* a run will fail before they wait 30s for it to.
* Real cancellation: the worker polls a per-job event, so Cancel stops the
  scrape instead of only hiding new log lines.
* Real progress: 0-100% driven by pages completed, plus a monotonically
  increasing log cursor so the client can fetch only new lines.
* Server-Sent Events at /api/stream/<job_id> — no more 1.2s polling.
* Exports in CSV / XLSX (formatted, with a Summary sheet) / JSON.
* Job store bounded by count *and* TTL, with on-disk export cleanup.
* Errors are structured (`error_type`) instead of a bare string.
* Chatbot is wired to the live job data via register_data_source().

Run:  python app.py    ->  http://127.0.0.1:8787
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import pandas as pd
from flask import (
    Blueprint, Flask, Response, jsonify, render_template_string, request,
    send_file, send_from_directory, stream_with_context,
)

try:
    from flask_cors import CORS
    _HAS_CORS = True
except Exception:  # optional dependency
    _HAS_CORS = False

from automation_core import (
    DriverUnavailable, ScrapeConfig, chrome_status, scrape, summarize,
)
from chatbot import chatbot_bp, register_data_source

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
logger = logging.getLogger("price_matrix.app")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.environ.get("PM_OUTPUT_DIR", os.path.join(BASE_DIR, "exports"))
os.makedirs(EXPORT_DIR, exist_ok=True)

MAX_PAGES = int(os.environ.get("PM_MAX_PAGES", 10))
JOB_TTL_SECONDS = int(os.environ.get("PM_JOB_TTL_SECONDS", 3 * 3600))
MAX_JOBS = int(os.environ.get("PM_MAX_JOBS", 40))
MAX_CONCURRENT_JOBS = int(os.environ.get("PM_MAX_CONCURRENT", 2))
LOG_LIMIT = 500

app = Flask(__name__, static_folder=None)
app.config["JSON_SORT_KEYS"] = False
app.register_blueprint(chatbot_bp)

ALLOWED_ORIGIN = os.environ.get("PM_ALLOWED_ORIGIN", "*")
if _HAS_CORS:
    CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGIN}})
else:
    @app.after_request
    def _cors(resp):  # minimal fallback
        resp.headers.setdefault("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        resp.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
        resp.headers.setdefault("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        return resp


# ---------------------------------------------------------------------------
# Job store
# ---------------------------------------------------------------------------
JOBS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
LOCK = threading.RLock()
RUNNING = threading.Semaphore(MAX_CONCURRENT_JOBS)


def _new_job(job_id: str, query: str, pages: int, platforms: List[str]) -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "status": "queued",          # queued | running | done | error | cancelled
        "query": query,
        "pages": pages,
        "platforms": platforms,
        "progress": 0,
        "log": [],
        "log_seq": 0,
        "data": None,
        "row_count": 0,
        "stats": None,
        "errors": {},
        "error": None,
        "error_type": None,
        "csv_path": None,
        "cancel_event": threading.Event(),
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "duration": None,
    }


def _public_job(job: Dict[str, Any], since: int = 0, include_data: bool = True) -> Dict[str, Any]:
    """Serialisable view; `since` returns only newer log lines."""
    log = job["log"]
    sliced = log[since:] if since and since < len(log) else (log if not since else [])
    out = {
        "job_id": job["job_id"],
        "status": job["status"],
        "query": job["query"],
        "pages": job["pages"],
        "platforms": job["platforms"],
        "progress": job["progress"],
        "log": sliced,
        "log_seq": len(log),
        "row_count": job["row_count"],
        "stats": job["stats"],
        "errors": job["errors"],
        "error": job["error"],
        "error_type": job["error_type"],
        "created_at": job["created_at"],
        "duration": job["duration"],
        "has_csv": bool(job.get("csv_path")),
    }
    if include_data and job["status"] in {"done", "cancelled"}:
        out["data"] = job["data"] or []
    return out


def _cleanup_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with LOCK:
        stale = [jid for jid, j in JOBS.items()
                 if j["created_at"] < cutoff and j["status"] not in {"running", "queued"}]
        while len(JOBS) - len(stale) > MAX_JOBS:
            for jid, j in JOBS.items():
                if jid not in stale and j["status"] not in {"running", "queued"}:
                    stale.append(jid)
                    break
            else:
                break
        for jid in stale:
            job = JOBS.pop(jid, None)
            path = (job or {}).get("csv_path")
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
def _run_job(job_id: str, cfg: ScrapeConfig) -> None:
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        return

    def log(msg: str) -> None:
        with LOCK:
            job["log"].append({"t": time.time(), "msg": str(msg)})
            if len(job["log"]) > LOG_LIMIT:
                del job["log"][:len(job["log"]) - LOG_LIMIT]

    def progress(pct: int) -> None:
        with LOCK:
            job["progress"] = max(job["progress"], int(pct))

    def cancelled() -> bool:
        return job["cancel_event"].is_set()

    acquired = RUNNING.acquire(timeout=300)
    if not acquired:
        with LOCK:
            job["status"] = "error"
            job["error"] = "Server is busy running other scrapes. Try again shortly."
            job["error_type"] = "busy"
            job["finished_at"] = time.time()
        return

    try:
        with LOCK:
            job["status"] = "running"
            job["started_at"] = time.time()
        log(f"Job {job_id} started · “{cfg.query}” · {cfg.pages} page(s) per store.")

        result = scrape(cfg, log=log, should_cancel=cancelled, on_progress=progress)
        records = json.loads(result.df.to_json(orient="records")) if not result.df.empty else []

        with LOCK:
            job["data"] = records
            job["row_count"] = len(records)
            job["stats"] = result.stats
            job["errors"] = result.errors
            job["csv_path"] = os.path.abspath(result.csv_path)
            was_cancelled = result.cancelled or cancelled()
            # A cancelled run keeps its real progress — claiming 100% would be a lie.
            if not was_cancelled:
                job["progress"] = 100
            job["status"] = "cancelled" if was_cancelled else "done"
            job["finished_at"] = time.time()
            job["duration"] = round(job["finished_at"] - (job["started_at"] or job["finished_at"]), 1)

        if result.errors:
            for platform, msg in result.errors.items():
                log(f"⚠ {platform.title()}: {msg[:200]}")
        log(f"Finished in {job['duration']}s with {job['row_count']} listings.")

    except DriverUnavailable as exc:
        logger.warning("Job %s: driver unavailable: %s", job_id, exc)
        with LOCK:
            job["status"] = "error"
            job["error"] = str(exc)
            job["error_type"] = "driver"
            job["finished_at"] = time.time()
        log(f"ERROR: {exc}")
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        with LOCK:
            job["status"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["error_type"] = "runtime"
            job["finished_at"] = time.time()
        log(f"ERROR: {type(exc).__name__}: {exc}")
        log(traceback.format_exc()[-1500:])
    finally:
        RUNNING.release()


# ---------------------------------------------------------------------------
# Chatbot data bridge
# ---------------------------------------------------------------------------
def _job_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    with LOCK:
        job = JOBS.get(job_id)
        if not job or not job.get("data"):
            return None
        return {"query": job["query"], "rows": job["data"], "stats": job["stats"]}


register_data_source(_job_snapshot)


# ---------------------------------------------------------------------------
# Routes — UI
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(path):
        return send_from_directory(BASE_DIR, "index.html")
    return render_template_string(
        "<h1>Price Matrix</h1><p>index.html not found next to app.py.</p>"), 404


@app.get("/favicon.ico")
def favicon():
    return ("", 204)


# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------
@app.get("/api/ping")
def ping():
    return jsonify({"ok": True, "time": time.time()})


@app.get("/api/health")
def health():
    status = chrome_status()
    with LOCK:
        running = sum(1 for j in JOBS.values() if j["status"] == "running")
        queued = sum(1 for j in JOBS.values() if j["status"] == "queued")
        total = len(JOBS)
    return jsonify({
        "ok": True,
        "scraper_ready": status["ready"],
        "chrome": status,
        "jobs": {"active": running, "queued": queued, "stored": total,
                 "max_concurrent": MAX_CONCURRENT_JOBS},
        "limits": {"max_pages": MAX_PAGES, "job_ttl_seconds": JOB_TTL_SECONDS},
        "version": "2.0",
    })


@app.post("/api/run")
def run():
    _cleanup_jobs()
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required", "error_type": "validation"}), 400
    if len(query) > 200:
        return jsonify({"error": "query is too long (200 chars max)", "error_type": "validation"}), 400

    try:
        pages = int(body.get("pages") or 2)
    except (TypeError, ValueError):
        return jsonify({"error": "pages must be an integer", "error_type": "validation"}), 400
    if not (1 <= pages <= MAX_PAGES):
        return jsonify({"error": f"pages must be between 1 and {MAX_PAGES}",
                        "error_type": "validation"}), 400

    platforms = body.get("platforms") or ["amazon", "flipkart"]
    platforms = [p.lower().strip() for p in platforms if str(p).lower().strip() in {"amazon", "flipkart"}]
    if not platforms:
        return jsonify({"error": "at least one valid platform is required",
                        "error_type": "validation"}), 400

    status = chrome_status()
    if not status["ready"]:
        return jsonify({
            "error": ("Chrome/Selenium is not available on the server. "
                      "Install Google Chrome and run `pip install -r requirements.txt`."),
            "error_type": "driver",
            "chrome": status,
        }), 503

    cfg = ScrapeConfig(
        query=query,
        pages=pages,
        headless=bool(body.get("headless", True)),
        platforms=tuple(platforms),
        include_sponsored=bool(body.get("include_sponsored", True)),
        dump_html=bool(body.get("dump_html", False)),
        output_dir=EXPORT_DIR,
    )

    job_id = uuid.uuid4().hex[:10]
    with LOCK:
        JOBS[job_id] = _new_job(job_id, query, pages, platforms)

    threading.Thread(target=_run_job, args=(job_id, cfg),
                     name=f"scrape-{job_id}", daemon=True).start()
    return jsonify({"job_id": job_id, "status": "queued"}), 202


@app.get("/api/status/<job_id>")
def status(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "unknown job_id", "error_type": "not_found"}), 404
        try:
            since = int(request.args.get("since", 0))
        except ValueError:
            since = 0
        include_data = request.args.get("data", "1") != "0"
        return jsonify(_public_job(job, since=since, include_data=include_data))


@app.get("/api/stream/<job_id>")
def stream(job_id):
    """Server-Sent Events: pushes log lines + progress, no polling required."""
    with LOCK:
        if job_id not in JOBS:
            return jsonify({"error": "unknown job_id", "error_type": "not_found"}), 404

    @stream_with_context
    def generate():
        cursor = 0
        last_progress = -1
        last_beat = time.time()
        while True:
            with LOCK:
                job = JOBS.get(job_id)
                if not job:
                    yield "event: end\ndata: {}\n\n"
                    return
                new_lines = job["log"][cursor:]
                cursor = len(job["log"])
                progress = job["progress"]
                state = job["status"]

            if new_lines:
                yield "event: log\ndata: " + json.dumps({"lines": new_lines, "seq": cursor}) + "\n\n"
            if progress != last_progress:
                last_progress = progress
                yield "event: progress\ndata: " + json.dumps({"progress": progress}) + "\n\n"

            if state in {"done", "error", "cancelled"}:
                with LOCK:
                    payload = _public_job(JOBS[job_id], since=0, include_data=True)
                    payload["log"] = []          # already streamed
                yield "event: done\ndata: " + json.dumps(payload, default=str) + "\n\n"
                return

            if time.time() - last_beat > 15:
                last_beat = time.time()
                yield ": keep-alive\n\n"
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@app.post("/api/jobs/<job_id>/cancel")
def cancel(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "unknown job_id", "error_type": "not_found"}), 404
        if job["status"] in {"done", "error", "cancelled"}:
            return jsonify({"ok": True, "status": job["status"], "note": "job already finished"})
        job["cancel_event"].set()
        job["log"].append({"t": time.time(), "msg": "Cancellation requested — finishing current page…"})
    return jsonify({"ok": True, "status": "cancelling"})


@app.get("/api/jobs")
def list_jobs():
    _cleanup_jobs()
    with LOCK:
        items = [{
            "job_id": j["job_id"], "query": j["query"], "status": j["status"],
            "row_count": j["row_count"], "created_at": j["created_at"],
            "duration": j["duration"],
        } for j in JOBS.values()]
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify({"jobs": items})


@app.delete("/api/jobs/<job_id>")
def delete_job(job_id):
    with LOCK:
        job = JOBS.pop(job_id, None)
    if not job:
        return jsonify({"error": "unknown job_id", "error_type": "not_found"}), 404
    path = job.get("csv_path")
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
def _job_dataframe(job: Dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(job.get("data") or [])


@app.get("/api/download/<job_id>")
def download(job_id):
    fmt = (request.args.get("format") or "csv").lower()
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "unknown job_id", "error_type": "not_found"}), 404
        if job["status"] not in {"done", "cancelled"} or not job.get("data"):
            return jsonify({"error": "job has no completed results yet",
                            "error_type": "not_ready"}), 409
        query = job["query"]
        df = _job_dataframe(job)
        stats = job.get("stats") or {}

    safe = "".join(c if c.isalnum() or c in "-_ " else "" for c in query).strip().replace(" ", "-")[:50] or "results"
    stamp = time.strftime("%Y%m%d-%H%M")

    if fmt == "json":
        payload = json.dumps({"query": query, "stats": stats,
                              "rows": job.get("data")}, indent=2, default=str)
        return Response(payload, mimetype="application/json", headers={
            "Content-Disposition": f'attachment; filename="price-matrix-{safe}-{stamp}.json"'})

    if fmt in {"xlsx", "excel"}:
        path = os.path.join(EXPORT_DIR, f"price-matrix-{safe}-{stamp}.xlsx")
        try:
            _write_xlsx(path, df, stats, query)
        except Exception as exc:
            logger.exception("XLSX export failed")
            return jsonify({"error": f"XLSX export failed: {exc}",
                            "error_type": "export"}), 500
        return send_file(path, as_attachment=True,
                         download_name=os.path.basename(path),
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    csv_path = job.get("csv_path")
    if csv_path and os.path.exists(csv_path):
        return send_file(csv_path, as_attachment=True,
                         download_name=f"price-matrix-{safe}-{stamp}.csv",
                         mimetype="text/csv")
    return Response(df.to_csv(index=False, encoding="utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="price-matrix-{safe}-{stamp}.csv"'})


def _write_xlsx(path: str, df: pd.DataFrame, stats: Dict[str, Any], query: str) -> None:
    """Formatted workbook: Listings sheet + Summary sheet."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    cols = ["platform", "title", "brand", "price", "mrp", "discount_pct", "rating",
            "reviews", "specs", "value_score", "price_diff", "price_diff_label",
            "cheaper_on", "match_score", "sponsored", "link"]
    present = [c for c in cols if c in df.columns]
    out = df[present].copy() if present else df.copy()

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        out.to_excel(writer, index=False, sheet_name="Listings")

        summary_rows = [("Query", query), ("Generated", time.strftime("%Y-%m-%d %H:%M")),
                        ("Total listings", stats.get("total", len(df)))]
        for name in ("amazon", "flipkart"):
            block = stats.get(name) or {}
            if block:
                summary_rows += [
                    (f"{name.title()} listings", block.get("count")),
                    (f"{name.title()} lowest", block.get("min")),
                    (f"{name.title()} average", block.get("avg")),
                    (f"{name.title()} cheapest item", block.get("cheapest_title")),
                ]
        summary_rows += [("Cheaper platform", stats.get("cheaper_platform")),
                         ("Saving vs other store", stats.get("saving")),
                         ("Matched pairs", stats.get("matched_pairs"))]
        bv = stats.get("best_value") or {}
        if bv:
            summary_rows += [("Best value pick", bv.get("title")),
                             ("Best value price", bv.get("price")),
                             ("Best value store", bv.get("platform"))]
        pd.DataFrame(summary_rows, columns=["Metric", "Value"]).to_excel(
            writer, index=False, sheet_name="Summary")

        header_fill = PatternFill("solid", fgColor="1F2937")
        header_font = Font(color="FFFFFF", bold=True)
        for sheet_name in ("Listings", "Summary"):
            ws = writer.sheets[sheet_name]
            for cell in ws[1]:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(vertical="center")
            ws.freeze_panes = "A2"
            for idx, column_cells in enumerate(ws.columns, start=1):
                longest = max((len(str(c.value)) for c in column_cells if c.value is not None), default=10)
                ws.column_dimensions[get_column_letter(idx)].width = min(max(12, longest + 2), 60)
            if sheet_name == "Listings":
                ws.auto_filter.ref = ws.dimensions
                for col_name in ("price", "mrp"):
                    if col_name in present:
                        letter = get_column_letter(present.index(col_name) + 1)
                        for cell in ws[letter][1:]:
                            cell.number_format = '₹#,##0'


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "endpoint not found", "error_type": "not_found"}), 404
    return index()


@app.errorhandler(500)
def server_error(e):
    logger.exception("Unhandled server error")
    return jsonify({"error": str(e), "error_type": "server"}), 500


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PM_PORT", os.environ.get("PRICE_LEDGER_PORT", 8787)))
    host = os.environ.get("PM_HOST", "127.0.0.1")
    st = chrome_status()
    banner = "READY" if st["ready"] else "NOT READY (install Chrome / selenium)"
    logger.info("=" * 72)
    logger.info(" Price Matrix v2  ->  http://%s:%s", host, port)
    logger.info(" Scraper: %s  %s", banner, st.get("chrome_version", ""))
    logger.info(" Exports: %s", EXPORT_DIR)
    logger.info("=" * 72)
    app.run(host=host, port=port, debug=False, threaded=True)
