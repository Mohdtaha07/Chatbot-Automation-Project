# Price Matrix v2

Live price comparison across **Amazon India** and **Flipkart** — Selenium scraping,
cross-store product matching, analytics dashboard, and a data-aware assistant.

```
├── automation_core.py   scraping + parsing + matching + analytics engine
├── app.py               Flask API, job manager, SSE streaming, exports
├── chatbot.py           data-aware assistant (answers about your actual results)
├── index.html           single-file dashboard (no build step, no CDN)
├── test_price_matrix.py 54 offline tests — no network or Chrome needed
├── requirements.txt
└── run.sh               venv + install + launch
```

## Quick start

```bash
./run.sh                       # creates .venv, installs deps, starts the server
# or manually:
pip install -r requirements.txt
python app.py                  # → http://127.0.0.1:8787
```

Open **http://127.0.0.1:8787** — Flask serves the dashboard itself, so there's no
`file://` + CORS dance any more.

**Requirements:** Python 3.9+ and **Google Chrome** installed. Selenium 4.20+ ships
Selenium Manager, which downloads a matching chromedriver automatically. No Chrome?
The dashboard still runs — the header warns you and *Preview with sample data* works.

```bash
python -m unittest test_price_matrix -v      # run the test suite
python automation_core.py "gaming laptop" -p 2 --show    # CLI, no web UI
```

---

## What changed from v1

### 🐛 Bugs fixed

| Issue | v1 behaviour | v2 |
|---|---|---|
| **Fake price differences** | When no similar product existed, it silently diffed against *the cheapest item on the other site* — so a ₹40k Lenovo was "compared" to a ₹90k MacBook. Every row got a confident-looking number. | Unmatched rows report `No match`. Matching is brand-bucketed with model-token bonuses and a confidence score; below 0.42 it declines to guess. |
| **Broken JS string literals** | `startRun()` and `loadDemo()` contained raw newlines inside `'...'` strings — **a syntax error that killed the entire script**. The page was non-functional on load. | Fixed; JS is syntax-checked in CI-style verification. |
| **Path traversal / crash on quirky queries** | `f"{query}_amazon_raw"` built folder names from raw input — `usb-c 3.5" jack` or `a/b` crashed or escaped the directory. | `slugify()` sanitises every path. Verified by test. |
| **Thousands of junk files** | One HTML file written **per listing** (`amz_0.html` … `amz_2000.html`), never cleaned up. | Parsed in memory; raw dumps only with `dump_html=True`, one file per *page*. |
| **Cancel didn't cancel** | Set a flag that only suppressed log lines. The scrape kept running to completion. | Cooperative `threading.Event` checked per page; partial results are **kept and exportable**. |
| **Flipkart price bleed** | Card detection climbed 5 parents unconditionally, so an unpriced card absorbed its neighbour's price and appeared as a phantom listing. | Climb stops as soon as a container holds >1 product link. Caught by a regression test. |
| **One store fails → everything fails** | An exception in either thread propagated and lost *both* result sets. | Per-platform isolation; failures surface in `errors` while the other store's data survives. |
| **Rating selector** | Flipkart rating used an obfuscated class (`._1psv1zeb9…`) that changes weekly — always empty. | Cascade of selectors + regex fallbacks per field. |
| **Progress bar was fake** | Pure CSS animation; `progress` was always `0`. | Real 0-100% from pages completed, pushed over SSE. |
| **Job store grew forever** | TTL cleanup only ran on `/api/run`; CSVs never deleted. | Bounded by count *and* TTL, with on-disk export cleanup. |
| **`h2` title scrape** | Grabbed the first `<h2>`, often catching "Results" headers. | Targeted selectors + `img[alt]` fallback. |

### ✨ New capabilities

**Scraper**
- Richer schema: MRP, discount %, review counts, images, sponsored flag, ASIN/PID, page number
- Spec extraction from titles → RAM, storage, CPU, GPU, screen, refresh rate, battery
- Brand detection over a 90-brand dictionary
- **Value score (0–100)**: 42% price position + 30% rating + 16% review volume + 12% discount
- Bot-wall/CAPTCHA detection, UA rotation, exponential backoff with jitter
- Images disabled + eager page-load strategy (~70% less bandwidth, noticeably faster)
- Tracking params stripped from every product URL
- CLI: `python automation_core.py "query" -p 3 --show`

**API**
- `GET /api/health` — reports whether Selenium *and* Chrome are actually usable, so the UI explains failures **before** you wait 30s for them
- `GET /api/stream/<id>` — Server-Sent Events replace 1.2s polling (client falls back to polling automatically)
- Exports in **CSV / XLSX / JSON** — the XLSX is formatted with frozen headers, autofilter, ₹ number formats and a **Summary** sheet
- `GET /api/jobs`, `DELETE /api/jobs/<id>`, incremental logs via `?since=`
- Structured errors (`error_type: driver|validation|not_found|busy`) → correct status codes (503 vs 500)
- Concurrency semaphore, and Flask serves `index.html` directly

**Chatbot** — now reads your actual results instead of matching keywords:

| Ask | Gets |
|---|---|
| "cheapest under 50k" | Real listings, budget-parsed (`50k`, `1.5 lakh`, `between 40k and 70k`) |
| "which store is cheaper?" | Min/avg for both + the exact delta |
| "best rated Flipkart option" | Platform + rating filtered |
| "biggest discount" | Ranked by MRP delta |
| "show me 16GB RTX 4050 deals" | Spec-filtered |
| "biggest price differences" | Largest matched cross-store gaps |
| "what should I buy?" | Highest value score, with reasoning |

Still 100% offline and deterministic — no API keys. FAQ layer handles how-to questions,
and the frontend has a local fallback so it answers even with the backend down.

**Dashboard**
- **Dark/light theme** with persistence
- **3 chart modes**: price distribution, price-vs-rating scatter, average-by-brand
- 5 KPI cards incl. median price and cross-store winner
- Filters: store, spec text, price range, **matched-only**, **hide sponsored** — with removable filter chips
- 7 sort modes incl. value score and cross-store gap; sortable headers
- **Compare drawer** — side-by-side matched products with a savings verdict
- Product thumbnails, spec tags, MRP strikethrough, discount %, value bars
- Store toggles, sponsored/headless options, quick-search chips, recent searches
- Cancel button, real progress %, timestamped colour-coded log
- Keyboard shortcuts (`/`, `Ctrl+K`, `Esc`), print stylesheet, reduced-motion support
- Zero external requests — no Google Fonts, no CDN. Works fully offline.

---

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PM_PORT` / `PM_HOST` | `8787` / `127.0.0.1` | Bind address |
| `PM_MAX_PAGES` | `10` | Per-store page cap |
| `PM_MAX_CONCURRENT` | `2` | Simultaneous scrape jobs |
| `PM_JOB_TTL_SECONDS` | `10800` | Job retention |
| `PM_OUTPUT_DIR` | `./exports` | Export location |
| `PM_ALLOWED_ORIGIN` | `*` | CORS origin |
| `PM_BASE_DELAY` | `1.6` | Politeness delay between pages |

---

## Notes & limitations

- **Scraping is inherently fragile.** Amazon and Flipkart rotate CSS classes and serve
  bot walls. v2 degrades gracefully — a broken selector loses one *column*, not the run —
  but selectors will still need occasional updates. The scan log tells you exactly what failed.
- **Be polite.** Defaults are deliberately unhurried. Scraping may conflict with these
  sites' Terms of Service; this is intended for personal/educational use. For production,
  use official APIs or an affiliate feed.
- **Getting blocked?** Try fewer pages, wait a few minutes, or untick *Headless browser*
  once to solve a CAPTCHA and warm up cookies.
- **Matching is heuristic.** Confidence is shown on hover — always confirm the variant
  (RAM/storage/colour) before buying.
- The dev server is fine for local use; for anything shared, run behind
  `waitress`/`gunicorn` (see `requirements.txt`).
