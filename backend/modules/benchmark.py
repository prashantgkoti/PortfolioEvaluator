"""
benchmark.py — Maps each holding to an appropriate benchmark index and
computes alpha (holding return minus benchmark return) per holding and
in aggregate (portfolio-weighted blended benchmark).

DECISION (DECISIONS.md #7): category -> index mapping is inferred from
market-cap for Indian stocks (using `market_cap` from yfinance as a rough
proxy: >₹70,000cr large-cap -> NIFTY50, ₹15,000-70,000cr -> NIFTY_MIDCAP150,
below that -> NIFTY_SMALLCAP250), overridden by sector for well-known
sectoral plays (Banking -> NIFTY_BANK, IT -> NIFTY_IT). Indian mutual funds
and ETFs default to NIFTY50 unless the scheme name hints at a category
(e.g. "midcap", "smallcap", "bank"). US holdings default to SP500 for
large/mega-cap and NASDAQ100 if the name/sector suggests tech. These are
reasonable heuristics, not precise category classifications — flagged as
a known limitation in README.md.
"""
from __future__ import annotations

from typing import Optional

from . import data_fetch


LARGE_CAP_INR = 70_000 * 1e7   # ~₹70,000 crore in rupees
MID_CAP_INR = 15_000 * 1e7     # ~₹15,000 crore in rupees


def determine_benchmark(asset_type: str, market: str, info: Optional[dict] = None, name: str = "") -> str:
    info = info or {}
    name_low = (name or "").lower()
    sector = (info.get("sector") or "").lower()

    if market == "US":
        if "technology" in sector or "nasdaq" in name_low or "tech" in name_low:
            return "NASDAQ100"
        return "SP500"

    # market == "IN"
    if "bank" in name_low or "financial services" in sector:
        return "NIFTY_BANK"
    if "information technology" in sector or " it " in f" {name_low} " or "technology" in sector:
        return "NIFTY_IT"
    if "smallcap" in name_low or "small cap" in name_low:
        return "NIFTY_SMALLCAP250"
    if "midcap" in name_low or "mid cap" in name_low:
        return "NIFTY_MIDCAP150"

    if asset_type == "stock":
        mcap = info.get("market_cap")
        if mcap is not None:
            if mcap >= LARGE_CAP_INR:
                return "NIFTY50"
            elif mcap >= MID_CAP_INR:
                return "NIFTY_MIDCAP150"
            else:
                return "NIFTY_SMALLCAP250"

    return "NIFTY50"


def get_benchmark_return(index_key: str, period: str = "1y") -> Optional[float]:
    hist = data_fetch.get_index_history(index_key, period=period)
    return data_fetch.period_return(hist)


def compute_alpha(holding_return: Optional[float], index_key: str, period: str = "1y") -> dict:
    bench_return = get_benchmark_return(index_key, period=period)
    if holding_return is None or bench_return is None:
        return {"benchmark": index_key, "benchmark_return": bench_return,
                "holding_return": holding_return, "alpha": None}
    return {
        "benchmark": index_key,
        "benchmark_return": round(bench_return, 2),
        "holding_return": round(holding_return, 2),
        "alpha": round(holding_return - bench_return, 2),
    }


def blended_benchmark_return(weights_by_index: dict, period: str = "1y") -> Optional[float]:
    """weights_by_index: {index_key: weight_fraction}. Returns weighted-average
    benchmark return, i.e. what the portfolio would have returned if every
    rupee/dollar had instead tracked its assigned index."""
    total_weight = sum(weights_by_index.values())
    if total_weight == 0:
        return None
    weighted_sum = 0.0
    resolved_weight = 0.0
    for idx_key, weight in weights_by_index.items():
        r = get_benchmark_return(idx_key, period=period)
        if r is not None:
            weighted_sum += r * weight
            resolved_weight += weight
    if resolved_weight == 0:
        return None
    return weighted_sum / resolved_weight
