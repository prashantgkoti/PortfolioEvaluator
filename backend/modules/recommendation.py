"""
recommendation.py — Combines fundamental + technical scores into one
verdict with human-readable reasoning, and checks generated recommendations
against the persisted history so the engine never starts from a blank slate.

DECISION (DECISIONS.md #9): weighting between fundamental and technical
analysis is asset-type dependent and documented here explicitly:
  - Stocks        : 60% fundamental / 40% technical (fundamentals matter
                     more for single-company risk).
  - ETFs          : 20% fundamental / 80% technical (fundamentals are
                     basically the index's, so trend/momentum dominates).
  - Mutual funds  : 30% fundamental / 70% technical (fundamental scoring is
                     a neutral placeholder per fundamental.py's limitation,
                     so technical NAV-trend carries most of the weight).

DECISION (DECISIONS.md #10): verdict thresholds on the composite 0-100
score:  >=75 Strong Buy | 60-74 Buy | 40-59 Hold | 25-39 Trim | <25 Exit.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import pandas as pd

from . import data_fetch, fundamental, technical, benchmark, db


WEIGHTS = {
    "stock": (0.60, 0.40),
    "etf": (0.20, 0.80),
    "mutual_fund": (0.30, 0.70),
}


def verdict_from_score(score: float) -> str:
    if score >= 75:
        return "Strong Buy"
    if score >= 60:
        return "Buy"
    if score >= 40:
        return "Hold"
    if score >= 25:
        return "Trim"
    return "Exit"


def analyze_symbol(symbol: str, market: str, asset_type: str, scheme_code: Optional[str] = None,
                    name: str = "") -> dict:
    """Runs the full fundamental + technical pipeline for one symbol and returns
    a result dict. Does NOT persist — call save_analysis_as_recommendation() for that.
    Handles missing/unavailable data gracefully throughout."""

    if asset_type == "mutual_fund":
        if not scheme_code:
            return {"error": "Mutual fund analysis requires an AMFI scheme code."}
        nav_info = data_fetch.get_mf_nav(scheme_code)
        if "error" in nav_info:
            return {"error": nav_info["error"]}
        hist = data_fetch.get_mf_history(scheme_code, days=400)
        price_series = hist.set_index("date")["nav"] if hist is not None else None
        current_price = nav_info.get("nav")
        info = {}
        fund_name = nav_info.get("scheme_name", name)
        fscore = fundamental.score_mf_fundamentals(info)
    else:
        info = data_fetch.get_fundamentals(symbol, market)
        hist = data_fetch.get_price_history(symbol, market, period="1y")
        price_series = hist["Close"] if hist is not None else None
        current_price = data_fetch.get_current_price(symbol, market)
        fund_name = info.get("long_name") or name or symbol
        if asset_type == "etf":
            fscore = fundamental.score_etf_fundamentals(info)
        else:
            fscore = fundamental.score_stock_fundamentals(info)

    if price_series is None and current_price is None:
        return {"error": f"No market data available for {symbol} ({market}). It may be delisted, "
                          f"an unsupported ticker, or a temporary data-source issue."}

    tscore = technical.score_technical(price_series) if price_series is not None else \
        {"score": 30, "reasons": ["No price history available for technical analysis."]}

    w_f, w_t = WEIGHTS.get(asset_type, (0.5, 0.5))
    composite = round(fscore["score"] * w_f + tscore["score"] * w_t, 1)
    verdict = verdict_from_score(composite)

    bench_key = benchmark.determine_benchmark(asset_type, market, info, fund_name)
    holding_return = technical._trailing_return(price_series, min(252, len(price_series))) if price_series is not None and len(price_series) > 1 else None
    alpha_info = benchmark.compute_alpha(holding_return, bench_key)

    reasoning_parts = [
        f"Composite score {composite}/100 ({w_f*100:.0f}% fundamental / {w_t*100:.0f}% technical weighting for {asset_type}s) → {verdict}."
    ]
    reasoning_parts.append("Fundamental: " + " ".join(fscore["reasons"]))
    reasoning_parts.append("Technical: " + " ".join(tscore["reasons"]))
    if alpha_info["alpha"] is not None:
        vs = "outperforming" if alpha_info["alpha"] >= 0 else "underperforming"
        reasoning_parts.append(
            f"1yr return of {alpha_info['holding_return']}% vs {bench_key.replace('_',' ')}'s "
            f"{alpha_info['benchmark_return']}% — {vs} by {abs(alpha_info['alpha'])} pts."
        )

    return {
        "symbol": symbol,
        "name": fund_name,
        "market": market,
        "asset_type": asset_type,
        "fundamental_score": fscore["score"],
        "technical_score": tscore["score"],
        "composite_score": composite,
        "verdict": verdict,
        "price_at_reco": current_price,
        "reasoning": "\n\n".join(reasoning_parts),
        "benchmark_index": bench_key,
        "alpha": alpha_info,
    }


def save_analysis_as_recommendation(analysis: dict) -> None:
    if "error" in analysis:
        return
    db.save_recommendation({
        "symbol": analysis["symbol"],
        "name": analysis["name"],
        "market": analysis["market"],
        "asset_type": analysis["asset_type"],
        "verdict": analysis["verdict"],
        "fundamental_score": analysis["fundamental_score"],
        "technical_score": analysis["technical_score"],
        "composite_score": analysis["composite_score"],
        "price_at_reco": analysis["price_at_reco"],
        "reasoning": analysis["reasoning"],
        "benchmark_index": analysis["benchmark_index"],
    })


def compare_with_history(symbol: str, new_analysis: dict) -> Optional[dict]:
    """Looks up the most recent PRIOR recommendation for this symbol (before the
    one just generated) and reports whether the thesis played out and whether
    it still holds. Returns None if there's no prior history (first-time symbol)."""
    history = db.get_recommendation_history(symbol)
    if not history:
        return None
    prior = history[0]  # most recent persisted row, prior to this run's not-yet-saved analysis

    price_then = prior.price_at_reco
    price_now = new_analysis.get("price_at_reco")
    price_change_pct = None
    if price_then and price_now:
        price_change_pct = round((price_now - price_then) / price_then * 100, 2)

    thesis_played_out = None
    if price_change_pct is not None:
        if prior.verdict in ("Strong Buy", "Buy"):
            thesis_played_out = price_change_pct > 0
        elif prior.verdict in ("Trim", "Exit"):
            thesis_played_out = price_change_pct < 0
        else:
            thesis_played_out = abs(price_change_pct) < 10

    verdict_changed = prior.verdict != new_analysis.get("verdict")
    score_then = prior.composite_score
    score_now = new_analysis.get("composite_score")
    score_delta = round(score_now - score_then, 1) if (score_then is not None and score_now is not None) else None

    return {
        "prior_date": prior.created_at,
        "prior_verdict": prior.verdict,
        "prior_score": score_then,
        "current_verdict": new_analysis.get("verdict"),
        "current_score": score_now,
        "score_delta": score_delta,
        "price_then": price_then,
        "price_now": price_now,
        "price_change_pct": price_change_pct,
        "thesis_played_out": thesis_played_out,
        "verdict_changed": verdict_changed,
        "summary": _build_history_summary(prior.verdict, new_analysis.get("verdict"), price_change_pct, thesis_played_out),
    }


def _build_history_summary(prior_verdict, current_verdict, price_change_pct, thesis_played_out) -> str:
    parts = [f"Last recommended '{prior_verdict}'."]
    if price_change_pct is not None:
        direction = "risen" if price_change_pct >= 0 else "fallen"
        parts.append(f"Since then, price has {direction} {abs(price_change_pct)}%.")
    if thesis_played_out is True:
        parts.append("That thesis has played out so far.")
    elif thesis_played_out is False:
        parts.append("That thesis has NOT played out as expected — worth a closer look.")
    if prior_verdict != current_verdict:
        parts.append(f"Verdict has now changed to '{current_verdict}' based on updated data.")
    else:
        parts.append(f"Verdict remains '{current_verdict}' — thesis still holds.")
    return " ".join(parts)
