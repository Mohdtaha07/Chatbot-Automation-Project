================================================================================
  PRICE MATRIX 4.0  (Price Ledger)
  Amazon  vs  Flipkart  Price Comparison & Analytics Dashboard
================================================================================

Project  : Price_Matrix_4.0  (part of Chatbot-Automation-Project)
Backend  : Python 3 + Flask + Selenium + BeautifulSoup + pandas
Frontend : Single-file HTML dashboards (index.html / index2.html)
Repo     : https://github.com/Mohdtaha07/Chatbot-Automation-Project


--------------------------------------------------------------------------------
1. WHAT IS THIS PROJECT?
--------------------------------------------------------------------------------

Price Matrix 4.0 is a web-based price comparison tool. You type a product name
(for example "iphone 15" or "gaming laptop"), and the system scrapes live
listings from Amazon India and Flipkart at the same time, parses the results,
and shows them side by side in a dashboard with a built-in chatbot assistant.

It is fully automated:

  * Both stores are scraped IN PARALLEL (two browser sessions, two threads).
  * Listings are parsed into a clean table: title, price, rating, reviews, link.
  * For every product, the app finds the closest match on the other store
    (fuzzy title matching) and computes the price difference, marking which
    store is cheaper.
  * Results can be filtered, sorted, charted, and downloaded as a CSV report.
  * A rule-based chatbot answers questions about the dashboard and its results.

The whole comparison is asynchronous: the frontend starts a "job", the backend
scrapes in the background, and the frontend polls for progress with a live log
until the results are ready.


--------------------------------------------------------------------------------
2. FEATURES
--------------------------------------------------------------------------------

Backend
  - Flask REST API with asynchronous job handling.
  - Two scrapers (Amazon + Flipkart) run in a ThreadPoolExecutor.
  - Selenium Chrome with anti-bot/stealth configuration and headless mode.
  - Retry logic per page with exponential backoff + random jitter.
  - Real cancellation support (threading.Event) that stops the browser.
  - Thread-safe in-memory JobStore with a concurrent-job cap (default 3).
  - Automatic TTL cleanup thread that frees stale jobs from memory.
  - Safe file handling: query is slugified, raw HTML goes to a temp dir that
    is deleted after each run, outputs get a timestamped unique filename.
  - CSV export (UTF-8 BOM so Excel opens it correctly).
  - Fuzzy title matching (difflib SequenceMatcher) to pair products across
    stores and compute "+/- price difference" per listing.

Frontend (index2.html — "Price Matrix", dark dashboard)
  - Search bar + pages-per-store selector + Enter-to-search.
  - System "seal" status: Idle / Scanning / Done.
  - Live scan log streaming messages from both scrapers.
  - KPI summary cards: total listings, Amazon lowest, Flipkart lowest,
    cheapest platform.
  - Price distribution histogram (inline SVG).
  - Filters: store (Amazon / Flipkart), min/max price, spec keyword search.
  - Sorting: original, price low->high, price high->low, best rating first.
  - "Show more results" pagination and clickable product links.
  - Download CSV button (server file, or client-side CSV in demo mode).
  - Matrix Assistant chatbot widget with quick question chips.
  - Demo mode: "Preview with sample data" loads built-in sample listings so
    the UI can be explored without a running scraper.
  - Toast notifications and graceful error banners.

Frontend (index.html — "Price Ledger", light/paper theme)
  - Earlier, simpler version of the same dashboard: search, live log,
    results table with filters, CSV download, and the chatbot panel.


--------------------------------------------------------------------------------
3. PROJECT STRUCTURE
--------------------------------------------------------------------------------

Price_Matrix_4.0/
|
|-- app.py               Flask server: REST API, job store, background threads,
|                        CORS config, cleanup thread, job lifecycle.
|
|-- automation_core.py   The scraping engine:
|                          - slugify()                 safe file names
|                          - _chrome_driver()          configured Chrome session
|                          - _scrape_amazon()          fetch + save raw HTML
|                          - _parse_amazon_html()      HTML -> DataFrame
|                          - _scrape_flipkart()        fetch + save raw HTML
|                          - _parse_flipkart_html()    HTML -> DataFrame
|                          - _compute_price_differences()  cross-store diff
|                          - run_scrape()              full pipeline, returns
|                                                      (DataFrame, csv_path)
|
|-- chatbot.py           Flask Blueprint providing POST /api/chat, a keyword
|                        based knowledge-base assistant (no external AI API).
|
|-- index.html           Dashboard v1 (light "Price Ledger" theme).
|-- index2.html          Dashboard v2 (dark "Price Matrix" theme) — the main
|                        UI with charts, filters, demo mode and chatbot widget.
|-- __pycache__/         Python bytecode cache (auto-generated, ignore it).


--------------------------------------------------------------------------------
4. HOW IT WORKS (PIPELINE)
--------------------------------------------------------------------------------

   Browser                Flask backend              automation_core
  ---------              --------------             ---------------
  POST /api/run   --->   create job (job_id)
                         start thread        --->   run_scrape(query, pages)
                                                      |  Thread 1: Amazon  \
                                                      |  Thread 2: Flipkart > parallel
                                                      |  (Selenium + Chrome)
                                                      |  raw HTML saved to temp dir
                                                      |  BeautifulSoup parsing
                                                      |  dedupe + price diff
                                                      v
  GET /api/status/<id> -> live log + status   <---   CSV written to output dir
  (poll every ~1.5 s)
  GET /api/download/<id> -> CSV file

Step by step
  1. The frontend sends the product query and the number of pages per store.
  2. The backend validates the input (non-empty, <= 200 chars, 1..50 pages)
     and rejects the request if too many jobs are already running (HTTP 429).
  3. A job is created with a 10-character id and a background thread starts
     run_scrape().
  4. Amazon and Flipkart are scraped concurrently. For each page the scraper
     loads the search URL, waits for result containers, scrolls the page,
     captures each product card's HTML, and writes it to a temporary folder.
     Failed pages are retried up to 3 times with increasing delays.
  5. The saved HTML is parsed with BeautifulSoup into a table:
     platform, title, price, rating, reviews, link.
     Amazon rating comes from "4.3 out of 5 stars" (first token is kept);
     prices are reduced to digits only.
  6. Both tables are combined, exact duplicates are dropped, and the price
     difference is computed: for each listing the best-matching listing on the
     opposite store is found by fuzzy title similarity. If no good match
     exists, the opposite store's lowest price is used as the reference.
     The result is a label like:
         "+₹2,000 (Cheaper on Flipkart)"
         "-₹500 (Cheaper on Amazon)"
         "Same Price"  /  "—"
  7. The combined table is saved as CSV and the job is marked "done".
  8. The frontend keeps polling /api/status until the job finishes, rendering
     the log live, then draws the KPIs, chart and results table.


--------------------------------------------------------------------------------
5. REQUIREMENTS
--------------------------------------------------------------------------------

  * Python 3.9 or newer (tested with 3.13)
  * Google Chrome or Chromium installed on the machine
  * ChromeDriver — usually NOT needed manually: Selenium 4.6+ downloads and
    manages it automatically (Selenium Manager). An explicit path can be set
    with the PRICE_LEDGER_CHROMEDRIVER_PATH environment variable.
  * Internet access (the tool scrapes live store pages)
  * A modern browser to open the dashboard

Python packages
  flask
  flask-cors
  selenium
  beautifulsoup4
  pandas

Install them with:

  pip install flask flask-cors selenium beautifulsoup4 pandas


--------------------------------------------------------------------------------
6. SETUP AND RUNNING
--------------------------------------------------------------------------------

1) Install the dependencies (see above).

2) Start the backend from inside the Price_Matrix_4.0 folder:

     cd Price_Matrix_4.0
     python app.py

   The server starts on http://127.0.0.1:8787 by default.
   (Change the port with the PRICE_LEDGER_PORT environment variable.)

3) Open the dashboard:

   Option A (recommended): serve the folder so the page and API share the
   same origin —

     python -m http.server 8787

   then open http://localhost:8787/index2.html  and enter a product name
   and click "Compare prices".

   Option B: open index2.html directly from disk (double-click). It will call
   the API at http://localhost:8787, which works as long as the Flask server
   is running and CORS is permissive (the default allows all origins).

   Note: index.html has the API base URL hardcoded to http://localhost:8787,
   while index2.html automatically uses a relative API base when it is served
   from port 8787 and falls back to http://localhost:8787 otherwise.

4) In the dashboard:
   - Type the product to search (e.g. "iphone 15").
   - Choose how many result pages per store to scrape (1 page = fast, more
     pages = more listings, slower).
   - Click "Compare prices" (or press Enter) and watch the live scan log.
   - When the status shows "Done", the KPI cards, price distribution chart
     and the results table are filled in. Use the filters and sorting
     controls, then click "Download CSV" to export the report.
   - Click "Preview with sample data" in the empty state to explore the UI
     without running a scrape.


--------------------------------------------------------------------------------
7. CONFIGURATION (ENVIRONMENT VARIABLES)
--------------------------------------------------------------------------------

  PRICE_LEDGER_PORT                  Backend port.                      (8787)
  PRICE_LEDGER_ALLOWED_ORIGIN        CORS origin for /api/*.             (*)
  PRICE_LEDGER_MAX_PAGES             Max pages per store per job.        (50)
  PRICE_LEDGER_MAX_CONCURRENT_JOBS   Simultaneous running jobs.           (3)
  PRICE_LEDGER_JOB_TTL_SECONDS       How long finished jobs stay.      (3600)
  PRICE_LEDGER_CLEANUP_INTERVAL_SECONDS  Cleanup thread interval.        (300)
  PRICE_LEDGER_OUTPUT_DIR            Folder for CSV reports.
                                     (default: <cwd>/data/output)
  PRICE_LEDGER_CHROMEDRIVER_PATH     Explicit chromedriver binary path.
                                     (default: auto via Selenium Manager)

Set them before launching, for example (Linux/macOS):

  PRICE_LEDGER_PORT=9000 PRICE_LEDGER_MAX_PAGES=10 python app.py

Windows (cmd):

  set PRICE_LEDGER_PORT=9000
  python app.py


--------------------------------------------------------------------------------
8. REST API REFERENCE
--------------------------------------------------------------------------------

GET /api/ping
    Health check.
    -> {"ok": true}

POST /api/run
    Start a new scraping job.
    Body (JSON):
      {
        "query":    "iphone 15",     (required, max 200 characters)
        "pages":    2,               (optional, 1..50, default 2)
        "headless": true             (optional, default true)
      }
    -> {"job_id": "a1b2c3d4e5"}
    Errors: 400 invalid input, 429 too many concurrent jobs.

GET /api/status/<job_id>
    Poll a job's progress and results.
    -> {
         "status": "running" | "done" | "error",
         "query": "...", "pages": 2,
         "log": ["[Amazon] Scraping page 1/2...", "..."],
         "data": [ { platform, title, price, rating, reviews, link,
                     price_diff }, ... ],
         "row_count": 41,
         "error": null,
         "csv_path": "/.../data/output/iphone-15_1699999999_comparison.csv",
         "cancelled": false,
         "created_at": 1699999900.0
       }
    -> 404 if the job id is unknown or malformed.

GET /api/download/<job_id>
    Download the finished CSV report as an attachment.
    -> CSV file
    -> 404 unknown job, 409 job not finished yet, 410 file missing on disk.

POST /api/jobs/<job_id>/cancel
    Request cancellation; this sets a real threading event that stops the
    scraper loops (it does not just hide the logs).
    -> {"ok": true}   (404 unknown job id)

POST /api/chat
    Ask the built-in assistant (rule/keyword based, no external API).
    Body: {"message": "how do I filter by price?"}
    -> {"reply": "..."}
    Topics covered: getting started, supported platforms, CSV/download,
    filters and sorting, status seal meaning, price/rating KPIs.


--------------------------------------------------------------------------------
9. CSV OUTPUT FORMAT
--------------------------------------------------------------------------------

File name:  <query-slug>_<unix-timestamp>_comparison.csv
Location :  PRICE_LEDGER_OUTPUT_DIR (default ./data/output)
Encoding :  UTF-8 with BOM (opens cleanly in Excel)

Columns:
  platform    "Amazon" or "Flipkart"
  title       product name as listed on the store
  price       price in rupees, digits only (empty or "N/A" if unavailable)
  rating      store rating (Amazon: numeric, e.g. 4.3)
  reviews     number of ratings/reviews (digits only, may be empty)
  link        absolute clickable product URL (or a store search URL fallback)
  price_diff  compared to the other store, e.g.
              "+₹2,000 (Cheaper on Flipkart)"
              "-₹500 (Cheaper on Amazon)"
              "Same Price" or "—" when no comparison is possible


--------------------------------------------------------------------------------
10. CHATBOT (Matrix Assistant)
--------------------------------------------------------------------------------

chatbot.py implements a lightweight, dependency-free assistant as a Flask
Blueprint. It matches keywords in the user's message against a small
knowledge base (getting started, platforms, download/export, filters/sorting,
status states, price/rating KPIs) and returns the matching answer. Anything
unrecognized falls back to a friendly help message listing what it can do.
No external AI service or API key is required.


--------------------------------------------------------------------------------
11. NOTES, LIMITS AND TROUBLESHOOTING
--------------------------------------------------------------------------------

Server not reachable / "Backend API not reachable at localhost:8787"
  - Make sure `python app.py` is running and the port matches.
  - If you changed PRICE_LEDGER_PORT, update the apiBase in the HTML file.

"No results" or very few listings
  - Amazon and Flipkart frequently change their CSS class names and may show
    CAPTCHAs or bot checks. The selectors in automation_core.py
    (_parse_amazon_html / _parse_flipkart_html) are the place to update them.
  - Try headless: false (visible browser) so you can see what the page shows.
  - Increase the delay between requests (BASE_DELAY_SECONDS) if you get
    blocked often.

ChromeDriver errors
  - Selenium Manager normally handles this automatically. If it cannot, set
    PRICE_LEDGER_CHROMEDRIVER_PATH to a local chromedriver binary that matches
    your Chrome version.

Jobs queue returning HTTP 429
  - At most 3 jobs run at once by default. Wait for one to finish or raise
    PRICE_LEDGER_MAX_CONCURRENT_JOBS.

Performance
  - One page per store takes roughly 10-30 seconds. More pages scale the
    time and request volume linearly. Keep pages low to be polite to the
    stores.

Scraping ethics and legality
  - This tool is intended for personal / educational use (it was built as a
    learning project). Store terms of service, robots.txt and local laws may
    restrict automated scraping. Scrape responsibly, keep request rates low,
    and do not use the data commercially without permission.

Known limitations
  - Jobs live in memory only: restarting the server loses job history
    (CSV files on disk remain).
  - Price comparison matches products by fuzzy title similarity; the pairing
    can occasionally mismatch similar models (e.g. 128 GB vs 256 GB variants).
  - Amazon ratings are parsed as the first numeric token, so the displayed
    value is the star rating, not the review count.
  - The dashboard has no built-in login/auth — do not expose the API publicly
    without adding authentication.


--------------------------------------------------------------------------------
12. QUICK START (TL;DR)
--------------------------------------------------------------------------------

  pip install flask flask-cors selenium beautifulsoup4 pandas
  cd Price_Matrix_4.0
  python app.py                     # terminal 1  -> API on :8787
  python -m http.server 8787        # terminal 2  -> serve the UI
  # open http://localhost:8787/index2.html and search for a product

================================================================================
  End of README — Price_Matrix_4.0
================================================================================