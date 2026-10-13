# Price Matrix v3

Live price comparison across **Amazon India** and **Flipkart** for **any product
category** — Selenium scraping, category-aware extraction, cross-store product
matching, analytics dashboard, and a data-aware assistant.

```
├── categories.py        category engine: detection, spec rules, variant guards
├── automation_core.py   scraping + parsing + matching + analytics engine
├── app.py               Flask API, job manager, SSE streaming, exports
├── chatbot.py           data-aware assistant (answers about your actual results)
├── index.html           single-file dashboard (no build step, no CDN)
├── test_price_matrix.py offline tests — no network or Chrome needed
├── requirements.txt
└── run.sh               venv + install + launch
```

## Quick start

```bash
./run.sh                       # creates .venv, installs deps, starts the server
./run.sh --test                # venv + deps + offline test suite
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
python -m unittest test_price_matrix -v                  # run the test suite
python automation_core.py --list-categories              # what can be compared
python automation_core.py "gaming laptop" -p 2 --show    # CLI, no web UI
python automation_core.py "nike running shoes" -c footwear -p 2 --show
```


---

## What changed in v3 — any product, not just laptops & phones

v2's scraping was already query-driven, but everything downstream assumed a
gadget: spec extraction only understood RAM/SSD/CPU/GPU/Hz/mAh, matching leaned
on model codes like `15s-fq5111tu`, the dashboard shipped laptop-only sample
rows, and the chips, filters and assistant all spoke specs. Search "air fryer"
and you got a table of blank spec columns; search "kurta" and matching had
nothing to grip on.

v3 adds a category engine (`categories.py`) between parsing and everything else.

**Category detection** — 23 modelled verticals (laptops, smartphones, tablets,
PC accessories, audio, TVs, cameras, gaming, home appliances, kitchen, furniture,
footwear, clothing, watches, bags, beauty, health, grocery, books, toys, sports,
automotive, stationery) plus a **generic fallback**, so an unknown vertical still
gets usable tags. Detection blends keyword evidence, brand/spec voting
(a title like *"ASUS TUF Gaming F15 — i5 11th Gen, 512GB SSD, 144Hz"* is a
laptop even without the word) and, when you pin a category, the query itself.

**Category-aware specs** — the right fields for the product:

| Product | Tags extracted |
|---|---|
| Voltas 1.5 Ton 5 Star Inverter Split AC | `Inverter Split AC · 1.5 Ton · 5 Star · Copper` |
| Nike Revolution 7 running shoes | `Running · Men · UK 9 · Black` |
| Atomic Habits | `Paperback · English · 320 Pages` |
| Philips Air Fryer 4.1 L | `Air Fryer · 4.1 L · 1400 W` |
| Tata Sampann Toor Dal | `1 kg · Unpolished` |
| Wildcraft 44 L backpack | `Backpack · 44 L · 15.6 inch · Fits 15.6" · Polyester` |
| Unknown vertical ("gardening tool kit") | `Stainless Steel · Pack of 3 · 500 g` (generic layer) |

**Smarter cross-store matching.** Category mismatch is now disqualifying, a
spec-fingerprint term rewards agreement (two "1.5 Ton 5 Star" ACs match even
with no model code in common) and *conflicting* specs are rejected rather than
reported as a saving: 16GB vs 8GB RAM, UK 9 vs UK 10, 1 Pack vs Pack of 2,
1.5 Ton vs 1 Ton. Accessories (covers, cables, chargers, refills) never match
the product they attach to — previously a ₹449 back cover "saved you ₹64,550"
against a ₹64,999 phone.

**UI** — a category picker beside the search box (auto-detect by default),
a category filter over the results, category badges on every row, an
"Accessory" tag, category-aware quick chips (AC, running shoes, whey protein,
groceries…) and multi-category sample data so *Preview with sample data* shows
the whole point. Pinning a category also disambiguates a bare query: `nike` +
Footwear → `nike shoes` on both stores.

**API** — `GET /api/categories` publishes the catalogue; `POST /api/run` takes
`category` (`auto` | `all` | a category id) and rejects unknown values; jobs,
CSV/JSON exports, the XLSX *Listings* sheet (with a per-category *Summary*
breakdown) and `/api/health` all carry the category through.

**Chatbot** — understands the wider spec vocabulary (`1.5 ton`, `5 star`,
`UK 9`, `500 sheets`, `paperback`, `front load`, `A4`…), can filter by category
("only running shoes"), isolate accessories, and answer "what categories are
these?" with the real mix of the current scan.

**Also fixed while in there**
- Generic title guard: rows whose "title" was really price text
  (`₹288 ₹1,599 81% off`) are dropped instead of exported — that junk made up
  60+ of 110 rows in the old earbuds export.
- Decimal bug: `1.79 kg` was parsed as `79 kg`.
- `"5G"` is a network generation, not five grams — it no longer becomes a weight.
- `"1Yr + 3Yr Warranty"` no longer surfaces as a product age.
- Quote-terminated sizes (`15.6"`) now parse; `15.6inlet` still doesn't.
- Removed the Cloudflare beacon/challenge scripts that had been saved into
  `index.html`, so "zero external requests" is true again.

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

Categories are selected per run (UI picker or `POST /api/run {"category": …}`)
and listed by `GET /api/categories` / `python automation_core.py --list-categories`.

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
  (RAM/storage/colour/size) before buying. v3 refuses matches across categories and
  across conflicting specs, which trades a few missed matches for far fewer fake
  "savings"; the scan log reports what was filtered.
- The dev server is fine for local use; for anything shared, run behind
  `waitress`/`gunicorn` (see `requirements.txt`).
