"""
fundamental.py — Fundamental scoring for stocks, ETFs, and mutual funds.

Produces a 0-100 score plus a list of plain-English reasons. Thresholds
below are DECISIONS (see DECISIONS.md #5) — reasonable, broadly-used rules
of thumb for the Indian market context, not proprietary research:

  Stocks (5 factors, 20 pts each):
    - P/E ratio       : <15 excellent, 15-25 good, 25-40 fair, >40 weak, <0 (loss-making) weak
    - P/B ratio        : <3 good, 3-6 fair, >6 weak
    - ROE              : >20% excellent, 12-20% good, 5-12% fair, <5% weak
    - Debt/Equity      : <50 good, 50-100 fair, >100 weak (yfinance reports this *100)
    - Revenue growth   : >15% excellent, 5-15% good, 0-5% fair, <0% weak

  ETFs: fundamentals are mostly about the underlying index, so ETFs get a
  neutral fundamental score (60/100) and are scored primarily on technicals.

  Mutual funds: scored on expense ratio proxy (unavailable via free feeds
  for most schemes) is skipped; instead funds are scored on trailing return
  consistency computed from NAV history in technical.py, and fundamental
  score defaults to a neutral 60/100 with a note. This is a known
  simplification — see README "Known Limitations".
"""
from __future__ import annotations

from typing import Tuple, List


def _score_metric(value, thresholds: List[Tuple[float, int]], reverse=False) -> int:
    """thresholds: list of (cutoff, points) evaluated in order. reverse=True means
    lower is better (cutoffs ascending, first match wins)."""
    if value is None:
        return 10  # unknown -> small default rather than 0, avoids unfairly tanking the score
    for cutoff, points in thresholds:
        if reverse:
            if value <= cutoff:
                return points
        else:
            if value >= cutoff:
                return points
    return thresholds[-1][1]


def score_stock_fundamentals(info: dict) -> dict:
    reasons = []
    score = 0

    pe = info.get("pe_ratio")
    if pe is not None and pe > 0:
        pe_pts = _score_metric(pe, [(15, 20), (25, 15), (40, 8)], reverse=True) if pe <= 40 else 3
        reasons.append(f"P/E of {pe:.1f} — {'attractively valued' if pe_pts >= 15 else 'fairly valued' if pe_pts>=8 else 'richly valued'}.")
    elif pe is not None and pe <= 0:
        pe_pts = 3
        reasons.append("Negative earnings (loss-making) — valuation on P/E isn't meaningful.")
    else:
        pe_pts = 10
        reasons.append("P/E data unavailable.")
    score += pe_pts

    pb = info.get("pb_ratio")
    pb_pts = _score_metric(pb, [(3, 20), (6, 12)], reverse=True) if pb is not None else 10
    if pb is not None:
        reasons.append(f"P/B of {pb:.1f} — {'reasonable' if pb_pts>=12 else 'elevated'} relative to book value.")
    score += pb_pts

    roe = info.get("roe")
    roe_pct = roe * 100 if roe is not None else None
    roe_pts = _score_metric(roe_pct, [(20, 20), (12, 14), (5, 8)]) if roe_pct is not None else 10
    if roe_pct is not None:
        reasons.append(f"ROE of {roe_pct:.1f}% — {'strong' if roe_pts>=14 else 'moderate' if roe_pts>=8 else 'weak'} capital efficiency.")
    score += roe_pts

    de = info.get("debt_to_equity")
    de_pts = _score_metric(de, [(50, 20), (100, 12)], reverse=True) if de is not None else 10
    if de is not None:
        reasons.append(f"Debt/Equity of {de:.0f} — {'conservative' if de_pts>=14 else 'moderate' if de_pts>=8 else 'high'} leverage.")
    score += de_pts

    rg = info.get("revenue_growth")
    rg_pct = rg * 100 if rg is not None else None
    rg_pts = _score_metric(rg_pct, [(15, 20), (5, 14), (0, 8)]) if rg_pct is not None else 10
    if rg_pct is not None:
        reasons.append(f"Revenue growth of {rg_pct:.1f}% YoY — {'robust' if rg_pts>=14 else 'modest' if rg_pts>=8 else 'sluggish'} top-line trend.")
    score += rg_pts

    return {"score": min(score, 100), "reasons": reasons}


def score_etf_fundamentals(info: dict) -> dict:
    return {
        "score": 60,
        "reasons": ["ETF fundamentals track the underlying index; not independently scored here — see technical signal for trend."],
    }


def score_mf_fundamentals(info: dict) -> dict:
    return {
        "score": 60,
        "reasons": ["Mutual fund expense-ratio/portfolio-quality data isn't available via free feeds in this build; "
                     "fund quality is instead assessed through NAV trend consistency — see technical signal."],
    }
