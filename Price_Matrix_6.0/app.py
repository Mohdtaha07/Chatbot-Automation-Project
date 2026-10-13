"""
app.py — the glue that links scrapper.py + parser.py + comparator.py + chatbot.py
          into the Price Matrix web UI (templates/index.html).

What it does
------------
* Serves the dashboard at  /                      (templates/index.html)
* POST /api/run                starts a job: scrape -> parse -> compare, in background
* GET  /api/stream/<job_id>    Server-Sent Events: live log lines, progress, final data
* GET  /api/status/<job_id>    polling fallback (same payload as the SSE 'done' event)
* POST /api/jobs/<id>/cancel   cooperative cancel (checked between pages)
* GET  /api/download/<id>      CSV / XLSX / JSON export built from the job's rows
* GET  /api/health             is selenium + a browser available?
* GET  /api/result/<file>      serve the artefacts on disk (compared_*.csv, *_cleaned.csv)
* /api/chat, /api/chat/suggestions   from chatbot.py (registered as a blueprint)

The heavy lifting is NOT reimplemented here — every stage calls the original
module functions:

    scrapper.scrape_platform(...)        -> raw HTML cards on disk
    parser.process_data(...)             -> amazon/flipkart_<product>_data_cleaned.csv
    comparator.run_price_comparison(...) -> compared_<product>_results.csv
    chatbot.answer(...)                  -> the Matrix Assistant replies

Run it
------
    pip install -r requirements.txt
    python app.py            # then open http://127.0.0.1:8787

Google Chrome must be installed (Selenium drives it; Selenium 4.6+ downloads the
matching driver itself). If Chrome isn't found, make_driver() falls back to
Edge -> Chromium automatically.

The old scripted CLIs still work unchanged, if you ever want them:
    python scrapper.py     # prompt -> scrape -> parse -> compare, terminal only
    python parser.py       # re-parse one platform's saved HTML
    python comparator.py   # re-run the comparison on existing cleaned CSVs

Env knobs (all optional):
    PM_HOST=0.0.0.0          bind address (default 127.0.0.1)
    PM_PORT=8787             port
    PM_OPEN_BROWSER=1        auto-open the dashboard in your default browser
    PM_KEEP_HTML=1           keep per-card HTML folders after the run
    PM_KEEP_CLEANED=1        keep *_data_cleaned.csv after the run
    PM_KEEP_COMPARED=1       keep compared_*_results.csv after the run
    PM_MAX_PAGES=10          upper bound for pages-per-store
    PM_CORS=0                disable permissive CORS on /api/* (see below)

Note on CORS: it is ON by default only so index.html also works when opened
straight from disk (Origin: null) or inside a sandboxed preview iframe. Served
normally from this Flask app the page is same-origin and CORS never matters.

Troubleshooting
---------------
* "Backend offline" under the search bar -> `python app.py` isn't running.
* "Scraper not ready: Chrome was not found" -> install Google Chrome (or Edge).
* 0 results / empty table -> Amazon or Flipkart served a bot wall. Use fewer
  pages, wait a minute, or untick "Headless browser" once to solve a CAPTCHA.
* Run looks stuck at a low % -> the scraper sleeps ~1-3 s per page on purpose
  to avoid IP blocks; watch the Live scan log.
* XLSX opened a tab instead of downloading -> you opened index.html via
  file://; Chrome blocks cross-origin downloads there. Use the served URL
  http://127.0.0.1:8787 instead, or export CSV.
* Port already in use -> PM_PORT=8899 python app.py (a served page follows
  automatically; only file:// opens need the API constant in index.html updated).
"""

from __future__ import annotations

import io
import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
import webbrowser
from typing import Any, Dict, List, Optional

import pandas as pd
from flask import Flask, Response, abort, jsonify, render_template, request, send_file

# ---------------------------------------------------------------------------
# The five project modules. These are the ONLY places pipeline work happens.
# ---------------------------------------------------------------------------
from paths import PROJECT_ROOT, data_folder, output_path
from logger import log, set_sink, clear_sink
from parser import process_data
from comparator import run_price_comparison
from scrapper import PLATFORMS, scrape_platform
from chatbot import chatbot_bp, register_data_source

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HOST = os.environ.get("PM_HOST", "127.0.0.1")
PORT = int(os.environ.get("PM_PORT", "8787"))
MAX_PAGES_CAP = int(os.environ.get("PM_MAX_PAGES", "10"))
OPEN_BROWSER = os.environ.get("PM_OPEN_BROWSER", "0") == "1"

KEEP_HTML = os.environ.get("PM_KEEP_HTML", "0") == "1"
KEEP_CLEANED = os.environ.get("PM_KEEP_CLEANED", "0") == "1"
KEEP_COMPARED = os.environ.get("PM_KEEP_COMPARED", "1") == "1"

JOB_HISTORY = 12          # how many finished jobs to remember
LOG_TAIL = 4000           # cap on in-memory log lines per job

app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
app.json.sort_keys = False          # keep dict order in API payloads
app.register_blueprint(chatbot_bp)          # /api/chat + /api/chat/suggestions


# ===========================================================================
# Jobs
# ===========================================================================
class Job:
    """One scrape->parse->compare run, with a log bus for the browser."""

    def __init__(self, query: str, pages: int, platforms: List[str],
                 headless: bool = False, include_sponsored: bool = True):
        self.id = uuid.uuid4().hex[:12]
        self.query = query
        self.slug = slugify(query)
        self.pages = max(1, min(MAX_PAGES_CAP, int(pages or 1)))
        self.platforms = [p for p in platforms if p in PLATFORM_NAMES] or list(PLATFORM_NAMES)
        self.headless = bool(headless)
        self.include_sponsored = bool(include_sponsored)

        self.status = "running"            # running | done | error | cancelled
        self.progress = 0
        self.error: Optional[str] = None
        self.error_type: Optional[str] = None
        self.errors: Dict[str, str] = {}   # per-platform failures
        self.data: List[Dict[str, Any]] = []
        self.comparison: List[Dict[str, Any]] = []

        self.started = time.time()
        self.finished: Optional[float] = None
        self.cancel_event = threading.Event()
        self.streams: List[queue.Queue] = []

        self._lock = threading.Lock()
        self._log: List[Dict[str, Any]] = []

    # ---- log bus ---------------------------------------------------------
    def emit(self, msg: str) -> None:
        """Sink installed via logger.set_sink — every module's log() lands here."""
        entry = {"t": time.time(), "msg": "" if msg is None else str(msg)}
        with self._lock:
            self._log.append(entry)
            if len(self._log) > LOG_TAIL:
                del self._log[:len(self._log) - LOG_TAIL]
        self.publish("log", {"lines": [entry]})

    def publish(self, event: str, payload: Dict[str, Any]) -> None:
        for q in list(self.streams):
            try:
                q.put_nowait((event, payload))
            except queue.Full:
                pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=4000)
        self.streams.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        try:
            self.streams.remove(q)
        except ValueError:
            pass

    # ---- progress --------------------------------------------------------
    def set_progress(self, pct: float) -> None:
        pct = max(0.0, min(100.0, float(pct)))
        with self._lock:
            if pct <= self.progress:        # monotonic: never jump backwards
                return
            self.progress = pct
        self.publish("progress", {"progress": round(pct)})

    # ---- lifecycle -------------------------------------------------------
    def finish(self, status: str, error: Optional[str] = None, error_type: Optional[str] = None) -> None:
        if status == "cancelled":
            self.cancel_event.set()
        self.status = status
        self.error = error
        self.error_type = error_type
        self.finished = time.time()
        if status == "done":
            self.set_progress(100)
        self.publish("done", self.snapshot())

    @property
    def duration(self) -> float:
        return round((self.finished or time.time()) - self.started, 1)

    def snapshot(self, since: int = 0) -> Dict[str, Any]:
        with self._lock:
            logs = self._log[since:]
            seq = len(self._log)
        return {
            "job_id": self.id,
            "query": self.query,
            "status": self.status,
            "progress": round(self.progress),
            "log": logs,
            "log_seq": seq,
            "errors": dict(self.errors),
            "data": self.data,
            "comparison": self.comparison,
            "count": len(self.data),
            "matched_pairs": len(self.comparison),
            "duration": self.duration,
            "error": self.error,
            "error_type": self.error_type,
            "files": {
                "cleaned": [f"{p}_{self.slug}_data_cleaned.csv" for p in self.platforms],
                "compared": f"compared_{self.slug}_results.csv",
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return self.snapshot()


PLATFORM_NAMES = [p["name"] for p in PLATFORMS]
JOBS: Dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def slugify(text: str) -> str:
    """Same normalisation parser.py / comparator.py already use for file names."""
    return text.strip().lower().replace(" ", "_")


def remember(job: Job) -> None:
    with JOBS_LOCK:
        JOBS[job.id] = job
        if len(JOBS) > JOB_HISTORY:
            for old_id in sorted(JOBS, key=lambda k: JOBS[k].started)[: len(JOBS) - JOB_HISTORY]:
                JOBS.pop(old_id, None)


def get_job(job_id: str) -> Optional[Job]:
    with JOBS_LOCK:
        return JOBS.get(job_id)


# ===========================================================================
# Browser helpers
# ===========================================================================
MAC_CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
WIN_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
CHROME_BINARIES = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "chrome", "brave-browser", "msedge", "microsoft-edge",
]


def browser_health() -> Dict[str, Any]:
    """Used by /api/health so the UI can warn *before* a run fails."""
    selenium_available = True
    selenium_error = None
    try:
        import selenium  # noqa: F401
        from selenium import webdriver  # noqa: F401
    except Exception as exc:                       # pragma: no cover
        selenium_available, selenium_error = False, str(exc)

    found = [b for b in CHROME_BINARIES if shutil.which(b)]
    chrome_found = bool(found)
    if not chrome_found:
        chrome_found = any(os.path.exists(p) for p in MAC_CHROME_PATHS + WIN_CHROME_PATHS)

    return {
        "ok": selenium_available and chrome_found,
        "scraper_ready": selenium_available and chrome_found,
        "selenium_available": selenium_available,
        "selenium_error": selenium_error,
        "chrome_found": chrome_found,
        "chrome_binaries": found,
        "platforms": PLATFORM_NAMES,
    }


def make_driver(headless: bool = False):
    """
    Build a Selenium driver the way scrapper.py expects.

    Chrome first (what the original script used, and what the Amazon/Flipkart
    selectors were tuned against), then Edge/Brave/Chromium as a courtesy so the
    app still works on machines without Chrome installed.
    """
    from selenium import webdriver

    common = ["--window-size=1440,1024", "--disable-blink-features=AutomationControlled"]
    if headless:
        common += ["--headless=new", "--disable-gpu", "--no-sandbox"]

    attempts = []
    try:
        opts = webdriver.ChromeOptions()
        for a in common:
            opts.add_argument(a)
        return webdriver.Chrome(options=opts)
    except Exception as exc:
        attempts.append(f"Chrome: {exc}")

    edge_opts = getattr(webdriver, "EdgeOptions", None)
    edge_driver = getattr(webdriver, "Edge", None)
    if edge_opts is not None and edge_driver is not None:
        try:
            opts = edge_opts()
            for a in common:
                opts.add_argument(a)
            return edge_driver(options=opts)
        except Exception as exc:
            attempts.append(f"Edge: {exc}")

    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    if chromium:
        try:
            opts = webdriver.ChromeOptions()
            opts.binary_location = chromium
            for a in common:
                opts.add_argument(a)
            return webdriver.Chrome(options=opts)
        except Exception as exc:
            attempts.append(f"Chromium: {exc}")

    raise RuntimeError(
        "No usable browser could be started. "
        + " | ".join(attempts)
        + " — install Google Chrome (or Edge) and make sure `pip install -r requirements.txt` has run."
    )


# ===========================================================================
# Normalising parsed CSVs into the row shape templates/index.html renders
# ===========================================================================
BRAND_NAMES = [
    "hp", "dell", "lenovo", "asus", "acer", "msi", "apple", "samsung", "xiaomi", "redmi",
    "realme", "infinix", "oneplus", "oppo", "vivo", "motorola", "nothing", "iqoo", "poco",
    "tecno", "honor", "lg", "sony", "toshiba", "microsoft", "panasonic", "canon", "nikon",
    "jbl", "boat", "noise", "bose", "sennheiser", "logitech", "razer", "corsair", "intel",
    "amd", "nvidia", "gigabyte", "zotac", "philips", "bosch", "haier", "whirlpool", "godrej",
    "voltas", "daikin", "hitachi", "carrier", "ifb", "agaro", "wipro", "tplink", "tp-link",
    "d-link", "netgear", "lava", "micromax", "blackberry",
]
BRAND_CASE = {
    "hp": "HP", "msi": "MSI", "asus": "ASUS", "lg": "LG", "jbl": "JBL", "amd": "AMD",
    "ifb": "IFB", "iqoo": "iQOO", "tp-link": "TP-Link", "tplink": "TP-Link",
}
_BRAND_RE = {b: re.compile(rf"(?<![a-z0-9]){re.escape(b)}(?![a-z0-9])") for b in set(BRAND_NAMES)}

SPEC_EXTRA_RES = [
    re.compile(r"\b(rtx\s?\d{3,4}|gtx\s?\d{3,4})\b", re.I),
    re.compile(r"\b(\d{2,3})\s?hz\b", re.I),
    re.compile(r"\b(\d{2}(?:\.\d)?)\s?(?:\"|inch|inches|-inch)\b", re.I),
    re.compile(r"\b(5g|4g|amoled|oled|ips|retina|touch(?:screen)?|wifi\s?6|bluetooth)\b", re.I),
]


def extract_brand(title: str) -> str:
    t = " " + (title or "").lower() + " "
    for brand in sorted(_BRAND_RE, key=len, reverse=True):
        if _BRAND_RE[brand].search(t):
            return BRAND_CASE.get(brand, brand.title())
    return (title.split()[0] if title else "") or "—"


def parse_money(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return float(digits) if digits else None


def extract_mrp(raw_price: Any, price: Optional[float]) -> Optional[float]:
    """Pull the struck-through MRP out of the raw price string when present."""
    if raw_price is None or (isinstance(raw_price, float) and pd.isna(raw_price)):
        return None
    s = str(raw_price)
    candidates: List[Optional[float]] = []
    m = re.search(r"(?:m\.?\s?r\.?\s?p\.?|list price)\s*:?\s*(?:₹|rs\.?|inr)?\s*([\d,]+)", s, re.I)
    if m:
        candidates.append(parse_money(m.group(1)))
    else:
        for amount in re.findall(r"(?:₹|rs\.?|inr)\s*([\d,]{3,})", s, re.I):
            candidates.append(parse_money(amount))
    candidates = [c for c in candidates if c and price and c > price * 1.02]
    return max(candidates) if candidates else None


def parse_rating(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value) or None
    m = re.search(r"([0-5](?:\.\d)?)", str(value))
    return float(m.group(1)) or None if m else None


def parse_review_count(explicit_count: Any = None, *texts: Any) -> int:
    """
    Review/rating volume, when the card actually advertised one.

    `Review_Count` is the explicit count column; `Review` holds the star rating
    ("4.4"), which must never be mistaken for a count. So: take the numeric
    column as-is, and only accept free text that literally mentions
    ratings/reviews (note that numpy floats *are* Python floats, so the two
    paths must stay separate).
    """
    try:
        n = int(float(explicit_count))
        if n > 0:
            return n
    except (TypeError, ValueError):
        pass

    for t in texts:
        if t is None:
            continue
        text = str(t)
        if not re.search(r"ratings?|reviews?|count", text, re.I):
            continue
        m = re.search(r"([\d,]+)", text)
        if m:
            n = int(m.group(1).replace(",", ""))
            if n > 0:
                return n
    return 0


def build_specs(title: str, ram: Any, storage: Any, processor: Any) -> str:
    bits: List[str] = []
    for v in (ram, storage, processor):
        v = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
        if v and v.upper() != "N/A" and v not in bits:
            bits.append(v.upper() if ("gb" in v.lower() or "tb" in v.lower()) else v)
    for rx in SPEC_EXTRA_RES:
        for m in rx.finditer(title or ""):
            token = m.group(1).strip()
            if token.lower() not in [b.lower() for b in bits]:
                bits.append(token)
    return " · ".join(bits[:5])


def build_rows_from_csv(csv_path: str, platform: str, job: Job) -> List[Dict[str, Any]]:
    """Turn parser.py's cleaned CSV into the exact row shape the dashboard renders."""
    df = pd.read_csv(csv_path).fillna("")
    rows: List[Dict[str, Any]] = []

    for _, r in df.iterrows():
        title = str(r.get("Title") or "").strip()
        if not title or title.lower() in {"unknown title", "unknown product"}:
            continue
        price = parse_money(r.get("Numeric_Price") if "Numeric_Price" in df.columns else r.get("Price"))
        if not price or price <= 0:
            continue

        mrp = extract_mrp(r.get("Price"), price)
        discount = round((mrp - price) / mrp * 100) if mrp else 0

        link = str(r.get("Link") or "").strip()
        if not link.lower().startswith("http"):
            link = ""

        rows.append({
            "platform": "Amazon" if platform == "amazon" else "Flipkart",
            "brand": extract_brand(title),
            "title": title,
            "price": price,
            "mrp": mrp,
            "discount_pct": discount,
            "rating": parse_rating(r.get("Review")),
            "reviews": parse_review_count(r.get("Review_Count"), r.get("Review")),
            "specs": build_specs(title, r.get("RAM"), r.get("Storage"), r.get("Processor")),
            "link": link,
            # Heuristic: the word "Sponsored" in the card title. It is what the
            # "Hide sponsored" toggle filters on, and it can be a no-op depending
            # on what the store actually returns in the card HTML.
            "sponsored": bool(re.search(r"\bsponsored\b", title, re.I)),
            "image": "",
            # cross-store fields are filled in by enrich_matches()
            "match_title": "",
            "match_link": "",
            "match_score": 0,
            "price_diff": None,
            "price_diff_label": "No match",
            "cheaper_on": "",
            "value_score": 0,
        })

    if not job.include_sponsored:
        rows = [r for r in rows if not r["sponsored"]]
    return rows


def enrich_matches(rows: List[Dict[str, Any]], comp_df: Optional[pd.DataFrame]) -> None:
    """
    Fold comparator.py's comparison table back onto the listing rows so the table's
    "vs. other store" column, the compare drawer and the chatbot's "matches"
    intent all see the real cross-store deltas.
    """
    if comp_df is None or getattr(comp_df, "empty", True):
        return

    used: set = set()

    def find(platform_name: str, title: str, price: float) -> Optional[Dict[str, Any]]:
        pool = [(i, r) for i, r in enumerate(rows) if r["platform"] == platform_name and i not in used]
        if not pool:
            return None
        for i, r in pool:                                  # 1) exact listing
            if r["title"] == title and abs(r["price"] - price) < 1:
                used.add(i)
                return r
        for i, r in pool:                                  # 2) closest price
            if abs(r["price"] - price) < 1:
                used.add(i)
                return r
        i, r = min(pool, key=lambda t: abs(t[1]["price"] - price))
        used.add(i)
        return r

    for _, c in comp_df.iterrows():
        a_price = float(c.get("Amazon_Price") or 0)
        f_price = float(c.get("Flipkart_Price") or 0)
        if a_price <= 0 or f_price <= 0:
            continue
        score = float(c.get("Similarity_Score") or 0)
        verdict = str(c.get("Cheaper_At") or "")
        a_title, f_title = str(c.get("Amazon_Title") or ""), str(c.get("Flipkart_Title") or "")

        a_row = find("Amazon", a_title, a_price)
        f_row = find("Flipkart", f_title, f_price)

        if a_row is not None:
            delta = a_price - f_price
            a_row.update({
                "match_title": f_title, "match_link": f_row["link"] if f_row else "",
                "match_score": score, "price_diff": delta,
                "price_diff_label": diff_label(delta, verdict), "cheaper_on": verdict,
            })
        if f_row is not None:
            delta = f_price - a_price
            f_row.update({
                "match_title": a_title, "match_link": a_row["link"] if a_row else "",
                "match_score": score, "price_diff": delta,
                "price_diff_label": diff_label(delta, verdict), "cheaper_on": verdict,
            })


def diff_label(delta: float, verdict: str) -> str:
    if abs(delta) < 1:
        return "Same price on both"
    sign = "+" if delta > 0 else "-"
    return f"{sign}₹{abs(int(round(delta))):,} · cheaper on {verdict or '—'}"


def score_value(rows: List[Dict[str, Any]]) -> None:
    """
    Value score, mirroring the weights described in chatbot.py's FAQ:
    42% price position | 30% rating | 16% review volume | 12% discount.
    """
    priced = [r for r in rows if r["price"]]
    if not priced:
        return
    prices = [r["price"] for r in priced]
    lo, hi = min(prices), max(prices)
    span = (hi - lo) or 1
    for r in priced:
        r["value_score"] = round(100 * (
            0.42 * (1 - (r["price"] - lo) / span)
            + 0.30 * ((r["rating"] or 0) / 5.0)
            + 0.16 * min(1.0, (r["reviews"] or 0) / 2000.0)
            + 0.12 * ((r["discount_pct"] or 0) / 100.0)
        ), 1)


def rows_to_dicts(df: pd.DataFrame) -> List[Dict[str, Any]]:
    return json.loads(df.to_json(orient="records", force_ascii=False))


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Lightweight stats — used by the chatbot's job_id data source."""
    priced = [r["price"] for r in rows if r.get("price")]
    if not priced:
        return {}
    return {"count": len(priced), "min": min(priced), "max": max(priced),
            "avg": sum(priced) / len(priced)}


# ===========================================================================
# The pipeline (runs in a worker thread per job)
# ===========================================================================
class ProgressTicker:
    """Spreads the 5-50% band across every page that will be walked."""

    def __init__(self, job: Job, total_pages: int):
        self.job = job
        self.total = max(1, total_pages)
        self.done = 0
        self._lock = threading.Lock()

    def tick(self, n: int = 1) -> None:
        with self._lock:
            self.done += n
            pct = min(50.0, 5 + (self.done / self.total) * 45.0)
        self.job.set_progress(pct)


def scrape_worker(job: Job, site_name: str, results: Dict[str, Any], ticker: ProgressTicker) -> None:
    """One platform, one thread — failures are captured per store, never fatal."""
    folder = data_folder(site_name, job.slug)
    try:
        log("")
        log("=========================================")
        log(f"[*] STARTING SCRAPER FOR: {site_name.upper()}")
        log("=========================================")

        # Fresh folder per run (a partial page count from an earlier run would
        # otherwise leave stale .html cards that parser.py would happily read).
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if name.endswith(".html"):
                    try:
                        os.remove(os.path.join(folder, name))
                    except OSError:
                        pass

        result = scrape_platform(
            job.query, site_name,
            max_pages=job.pages,
            out_dir=folder,
            driver_factory=lambda: make_driver(headless=job.headless),
            headless=job.headless,
            cancel_event=job.cancel_event,
            on_page=lambda i, total: ticker.tick(1),
        )
        results[site_name] = {"folder": folder, **result}
        log(f"[+] Finished raw scraping for {site_name} — {result['files_saved']} card(s) saved.")
    except Exception as exc:                                  # noqa: BLE001
        results[site_name] = {"folder": folder, "files_saved": 0,
                              "cancelled": job.cancel_event.is_set(), "error": str(exc)}
        job.errors[site_name] = str(exc)
        log(f"[x] {site_name.upper()} failed: {exc}")
        ticker.tick(job.pages)                                # keep the bar moving
    finally:
        ticker.tick(0)          # recompute the bar from however far we got


def run_pipeline(job: Job) -> None:
    """scrape (parallel) -> parse -> compare -> package. Logs stream the whole way."""
    set_sink(job.emit)
    compared_df: Optional[pd.DataFrame] = None
    try:
        remember(job)
        log(f"[+] Job {job.id} — query \"{job.query}\", {job.pages} page(s)/store, "
            f"stores: {', '.join(job.platforms)}")
        log(f"[+] Output folder: {PROJECT_ROOT}")

        ticker = ProgressTicker(job, total_pages=job.pages * len(job.platforms))
        job.set_progress(3)

        # ---- 1) SCRAPE both stores concurrently --------------------------
        results: Dict[str, Any] = {}
        threads = [
            threading.Thread(target=scrape_worker, args=(job, name, results, ticker),
                             name=f"scrape-{name}", daemon=True)
            for name in job.platforms
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        job.set_progress(55)

        # ---- 2) PARSE each store's HTML with parser.py -------------------
        cleaned_paths: Dict[str, str] = {}
        for name in job.platforms:
            info = results.get(name) or {}
            folder = info.get("folder") or data_folder(name, job.slug)
            if info.get("files_saved"):
                csv_path = output_path(f"{name}_{job.slug}_data_cleaned.csv")
                log(f"[*] Running data parsing pipeline for {name}...")
                process_data(job.query, name, source_folder=folder, output_file=csv_path)
                if os.path.exists(csv_path):
                    cleaned_paths[name] = csv_path
                    parsed = build_rows_from_csv(csv_path, name, job)
                    job.data.extend(parsed)
                    log(f"[+] {name}: {len(parsed)} usable listing(s) after cleaning.")
                    # NOTE: the CSV stays on disk until step 3 has read it —
                    # comparator.py consumes these exact files.
                else:
                    job.errors.setdefault(name, "parser produced no CSV")
                    log(f"[x] {name}: parser produced no CSV.")
            else:
                log(f"[!] {name}: no raw HTML to parse (0 cards captured).")

        job.set_progress(72)

        # ---- 3) COMPARE with comparator.py -------------------------------
        missing = [n for n in ("amazon", "flipkart") if n not in cleaned_paths]
        if len(cleaned_paths) == 2:
            log("")
            log("=========================================")
            log("[+] ALL DATASETS CAPTURED & PARSED")
            log("[*] Initiating Cross-Platform Comparison Evaluation...")
            log("=========================================")
            compared_path = output_path(f"compared_{job.slug}_results.csv")
            compared_df = run_price_comparison(
                job.query,
                amazon_file=cleaned_paths["amazon"],
                flipkart_file=cleaned_paths["flipkart"],
                output_filename=compared_path,
            )
            if compared_df is not None:
                job.comparison = rows_to_dicts(compared_df)
                log(f"[+] Cross-store matches: {len(job.comparison)}")
            if not KEEP_COMPARED:
                try:
                    os.remove(compared_path)
                except OSError:
                    pass
        else:
            log(f"[!] Comparison skipped — missing cleaned data for: {', '.join(missing) or 'n/a'}. "
                f"Both stores must return priced listings to cross-match.")

        job.set_progress(90)

        # ---- 3b) Cleaned CSVs are now safe to remove ----------------------
        if not KEEP_CLEANED:
            for path in cleaned_paths.values():
                try:
                    os.remove(path)
                except OSError:
                    pass

        # ---- 4) Fold matches + value scores onto the rows -----------------
        enrich_matches(job.data, compared_df)
        score_value(job.data)
        job.data.sort(key=lambda r: -(r.get("value_score") or 0))

        # ---- 5) Optional cleanup of the raw HTML --------------------------
        if not KEEP_HTML:
            for name in job.platforms:
                folder = (results.get(name) or {}).get("folder") or data_folder(name, job.slug)
                shutil.rmtree(folder, ignore_errors=True)
            log("[+] Raw HTML folders cleaned (PM_KEEP_HTML=1 to keep them).")

        # ---- 6) Wrap up ---------------------------------------------------
        if job.cancel_event.is_set():
            log("[!] Job cancelled.")
            job.finish("cancelled")
            return

        if not job.data:
            detail = ("No listings were parsed from either store. That is usually a bot wall or a "
                      "CAPTCHA — try fewer pages, wait a minute, or turn OFF headless mode once.")
            log(f"[x] {detail}")
            job.finish("error", error=detail, error_type="empty")
            return

        log("")
        log(f"[+] DONE — {len(job.data)} listing(s) across {len(job.platforms)} store(s), "
            f"{len(job.comparison)} cross-store match(es).")
        job.finish("done")
    except Exception as exc:                                  # noqa: BLE001
        log(f"[x] Pipeline error: {exc}")
        job.errors["pipeline"] = str(exc)
        job.finish("error", error=str(exc), error_type="pipeline")
    finally:
        clear_sink()


# ===========================================================================
# HTTP routes
# ===========================================================================
@app.get("/")
def index():
    """
    Serve the dashboard exactly as it exists on disk — no rewriting, no injection.

    Order:
      1. templates/index.html  (conventional Flask layout, if you put one there)
      2. index.html next to app.py

    The page is same-origin with the API, so its own `const API = ...` resolves to
    '' and every relative /api/... call lands here directly.
    """
    template = os.path.join(PROJECT_ROOT, "templates", "index.html")
    if os.path.exists(template):
        return render_template("index.html")

    root_html = os.path.join(PROJECT_ROOT, "index.html")
    if os.path.exists(root_html):
        with open(root_html, "r", encoding="utf-8") as fh:
            return fh.read()

    return (
        "<h1>Price Matrix</h1><p>No <code>index.html</code> found next to app.py. "
        "Put the dashboard HTML there (or in <code>templates/index.html</code>) and reload.</p>",
        404,
    )


# ---------------------------------------------------------------------------
# CORS — needed by the two ways the dashboard can run off-origin
# ---------------------------------------------------------------------------
# Flask normally serves the page itself (same-origin, no CORS needed). But the
# dashboard also supports:
#   * being opened straight from disk   -> the browser sends `Origin: null`
#   * being embedded in a sandboxed preview iframe -> also `Origin: null`
# In both cases fetch()/EventSource are cross-origin requests, and a JSON POST
# additionally triggers a preflight OPTIONS. Without these headers the UI would
# report "Backend offline" even though the server is fine.
#
# Local-only dev tool, no cookies/sessions/auth, so a permissive policy is
# acceptable — set PM_CORS=0 to turn it off.
ALLOW_ANY_ORIGIN = os.environ.get("PM_CORS", "1") == "1"

CORS_METHODS = "GET, POST, OPTIONS"
CORS_HEADERS = "Content-Type"


@app.after_request
def add_cors_headers(response):
    if ALLOW_ANY_ORIGIN and request.path.startswith("/api/"):
        response.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin") or "*"
        response.headers["Access-Control-Allow-Headers"] = CORS_HEADERS
        response.headers["Access-Control-Allow-Methods"] = CORS_METHODS
        response.headers["Vary"] = "Origin"
    return response


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def api_preflight(_any: str):
    """Answer the preflight for cross-origin JSON POSTs (/api/run, /api/chat)."""
    return ("", 204)


@app.get("/api/health")
def api_health():
    info = browser_health()
    info["output_dir"] = PROJECT_ROOT
    info["keep"] = {"html": KEEP_HTML, "cleaned": KEEP_CLEANED, "compared": KEEP_COMPARED}
    return jsonify(info)


@app.post("/api/run")
def api_run():
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "A product query is required."}), 400

    platforms = [str(p).lower() for p in (body.get("platforms") or PLATFORM_NAMES)]
    platforms = [p for p in platforms if p in PLATFORM_NAMES]
    if not platforms:
        return jsonify({"error": "Select at least one store."}), 400

    try:
        pages = max(1, min(MAX_PAGES_CAP, int(body.get("pages", 2))))
    except (TypeError, ValueError):
        pages = 2

    job = Job(
        query=query,
        pages=pages,
        platforms=platforms,
        headless=bool(body.get("headless", False)),
        include_sponsored=bool(body.get("include_sponsored", True)),
    )
    remember(job)
    threading.Thread(target=run_pipeline, args=(job,), name=f"job-{job.id}", daemon=True).start()
    return jsonify({"job_id": job.id, "status": job.status, "query": job.query,
                    "pages": job.pages, "platforms": job.platforms})


@app.get("/api/status/<job_id>")
def api_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        return jsonify({"error": "Unknown job id."}), 404
    try:
        since = max(0, int(request.args.get("since", 0)))
    except (TypeError, ValueError):
        since = 0
    return jsonify(job.snapshot(since=since))


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    return api_status(job_id)


@app.post("/api/jobs/<job_id>/cancel")
def api_cancel(job_id: str):
    job = get_job(job_id)
    if job is None:
        return jsonify({"error": "Unknown job id."}), 404
    job.cancel_event.set()
    job.emit("[!] Cancel requested — stopping after the current page...")
    return jsonify({"job_id": job.id, "cancelled": True, "status": job.status})


@app.get("/api/stream/<job_id>")
def api_stream(job_id: str):
    """Server-Sent Events: live log + progress + a final 'done' payload."""
    job = get_job(job_id)
    if job is None:
        return jsonify({"error": "Unknown job id."}), 404

    def gen():
        q = job.subscribe()
        try:
            # Replay whatever already happened (covers late subscribers).
            snap = job.snapshot()
            yield sse("log", {"lines": snap["log"]})
            yield sse("progress", {"progress": snap["progress"]})
            if job.status in ("done", "error", "cancelled"):
                yield sse("done", snap)
                return
            while True:
                try:
                    event, payload = q.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    if job.status in ("done", "error", "cancelled"):
                        yield sse("done", job.snapshot())
                        return
                    continue
                yield sse(event, payload)
                if event == "done":
                    return
        finally:
            job.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


def sse(event: str, payload: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


@app.get("/api/download/<job_id>")
def api_download(job_id: str):
    job = get_job(job_id)
    if job is None:
        return jsonify({"error": "Unknown job id."}), 404
    if not job.data:
        return jsonify({"error": "This job has no rows to export."}), 400

    fmt = (request.args.get("format") or "csv").lower()
    stamp = time.strftime("%Y%m%d-%H%M")

    if fmt == "json":
        buf = io.BytesIO(json.dumps(job.data, ensure_ascii=False, indent=2).encode("utf-8"))
        return send_file(buf, mimetype="application/json", as_attachment=True,
                         download_name=f"price-matrix-{job.slug}-{stamp}.json")

    if fmt == "xlsx":
        try:
            return send_file(build_xlsx(job), as_attachment=True,
                             download_name=f"price-matrix-{job.slug}-{stamp}.xlsx",
                             mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except ImportError:
            return jsonify({"error": "XLSX export needs openpyxl — run `pip install openpyxl`, "
                                     "or export CSV instead."}), 500

    df = pd.DataFrame(job.data)
    buf = io.BytesIO(df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"))
    return send_file(buf, mimetype="text/csv; charset=utf-8", as_attachment=True,
                     download_name=f"price-matrix-{job.slug}-{stamp}.csv")


@app.get("/api/result/<path:filename>")
def api_result(filename: str):
    """Serve artefacts sitting next to app.py (compared_*.csv, *_data_cleaned.csv)."""
    safe = os.path.basename(filename)
    path = output_path(safe)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=False)


# ---------------------------------------------------------------------------
# XLSX builder — the "formatted + summary" export promised by the UI menu
# ---------------------------------------------------------------------------
LISTING_COLS = ["platform", "brand", "title", "price", "mrp", "discount_pct", "rating",
                "reviews", "specs", "value_score", "price_diff", "cheaper_on", "match_score",
                "match_title", "link"]


def build_xlsx(job: Job) -> io.BytesIO:
    df = pd.DataFrame(job.data)
    if df.empty:
        raise ValueError("No rows")
    for col in LISTING_COLS:
        if col not in df.columns:
            df[col] = ""
    df = df[LISTING_COLS]

    comp = pd.DataFrame(job.comparison)
    priced = sorted(r["price"] for r in job.data if r.get("price"))
    summary = pd.DataFrame([
        ["Query", job.query],
        ["Run at", time.strftime("%Y-%m-%d %H:%M", time.localtime(job.started))],
        ["Stores", ", ".join(p.title() for p in job.platforms)],
        ["Pages per store", job.pages],
        ["Listings parsed", len(job.data)],
        ["Amazon listings", sum(1 for r in job.data if r["platform"] == "Amazon")],
        ["Flipkart listings", sum(1 for r in job.data if r["platform"] == "Flipkart")],
        ["Cross-store matches", len(job.comparison)],
        ["Lowest price", min(priced) if priced else ""],
        ["Highest price", max(priced) if priced else ""],
        ["Median price", priced[len(priced) // 2] if priced else ""],
        ["Duration (s)", job.duration],
    ], columns=["Metric", "Value"])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        if not comp.empty:
            comp.to_excel(writer, sheet_name="Cross-store matches", index=False)
        df.to_excel(writer, sheet_name="All listings", index=False)
        for platform in ("Amazon", "Flipkart"):
            sub = df[df["platform"] == platform]
            if not sub.empty:
                sub.to_excel(writer, sheet_name=platform, index=False)

        from openpyxl.styles import Alignment, Font, PatternFill
        head_fill = PatternFill("solid", fgColor="F2C14E")
        for ws in writer.book.worksheets:
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.fill = head_fill
                cell.alignment = Alignment(vertical="center")
            ws.freeze_panes = "A2"
            for column_cells in ws.columns:
                longest = max((len(str(c.value)) for c in column_cells if c.value is not None), default=8)
                ws.column_dimensions[column_cells[0].column_letter].width = min(52, max(10, longest + 2))
    buf.seek(0)
    return buf


# ===========================================================================
# Chatbot wiring — give chatbot.py a live view of finished jobs
# ===========================================================================
def chatbot_data_source(job_id: str) -> Optional[Dict[str, Any]]:
    """
    chatbot.register_data_source() callback: answer questions about a job's rows
    even when the browser sends only {message, job_id}.
    """
    job = get_job(job_id)
    if job is None or not job.data:
        return None
    return {"query": job.query, "rows": job.data, "stats": summarise(job.data)}


register_data_source(chatbot_data_source)


# ===========================================================================
# Entry point
# ===========================================================================
def print_banner() -> None:
    health = browser_health()
    shown_host = "127.0.0.1" if HOST in ("0.0.0.0", "") else HOST
    log("=" * 62)
    log("  Price Matrix — Amazon <-> Flipkart comparison engine")
    log("=" * 62)
    log(f"  Dashboard : http://{shown_host}:{PORT}")
    log(f"  Outputs   : {PROJECT_ROOT}")
    log(f"  Selenium  : {'ready' if health['selenium_available'] else 'MISSING (pip install -r requirements.txt)'}")
    log(f"  Browser   : {'found — ' + ', '.join(health['chrome_binaries']) if health['chrome_found'] else 'NOT FOUND — install Google Chrome'}")
    log("  Secrets   : none needed, everything runs locally")
    log("=" * 62)
    if not health["scraper_ready"]:
        log("[!] The scraper is not ready. The dashboard will still open and you can")
        log("[!] explore it with sample data, but live runs will fail until fixed.")
    log("")


def main() -> None:
    print_banner()
    if OPEN_BROWSER:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    # threaded=True so SSE streams and scrapes don't block each other;
    # use_reloader off so the pipeline isn't started twice.
    app.run(host=HOST, port=PORT, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
