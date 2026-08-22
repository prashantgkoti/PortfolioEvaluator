"""
data_fetch.py — All external market-data calls live here, and nowhere else.

DECISION (DECISIONS.md #2): yfinance is used for both Indian (NSE, via the
".NS" suffix) and US tickers, since it is free, keyless, and covers both
markets from one library. nsepython is documented as an alternative for
Indian data in README.md but not hard-depended on, since yfinance already
covers NSE symbols adequately for this tool's purposes.

DECISION (DECISIONS.md #3): mutual fund NAVs use `mftool`, which wraps the
AMFI daily NAV feed. If mftool is unavailable/unreachable, the module
degrades gracefully and callers get a clear "data unavailable" signal
instead of a crash (requirement: handle missing data gracefully).

Every public function returns either data or a dict with an "error" key —
callers must check for that key. Nothing raises for "normal" failure modes
like delisted tickers or network hiccups.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from typing import Optional

import pandas as pd

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

try:
    from mftool import Mftool
    _mf = Mftool()
except Exception:  # pragma: no cover - mftool may fail to init offline
    _mf = None


def to_yf_symbol(symbol: str, market: str) -> str:
    """Indian NSE tickers need a '.NS' suffix for yfinance; US tickers are used as-is."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("No symbol provided")
    symbol = symbol.strip().upper()
    if market == "IN" and not symbol.endswith((".NS", ".BO")):
        return f"{symbol}.NS"
    return symbol


@lru_cache(maxsize=256)
def _cached_ticker_history(yf_symbol: str, period: str) -> Optional[pd.DataFrame]:
    if yf is None:
        return None
    try:
        t = yf.Ticker(yf_symbol)
        hist = t.history(period=period, auto_adjust=True)
        if hist is None or hist.empty:
            return None
        return hist
    except Exception:
        return None


def get_price_history(symbol: str, market: str = "IN", period: str = "1y") -> Optional[pd.DataFrame]:
    """Returns a DataFrame with a 'Close' column indexed by date, or None if unavailable
    (e.g. delisted stock, bad/missing ticker, network issue) — callers must handle None."""
    try:
        yf_symbol = to_yf_symbol(symbol, market)
    except ValueError:
        return None
    return _cached_ticker_history(yf_symbol, period)


def get_current_price(symbol: str, market: str = "IN") -> Optional[float]:
    hist = get_price_history(symbol, market, period="5d")
    if hist is None or hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


@lru_cache(maxsize=256)
def get_fundamentals(symbol: str, market: str = "IN") -> dict:
    """Pulls key fundamental fields from yfinance's `.info`. Returns {} on failure
    (not None) so callers can safely use `.get()` without extra branching."""
    if yf is None:
        return {}
    try:
        yf_symbol = to_yf_symbol(symbol, market)
    except ValueError:
        return {}
    try:
        t = yf.Ticker(yf_symbol)
        info = t.info or {}
        return {
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "pb_ratio": info.get("priceToBook"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "profit_margin": info.get("profitMargins"),
            "dividend_yield": info.get("dividendYield"),
            "market_cap": info.get("marketCap"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "long_name": info.get("longName") or info.get("shortName"),
            "quote_type": info.get("quoteType"),  # EQUITY / ETF
            "currency": info.get("currency", "INR" if market == "IN" else "USD"),
        }
    except Exception:
        return {}


def get_mf_nav(scheme_code: str) -> dict:
    """Fetch current NAV + basic scheme info for an AMFI mutual fund scheme code."""
    if _mf is None:
        return {"error": "Mutual fund data source (mftool/AMFI feed) unavailable."}
    try:
        quote = _mf.get_scheme_quote(scheme_code)
        if not quote:
            return {"error": f"No AMFI data found for scheme code {scheme_code}."}
        return {
            "scheme_name": quote.get("scheme_name"),
            "nav": float(quote.get("nav", 0) or 0),
            "date": quote.get("last_updated"),
        }
    except Exception as e:
        return {"error": f"Mutual fund lookup failed: {e}"}


def get_mf_history(scheme_code: str, days: int = 365) -> Optional[pd.DataFrame]:
    if _mf is None:
        return None
    try:
        df = _mf.get_scheme_historical_nav(scheme_code, as_Dataframe=True)
        if df is None or df.empty:
            return None
        df = df.reset_index().rename(columns={"index": "date"})
        df["date"] = pd.to_datetime(df["date"], format="%d-%m-%Y", errors="coerce")
        df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
        df = df.dropna().sort_values("date")
        cutoff = dt.datetime.now() - dt.timedelta(days=days)
        return df[df["date"] >= cutoff]
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Benchmark index price helpers
# --------------------------------------------------------------------------- #

INDEX_YF_SYMBOLS = {
    "NIFTY50": "^NSEI",
    "NIFTY_NEXT50": "^NSMIDCP",       # yfinance coverage of this is inconsistent; falls back to NIFTY50 if unavailable
    "NIFTY_MIDCAP150": "NIFTYMIDCAP150.NS",
    "NIFTY_SMALLCAP250": "NIFTYSMLCAP250.NS",
    "NIFTY_BANK": "^NSEBANK",
    "NIFTY_IT": "^CNXIT",
    "SENSEX": "^BSESN",
    "SP500": "^GSPC",
    "NASDAQ100": "^NDX",
}


def get_index_history(index_key: str, period: str = "1y") -> Optional[pd.DataFrame]:
    yf_symbol = INDEX_YF_SYMBOLS.get(index_key)
    if not yf_symbol:
        return None
    hist = _cached_ticker_history(yf_symbol, period)
    if hist is None and index_key not in ("NIFTY50", "SP500"):
        # graceful fallback for indices yfinance doesn't reliably carry
        fallback = "NIFTY50" if index_key.startswith("NIFTY") or index_key == "SENSEX" else "SP500"
        hist = _cached_ticker_history(INDEX_YF_SYMBOLS[fallback], period)
    return hist


def period_return(hist: Optional[pd.DataFrame]) -> Optional[float]:
    """% return from first to last close in the given history frame."""
    if hist is None or hist.empty or len(hist) < 2:
        return None
    start = hist["Close"].iloc[0]
    end = hist["Close"].iloc[-1]
    if start in (0, None):
        return None
    return (end - start) / start * 100.0
