# Price Ledger backend

Local Flask API that drives a Selenium scrape of Amazon.in search results and
writes the parsed listings to CSV. Runs on your own machine — it needs a real
Chrome install and real internet access.

## Run it (VS Code integrated terminal)

1. Open this folder in VS Code.
2. Open a terminal (`` Ctrl+` `` / `` Cmd+` ``) and create a virtual environment:

   ```bash
   python -m venv .venv
   ```

3. Activate it:

   - macOS/Linux: `source .venv/bin/activate`
   - Windows (PowerShell): `.venv\Scripts\Activate.ps1`
   - Windows (cmd): `.venv\Scripts\activate.bat`

   (VS Code will usually offer to auto-select this interpreter — click "Yes" if prompted.)

4. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

5. Make sure Google Chrome (or Chromium) is installed on this machine.
   Selenium 4.6+ downloads the matching `chromedriver` automatically, so you
   don't need to install one yourself.

6. Start the server:

   ```bash
   python app.py
   ```

   You should see `Price Ledger backend starting on http://127.0.0.1:8787`.

7. Point your dashboard's "API base URL" field at `http://localhost:8787`.

## Quick manual test without the dashboard

```bash
python automation_core.py "wireless mouse" 2
```

This runs a 2-page scrape directly and writes `wireless mouse_data.csv` in
this folder — useful for confirming Chrome/Selenium work before wiring up
the Flask server.

## API

| Method | Path                       | Description                                  |
|--------|----------------------------|-----------------------------------------------|
| GET    | `/api/ping`                | Health check                                  |
| POST   | `/api/run`                 | Body: `{"query": "...", "pages": 5, "headless": true}` → `{"job_id": "..."}` |
| GET    | `/api/status/<job_id>`     | Poll for progress/log/result                  |
| GET    | `/api/download/<job_id>`   | Download the finished CSV once `status: done` |
| POST   | `/api/jobs/<job_id>/cancel`| Best-effort stop between pages                |

## Config (optional environment variables)

| Variable                          | Default | Purpose                                  |
|------------------------------------|---------|-------------------------------------------|
| `PRICE_LEDGER_PORT`                | `8787`  | Port the Flask server listens on          |
| `PRICE_LEDGER_ALLOWED_ORIGIN`      | `*`     | Restrict CORS to your dashboard's origin  |
| `PRICE_LEDGER_MAX_PAGES`           | `50`    | Upper bound on `pages` accepted by `/api/run` |
| `PRICE_LEDGER_JOB_TTL_SECONDS`     | `3600`  | How long finished job records are kept in memory |

## Notes / limitations

- This scrapes a live third-party website. Amazon's markup and anti-bot
  behavior can change at any time — if `Page N/M: 0 items found` shows up
  repeatedly, the selectors in `automation_core.py` likely need updating.
- Respect Amazon's Terms of Service and `robots.txt` for your use case; this
  tool includes a polite delay between page loads but you're responsible for
  how you use it.
- Job data is stored in memory only (`JOBS` dict) — restarting the server
  clears history, and nothing here is meant for concurrent multi-user
  production use.
