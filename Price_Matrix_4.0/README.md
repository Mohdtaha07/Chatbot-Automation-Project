# Price Matrix 4.0 â€” Amazon â‡„ Flipkart Price Comparison & Analytics Dashboard

A web-based tool that scrapes live product listings from **Amazon India** and **Flipkart** in parallel, parses them into a clean comparison table, and displays everything in an interactive dashboard with a built-in chatbot assistant.

| | |
|---|---|
| **Project** | `Price_Matrix_4.0` (part of `Chatbot-Automation-Project`) |
| **Backend** | Python 3 + Flask + Selenium + BeautifulSoup + pandas |
| **Frontend** | Single-file HTML dashboards (`index.html` / `index2.html`) |
| **Repo** | <https://github.com/Mohdtaha07/Chatbot-Automation-Project> |

---

## Table of Contents

- [What Is This Project?](#what-is-this-project)
- [Features](#features)
- [Project Structure](#project-structure)
- [How It Works (Pipeline)](#how-it-works-pipeline)
- [Requirements](#requirements)
- [Setup and Running](#setup-and-running)
- [Configuration (Environment Variables)](#configuration-environment-variables)
- [REST API Reference](#rest-api-reference)
- [CSV Output Format](#csv-output-format)
- [Chatbot (Matrix Assistant)](#chatbot-matrix-assistant)
- [Notes, Limits and Troubleshooting](#notes-limits-and-troubleshooting)
- [Quick Start (TL;DR)](#quick-start-tldr)

---

## What Is This Project?

Price Matrix 4.0 is a web-based price comparison tool. You type a product name (for example `iphone 15` or `gaming laptop`), and the system scrapes live listings from Amazon India and Flipkart at the same time, parses the results, and shows them side by side in a dashboard with a built-in chatbot assistant.

It is fully automated:

- Both stores are scraped **in parallel** (two browser sessions, two threads).
- Listings are parsed into a clean table: **title, price, rating, reviews, link**.
- For every product, the app finds the closest match on the other store (fuzzy title matching) and computes the price difference, marking which store is cheaper.
- Results can be **filtered, sorted, charted, and downloaded** as a CSV report.
- A rule-based **chatbot** answers questions about the dashboard and its results.

The whole comparison is asynchronous: the frontend starts a "job", the backend scrapes in the background, and the frontend polls for progress with a live log until the results are ready.

---

## Features

### Backend

- Flask REST API with asynchronous job handling.
- Two scrapers (Amazon + Flipkart) run in a `ThreadPoolExecutor`.
- Selenium Chrome with anti-bot/stealth configuration and headless mode.
- Retry logic per page with exponential backoff + random jitter.
- Real cancellation support (`threading.Event`) that stops the browser.
- Thread-safe in-memory `JobStore` with a concurrent-job cap (default 3).
- Automatic TTL cleanup thread that frees stale jobs from memory.
- Safe file handling: query is slugified, raw HTML goes to a temp dir that is deleted after each run, outputs get a timestamped unique filename.
- CSV export (UTF-8 BOM so Excel opens it correctly).
- Fuzzy title matching (`difflib.SequenceMatcher`) to pair products across stores and compute `+/-` price difference per listing.

### Frontend â€” `index2.html` ("Price Matrix", dark dashboard)

- Search bar + pages-per-store selector + Enter-to-search.
- System "seal" status: **Idle / Scanning / Done**.
- Live scan log streaming messages from both scrapers.
- KPI summary cards: total listings, Amazon lowest, Flipkart lowest, cheapest platform.
- Price distribution histogram (inline SVG).
- Filters: store (Amazon / Flipkart), min/max price, spec keyword search.
- Sorting: original, price low â†’ high, price high â†’ low, best rating first.
- "Show more results" pagination and clickable product links.
- Download CSV button (server file, or client-side CSV in demo mode).
- Matrix Assistant chatbot widget with quick question chips.
- **Demo mode**: "Preview with sample data" loads built-in sample listings so the UI can be explored without a running scraper.
- Toast notifications and graceful error banners.

### Frontend â€” `index.html` ("Price Ledger", light/paper theme)

- Earlier, simpler version of the same dashboard: search, live log, results table with filters, CSV download, and the chatbot panel.

---

## Project Structure

```
Price_Matrix_4.0/
|
|-- app.py               Flask server: REST API, job store, background threads,
|                        CORS config, cleanup thread, job lifecycle.
|
|-- automation_core.py   The scraping engine:
|                          - slugify()                     safe file names
|                          - _chrome_driver()              configured Chrome session
|                          - _scrape_amazon()              fetch + save raw HTML
|                          - _parse_amazon_html()          HTML -> DataFrame
|                          - _scrape_flipkart()            fetch + save raw HTML
|                          - _parse_flipkart_html()        HTML -> DataFrame
|                          - _compute_price_differences()  cross-store diff
|                          - run_scrape()                  full pipeline, returns
|                                                          (DataFrame, csv_path)
|
|-- chatbot.py           Flask Blueprint providing POST /api/chat, a keyword
|                        based knowledge-base assistant (no external AI API).
|
|-- index.html           Dashboard v1 (light "Price Ledger" theme).
|-- index2.html          Dashboard v2 (dark "Price Matrix" theme) â€” the main
|                        UI with charts, filters, demo mode and chatbot widget.
|-- __pycache__/         Python bytecode cache (auto-generated, ignore it).
```

---

## How It Works (Pipeline)

```
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
```

**Step by step**

1. The frontend sends the product query and the number of pages per store.
2. The backend validates the input (non-empty, â‰¤ 200 chars, 1â€“50 pages) and rejects the request if too many jobs are already running (`HTTP 429`).
3. A job is created with a 10-character id and a background thread starts `run_scrape()`.
4. Amazon and Flipkart are scraped concurrently. For each page the scraper loads the search URL, waits for result containers, scrolls the page, captures each product card's HTML, and writes it to a temporary folder. Failed pages are retried up to 3 times with increasing delays.
5. The saved HTML is parsed with BeautifulSoup into a table: `platform, title, price, rating, reviews, link`. Amazon rating comes from `"4.3 out of 5 stars"` (first token is kept); prices are reduced to digits only.
6. Both tables are combined, exact duplicates are dropped, and the price difference is computed: for each listing the best-matching listing on the opposite store is found by fuzzy title similarity. If no good match exists, the opposite store's lowest price is used as the reference. The result is a label like:
   ```
   "+â‚¹2,000 (Cheaper on Flipkart)"
   "-â‚¹500 (Cheaper on Amazon)"
   "Same Price"   /   "â€”"
   ```
7. The combined table is saved as CSV and the job is marked `done`.
8. The frontend keeps polling `/api/status` until the job finishes, rendering the log live, then draws the KPIs, chart and results table.

---

## Requirements

- **Python 3.9 or newer** (tested with 3.13)
- **Google Chrome or Chromium** installed on the machine
- **ChromeDriver** â€” usually *not* needed manually: Selenium 4.6+ downloads and manages it automatically (Selenium Manager). An explicit path can be set with the `PRICE_LEDGER_CHROMEDRIVER_PATH` environment variable.
- Internet access (the tool scrapes live store pages)
- A modern browser to open the dashboard

### Python packages

```
flask
flask-cors
selenium
beautifulsoup4
pandas
```

Install them with:

```bash
pip install flask flask-cors selenium beautifulsoup4 pandas
```

---

## Setup and Running

**1. Install the dependencies** (see above).

**2. Start the backend** from inside the `Price_Matrix_4.0` folder:

```bash
cd Price_Matrix_4.0
python app.py
```

The server starts on `http://127.0.0.1:8787` by default. (Change the port with the `PRICE_LEDGER_PORT` environment variable.)

**3. Open the dashboard:**

- **Option A (recommended):** serve the folder so the page and API share the same origin â€”

  ```bash
  python -m http.server 8787
  ```

  then open <http://localhost:8787/index2.html>, enter a product name and click **Compare prices**.

- **Option B:** open `index2.html` directly from disk (double-click). It will call the API at `http://localhost:8787`, which works as long as the Flask server is running and CORS is permissive (the default allows all origins).

> **Note:** `index.html` has the API base URL hardcoded to `http://localhost:8787`, while `index2.html` automatically uses a relative API base when it is served from port 8787 and falls back to `http://localhost:8787` otherwise.

**4. In the dashboard:**

- Type the product to search (e.g. `iphone 15`).
- Choose how many result pages per store to scrape (1 page = fast, more pages = more listings, slower).
- Click **Compare prices** (or press Enter) and watch the live scan log.
- When the status shows **Done**, the KPI cards, price distribution chart and the results table are filled in. Use the filters and sorting controls, then click **Download CSV** to export the report.
- Click **Preview with sample data** in the empty state to explore the UI without running a scrape.

---

## Configuration (Environment Variables)

| Variable | Description | Default |
|---|---|---|
| `PRICE_LEDGER_PORT` | Backend port. | `8787` |
| `PRICE_LEDGER_ALLOWED_ORIGIN` | CORS origin for `/api/*`. | `*` |
| `PRICE_LEDGER_MAX_PAGES` | Max pages per store per job. | `50` |
| `PRICE_LEDGER_MAX_CONCURRENT_JOBS` | Simultaneous running jobs. | `3` |
| `PRICE_LEDGER_JOB_TTL_SECONDS` | How long finished jobs stay in memory. | `3600` |
| `PRICE_LEDGER_CLEANUP_INTERVAL_SECONDS` | Cleanup thread interval. | `300` |
| `PRICE_LEDGER_OUTPUT_DIR` | Folder for CSV reports. | `<cwd>/data/output` |
| `PRICE_LEDGER_CHROMEDRIVER_PATH` | Explicit chromedriver binary path. | auto via Selenium Manager |

Set them before launching, for example:

**Linux / macOS**

```bash
PRICE_LEDGER_PORT=9000 PRICE_LEDGER_MAX_PAGES=10 python app.py
```

**Windows (cmd)**

```bat
set PRICE_LEDGER_PORT=9000
python app.py
```

---

## REST API Reference

### `GET /api/ping`

Health check.

```json
{ "ok": true }
```

### `POST /api/run`

Start a new scraping job.

**Body (JSON):**

```json
{
  "query": "iphone 15",
  "pages": 2,
  "headless": true
}
```

| Field | Required | Description |
|---|---|---|
| `query` | âœ… | Product to search (max 200 characters). |
| `pages` | â€” | Pages per store, 1â€“50 (default `2`). |
| `headless` | â€” | Run Chrome headless (default `true`). |

**Response:**

```json
{ "job_id": "a1b2c3d4e5" }
```

**Errors:** `400` invalid input, `429` too many concurrent jobs.

### `GET /api/status/<job_id>`

Poll a job's progress and results.

```json
{
  "status": "running",
  "query": "iphone 15",
  "pages": 2,
  "log": ["[Amazon] Scraping page 1/2...", "..."],
  "data": [
    {
      "platform": "Amazon",
      "title": "Apple iPhone 15 (128 GB) - Blue",
      "price": "65999",
      "rating": "4.3",
      "reviews": "12453",
      "link": "https://www.amazon.in/...",
      "price_diff": "+â‚¹2,000 (Cheaper on Flipkart)"
    }
  ],
  "row_count": 41,
  "error": null,
  "csv_path": "/.../data/output/iphone-15_1699999999_comparison.csv",
  "cancelled": false,
  "created_at": 1699999900.0
}
```

`status` is one of `running`, `done`, `error`. Returns `404` if the job id is unknown or malformed.

### `GET /api/download/<job_id>`

Download the finished CSV report as an attachment.

- Returns the CSV file.
- `404` unknown job Â· `409` job not finished yet Â· `410` file missing on disk.

### `POST /api/jobs/<job_id>/cancel`

Request cancellation; this sets a real threading event that stops the scraper loops (it does not just hide the logs).

```json
{ "ok": true }
```

`404` for an unknown job id.

### `POST /api/chat`

Ask the built-in assistant (rule/keyword based, no external API).

**Body:**

```json
{ "message": "how do I filter by price?" }
```

**Response:**

```json
{ "reply": "Use the table controls above the results matrix to filter ..." }
```

Topics covered: getting started, supported platforms, CSV/download, filters and sorting, status seal meaning, price/rating KPIs.

---

## CSV Output Format

| | |
|---|---|
| **File name** | `<query-slug>_<unix-timestamp>_comparison.csv` |
| **Location** | `PRICE_LEDGER_OUTPUT_DIR` (default `./data/output`) |
| **Encoding** | UTF-8 with BOM (opens cleanly in Excel) |

**Columns:**

| Column | Description |
|---|---|
| `platform` | `Amazon` or `Flipkart` |
| `title` | Product name as listed on the store. |
| `price` | Price in rupees, digits only (empty or `N/A` if unavailable). |
| `rating` | Store rating (Amazon: numeric, e.g. `4.3`). |
| `reviews` | Number of ratings/reviews (digits only, may be empty). |
| `link` | Absolute clickable product URL (or a store search URL fallback). |
| `price_diff` | Compared to the other store, e.g. `+â‚¹2,000 (Cheaper on Flipkart)`, `-â‚¹500 (Cheaper on Amazon)`, `Same Price`, or `â€”` when no comparison is possible. |

---

## Chatbot (Matrix Assistant)

`chatbot.py` implements a lightweight, dependency-free assistant as a Flask Blueprint. It matches keywords in the user's message against a small knowledge base (getting started, platforms, download/export, filters/sorting, status states, price/rating KPIs) and returns the matching answer. Anything unrecognized falls back to a friendly help message listing what it can do. **No external AI service or API key is required.**

---

## Notes, Limits and Troubleshooting

### Server not reachable â€” *"Backend API not reachable at localhost:8787"*

- Make sure `python app.py` is running and the port matches.
- If you changed `PRICE_LEDGER_PORT`, update the `apiBase` in the HTML file.

### "No results" or very few listings

- Amazon and Flipkart frequently change their CSS class names and may show CAPTCHAs or bot checks. The selectors in `automation_core.py` (`_parse_amazon_html` / `_parse_flipkart_html`) are the place to update them.
- Try `headless: false` (visible browser) so you can see what the page shows.
- Increase the delay between requests (`BASE_DELAY_SECONDS`) if you get blocked often.

### ChromeDriver errors

- Selenium Manager normally handles this automatically. If it cannot, set `PRICE_LEDGER_CHROMEDRIVER_PATH` to a local chromedriver binary that matches your Chrome version.

### Jobs queue returning HTTP 429

- At most 3 jobs run at once by default. Wait for one to finish or raise `PRICE_LEDGER_MAX_CONCURRENT_JOBS`.

### Performance

- One page per store takes roughly **10â€“30 seconds**. More pages scale the time and request volume linearly. Keep pages low to be polite to the stores.

### Scraping ethics and legality

- This tool is intended for **personal / educational use** (it was built as a learning project). Store terms of service, `robots.txt` and local laws may restrict automated scraping. Scrape responsibly, keep request rates low, and do not use the data commercially without permission.

### Known limitations

- Jobs live in memory only: restarting the server loses job history (CSV files on disk remain).
- Price comparison matches products by fuzzy title similarity; the pairing can occasionally mismatch similar models (e.g. 128 GB vs 256 GB variants).
- Amazon ratings are parsed as the first numeric token, so the displayed value is the star rating, not the review count.
- The dashboard has no built-in login/auth â€” do not expose the API publicly without adding authentication.

---

## Quick Start (TL;DR)

```bash
pip install flask flask-cors selenium beautifulsoup4 pandas
cd Price_Matrix_4.0
python app.py                  # terminal 1  -> API on :8787
python -m http.server 8787     # terminal 2  -> serve the UI
# open http://localhost:8787/index2.html and search for a product
```