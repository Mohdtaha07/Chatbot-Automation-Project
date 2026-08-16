from __future__ import annotations

# Standard Library Imports
import logging
import os
import random
import re
import shutil
import tempfile
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from difflib import SequenceMatcher
from threading import Event
from typing import Callable, Iterator, Optional, Tuple

# Third-Party Imports
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Configure Logger
logger = logging.getLogger("price_ledger")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

PAGE_LOAD_TIMEOUT = 30
RETRIES_PER_PAGE = 3
BASE_DELAY_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 8.0

# Where finished CSVs are written. Configurable so deployments can point this
# at a real data volume instead of littering the process's cwd.
OUTPUT_DIR = os.environ.get(
    "PRICE_LEDGER_OUTPUT_DIR", os.path.join(os.getcwd(), "data", "output")
)
# Optional explicit path to a chromedriver binary. If unset, Selenium Manager
# (bundled with Selenium 4.6+) resolves it automatically.
CHROMEDRIVER_PATH = os.environ.get("PRICE_LEDGER_CHROMEDRIVER_PATH")

os.makedirs(OUTPUT_DIR, exist_ok=True)


def slugify(text: str, max_len: int = 60) -> str:
    """Turn arbitrary user input into a filesystem-safe slug.

    The raw query used to be interpolated directly into folder/file names
    (e.g. f"{query}_amazon_raw"). That breaks on slashes, dots, and other
    special characters, and opens the door to path traversal if `query`
    ever contains something like "../../etc". Everything that touches the
    filesystem should go through this function instead of the raw query.
    """
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if not text:
        text = "query"
    return text[:max_len]


def make_full_url(href: str, platform: str, title: str = "") -> str:
    """Ensure raw scraped links are converted into absolute, clickable URLs."""
    if href:
        href = href.strip()
        if href.startswith("http://") or href.startswith("https://"):
            return href
        if href.startswith("//"):
            return "https:" + href

        base = "https://www.flipkart.com" if platform.lower() == "flipkart" else "https://www.amazon.in"
        if not href.startswith("/"):
            href = "/" + href
        return base + href

    if title:
        q = urllib.parse.quote_plus(title)
        if platform.lower() == "flipkart":
            return f"https://www.flipkart.com/search?q={q}"
        return f"https://www.amazon.in/s?k={q}"
    return ""


def _clean_price(raw: str) -> str:
    """Extract numeric value for price."""
    return re.sub(r"[^\d]", "", raw or "")


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter, capped, instead of a flat sleep."""
    return min(MAX_BACKOFF_SECONDS, BASE_DELAY_SECONDS * (1.6 ** (attempt - 1))) + random.uniform(0, 0.5)


def _should_stop(cancel_event: Optional[Event]) -> bool:
    return bool(cancel_event and cancel_event.is_set())


@contextmanager
def _chrome_driver(headless: bool) -> Iterator[webdriver.Chrome]:
    """Configures Chrome with stealth flags and guarantees driver.quit() runs.

    The original version built the driver, then called methods on it before
    entering a try/finally, so a failure during setup (e.g. execute_cdp_cmd)
    would leak the Chrome process. Wrapping the whole lifecycle in a context
    manager closes that gap.
    """
    options = Options()
    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )

    service = Service(executable_path=CHROMEDRIVER_PATH) if CHROMEDRIVER_PATH else Service()
    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        yield driver
    finally:
        try:
            driver.quit()
        except Exception:
            logger.warning("Error while quitting Chrome driver", exc_info=True)


def _scrape_amazon(
    query: str,
    pages: int,
    headless: bool,
    log: Callable[[str], None],
    cancel_event: Optional[Event],
    workdir: str,
) -> pd.DataFrame:
    data_folder = os.path.join(workdir, "amazon_raw")
    os.makedirs(data_folder, exist_ok=True)
    file_count = 0

    with _chrome_driver(headless) as driver:
        for i in range(1, pages + 1):
            if _should_stop(cancel_event):
                log("[Amazon] Cancelled.")
                break

            url = f"https://www.amazon.in/s?k={urllib.parse.quote_plus(query)}&page={i}"
            log(f"[Amazon] Scraping page {i}/{pages}...")

            elems = []
            for attempt in range(1, RETRIES_PER_PAGE + 1):
                if _should_stop(cancel_event):
                    break
                try:
                    driver.get(url)
                    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                        EC.presence_of_element_located((By.CLASS_NAME, "puis-card-container"))
                    )
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.5);")
                    time.sleep(1.0)
                    elems = driver.find_elements(By.CLASS_NAME, "puis-card-container")
                    if elems:
                        break
                except Exception:
                    log(f"[Amazon] Retrying page {i} (attempt {attempt}/{RETRIES_PER_PAGE})...")
                    time.sleep(_backoff_delay(attempt))

            log(f"[Amazon] Page {i}: {len(elems)} listings captured.")
            for elem in elems:
                try:
                    html = elem.get_attribute("outerHTML")
                    if html and len(html) > 100:
                        with open(os.path.join(data_folder, f"amz_{file_count}.html"), "w", encoding="utf-8") as f:
                            f.write(html)
                        file_count += 1
                except StaleElementReferenceException:
                    continue
            time.sleep(BASE_DELAY_SECONDS)

    return _parse_amazon_html(data_folder, query)


def _parse_amazon_html(data_folder: str, query: str) -> pd.DataFrame:
    rows = {"platform": [], "title": [], "price": [], "rating": [], "reviews": [], "link": []}
    files = sorted([f for f in os.listdir(data_folder) if f.endswith(".html")])

    for fname in files:
        path = os.path.join(data_folder, fname)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            soup = BeautifulSoup(f.read(), "html.parser")

        h2 = soup.find("h2")
        title = h2.get_text(strip=True) if h2 else ""

        a_tag = (h2.find("a", href=True) if h2 else None) or soup.find("a", class_="a-link-normal", href=True) or soup.find("a", href=True)
        raw_href = a_tag["href"] if a_tag and a_tag.has_attr("href") else ""
        link = make_full_url(raw_href, "amazon", title or query)

        price_tag = soup.find("span", class_="a-price-whole")
        price = _clean_price(price_tag.get_text(strip=True)) if price_tag else ""

        rating_tag = soup.find("span", class_="a-icon-alt")
        rating = rating_tag.get_text(strip=True).split(" ")[0] if rating_tag else ""

        reviews_tag = soup.find("span", class_="a-size-base s-underline-text")
        reviews = _clean_price(reviews_tag.get_text(strip=True)) if reviews_tag else ""

        if not title or not price:
            continue

        rows["platform"].append("Amazon")
        rows["title"].append(title)
        rows["price"].append(price)
        rows["rating"].append(rating)
        rows["reviews"].append(reviews)
        rows["link"].append(link)

    return pd.DataFrame(rows)


def _scrape_flipkart(
    query: str,
    pages: int,
    headless: bool,
    log: Callable[[str], None],
    cancel_event: Optional[Event],
    workdir: str,
) -> pd.DataFrame:
    data_folder = os.path.join(workdir, "flipkart_raw")
    os.makedirs(data_folder, exist_ok=True)
    file_count = 0

    with _chrome_driver(headless) as driver:
        driver.get("https://www.flipkart.com")
        time.sleep(1.5)

        for i in range(1, pages + 1):
            if _should_stop(cancel_event):
                log("[Flipkart] Cancelled.")
                break

            url = f"https://www.flipkart.com/search?q={urllib.parse.quote_plus(query)}&page={i}"
            log(f"[Flipkart] Fetching page {i}/{pages}...")

            elems = []
            for attempt in range(1, RETRIES_PER_PAGE + 1):
                if _should_stop(cancel_event):
                    break
                try:
                    driver.get(url)
                    time.sleep(2.0)

                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.3);")
                    time.sleep(0.8)
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.7);")
                    time.sleep(0.8)

                    elems = driver.find_elements(
                        By.CSS_SELECTOR,
                        "div[data-id], div._75W9qi, div._1sd2S1, div.cPHRSc, div._2kHM0A, div._1AtVbE, div.slKeyU, div._4ddW1p, div._13oc-S, div._2kHM0A",
                    )
                    if elems:
                        break
                except Exception:
                    log(f"[Flipkart] Retrying page {i} (attempt {attempt}/{RETRIES_PER_PAGE})...")
                    time.sleep(_backoff_delay(attempt))

            log(f"[Flipkart] Page {i}: captured {len(elems)} potential raw cards.")
            for elem in elems:
                try:
                    html = elem.get_attribute("outerHTML")
                    if html and len(html) > 80:
                        with open(os.path.join(data_folder, f"fk_{file_count}.html"), "w", encoding="utf-8") as f:
                            f.write(html)
                        file_count += 1
                except StaleElementReferenceException:
                    continue
            time.sleep(BASE_DELAY_SECONDS)

    return _parse_flipkart_html(data_folder, query)


def _parse_flipkart_html(data_folder: str, query: str) -> pd.DataFrame:
    rows = {"platform": [], "title": [], "price": [], "rating": [], "reviews": [], "link": []}
    files = sorted([f for f in os.listdir(data_folder) if f.endswith(".html")])

    for fname in files:
        path = os.path.join(data_folder, fname)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            soup = BeautifulSoup(f.read(), "html.parser")

        title = ""
        title_tag = soup.select_one("a.wPML33, div._4rR01T, a.s1Q98w, a.IRy530, div.Kz310d, a.WpA932, a[title], div.B_NuCI, a.title, div._2WkL35")
        if title_tag:
            title = title_tag.get("title", "") or title_tag.get_text(strip=True)

        if not title:
            img_tag = soup.select_one("img[alt]")
            if img_tag and len(img_tag.get("alt", "")) > 3:
                title = img_tag["alt"]
            else:
                a_tag_any = soup.select_one('a[href*="/p/"]')
                if a_tag_any:
                    title = a_tag_any.get_text(strip=True)

        a_tag = soup.select_one('a[href*="/p/"], a[href*="pid="], a._2UzuK3, a.CGzT34, a[href]')
        raw_href = a_tag["href"] if a_tag and a_tag.has_attr("href") else ""
        link = make_full_url(raw_href, "flipkart", title or query)

        price = ""
        price_tag = soup.select_one("div._30jeq3, div._1_WHN1, div.Nx9bqj, div.D4Gf3k, div._25bR3d, div._30jeq3")
        if price_tag:
            price = _clean_price(price_tag.get_text(strip=True))

        if not price:
            text_content = soup.get_text()
            price_match = re.search(r"(?:₹|Rs\.?)\s*([\d,]+)", text_content)
            if price_match:
                price = _clean_price(price_match.group(1))

        rating_tag = soup.select_one("._1psv1zeb9._1psv1ze0._1psv1zekr._1psv1ze9f._1psv1ze2u._1psv1ze53")
        rating = rating_tag.get_text(strip=True) if rating_tag else ""

        reviews_tag = soup.select_one("div._3LWZlK, div.X133Au, div._5OvM3d, div._38r20e, span._1l43rN")
        reviews = _clean_price(reviews_tag.get_text(strip=True)) if reviews_tag else ""

        if not title and not price:
            continue

        if not title:
            title = f"Flipkart Item ({query})"

        rows["platform"].append("Flipkart")
        rows["title"].append(title)
        rows["price"].append(price if price else "N/A")
        rows["rating"].append(rating)
        rows["reviews"].append(reviews)
        rows["link"].append(link)

    return pd.DataFrame(rows)


def _compute_price_differences(df: pd.DataFrame) -> pd.DataFrame:
    """Computes price difference between Amazon and Flipkart items."""
    df = df.copy()
    df["price_num"] = pd.to_numeric(df["price"], errors="coerce")

    amz_df = df[df["platform"] == "Amazon"].copy()
    fk_df = df[df["platform"] == "Flipkart"].copy()

    amz_min = amz_df["price_num"].min() if not amz_df.empty else None
    fk_min = fk_df["price_num"].min() if not fk_df.empty else None

    diff_list = []
    for _, row in df.iterrows():
        p_val = row["price_num"]
        if pd.isna(p_val):
            diff_list.append("—")
            continue

        current_platform = row["platform"]
        opposite_df = fk_df if current_platform == "Amazon" else amz_df
        opposite_min = fk_min if current_platform == "Amazon" else amz_min

        best_match_price = None
        best_ratio = 0.0

        for _, opp_row in opposite_df.iterrows():
            if pd.isna(opp_row["price_num"]):
                continue
            ratio = SequenceMatcher(None, str(row["title"]).lower(), str(opp_row["title"]).lower()).ratio()
            if ratio > 0.35 and ratio > best_ratio:
                best_ratio = ratio
                best_match_price = opp_row["price_num"]

        target_price = best_match_price if best_match_price is not None else opposite_min

        if target_price is not None:
            diff = int(p_val - target_price)
            opp_name = "Flipkart" if current_platform == "Amazon" else "Amazon"
            if diff > 0:
                diff_str = f"+₹{diff:,} (Cheaper on {opp_name})"
            elif diff < 0:
                diff_str = f"-₹{abs(diff):,} (Cheaper on {current_platform})"
            else:
                diff_str = "Same Price"
            diff_list.append(diff_str)
        else:
            diff_list.append("—")

    df["price_diff"] = diff_list
    df.drop(columns=["price_num"], inplace=True)
    return df


def run_scrape(
    query: str,
    pages: int = 2,
    headless: bool = True,
    log: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[Event] = None,
) -> Tuple[pd.DataFrame, str]:
    query = (query or "").strip()
    if not query:
        raise ValueError("query must not be empty")

    def emit(msg: str):
        if log:
            try:
                log(msg)
            except Exception:
                pass
        logger.info(msg)

    emit(f"Starting comparison scan for '{query}' across Amazon & Flipkart...")

    # Raw scraped HTML lives in a per-run temp dir (not the process cwd), so
    # concurrent/duplicate queries never collide and stale files from a
    # previous run never leak into a new one. It's removed once parsing is
    # done, whether or not the run succeeded.
    slug = slugify(query)
    workdir = tempfile.mkdtemp(prefix=f"pl_{slug}_")

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_amz = executor.submit(_scrape_amazon, query, pages, headless, emit, cancel_event, workdir)
            future_fk = executor.submit(_scrape_flipkart, query, pages, headless, emit, cancel_event, workdir)

            df_amazon = future_amz.result()
            df_flipkart = future_fk.result()

        df_combined = pd.concat([df_amazon, df_flipkart], ignore_index=True)
        df_combined.drop_duplicates(subset=["platform", "title", "price"], inplace=True)
        df_combined = _compute_price_differences(df_combined)

        # Unique, safe output filename: slug + timestamp avoids both illegal
        # characters and collisions between repeated/concurrent runs of the
        # same query.
        csv_filename = f"{slug}_{int(time.time())}_comparison.csv"
        csv_path = os.path.join(OUTPUT_DIR, csv_filename)
        df_combined.to_csv(csv_path, index=False, encoding="utf-8-sig")

        emit(
            f"Scan finished. Extracted {len(df_combined)} total items "
            f"({len(df_amazon)} Amazon, {len(df_flipkart)} Flipkart)."
        )
        return df_combined, csv_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
