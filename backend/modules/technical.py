"""
technical.py — Technical scoring for stocks, ETFs, and (via NAV history) MFs.

DECISION (DECISIONS.md #6): a 0-100 score built from three equally-weighted
(≈33 pts each) signals computed from price/NAV history:
  1. Trend    : price/NAV vs its 50-day and 200-day SMA, plus whether the
                50-day is above the 200-day (golden cross vs death cross).
  2. Momentum : 3-month and 6-month trailing return, positive is bullish.
  3. RSI(14)  : classic overbought(>70)/oversold(<30)/neutral read, scored
                so "neutral-to-mildly-bullish" (45-65) scores highest —
                deeply overbought is treated as a caution flag, not a plus.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def _sma(series: pd.Series, window: int) -> Optional[float]:
    if len(series) < window:
        return None
    return float(series.rolling(window).mean().iloc[-1])


def _rsi(series: pd.Series, window: int = 14) -> Optional[float]:
    if len(series) < window + 1:
        return None
    delta = series.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window).mean().iloc[-1]
    avg_loss = loss.rolling(window).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def _trailing_return(series: pd.Series, days: int) -> Optional[float]:
    if len(series) < days:
        return None
    start = series.iloc[-days]
    end = series.iloc[-1]
    if start == 0:
        return None
    return float((end - start) / start * 100)


def score_technical(price_series: pd.Series) -> dict:
    """price_series: pandas Series of closing prices/NAVs, indexed by date, ascending."""
    reasons = []
    if price_series is None or len(price_series) < 10:
        return {"score": 30, "reasons": ["Insufficient price history for technical analysis (needs more trading days)."]}

    last = float(price_series.iloc[-1])
    sma50 = _sma(price_series, 50)
    sma200 = _sma(price_series, 200)

    # --- 1. Trend (0-34) ---
    trend_pts = 0
    if sma50 is not None:
        if last > sma50:
            trend_pts += 12
            reasons.append("Trading above its 50-day average — short-term uptrend.")
        else:
            reasons.append("Trading below its 50-day average — short-term weakness.")
    else:
        trend_pts += 6
    if sma200 is not None:
        if last > sma200:
            trend_pts += 12
            reasons.append("Trading above its 200-day average — intact long-term uptrend.")
        else:
            reasons.append("Trading below its 200-day average — long-term trend is weak.")
    else:
        trend_pts += 6
    if sma50 is not None and sma200 is not None:
        if sma50 > sma200:
            trend_pts += 10
            reasons.append("Golden cross setup (50-day above 200-day) — bullish structure.")
        else:
            reasons.append("Death cross setup (50-day below 200-day) — bearish structure.")

    # --- 2. Momentum (0-33) ---
    ret_3m = _trailing_return(price_series, 63)
    ret_6m = _trailing_return(price_series, 126)
    mom_pts = 0
    if ret_3m is not None:
        if ret_3m > 8:
            mom_pts += 17
        elif ret_3m > 0:
            mom_pts += 11
        else:
            mom_pts += 4
        reasons.append(f"3-month return of {ret_3m:.1f}%.")
    else:
        mom_pts += 8
    if ret_6m is not None:
        if ret_6m > 15:
            mom_pts += 16
        elif ret_6m > 0:
            mom_pts += 10
        else:
            mom_pts += 4
        reasons.append(f"6-month return of {ret_6m:.1f}%.")
    else:
        mom_pts += 8

    # --- 3. RSI (0-33) ---
    rsi = _rsi(price_series)
    if rsi is not None:
        if 45 <= rsi <= 65:
            rsi_pts = 33
            reasons.append(f"RSI(14) of {rsi:.0f} — healthy, neither overbought nor oversold.")
        elif rsi < 30:
            rsi_pts = 20
            reasons.append(f"RSI(14) of {rsi:.0f} — oversold, could indicate a bounce or continued weakness.")
        elif rsi > 70:
            rsi_pts = 12
            reasons.append(f"RSI(14) of {rsi:.0f} — overbought, near-term pullback risk.")
        else:
            rsi_pts = 24
            reasons.append(f"RSI(14) of {rsi:.0f} — moderate momentum.")
    else:
        rsi_pts = 15

    total = trend_pts + mom_pts + rsi_pts
    return {"score": round(min(total, 100), 1), "reasons": reasons}
