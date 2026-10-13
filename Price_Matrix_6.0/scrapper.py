"""
scrapper.py — raw scraper + orchestration.

This file now works in two modes, with the SAME scraping logic in both:

1. CLI / original behaviour
   `python scrapper.py`  → asks for a product, scrapes Amazon page 1-10, then
   Flipkart page 1-10, parses both with parser.py, and finishes with the
   cross-platform comparison from comparator.py — exactly the sequential
   pipeline that existed before. Nothing to configure, no web server needed.

2. Imported by app.py (the web UI)
   app.py imports `scrape_platform`, `PLATFORMS` and `run_full_pipeline` and
   runs the two stores in parallel worker threads, streaming the same log
   lines to the browser. The driver factory lets app.py silently fall back to
   a different browser (Edge/Brave/Chromium) when Chrome isn't on PATH.

Imports are guarded so `from scrapper import scrape_platform` never triggers a
scrape or blocks on input().
"""

import os
import time

try:  # selenium is only needed to actually drive a browser
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    _SELENIUM_IMPORT_ERROR = None
except Exception as _e:                                    # pragma: no cover
    webdriver = None
    By = None
    _SELENIUM_IMPORT_ERROR = _e

from paths import root_path
from logger import log

# ---------------------------------------------------------------------------
# The two targets, in the order the original script ran them.
# ---------------------------------------------------------------------------
PLATFORMS = [
    {"name": "amazon", "card_class": "puis-card-container"},
    {"name": "flipkart", "card_class": "tWp6CS"},
]

PLATFORM_BY_NAME = {p["name"]: p for p in PLATFORMS}

DEFAULT_PAGES = 10   # original behaviour: pages 1..10 per store


# ---------------------------------------------------------------------------
# Core scraper (one platform, page by page)
# ---------------------------------------------------------------------------
def scrape_platform(product_query, site_name, card_class=None, max_pages=DEFAULT_PAGES,
                    out_dir=None, driver_factory=None, headless=False, cancel_event=None,
                    on_page=None):
    """
    Save one raw HTML file per result card for `site_name`.

    Parameters
    ----------
    product_query : str   what the user searched (drives folder + file names)
    site_name     : str   "amazon" or "flipkart"
    card_class    : str   container class; defaults to PLATFORMS entry
    max_pages     : int   how many result pages to walk (default 10)
    out_dir       : str   where to drop the HTML files. Defaults to
                          "<site>_<query>_data" in the project root — the folder
                          parser.py looks in when run standalone.
    driver_factory: callable() -> WebDriver   custom driver builder (app.py uses
                          it for headless / non-Chrome browsers). Default: Chrome.
    headless      : bool  ask the default Chrome path to run headless.
    cancel_event  : threading.Event  optional; checked between pages so the web
                          UI's Cancel button stops the walk promptly.
    on_page       : callable(page_number, max_pages)  optional progress hook,
                          called once per finished page (app.py drives the bar).

    Returns
    -------
    dict: {"platform", "pages_scraped", "files_saved", "cancelled"}
    """
    site_name = site_name.strip().lower()
    card_class = card_class or PLATFORM_BY_NAME.get(site_name, {}).get("card_class")
    folder_name = f"{site_name}_{product_query.strip().lower()}_data"
    target_folder = out_dir or root_path(folder_name)
    os.makedirs(target_folder, exist_ok=True)

    # --- driver -------------------------------------------------------------
    if driver_factory is not None:
        driver = driver_factory()
    else:
        if webdriver is None:
            raise RuntimeError(
                "selenium is not installed — run `pip install -r requirements.txt` "
                f"(import error: {_SELENIUM_IMPORT_ERROR})"
            )
        if headless:
            options = webdriver.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            driver = webdriver.Chrome(options=options)
        else:
            driver = webdriver.Chrome()

    file_no = 0
    pages_scraped = 0
    cancelled = False

    try:
        # Looping through pages 1 to max_pages
        for i in range(1, max_pages + 1):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                log(f"[!] Cancel requested — stopping {site_name} after page {i - 1}.")
                break

            if site_name == "amazon":
                url = f"https://www.amazon.in/s?k={product_query}&page={i}&xpid=ZvtZrtPiBlTJe&crid=2R0CIU8T257B6&qid=1784997087&sprefix=laptop%2Caps%2C273&ref=sr_pg_2"
            else:
                url = f"https://www.flipkart.com/search?q={product_query}&otracker=search&otracker1=search&marketplace=FLIPKART&as-show=on&as=off&page={i}"

            log(f"[{site_name}] page {i}/{max_pages} → {url}")
            driver.get(url)
            time.sleep(1)  # Structural cushion for loading

            # Extract site-specific target elements
            if site_name == "amazon":
                elems = driver.find_elements(By.CLASS_NAME, card_class)
            else:
                elems = driver.find_elements(By.XPATH, "//div[@data-id] | //div[contains(@class, 'tWp6CS')]")
                if not elems:
                    elems = driver.find_elements(By.CLASS_NAME, "_1AtVbE")

            page_saved = 0
            for elem in elems:
                try:
                    d = elem.get_attribute("outerHTML")
                    # Save the HTML file inside the dynamically created site folder
                    with open(os.path.join(target_folder, f"{product_query}_{file_no}.html"), "w", encoding="utf-8") as f:
                        f.write(d)
                    file_no += 1
                    page_saved += 1
                except Exception:
                    continue

            pages_scraped = i
            log(f"[{site_name}] page {i} done — {page_saved} card(s) saved (total {file_no})")

            if on_page is not None:
                try:
                    on_page(i, max_pages)
                except Exception:
                    pass

            time.sleep(2)  # Prevent IP blocks
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return {
        "platform": site_name,
        "pages_scraped": pages_scraped,
        "files_saved": file_no,
        "cancelled": cancelled,
    }


# ---------------------------------------------------------------------------
# Original sequential pipeline (CLI mode) — unchanged order of operations
# ---------------------------------------------------------------------------
def run_full_pipeline(user_product, max_pages=DEFAULT_PAGES, headless=False):
    """
    Scrape → parse → compare, exactly as the original script did:
    both stores one after another, then the comparison engine.

    app.py does this concurrently instead; both paths call the same functions.
    """
    # Deferred imports keep this module importable without pandas/bs4 present.
    from parser import process_data
    from comparator import run_price_comparison

    for platform in PLATFORMS:
        site_name = platform["name"]
        card_class = platform["card_class"]

        log(f"\n=========================================")
        log(f"[*] STARTING SCRAPER FOR: {site_name.upper()}")
        log(f"=========================================")

        try:
            scrape_platform(user_product, site_name, card_class=card_class,
                            max_pages=max_pages, headless=headless)
        except Exception as exc:
            # A missing browser (or a store being down) shouldn't abort the whole
            # run — report it and let the other platform + parser + comparator
            # do whatever they can, exactly like app.py's per-store isolation.
            log(f"[x] {site_name.upper()} failed: {exc}")
            continue

        log(f"[+] Finished raw scraping for {site_name}. Saving session files...")

        # 3. Trigger the parser immediately for this specific platform
        log(f"[*] Running data parsing pipeline for {site_name}...")
        process_data(user_product, site_name)

    log(f"\n=========================================")
    log(f"[+] ALL DATASETS CAPTURED & PARSED")
    log(f"[*] Initiating Cross-Platform Comparison Evaluation...")
    log(f"=========================================")

    # 4. Trigger the comparison engine automatically
    return run_price_comparison(user_product)


# 1. Capture user product query once
if __name__ == "__main__":
    user_product = input("What product do you want to scrape? ").strip()
    run_full_pipeline(user_product)
