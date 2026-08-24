"""
automation_core.py — Price Matrix scraping + analytics engine (v2)

What changed vs v1
------------------
* No more dumping thousands of tiny HTML files to disk. Pages are parsed
  in-memory (optional raw dump kept behind `dump_html=True` for debugging).
* Resilient extraction: every field goes through a *cascade* of CSS selectors
  and then regex fallbacks, so a class-name change on Amazon/Flipkart degrades
  a single column instead of killing the whole run.
* Richer schema: MRP, discount %, rating, review count, image, sponsored flag,
  ASIN/PID, parsed specs (RAM / storage / CPU / screen / refresh rate), brand.
* Real cross-platform matching: brand-bucketed, token-Jaccard + sequence blend
  with a model-number bonus, instead of an O(n^2) SequenceMatcher sweep that
  silently fell back to "cheapest on the other site".
* Cooperative cancellation (`should_cancel`) and real progress reporting
  (`on_progress`) so the API/UI can show % complete and stop a run.
* Per-platform error isolation: if Amazon gets blocked, Flipkart results still
  come back, and the failure is surfaced in `ScrapeResult.errors`.
* Bot-wall / CAPTCHA detection, UA rotation, exponential backoff with jitter,
  image loading disabled for speed, eager page-load strategy.
* Safe filenames (a query like "usb-c 3.5\" jack" no longer breaks the CSV path).

Public API
----------
    run_scrape(query, pages=2, headless=True, log=None, ...) -> (DataFrame, csv_path)
    scrape(config) -> ScrapeResult
    chrome_status() -> dict          # for /api/health
"""

from __future__ import annotations

# ----------------------------------------------------------------------------
# Standard library
# ----------------------------------------------------------------------------
import json
import logging
import os
import random
import re
import shutil
import statistics
import subprocess
import threading
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ----------------------------------------------------------------------------
# Third party
# ----------------------------------------------------------------------------
import pandas as pd
from bs4 import BeautifulSoup

try:  # Selenium is required to scrape, but importing this module must not fail
    from selenium import webdriver
    from selenium.common.exceptions import (
        TimeoutException,
        WebDriverException,
    )
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    SELENIUM_AVAILABLE = True
    SELENIUM_IMPORT_ERROR = ""
except Exception as _exc:  # pragma: no cover - depends on environment
    SELENIUM_AVAILABLE = False
    SELENIUM_IMPORT_ERROR = str(_exc)
    webdriver = None  # type: ignore


# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------
logger = logging.getLogger("price_ledger")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


# ----------------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------------
PAGE_LOAD_TIMEOUT = int(os.environ.get("PM_PAGE_LOAD_TIMEOUT", 35))
RETRIES_PER_PAGE = int(os.environ.get("PM_RETRIES", 3))
BASE_DELAY_SECONDS = float(os.environ.get("PM_BASE_DELAY", 1.6))
OUTPUT_DIR = os.environ.get("PM_OUTPUT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports"))

# --- speed tunables ---------------------------------------------------------
# Pages *within* one store are fetched concurrently. Stores themselves stay
# sequential (Amazon fully finishes before Flipkart starts).
PAGE_WORKERS = int(os.environ.get("PM_PAGE_WORKERS", 4))
# Plain-HTTP attempt before falling back to a real browser. requests is ~10-20x
# faster than Selenium when the store doesn't challenge us.
HTTP_FIRST = os.environ.get("PM_HTTP_FIRST", "1") not in {"0", "false", "False"}
HTTP_TIMEOUT = float(os.environ.get("PM_HTTP_TIMEOUT", 12))
# Small stagger so concurrent page requests don't hit the origin in one burst.
# Kept low deliberately: it is a jitter, not a rate limit, and a large value
# would serialise the very requests we are trying to overlap.
INTRA_STORE_DELAY = float(os.environ.get("PM_INTRA_DELAY", 0.12))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

BLOCK_MARKERS = (
    "enter the characters you see below",
    "type the characters you see in this image",
    "robot check",
    "to discuss automated access to amazon data",
    "sorry, we just need to make sure you're not a robot",
    "captcha",
)

KNOWN_BRANDS = {
    "hp", "dell", "lenovo", "asus", "acer", "msi", "apple", "samsung", "lg", "sony",
    "xiaomi", "redmi", "realme", "oneplus", "oppo", "vivo", "poco", "infinix", "tecno",
    "motorola", "nokia", "google", "nothing", "iqoo", "honor", "huawei", "microsoft",
    "gigabyte", "razer", "alienware", "toshiba", "panasonic", "philips", "boat", "jbl",
    "bose", "sennheiser", "skullcandy", "noise", "fireboltt", "amazfit", "garmin",
    "fitbit", "canon", "nikon", "gopro", "logitech", "corsair", "hyperx", "steelseries",
    "zebronics", "portronics", "ambrane", "anker", "sandisk", "seagate", "wd", "crucial",
    "kingston", "adata", "transcend", "tp-link", "netgear", "dlink", "mi", "haier",
    "whirlpool", "godrej", "voltas", "bluestar", "daikin", "ifb", "bosch", "prestige",
    "havells", "usha", "bajaj", "crompton", "orient", "symphony", "titan", "casio",
    "fastrack", "puma", "nike", "adidas", "levis", "wildcraft", "american", "safari",
}

_STOPWORDS = {
    "with", "and", "the", "for", "inch", "inches", "laptop", "smartphone", "mobile",
    "new", "latest", "model", "series", "gb", "tb", "ram", "ssd", "hdd", "storage",
    "display", "screen", "black", "blue", "white", "grey", "gray", "silver", "gold",
    "green", "red", "pink", "purple", "renewed", "refurbished", "pack", "combo",
}


# ----------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------
class ScrapeCancelled(RuntimeError):
    """Raised internally when the caller asks for a run to stop."""


class DriverUnavailable(RuntimeError):
    """Chrome / chromedriver could not be started."""


# ----------------------------------------------------------------------------
# Config + result containers
# ----------------------------------------------------------------------------
@dataclass
class ScrapeConfig:
    query: str
    pages: int = 2
    headless: bool = True
    platforms: Sequence[str] = ("amazon", "flipkart")
    delay: float = BASE_DELAY_SECONDS
    retries: int = RETRIES_PER_PAGE
    timeout: int = PAGE_LOAD_TIMEOUT
    include_sponsored: bool = True
    dump_html: bool = False
    output_dir: str = OUTPUT_DIR
    min_match_score: float = 0.42


@dataclass
class ScrapeResult:
    df: pd.DataFrame
    csv_path: str
    stats: Dict[str, Any] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)
    cancelled: bool = False


COLUMNS = [
    "platform", "title", "brand", "price", "mrp", "discount_pct", "rating",
    "reviews", "specs", "link", "image", "sponsored", "page", "product_id",
    "match_title", "match_score", "price_diff", "price_diff_label", "cheaper_on",
    "value_score",
]


# ----------------------------------------------------------------------------
# Small text / number helpers
# ----------------------------------------------------------------------------
def slugify(text: str, max_len: int = 60) -> str:
    """Filesystem-safe slug (the old code happily built paths from raw queries)."""
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return (text or "search")[:max_len].strip("-") or "search"


def parse_price(raw: Any) -> Optional[float]:
    """'₹1,23,499.00' -> 123499.0 ; returns None when there is no number."""
    if raw is None:
        return None
    s = str(raw)
    s = s.replace("\u20b9", " ").replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        val = float(m.group(0))
    except ValueError:
        return None
    return val if val > 0 else None


def parse_rating(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    m = re.search(r"([0-5](?:\.\d)?)", str(raw).replace(",", "."))
    if not m:
        return None
    val = float(m.group(1))
    return val if 0 < val <= 5 else None


def parse_count(raw: Any) -> Optional[int]:
    """'1,234 ratings' -> 1234 ; '2.3k' -> 2300."""
    if raw is None:
        return None
    s = str(raw).lower().replace(",", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*([km])?", s)
    if not m:
        return None
    n = float(m.group(1))
    if m.group(2) == "k":
        n *= 1_000
    elif m.group(2) == "m":
        n *= 1_000_000
    return int(n)


def make_full_url(href: str, platform: str, title: str = "") -> str:
    """Raw scraped hrefs -> absolute, clickable, tracking-trimmed URLs."""
    base = "https://www.flipkart.com" if platform.lower().startswith("flip") else "https://www.amazon.in"
    href = (href or "").strip()

    if href:
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("http://") or href.startswith("https://"):
            return _trim_url(href)
        if not href.startswith("/"):
            href = "/" + href
        return _trim_url(base + href)

    if title:
        q = urllib.parse.quote_plus(title[:120])
        return f"{base}/search?q={q}" if "flipkart" in base else f"{base}/s?k={q}"
    return ""


_TRACKING_KEYS = {
    "ref", "ref_", "qid", "sr", "sprefix", "crid", "th", "psc", "_encoding",
    "lid", "marketplace", "srno", "otracker", "otracker1", "fm", "iid", "ppt",
    "ppn", "ssid", "spotlightTagId", "store", "as-show", "as-pos", "as-type",
}


def _trim_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
        keep = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
                if k not in _TRACKING_KEYS]
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                        urllib.parse.urlencode(keep), ""))
    except Exception:
        return url


def normalize_title(title: str) -> str:
    t = str(title or "").lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = re.sub(r"[^a-z0-9.+ ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def detect_brand(title: str) -> str:
    words = normalize_title(title).split()
    for w in words[:6]:
        if w in KNOWN_BRANDS:
            return w.upper() if len(w) <= 3 else w.capitalize()
    return words[0].capitalize() if words else ""


_MODEL_RE = re.compile(r"\b(?=[a-z0-9-]*\d)[a-z]{0,4}\d{2,}[a-z0-9-]*\b")


def model_tokens(title: str) -> set:
    """Model-ish tokens ('15s-fq5111tu', 'rtx4050', 'm2') carry the real signal."""
    return {t for t in _MODEL_RE.findall(normalize_title(title)) if len(t) >= 3}


def content_tokens(title: str) -> set:
    return {t for t in normalize_title(title).split() if len(t) > 2 and t not in _STOPWORDS}


_SPEC_PATTERNS = [
    ("ram", re.compile(r"\b(\d{1,3})\s?gb\s?(?:ddr\d?x?\s?)?ram\b", re.I)),
    ("storage", re.compile(r"\b(\d{3,4}\s?gb|\d{1,2}\s?tb)\s?(?:ssd|hdd|emmc|ufs|rom|storage)\b", re.I)),
    ("cpu", re.compile(r"\b(core\s?i[3579](?:[- ]\d{4,5}\w*)?|ryzen\s?[3579]|celeron|pentium|snapdragon\s?\d+\w*|dimensity\s?\d+|apple\s?m[1-4](?:\s?(?:pro|max|ultra))?|exynos\s?\d+)\b", re.I)),
    ("gpu", re.compile(r"\b(rtx\s?\d{4}|gtx\s?\d{3,4}|radeon\s?\w*\s?\d{3,4}|iris\s?xe)\b", re.I)),
    ("screen", re.compile(r"\b(\d{1,2}(?:\.\d)?)\s?(?:inch|\"|inches|-inch)\b", re.I)),
    ("refresh", re.compile(r"\b(\d{2,3})\s?hz\b", re.I)),
    ("battery", re.compile(r"\b(\d{4,5})\s?mah\b", re.I)),
]


def extract_specs(title: str) -> Dict[str, str]:
    specs: Dict[str, str] = {}
    for key, pat in _SPEC_PATTERNS:
        m = pat.search(title or "")
        if m:
            specs[key] = re.sub(r"\s+", " ", m.group(1)).strip().upper() if key in {"ram", "storage", "gpu"} else m.group(1).strip()
    if "ram" in specs and not specs["ram"].endswith("GB"):
        specs["ram"] += "GB"
    if "screen" in specs:
        specs["screen"] = f'{specs["screen"]}"'
    if "refresh" in specs:
        specs["refresh"] = f'{specs["refresh"]}Hz'
    if "battery" in specs:
        specs["battery"] = f'{specs["battery"]}mAh'
    return specs


def specs_to_text(specs: Dict[str, str]) -> str:
    order = ["cpu", "gpu", "ram", "storage", "screen", "refresh", "battery"]
    return " · ".join(specs[k] for k in order if specs.get(k))


# ----------------------------------------------------------------------------
# Driver management
# ----------------------------------------------------------------------------
def _chrome_binary() -> Optional[str]:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                 "chrome", "/usr/bin/google-chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        path = shutil.which(name) if not name.startswith("/") else (name if os.path.exists(name) else None)
        if path:
            return path
    win = os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe")
    return win if os.path.exists(win) else None


def _selenium_available() -> bool:
    """Re-check Selenium at call time.

    The module-level import runs once at boot, but Selenium may be pip-installed
    afterwards (Codespaces `postCreate`, or a user fixing a missing dep while the
    server runs). Without this, the app would report it missing forever.
    """
    global SELENIUM_AVAILABLE, SELENIUM_IMPORT_ERROR
    global webdriver, Options, By, EC, WebDriverWait, TimeoutException, WebDriverException
    if SELENIUM_AVAILABLE:
        return True
    try:
        from selenium import webdriver as _wd
        from selenium.common.exceptions import (
            TimeoutException as _TE, WebDriverException as _WDE,
        )
        from selenium.webdriver.chrome.options import Options as _Opt
        from selenium.webdriver.common.by import By as _By
        from selenium.webdriver.support import expected_conditions as _EC
        from selenium.webdriver.support.ui import WebDriverWait as _Wait
    except Exception as exc:
        SELENIUM_IMPORT_ERROR = str(exc)
        return False
    webdriver, Options, By, EC, WebDriverWait = _wd, _Opt, _By, _EC, _Wait
    TimeoutException, WebDriverException = _TE, _WDE
    SELENIUM_AVAILABLE, SELENIUM_IMPORT_ERROR = True, ""
    return True


# chrome_status() is polled by /api/health, so cache it briefly rather than
# spawning `chrome --version` on every request. The TTL is short enough that a
# background install is noticed within seconds.
_STATUS_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}
_STATUS_TTL = float(os.environ.get("PM_STATUS_TTL", 5))


def chrome_status(force: bool = False) -> Dict[str, Any]:
    """Used by /api/health so the UI can say 'install Chrome' instead of failing silently."""
    now = time.time()
    cached = _STATUS_CACHE.get("value")
    if not force and cached and (now - _STATUS_CACHE["at"]) < _STATUS_TTL:
        return cached

    selenium_ok = _selenium_available()
    binary = _chrome_binary()
    version = ""
    if binary:
        try:
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=8)
            version = (out.stdout or out.stderr).strip()
        except Exception:
            version = "unknown"

    status = {
        "selenium_available": selenium_ok,
        "selenium_error": "" if selenium_ok else SELENIUM_IMPORT_ERROR,
        "chrome_found": bool(binary),
        "chrome_path": binary or "",
        "chrome_version": version,
        "ready": bool(selenium_ok and binary),
    }
    _STATUS_CACHE["at"], _STATUS_CACHE["value"] = now, status
    return status


def _build_driver(headless: bool, timeout: int = PAGE_LOAD_TIMEOUT):
    if not _selenium_available():
        raise DriverUnavailable(
            "Selenium is not installed. Run: pip install -r requirements.txt "
            f"({SELENIUM_IMPORT_ERROR})"
        )

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    for flag in (
        "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
        "--window-size=1920,1080", "--disable-blink-features=AutomationControlled",
        "--disable-notifications", "--disable-popup-blocking", "--lang=en-IN",
        "--log-level=3", "--disable-extensions", "--mute-audio",
    ):
        options.add_argument(flag)
    options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    # Images are ~70% of the bytes on a search page and we never need them.
    options.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
        "credentials_enable_service": False,
        "intl.accept_languages": "en-IN,en",
    })
    options.page_load_strategy = "eager"

    binary = _chrome_binary()
    if binary:
        options.binary_location = binary

    try:
        driver = webdriver.Chrome(options=options)
    except WebDriverException as exc:
        raise DriverUnavailable(
            "Could not start Chrome. Install Google Chrome (or Chromium) and make sure it "
            f"matches your Selenium version. Original error: {exc}"
        ) from exc

    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-IN','en']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                window.chrome = window.chrome || { runtime: {} };
            """
        })
    except Exception:
        pass

    driver.set_page_load_timeout(timeout)
    driver.implicitly_wait(0)
    return driver


def _looks_blocked(html: str) -> bool:
    head = (html or "")[:6000].lower()
    return any(marker in head for marker in BLOCK_MARKERS)


def _human_scroll(driver, steps: int = 4, pause: float = 0.45) -> None:
    for i in range(1, steps + 1):
        try:
            driver.execute_script(f"window.scrollTo(0, document.body.scrollHeight * {i / (steps + 1):.2f});")
        except Exception:
            return
        time.sleep(pause + random.uniform(0, 0.25))


# ----------------------------------------------------------------------------
# Page fetching (shared by both platforms)
# ----------------------------------------------------------------------------
def _fetch_page(driver, url: str, wait_css: str, cfg: ScrapeConfig,
                log: Callable[[str], None], label: str) -> Optional[str]:
    """Load a URL with retries/backoff; returns page HTML or None."""
    for attempt in range(1, cfg.retries + 1):
        try:
            driver.get(url)
            try:
                WebDriverWait(driver, min(cfg.timeout, 20)).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_css))
                )
            except TimeoutException:
                pass  # fall through: sometimes the layout differs but content exists
            _human_scroll(driver)
            html = driver.page_source or ""

            if _looks_blocked(html):
                log(f"[{label}] Bot wall detected (attempt {attempt}/{cfg.retries}) — backing off.")
                time.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
                continue
            if len(html) < 5000:
                log(f"[{label}] Suspiciously small response (attempt {attempt}/{cfg.retries}).")
                time.sleep(1.2 * attempt)
                continue
            return html
        except TimeoutException:
            log(f"[{label}] Timeout loading page (attempt {attempt}/{cfg.retries}).")
        except WebDriverException as exc:
            log(f"[{label}] Driver error (attempt {attempt}/{cfg.retries}): {str(exc)[:120]}")
        time.sleep(1.0 * attempt + random.uniform(0, 0.8))
    return None


# ----------------------------------------------------------------------------
# Fast path: plain HTTP
# ----------------------------------------------------------------------------
# Selenium spends most of its time booting Chrome and executing JS we don't
# need — the search pages are server-rendered, so a plain GET usually returns
# the same markup in a fraction of the time. We try HTTP first and fall back to
# a browser the moment a store challenges us.
_SESSIONS: Dict[int, Any] = {}
_SESSION_LOCK = threading.Lock()


def _http_session():
    """One pooled, connection-reusing Session per thread."""
    try:
        import requests
    except Exception:
        return None
    tid = threading.get_ident()
    with _SESSION_LOCK:
        sess = _SESSIONS.get(tid)
        if sess is None:
            sess = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=PAGE_WORKERS + 2,
                pool_maxsize=PAGE_WORKERS + 2,
                max_retries=0,
            )
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            _SESSIONS[tid] = sess
        return sess


def _http_headers(referer: str = "") -> Dict[str, str]:
    h = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    if referer:
        h["Referer"] = referer
    return h


def _fetch_http(url: str, referer: str = "") -> Tuple[Optional[str], str]:
    """Return (html, reason). html is None when the fast path is unusable."""
    sess = _http_session()
    if sess is None:
        return None, "requests-missing"
    try:
        resp = sess.get(url, headers=_http_headers(referer), timeout=HTTP_TIMEOUT,
                        allow_redirects=True)
    except Exception as exc:
        return None, type(exc).__name__
    if resp.status_code != 200:
        return None, f"http-{resp.status_code}"
    html = resp.text or ""
    if len(html) < 20000:          # challenge/interstitial pages are tiny
        return None, "too-small"
    if _looks_blocked(html):
        return None, "bot-wall"
    return html, "ok"


def _maybe_dump(cfg: ScrapeConfig, platform: str, page: int, html: str) -> None:
    if not cfg.dump_html:
        return
    folder = os.path.join(cfg.output_dir, f"{slugify(cfg.query)}_{platform}_raw")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"page_{page}.html"), "w", encoding="utf-8") as fh:
        fh.write(html)


# ----------------------------------------------------------------------------
# Amazon
# ----------------------------------------------------------------------------
AMZ_CARD_SELECTORS = [
    'div[data-component-type="s-search-result"]',
    "div.puis-card-container",
    "div.s-result-item[data-asin]",
]
AMZ_TITLE_SELECTORS = ["h2 span.a-text-normal", "h2 a span", "h2 span", "h2", "span.a-size-medium.a-color-base.a-text-normal"]
AMZ_LINK_SELECTORS = ["h2 a.a-link-normal[href]", "a.a-link-normal.s-no-outline[href]", "a.a-link-normal[href]", "a[href]"]
AMZ_PRICE_SELECTORS = ["span.a-price:not(.a-text-price) > span.a-offscreen", "span.a-price > span.a-offscreen", "span.a-price-whole"]
AMZ_MRP_SELECTORS = ["span.a-price.a-text-price > span.a-offscreen", "span.a-text-price span.a-offscreen"]
AMZ_RATING_SELECTORS = ["span.a-icon-alt", "i.a-icon-star-small > span", "span[aria-label*='out of 5']"]
AMZ_REVIEW_SELECTORS = ["span.a-size-base.s-underline-text", "span[aria-label$='ratings']", "a[href*='#customerReviews'] span"]


def _sel_text(soup: BeautifulSoup, selectors: Iterable[str]) -> str:
    for sel in selectors:
        try:
            node = soup.select_one(sel)
        except Exception:
            continue
        if node:
            txt = node.get_text(" ", strip=True)
            if txt:
                return txt
    return ""


def _sel_attr(soup: BeautifulSoup, selectors: Iterable[str], attr: str) -> str:
    for sel in selectors:
        try:
            node = soup.select_one(sel)
        except Exception:
            continue
        if node and node.get(attr):
            return str(node.get(attr))
    return ""


def _parse_amazon_page(html: str, page: int, query: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml") if _has_lxml() else BeautifulSoup(html, "html.parser")
    cards = []
    for sel in AMZ_CARD_SELECTORS:
        cards = soup.select(sel)
        if cards:
            break

    rows: List[Dict[str, Any]] = []
    for card in cards:
        title = _sel_text(card, AMZ_TITLE_SELECTORS)
        if not title:
            img = card.select_one("img.s-image[alt]")
            title = img["alt"].strip() if img and img.get("alt") else ""
        if not title:
            continue

        price = parse_price(_sel_text(card, AMZ_PRICE_SELECTORS))
        if price is None:
            continue  # unpriced cards are ads/placeholders

        mrp = parse_price(_sel_text(card, AMZ_MRP_SELECTORS))
        if mrp and mrp <= price:
            mrp = None

        href = _sel_attr(card, AMZ_LINK_SELECTORS, "href")
        asin = card.get("data-asin") or ""
        link = f"https://www.amazon.in/dp/{asin}" if asin else make_full_url(href, "amazon", title)

        card_text = card.get_text(" ", strip=True).lower()
        rows.append({
            "platform": "Amazon",
            "title": title,
            "price": price,
            "mrp": mrp,
            "rating": parse_rating(_sel_text(card, AMZ_RATING_SELECTORS)),
            "reviews": parse_count(_sel_text(card, AMZ_REVIEW_SELECTORS)),
            "link": link,
            "image": _sel_attr(card, ["img.s-image"], "src"),
            "sponsored": "sponsored" in card_text[:400],
            "page": page,
            "product_id": asin,
        })
    return rows


# ----------------------------------------------------------------------------
# Generic parallel-page store engine
# ----------------------------------------------------------------------------
# Both stores share this. Pages within a store are fetched CONCURRENTLY, while
# the stores themselves run sequentially (Amazon completes, then Flipkart) so
# results arrive in a predictable order.
#
# Strategy per page:
#   1. plain HTTP (fast)  -> if it returns real markup, we never touch Chrome
#   2. shared Selenium driver (slow) -> only for pages HTTP couldn't get
#
# One browser is booted lazily and reused for every fallback page in the store,
# instead of one driver per store regardless of need.

@dataclass
class _StoreSpec:
    name: str            # "Amazon"
    key: str             # "amazon"
    url: Callable[[str, int], str]
    wait_css: str
    parser: Callable[[str, int, str], List[Dict[str, Any]]]
    home: str


class _DriverPool:
    """A small pool of reusable Chrome instances, shared across BOTH stores.

    Two things matter for speed here:
      * Chrome boot (~2-4s) is the single most expensive operation, so drivers
        are created lazily (never at all if the HTTP fast path works) and are
        reused for Amazon *and* Flipkart rather than rebooted per store.
      * One driver can only load one URL at a time, so the pool holds several
        and hands them out per thread, letting fallback pages run in parallel.
    """

    def __init__(self, cfg: ScrapeConfig, log: Callable[[str], None], size: int):
        self.cfg, self.log = cfg, log
        self.size = max(1, size)
        self._idle: List[Any] = []
        self._created = 0
        self._lock = threading.Condition()
        self._failed = False
        self._warmed: set = set()

    def _spawn(self):
        self.log(f"  ↳ starting browser {self._created + 1}/{self.size} for fallback pages…")
        driver = _build_driver(self.cfg.headless, self.cfg.timeout)
        self._created += 1
        return driver

    def acquire(self, timeout: float = 120.0):
        """Get a driver, creating one if the pool hasn't reached its cap."""
        deadline = time.time() + timeout
        with self._lock:
            while True:
                if self._failed:
                    return None
                if self._idle:
                    return self._idle.pop()
                if self._created < self.size:
                    self._created += 1          # reserve the slot
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._lock.wait(remaining)
        # Build outside the lock so concurrent boots overlap.
        try:
            self.log(f"  ↳ starting browser {self._created}/{self.size} for fallback pages…")
            return _build_driver(self.cfg.headless, self.cfg.timeout)
        except DriverUnavailable as exc:
            with self._lock:
                self._created -= 1
                self._failed = True
                self._lock.notify_all()
            self.log(f"  ↳ browser unavailable: {str(exc)[:120]}")
            return None

    def release(self, driver) -> None:
        if driver is None:
            return
        with self._lock:
            self._idle.append(driver)
            self._lock.notify()

    def warm(self, driver, home: str, store_key: str) -> None:
        """Visit a store's homepage once per driver to pick up cookies."""
        if not home or not driver:
            return
        token = (id(driver), store_key)
        if token in self._warmed:
            return
        self._warmed.add(token)
        try:
            driver.get(home)
            time.sleep(0.8)
            if store_key == "flipkart":
                for sel in ("button._2KpZ6l._2doB4z", "button._2KpZ6l", "span._30XB9F"):
                    try:
                        driver.find_element(By.CSS_SELECTOR, sel).click()
                        break
                    except Exception:
                        continue
        except Exception:
            pass

    @property
    def used(self) -> bool:
        return self._created > 0

    def close(self):
        with self._lock:
            drivers, self._idle = self._idle, []
            self._created = 0
        for d in drivers:
            try:
                d.quit()
            except Exception:
                pass


def _scrape_store(spec: _StoreSpec, cfg: ScrapeConfig, log: Callable[[str], None],
                  should_cancel: Callable[[], bool], tick: Callable[[], None],
                  on_rows: Optional[Callable[[str, List[Dict[str, Any]]], None]] = None,
                  pool: Optional["_DriverPool"] = None
                  ) -> List[Dict[str, Any]]:
    """Fetch every page of one store, pages in parallel, HTTP-first."""
    owns_pool = pool is None
    if pool is None:
        pool = _DriverPool(cfg, log, size=min(PAGE_WORKERS, max(1, cfg.pages)))
    workers = max(1, min(PAGE_WORKERS, cfg.pages))
    results: Dict[int, List[Dict[str, Any]]] = {}
    http_hits = {"n": 0}
    t_start = time.time()

    def do_page(page: int) -> Tuple[int, List[Dict[str, Any]], str]:
        if should_cancel():
            return page, [], "cancelled"
        url = spec.url(cfg.query, page)
        # Jitter each request slightly so the batch doesn't land as one burst.
        # Bounded by a single stagger window so pages still overlap.
        time.sleep(random.uniform(0, INTRA_STORE_DELAY * 2))

        html, how = None, ""
        if HTTP_FIRST:
            html, reason = _fetch_http(url, referer=spec.home)
            if html:
                how, http_hits["n"] = "http", http_hits["n"] + 1
            else:
                log(f"[{spec.name}] Page {page}: fast path unavailable ({reason}) — using browser.")

        if html is None:
            if should_cancel():
                return page, [], "cancelled"
            driver = pool.acquire()
            if driver is None:
                return page, [], "no-driver"
            try:
                pool.warm(driver, spec.home, spec.key)
                if should_cancel():
                    return page, [], "cancelled"
                html = _fetch_page(driver, url, spec.wait_css, cfg, log, spec.name)
            finally:
                pool.release(driver)
            how = "browser"

        if not html:
            return page, [], "failed"

        _maybe_dump(cfg, spec.key, page, html)
        return page, spec.parser(html, page, cfg.query), how

    pages = list(range(1, cfg.pages + 1))
    try:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix=f"{spec.key}-page") as pool_ex:
            futures = {pool_ex.submit(do_page, p): p for p in pages}
            for fut in as_completed(futures):
                page = futures[fut]
                try:
                    page, rows, how = fut.result()
                except Exception as exc:
                    log(f"[{spec.name}] Page {page} errored: {type(exc).__name__}: {str(exc)[:120]}")
                    rows, how = [], "error"
                results[page] = rows
                if how == "cancelled":
                    pass
                elif how in {"failed", "no-driver", "error"}:
                    log(f"[{spec.name}] Page {page} could not be loaded — skipping.")
                else:
                    tag = "fast" if how == "http" else "browser"
                    log(f"[{spec.name}] Page {page}: parsed {len(rows)} priced listings ({tag}).")
                tick()
    finally:
        if owns_pool:
            pool.close()

    # Reassemble in page order so output is deterministic despite concurrency.
    ordered: List[Dict[str, Any]] = []
    for page in pages:
        ordered.extend(results.get(page, []))

    elapsed = time.time() - t_start
    fast = http_hits["n"]
    log(f"[{spec.name}] Done — {len(ordered)} listings from {len(pages)} page(s) "
        f"in {elapsed:.1f}s ({fast} fast / {len(pages) - fast} browser).")

    if on_rows and ordered:
        try:
            on_rows(spec.name, ordered)
        except Exception:
            pass
    return ordered


AMAZON_SPEC = _StoreSpec(
    name="Amazon", key="amazon",
    url=lambda q, p: f"https://www.amazon.in/s?k={urllib.parse.quote_plus(q)}&page={p}&ref=nb_sb_noss",
    wait_css="div.s-main-slot",
    parser=lambda html, page, q: _parse_amazon_page(html, page, q),
    home="https://www.amazon.in/",
)


def _scrape_amazon(cfg: ScrapeConfig, log: Callable[[str], None],
                   should_cancel: Callable[[], bool],
                   tick: Callable[[], None],
                   on_rows: Optional[Callable[[str, List[Dict[str, Any]]], None]] = None,
                   pool: Optional["_DriverPool"] = None) -> List[Dict[str, Any]]:
    return _scrape_store(AMAZON_SPEC, cfg, log, should_cancel, tick, on_rows, pool)


# ----------------------------------------------------------------------------
# Flipkart
# ----------------------------------------------------------------------------
FK_CARD_SELECTORS = [
    "div[data-id]",
    "div._75nlfW > div",
    "div._1AtVbE",
    "div._13oc-S",
]
FK_TITLE_SELECTORS = [
    "div.KzDlHZ", "a.wjcEIp", "div._4rR01T", "a.s1Q9rs", "a.IRpwTa",
    "div.Kz310d", "a.WKTcLC", "div.syl9yP",
]
FK_PRICE_SELECTORS = ["div.Nx9bqj", "div._30jeq3", "div._4b5DiR", "div.hl05eU div"]
FK_MRP_SELECTORS = ["div.yRaY8j", "div._3I9_wc", "div.ZYYwLA"]
FK_RATING_SELECTORS = ["div.XQDdHH", "div._3LWZlK", "span.Y1HWO0 div"]
FK_REVIEW_SELECTORS = ["span.Wphh3N", "span._2_R_DZ", "span.Wphh3N span"]


def _parse_flipkart_page(html: str, page: int, query: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml") if _has_lxml() else BeautifulSoup(html, "html.parser")

    # Flipkart's grid/list markup changes often; anchor on product links instead.
    anchors = soup.select('a[href*="/p/"]')
    seen_links: set = set()
    rows: List[Dict[str, Any]] = []

    for anchor in anchors:
        href = anchor.get("href") or ""
        key = href.split("?")[0]
        if not key or key in seen_links:
            continue

        # Climb to the smallest ancestor that holds a price *and still describes
        # only this product* — stopping the moment another product link appears
        # prevents a card without a price from stealing its neighbour's.
        card = anchor
        node = anchor
        for _ in range(6):
            if node.parent is None:
                break
            node = node.parent
            try:
                if len(node.select('a[href*="/p/"]')) > 1:
                    break  # we've reached a container holding sibling products
            except Exception:
                break
            card = node
            if node.select_one(",".join(FK_PRICE_SELECTORS)) or re.search(
                    r"₹\s?[\d,]{3,}", node.get_text(" ", strip=True)):
                break

        title = (anchor.get("title") or "").strip()
        if not title:
            title = _sel_text(card, FK_TITLE_SELECTORS)
        if not title:
            img = card.select_one("img[alt]")
            if img and len(img.get("alt", "")) > 4:
                title = img["alt"].strip()
        if not title:
            title = anchor.get_text(" ", strip=True)
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) < 4:
            continue

        price_text = _sel_text(card, FK_PRICE_SELECTORS)
        price = parse_price(price_text)
        if price is None:
            m = re.search(r"₹\s?([\d,]{3,})", card.get_text(" ", strip=True))
            price = parse_price(m.group(1)) if m else None
        if price is None:
            continue

        mrp = parse_price(_sel_text(card, FK_MRP_SELECTORS))
        if mrp and mrp <= price:
            mrp = None

        rating = parse_rating(_sel_text(card, FK_RATING_SELECTORS))
        reviews = parse_count(_sel_text(card, FK_REVIEW_SELECTORS))
        img_node = card.select_one("img[src]")
        pid = ""
        pid_match = re.search(r"pid=([A-Z0-9]+)", href)
        if pid_match:
            pid = pid_match.group(1)

        seen_links.add(key)
        rows.append({
            "platform": "Flipkart",
            "title": title,
            "price": price,
            "mrp": mrp,
            "rating": rating,
            "reviews": reviews,
            "link": make_full_url(href, "flipkart", title),
            "image": img_node["src"] if img_node else "",
            "sponsored": "sponsored" in card.get_text(" ", strip=True).lower()[:300],
            "page": page,
            "product_id": pid,
        })
    return rows


FLIPKART_SPEC = _StoreSpec(
    name="Flipkart", key="flipkart",
    url=lambda q, p: f"https://www.flipkart.com/search?q={urllib.parse.quote_plus(q)}&page={p}",
    wait_css='a[href*="/p/"]',
    parser=lambda html, page, q: _parse_flipkart_page(html, page, q),
    home="https://www.flipkart.com/",
)


def _scrape_flipkart(cfg: ScrapeConfig, log: Callable[[str], None],
                     should_cancel: Callable[[], bool],
                     tick: Callable[[], None],
                     on_rows: Optional[Callable[[str, List[Dict[str, Any]]], None]] = None,
                     pool: Optional["_DriverPool"] = None) -> List[Dict[str, Any]]:
    return _scrape_store(FLIPKART_SPEC, cfg, log, should_cancel, tick, on_rows, pool)


def _has_lxml() -> bool:
    global _LXML
    try:
        return _LXML
    except NameError:
        pass
    try:
        import lxml  # noqa: F401
        _LXML = True
    except Exception:
        _LXML = False
    return _LXML


# ----------------------------------------------------------------------------
# Matching + analytics
# ----------------------------------------------------------------------------
def similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Blend of token Jaccard, sequence ratio and a model-number bonus."""
    ta, tb = a["_tokens"], b["_tokens"]
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, a["_norm"][:90], b["_norm"][:90]).ratio()
    score = 0.55 * jac + 0.45 * seq

    ma, mb = a["_models"], b["_models"]
    if ma and mb:
        if ma & mb:
            score = min(1.0, score + 0.28)
        elif jac < 0.5:
            score *= 0.85

    if a["brand"] and b["brand"]:
        if a["brand"].lower() != b["brand"].lower():
            score *= 0.35

    # Wildly different prices are almost never the same product.
    pa, pb = a.get("price"), b.get("price")
    if pa and pb:
        ratio = min(pa, pb) / max(pa, pb)
        if ratio < 0.35:
            score *= 0.5

    return round(score, 4)


def match_and_score(rows: List[Dict[str, Any]], min_score: float = 0.42) -> List[Dict[str, Any]]:
    """Attach match_title / match_score / price_diff / cheaper_on / value_score."""
    for r in rows:
        r["_norm"] = normalize_title(r["title"])
        r["_tokens"] = content_tokens(r["title"])
        r["_models"] = model_tokens(r["title"])
        r["brand"] = r.get("brand") or detect_brand(r["title"])
        specs = extract_specs(r["title"])
        r["specs"] = specs_to_text(specs)
        r["_specs"] = specs

    amazon = [r for r in rows if r["platform"] == "Amazon"]
    flipkart = [r for r in rows if r["platform"] == "Flipkart"]

    # Bucket by brand so we compare ~dozens of candidates rather than everything.
    def bucket(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for it in items:
            out.setdefault((it["brand"] or "").lower(), []).append(it)
        return out

    amz_buckets, fk_buckets = bucket(amazon), bucket(flipkart)

    def best_match(row: Dict[str, Any], other_all: List[Dict[str, Any]],
                   other_buckets: Dict[str, List[Dict[str, Any]]]):
        candidates = other_buckets.get((row["brand"] or "").lower()) or other_all
        best, best_score = None, 0.0
        for cand in candidates:
            s = similarity(row, cand)
            if s > best_score:
                best, best_score = cand, s
        if best_score < min_score and candidates is not other_all:
            for cand in other_all:
                s = similarity(row, cand)
                if s > best_score:
                    best, best_score = cand, s
        return best, best_score

    for row in rows:
        other_all = flipkart if row["platform"] == "Amazon" else amazon
        other_buckets = fk_buckets if row["platform"] == "Amazon" else amz_buckets
        opposite = "Flipkart" if row["platform"] == "Amazon" else "Amazon"

        row["match_title"] = ""
        row["match_score"] = 0.0
        row["price_diff"] = None
        row["price_diff_label"] = "No match"
        row["cheaper_on"] = ""

        if not other_all:
            row["price_diff_label"] = f"{opposite} unavailable"
            continue

        match, score = best_match(row, other_all, other_buckets)
        if not match or score < min_score:
            continue

        diff = round(row["price"] - match["price"])
        row["match_title"] = match["title"]
        row["match_score"] = round(score, 3)
        row["price_diff"] = diff
        row["match_link"] = match.get("link", "")
        if diff > 0:
            row["price_diff_label"] = f"+₹{abs(diff):,} · cheaper on {opposite}"
            row["cheaper_on"] = opposite
        elif diff < 0:
            row["price_diff_label"] = f"-₹{abs(diff):,} · cheaper on {row['platform']}"
            row["cheaper_on"] = row["platform"]
        else:
            row["price_diff_label"] = "Same price on both"
            row["cheaper_on"] = "Tie"

    # value_score: cheap + well rated + credibly reviewed, normalised 0-100.
    prices = [r["price"] for r in rows if r.get("price")]
    if prices:
        p_min, p_max = min(prices), max(prices)
        span = (p_max - p_min) or 1.0
        for r in rows:
            price_component = 1 - ((r["price"] - p_min) / span)
            rating_component = ((r.get("rating") or 0) / 5.0)
            reviews_component = min(1.0, (r.get("reviews") or 0) / 2000.0)
            discount = 0.0
            if r.get("mrp"):
                discount = min(1.0, max(0.0, (r["mrp"] - r["price"]) / r["mrp"]))
            r["value_score"] = round(100 * (0.42 * price_component + 0.3 * rating_component
                                            + 0.16 * reviews_component + 0.12 * discount), 1)

    for r in rows:
        r.pop("_norm", None)
        r.pop("_tokens", None)
        r.pop("_models", None)
        r.pop("_specs", None)
        if r.get("mrp"):
            r["discount_pct"] = round(100 * (r["mrp"] - r["price"]) / r["mrp"])
        else:
            r["discount_pct"] = None
    return rows


def dedupe(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in rows:
        key = (r["platform"], r.get("product_id") or normalize_title(r["title"])[:80], round(r["price"] or 0))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def summarize(df: pd.DataFrame) -> Dict[str, Any]:
    """Compact stats block reused by the API, the UI KPIs and the chatbot."""
    def platform_block(name: str) -> Optional[Dict[str, Any]]:
        sub = df[(df["platform"] == name) & df["price"].notna()]
        if sub.empty:
            return None
        prices = sub["price"].astype(float).tolist()
        cheapest = sub.loc[sub["price"].idxmin()]
        return {
            "count": int(len(sub)),
            "min": float(min(prices)),
            "max": float(max(prices)),
            "avg": round(float(statistics.fmean(prices)), 2),
            "median": round(float(statistics.median(prices)), 2),
            "cheapest_title": str(cheapest["title"]),
            "cheapest_link": str(cheapest.get("link", "")),
        }

    amz, fk = platform_block("Amazon"), platform_block("Flipkart")
    winner, saving = None, 0.0
    if amz and fk:
        if amz["min"] < fk["min"]:
            winner, saving = "Amazon", fk["min"] - amz["min"]
        elif fk["min"] < amz["min"]:
            winner, saving = "Flipkart", amz["min"] - fk["min"]
        else:
            winner, saving = "Tie", 0.0
    elif amz:
        winner = "Amazon"
    elif fk:
        winner = "Flipkart"

    best_value = None
    if "value_score" in df.columns and df["value_score"].notna().any():
        row = df.loc[df["value_score"].idxmax()]
        best_value = {
            "title": str(row["title"]),
            "platform": str(row["platform"]),
            "price": float(row["price"]) if pd.notna(row["price"]) else None,
            "rating": float(row["rating"]) if pd.notna(row.get("rating")) else None,
            "value_score": float(row["value_score"]),
            "link": str(row.get("link", "")),
        }

    matched = int((df["match_score"].fillna(0) > 0).sum()) if "match_score" in df.columns else 0
    return {
        "total": int(len(df)),
        "amazon": amz,
        "flipkart": fk,
        "cheaper_platform": winner,
        "saving": round(float(saving), 2),
        "matched_pairs": matched,
        "best_value": best_value,
    }


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------
def scrape(cfg: ScrapeConfig,
           log: Optional[Callable[[str], None]] = None,
           should_cancel: Optional[Callable[[], bool]] = None,
           on_progress: Optional[Callable[[int], None]] = None,
           on_partial: Optional[Callable[[str, List[Dict[str, Any]]], None]] = None
           ) -> ScrapeResult:
    """Scrape each platform in turn and return a fully analysed result.

    Stores run sequentially (Amazon, then Flipkart) while each store's pages are
    fetched concurrently. `on_partial(store_name, rows)` fires the moment a
    store finishes, so the UI can render Amazon's listings while Flipkart is
    still loading.
    """
    cfg.query = (cfg.query or "").strip()
    if not cfg.query:
        raise ValueError("query must not be empty")
    cfg.pages = max(1, int(cfg.pages))
    platforms = [p.lower() for p in cfg.platforms] or ["amazon", "flipkart"]

    def emit(msg: str) -> None:
        logger.info(msg)
        if log:
            try:
                log(msg)
            except Exception:
                pass

    cancelled_flag = should_cancel or (lambda: False)
    total_units = cfg.pages * len(platforms)
    done_units = {"n": 0}

    def tick() -> None:
        done_units["n"] += 1
        if on_progress:
            try:
                on_progress(min(99, int(100 * done_units["n"] / max(1, total_units))))
            except Exception:
                pass

    def on_store_rows(store: str, rows: List[Dict[str, Any]]) -> None:
        """Publish one store's listings as soon as that store is done."""
        if not on_partial:
            return
        try:
            enriched = match_and_score([dict(r) for r in dedupe(rows)], cfg.min_match_score)
            on_partial(store, enriched)
        except Exception:
            pass

    emit(f"Starting comparison scan for “{cfg.query}” · {cfg.pages} page(s) per store.")
    emit(f"Order: {' → '.join(p.title() for p in platforms)} "
         f"(sequential) · up to {min(PAGE_WORKERS, cfg.pages)} page(s) at a time.")

    # Chrome is now only a *fallback*, so a missing browser is no longer fatal —
    # the HTTP fast path may well carry the whole run. We only hard-fail when
    # neither route is available.
    status = chrome_status()
    if not status["ready"]:
        if not HTTP_FIRST or _http_session() is None:
            raise DriverUnavailable(
                "No usable fetch method. Chrome/Selenium is unavailable "
                f"(selenium={status['selenium_available']}, chrome={status['chrome_found']}) "
                "and the HTTP fast path is disabled or `requests` is missing. "
                "Install Google Chrome or run `pip install -r requirements.txt`."
            )
        emit("Chrome not detected — using the HTTP fast path only. "
             "If a store challenges us, those pages will be skipped.")
    elif status["chrome_version"]:
        emit(f"Browser available for fallback: {status['chrome_version']}")

    tasks = {
        "amazon": _scrape_amazon,
        "flipkart": _scrape_flipkart,
    }
    results: Dict[str, List[Dict[str, Any]]] = {}
    errors: Dict[str, str] = {}
    was_cancelled = False

    # ONE driver pool shared by every store: Chrome boots at most once per
    # worker for the whole run instead of once per store, and only if the HTTP
    # fast path actually fails.
    shared_pool = _DriverPool(cfg, emit, size=min(PAGE_WORKERS, max(1, cfg.pages)))

    # Stores run STRICTLY SEQUENTIALLY in the order given (default: Amazon then
    # Flipkart) so results arrive predictably. The speed-up comes from fetching
    # each store's pages concurrently, not from overlapping the stores.
    for idx, name in enumerate(platforms):
        if name not in tasks:
            continue
        if cancelled_flag():
            was_cancelled = True
            emit(f"[{name.title()}] Skipped — run cancelled.")
            results[name] = []
            continue

        emit(f"── Store {idx + 1}/{len(platforms)}: {name.title()} ──")
        try:
            results[name] = tasks[name](cfg, emit, cancelled_flag, tick,
                                        on_store_rows, shared_pool)
        except ScrapeCancelled:
            was_cancelled = True
            results[name] = []
            emit(f"[{name.title()}] Cancelled by user.")
        except DriverUnavailable as exc:
            errors[name] = str(exc)
            results[name] = []
            emit(f"[{name.title()}] {exc}")
        except Exception as exc:  # isolate one platform's failure
            errors[name] = f"{type(exc).__name__}: {exc}"
            results[name] = []
            emit(f"[{name.title()}] Failed: {type(exc).__name__}: {str(exc)[:200]}")

    if shared_pool.used:
        emit("Closing browser(s)…")
    shared_pool.close()

    if cancelled_flag():
        was_cancelled = True

    rows: List[Dict[str, Any]] = []
    for name in platforms:
        rows.extend(results.get(name, []))

    rows = dedupe(rows)
    if not cfg.include_sponsored:
        rows = [r for r in rows if not r.get("sponsored")]
    emit(f"Collected {len(rows)} unique listings. Matching products across stores…")
    rows = match_and_score(rows, cfg.min_match_score)

    df = pd.DataFrame(rows)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    extra = [c for c in df.columns if c not in COLUMNS]
    df = df[COLUMNS + extra]
    if not df.empty:
        df = df.sort_values(by=["price"], kind="stable", na_position="last").reset_index(drop=True)

    os.makedirs(cfg.output_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    csv_path = os.path.join(cfg.output_dir, f"{slugify(cfg.query)}_{stamp}.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    stats = summarize(df) if not df.empty else {"total": 0}
    # Report post-dedupe counts so the log matches what the table actually shows.
    amz_n = int((df["platform"] == "Amazon").sum()) if not df.empty else 0
    fk_n = int((df["platform"] == "Flipkart").sum()) if not df.empty else 0
    verb = "Scan cancelled" if was_cancelled else "Scan complete"
    emit(f"{verb} — {len(df)} listings ({amz_n} Amazon, {fk_n} Flipkart), "
         f"{stats.get('matched_pairs', 0)} cross-store matches.")
    if on_progress and not was_cancelled:
        on_progress(100)

    return ScrapeResult(df=df, csv_path=csv_path, stats=stats, errors=errors, cancelled=was_cancelled)


def run_scrape(query: str,
               pages: int = 2,
               headless: bool = True,
               log: Optional[Callable[[str], None]] = None,
               should_cancel: Optional[Callable[[], bool]] = None,
               on_progress: Optional[Callable[[int], None]] = None,
               platforms: Sequence[str] = ("amazon", "flipkart"),
               include_sponsored: bool = True,
               dump_html: bool = False,
               on_partial: Optional[Callable[[str, List[Dict[str, Any]]], None]] = None
               ) -> Tuple[pd.DataFrame, str]:
    """Backwards-compatible wrapper around `scrape()` (same signature as v1)."""
    cfg = ScrapeConfig(query=query, pages=pages, headless=headless,
                       platforms=platforms, include_sponsored=include_sponsored,
                       dump_html=dump_html)
    result = scrape(cfg, log=log, should_cancel=should_cancel,
                    on_progress=on_progress, on_partial=on_partial)
    return result.df, result.csv_path


# ----------------------------------------------------------------------------
# CLI — handy for testing the engine without the web UI
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Price Matrix scraper")
    ap.add_argument("query", help="product to search for")
    ap.add_argument("-p", "--pages", type=int, default=2)
    ap.add_argument("--show", action="store_true", help="print a preview table")
    ap.add_argument("--no-headless", action="store_true")
    ap.add_argument("--dump-html", action="store_true")
    ap.add_argument("--platforms", default="amazon,flipkart")
    args = ap.parse_args()

    cfg = ScrapeConfig(query=args.query, pages=args.pages,
                       headless=not args.no_headless,
                       platforms=tuple(p.strip() for p in args.platforms.split(",") if p.strip()),
                       dump_html=args.dump_html)
    res = scrape(cfg, log=print)
    print(f"\nSaved -> {res.csv_path}")
    print(json.dumps(res.stats, indent=2, default=str))
    if args.show and not res.df.empty:
        with pd.option_context("display.max_colwidth", 60, "display.width", 200):
            print(res.df[["platform", "title", "price", "rating", "price_diff_label"]].head(20))
