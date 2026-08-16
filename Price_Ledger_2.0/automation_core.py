"""
Core scraping logic for the Price Ledger dashboard.

Scrapes Amazon.in search results for a query across N pages, saves each
result card as raw HTML, then parses those files into a DataFrame and
writes {query}_data.csv.

`log(message)` is an optional callback so a caller (e.g. the Flask job
runner in app.py) can stream human-readable progress lines to a frontend
while the scrape is still running.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger("price_ledger")

RESULT_CARD_CLASS = "puis-card-container"
PAGE_LOAD_TIMEOUT = 20
RETRIES_PER_PAGE = 3
BASE_DELAY_SECONDS = 2.5  # polite delay between pages, plus jitter added at call site


@dataclass
class ScrapeStats:
    pages_requested: int
    pages_completed: int
    items_saved: int
    rows_parsed: int
    csv_path: str


def make_full_url(href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.amazon.in" + href
    return "https://www.amazon.in/" + href


def _clean_price(raw: str) -> str:
    """'1,999.' -> '1999' — keep digits only so the column is numeric-friendly."""
    return re.sub(r"[^\d]", "", raw or "")


def _build_driver(headless: bool) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    # Selenium 4.6+ resolves the matching chromedriver automatically —
    # no webdriver-manager / manual driver download needed.
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver


def _load_page_with_retries(driver, url: str, emit) -> list:
    """Load `url` and return the result-card elements, retrying on timeout/staleness."""
    last_error = None
    for attempt in range(1, RETRIES_PER_PAGE + 1):
        try:
            driver.get(url)
            WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                EC.presence_of_element_located((By.CLASS_NAME, RESULT_CARD_CLASS))
            )
            # A short scroll helps trigger lazy-loaded cards below the fold.
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.6);")
            time.sleep(0.5)
            return driver.find_elements(By.CLASS_NAME, RESULT_CARD_CLASS)
        except TimeoutException:
            last_error = "timed out waiting for results"
        except WebDriverException as e:
            last_error = str(e).splitlines()[0]
        emit(f"  attempt {attempt}/{RETRIES_PER_PAGE} failed ({last_error}), retrying...")
        time.sleep(2 * attempt)
    emit(f"  giving up on this page after {RETRIES_PER_PAGE} attempts ({last_error})")
    return []


def run_scrape(query: str, pages: int = 19, headless: bool = True, log=None) -> tuple[pd.DataFrame, str]:
    query = (query or "").strip()
    if not query:
        raise ValueError("query must not be empty")
    pages = max(1, int(pages))

    def emit(msg: str):
        if log:
            try:
                log(msg)
            except Exception:
                pass
        logger.info(msg)

    data_folder = f"{query}_data"
    os.makedirs(data_folder, exist_ok=True)
    file_count = 0
    pages_completed = 0

    emit(f"Launching Chrome (headless={headless})")
    driver = _build_driver(headless)

    try:
        for i in range(1, pages + 1):
            url = f"https://www.amazon.in/s?k={query}&page={i}"
            emit(f"Page {i}/{pages}: requesting {url}")

            elems = _load_page_with_retries(driver, url, emit)
            emit(f"Page {i}/{pages}: {len(elems)} items found")

            for elem in elems:
                try:
                    html = elem.get_attribute("outerHTML")
                except StaleElementReferenceException:
                    continue
                if not html:
                    continue
                with open(f"{data_folder}/{query}_{file_count}.html", "w", encoding="utf-8") as f:
                    f.write(html)
                file_count += 1

            if elems:
                pages_completed += 1

            if i < pages:
                time.sleep(BASE_DELAY_SECONDS)
    finally:
        driver.quit()

    emit(f"Scrape finished — {file_count} listing files saved across {pages_completed}/{pages} pages. Parsing HTML...")

    df = _parse_saved_html(data_folder, emit)
    csv_path = f"{data_folder}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    emit(f"Wrote {len(df)} rows to {csv_path}")
    return df, csv_path


def _parse_saved_html(data_folder: str, emit) -> pd.DataFrame:
    rows = {"title": [], "price": [], "link": []}
    files = sorted(
        f for f in os.listdir(data_folder)
        if os.path.isfile(os.path.join(data_folder, f)) and f.lower().endswith((".html", ".htm", ".txt"))
    )

    for fname in files:
        path = os.path.join(data_folder, fname)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                html_doc = f.read()
        except OSError as e:
            emit(f"Read error {fname}: {e}")
            continue

        soup = BeautifulSoup(html_doc, "html.parser")

        h2 = soup.find("h2")
        title = h2.get_text(strip=True) if h2 else ""

        a_tag = h2.find("a", href=True) if h2 else None
        if not a_tag:
            candidates = soup.find_all("a", href=True)
            product_candidates = [a for a in candidates if any(p in a["href"] for p in ("/dp/", "/gp/", "/product/"))]
            a_tag = product_candidates[0] if product_candidates else (candidates[0] if candidates else None)
        href = a_tag["href"] if a_tag and a_tag.has_attr("href") else ""
        link = make_full_url(href)

        price_tag = soup.find("span", class_="a-price-whole")
        price = _clean_price(price_tag.get_text(strip=True)) if price_tag else ""

        # Skip rows that carried no useful data at all (e.g. a sponsored
        # placeholder card with none of the expected fields).
        if not title and not link and not price:
            continue

        rows["title"].append(title)
        rows["price"].append(price)
        rows["link"].append(link)

    return pd.DataFrame(rows)


if __name__ == "__main__":
    # Quick manual test from the terminal: python automation_core.py "wireless mouse" 2
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    q = sys.argv[1] if len(sys.argv) > 1 else "laptop stand"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    run_scrape(q, pages=n, headless=True)
