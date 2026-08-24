# Portfolio Snapshot — Live Web App

A dynamic version of the portfolio dashboard: a FastAPI backend (reusing the
exact same `modules/*.py` logic as the Streamlit app — CAS parsing, live
price fetching, fundamental+technical scoring, benchmark comparison, SIP
projection) serving a single-page HTML/JS dashboard that talks to it live.

Unlike the earlier static HTML export, **nothing here is hardcoded**: upload
your own CAS, refresh live prices, run scored verdicts, add manual holdings
— all real, all computed on request.

> ⚠️ Not investment advice. See the in-app disclaimers.

## What's dynamic here (vs. the static snapshot)

| Feature | Static HTML | This app |
|---|---|---|
| Holdings data | Hardcoded at generation time | Uploaded live, parsed live |
| Prices | Frozen at CAS date | "Refresh live prices" hits yfinance/AMFI live |
| Buy/Hold/Trim verdicts | Not available | Live fundamental+technical scoring |
| Benchmark comparison | Not available | Live, via `/api/benchmark` |
| Persistence | None | SQLite, survives restarts |
| Add US/unlisted/gold holdings | Not available | Full forms, live |
| Equity cost basis | Never available (CAS doesn't carry it) | Real, FIFO-computed from an uploaded tradebook |

## Handling unfamiliar file formats

Every upload goes through up to three tiers, cheapest and most reliable first — **the
first two never leave your machine**:

1. **Exact parsers** — NSDL CAS PDFs and Zerodha Console tradebooks, verified against real
   statements.
2. **Heuristic column matching** — for other `.xlsx`/`.csv` tradebook or ledger exports
   (Motilal Oswal, Angel One, and similar). Looks for recognizable columns (Symbol/Scrip,
   ISIN, Date, Qty, Price, Buy/Sell) by fuzzy name matching rather than requiring an exact
   template, so most reasonably-standard broker exports work without any extra setup —
   no API key needed. Requires at least Symbol + Quantity + Price, plus a Date or Buy/Sell
   column, before it trusts a match; otherwise it reports no confident match rather than
   guessing.
3. **AI-assisted extraction** — opt-in only (see below), last resort for anything the first
   two tiers can't confidently parse.

## AI-assisted parsing (opt-in, last resort)

For files neither of the above can handle, there's an optional fallback that sends the
file's extracted text to an LLM for structured extraction. **This is the only path in the
whole app that sends data outside your machine**, and it's off by default. Two providers
are supported — pick whichever key you have:

- **Google Gemini** (has a free tier): set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`)
- **Anthropic Claude**: set `ANTHROPIC_API_KEY`

If both happen to be set, Gemini is used by default (override with `LLM_PROVIDER=anthropic`
or `LLM_PROVIDER=gemini`).

1. Set the relevant environment variable on the machine running the backend (never entered
   into the app itself, never stored in the database).
2. Restart the server.
3. Go to **Manage Uploads** in the app and toggle "Enable AI-assisted parsing" on.

Every AI-extracted holding/transaction is tagged with a note to double-check it against the
source document — treat it as best-effort, not verified.

```powershell
# Windows PowerShell — set for the current session (Gemini, free tier)
$env:GEMINI_API_KEY = "your-key-here"
uv run uvicorn backend.main:app --reload --port 8000
```

## Starting fresh

**Manage Uploads → Danger zone** lets you wipe every holding, transaction, trend point, and
batch in one action (type `DELETE ALL DATA` to confirm — this can't be undone). Your
AI-parsing preference is kept. After a wipe, the next file you upload starts a clean history;
nothing is merged with what was deleted.

## Setup

```bash
cd portfolio_webapp
uv venv
uv pip install -r requirements.txt
```

(or plain `pip install -r requirements.txt` in a venv if you're not using `uv`)

## Run

```bash
uv run uvicorn backend.main:app --reload --port 8000
```

Then open **http://localhost:8000** — the backend serves both the API and
the dashboard from one process.

## Using it

1. On first load, you'll see an upload prompt — drop in your NSDL CAS PDF, a Zerodha
   tradebook .xlsx, or a **.zip bundling any mix of both** (e.g. one CAS plus several years
   of tradebooks — Zerodha caps a single tradebook export at 365 days, so multi-year history
   usually arrives as several files). The upload box stays visible after your first upload too
   — add more files any time, they accumulate rather than replace what's already there.
2. The dashboard populates from the parsed data: allocation, equity
   holdings, mutual fund folios (with real cost basis where available),
   and the CAS's own 13-month value trend.
3. **Tradebook uploads compute real, FIFO-exact cost basis** for equities — something CAS
   alone can never provide — and apply it automatically to matching holdings by ISIN. If the
   uploaded trade history doesn't fully cover a position's current quantity (e.g. only one
   year of a multi-year holding), the response flags that explicitly rather than presenting
   an approximation as precise.
4. **Observations** tab — concentration, small-position, and mutual-fund
   overlap analysis, computed fresh from your actual holdings each time.
5. **Verdicts** tab — click "Run verdicts" to score every holding with a
   resolvable ticker against the fundamental+technical engine, live.
6. **Tradebook** tab — view FIFO-computed positions (open and closed) across every
   tradebook transaction uploaded so far, including realized P&L on fully-exited positions.
7. **Growth Projection** — sliders recompute the SIP math live via the API.
8. **Add Holdings** — US trades, unlisted shares, gold, or other assets.
9. **Manage Uploads** — delete a batch to remove those holdings.

Re-uploading a CAS adds a new batch rather than replacing the old one —
delete the old batch first from **Manage Uploads** if you want a clean swap. Re-uploading
the same tradebook is safe and won't double-count: trades are deduplicated by their broker
Trade ID.

## API reference

All endpoints are under `/api/`. A few highlights:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/upload` | POST (multipart) | Unified entry point — a CAS PDF, a tradebook .xlsx, a .zip bundling either, or (if enabled) anything else via AI-assisted fallback |
| `/api/settings` | GET / POST | Check/toggle AI-assisted parsing |
| `/api/reset` | POST | Wipe all data (requires `{"confirm": "DELETE ALL DATA"}`) |
| `/api/cas/upload` | POST (multipart) | Single-CAS convenience endpoint (same underlying logic as `/api/upload`) |
| `/api/tradebook/upload` | POST (multipart) | Single-tradebook convenience endpoint, returns FIFO cost-basis reconciliation detail |
| `/api/tradebook/positions` | GET | FIFO-computed positions (open + closed) across every uploaded trade |
| `/api/portfolio` | GET | Full holdings + totals + allocation |
| `/api/portfolio/refresh` | POST | Same, with live price refresh |
| `/api/portfolio/verdicts` | GET | Live scored buy/hold/trim/exit per holding |
| `/api/observations` | GET | Concentration, sector tilt, fund overlap |
| `/api/recommendation` | POST | Score any symbol on demand |
| `/api/projection` | POST | SIP corpus projection |
| `/api/benchmark` | GET | Per-holding + blended NIFTY/S&P alpha |
| `/api/manual-holdings/{us,unlisted,gold,other}` | POST | Add off-CAS assets |

Interactive API docs (auto-generated by FastAPI) are at
**http://localhost:8000/docs** once the server is running.

## Known limitations (inherited from the parsing/scoring logic)

- CAS parsing covers NSDL's own table layouts (verified against a real
  statement); CDSL/CAMS/KFintech statements may use different layouts.
- Corporate bonds use a distinct table format not yet parsed.
- Equity holdings from CAS have no cost basis (NSDL/CDSL don't track it) —
  only mutual fund folios do.
- ISIN → NSE ticker mapping covers ~35 common names; unmapped stocks show
  as N/A for verdicts until edited.
- Sector tags in the Observations tab are a small hardcoded lookup, not a
  live classification service.

## Architecture note

`backend/modules/` is a direct copy of the Streamlit app's `modules/`
package — every function in there is framework-agnostic (no Streamlit
imports), so it works unmodified as a FastAPI backend. If you fix a bug or
add a feature in one copy, consider porting it to the other.
