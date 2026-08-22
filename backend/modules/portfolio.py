"""
portfolio.py — Aggregates holdings from ALL sources (CAS upload, manual US
trades, unlisted shares, gold, other) into one unified view, refreshes
current prices where a live feed exists, and produces a per-holding verdict
using the same fundamental+technical framework as the standalone
recommendation engine (requirement #2 & #8).

An INR-equivalent value is computed for every holding so allocation charts
and totals are meaningful across currencies (DECISION, DECISIONS.md #11:
a single illustrative USD/INR rate is used, refreshed via yfinance's
USDINR=X pair with a hardcoded fallback if that feed is unavailable —
this is NOT a live treasury-grade FX rate and is clearly labelled as such
in the UI).
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from . import db, data_fetch, recommendation, benchmark

FALLBACK_USDINR = 87.0


def has_symbol(value) -> bool:
    """True only for a real, non-empty ticker string. Guards against pandas
    turning a missing symbol into NaN (a float) rather than None — `not NaN`
    is False in Python, so a naive truthiness check silently passes NaN
    through to yfinance and crashes with 'float has no attribute strip'."""
    return isinstance(value, str) and value.strip() != ""


def get_usdinr_rate() -> float:
    hist = data_fetch.get_price_history("USDINR=X", market="US", period="5d")
    if hist is not None and not hist.empty:
        try:
            return float(hist["Close"].iloc[-1])
        except Exception:
            pass
    return FALLBACK_USDINR


def holdings_to_dataframe(holdings: List[db.PortfolioHolding]) -> pd.DataFrame:
    rows = []
    for h in holdings:
        rows.append({
            "id": h.id,
            "source": h.source,
            "symbol": h.symbol,
            "name": h.name,
            "isin": h.isin,
            "asset_type": h.asset_type,
            "market": h.market,
            "quantity": h.quantity,
            "avg_cost": h.avg_cost,
            "unit": h.unit,
            "current_price": h.current_price,
            "current_value": h.current_value,
            "currency": h.currency,
            "batch_id": h.batch_id,
            "notes": h.notes,
        })
    return pd.DataFrame(rows)


def refresh_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Refreshes current_price/current_value for holdings that have a resolvable
    live-feed symbol (stocks/ETFs in IN or US). Leaves unlisted/gold/other rows
    untouched since they rely on user-supplied estimates."""
    df = df.copy()
    for idx, row in df.iterrows():
        if row["asset_type"] in ("stock", "etf") and has_symbol(row["symbol"]) and row["market"] in ("IN", "US"):
            price = data_fetch.get_current_price(row["symbol"], row["market"])
            if price is not None:
                df.at[idx, "current_price"] = price
                df.at[idx, "current_value"] = price * (row["quantity"] or 0)
    return df


def add_inr_columns(df: pd.DataFrame, usdinr: float) -> pd.DataFrame:
    df = df.copy()
    def to_inr(row):
        if row["current_value"] is None:
            return None
        if row["currency"] == "USD":
            return row["current_value"] * usdinr
        return row["current_value"]
    def cost_inr(row):
        if row["avg_cost"] is None or row["quantity"] is None:
            return None
        base_cost = row["avg_cost"] * row["quantity"]
        return base_cost * usdinr if row["currency"] == "USD" else base_cost
    df["value_inr"] = df.apply(to_inr, axis=1)
    df["cost_inr"] = df.apply(cost_inr, axis=1)
    df["gain_loss_inr"] = df["value_inr"] - df["cost_inr"]
    df["gain_loss_pct"] = df.apply(
        lambda r: round((r["gain_loss_inr"] / r["cost_inr"]) * 100, 2)
        if r["cost_inr"] not in (None, 0) and r["gain_loss_inr"] is not None else None,
        axis=1,
    )
    return df


def evaluate_holding(row: pd.Series) -> dict:
    """Runs the recommendation engine's analysis for a single portfolio holding
    and returns a buy-more/hold/trim/exit verdict with reasoning. Gracefully
    skips assets with no live-feed analysis path (unlisted, gold, other)."""
    if row["asset_type"] in ("unlisted_equity", "gold", "other"):
        return {
            "verdict": "N/A",
            "reasoning": "No public market data exists for this asset type — "
                         "value is based on your own estimate; consider periodic manual revaluation.",
            "composite_score": None,
        }
    if row["asset_type"] == "mutual_fund":
        return {
            "verdict": "N/A",
            "reasoning": "Mutual fund holdings from CAS aren't matched to an AMFI scheme code automatically. "
                         "Use the Recommendations page with the scheme code to get a scored verdict for this fund.",
            "composite_score": None,
        }
    if not has_symbol(row["symbol"]):
        return {
            "verdict": "N/A",
            "reasoning": "No resolvable ticker symbol for this holding (ISIN-to-symbol mapping wasn't available). "
                         "Edit this holding to add the correct NSE symbol for a scored verdict.",
            "composite_score": None,
        }

    analysis = recommendation.analyze_symbol(row["symbol"], row["market"], row["asset_type"], name=row["name"])
    if "error" in analysis:
        return {"verdict": "N/A", "reasoning": analysis["error"], "composite_score": None}

    # Portfolio context nudges the pure market verdict: a large unrealized loss on an
    # otherwise-weak name reinforces "Trim/Exit"; a big unrealized gain on a strong
    # name reinforces "Buy more". This is a light adjustment, documented here rather
    # than hidden in code.
    verdict = analysis["verdict"]
    gain_pct = row.get("gain_loss_pct")
    reasoning = analysis["reasoning"]
    if gain_pct is not None:
        if verdict in ("Strong Buy", "Buy") and gain_pct > 0:
            reasoning += f"\n\nPortfolio context: already up {gain_pct}% on this position and the underlying case remains strong — consider adding on dips rather than chasing."
        elif verdict in ("Trim", "Exit") and gain_pct < -15:
            reasoning += f"\n\nPortfolio context: down {abs(gain_pct)}% and the underlying case has weakened — re-examine the original thesis before averaging down."

    return {"verdict": verdict, "reasoning": reasoning, "composite_score": analysis["composite_score"],
            "benchmark_index": analysis.get("benchmark_index")}


def portfolio_benchmark_alpha(df: pd.DataFrame, period: str = "1y") -> dict:
    """Computes portfolio-level blended benchmark return and alpha, weighted by
    current INR value of each holding that has a resolvable benchmark."""
    weights = {}
    total_value = df["value_inr"].fillna(0).sum()
    if total_value == 0:
        return {"portfolio_return": None, "benchmark_return": None, "alpha": None}

    for _, row in df.iterrows():
        if row["value_inr"] is None or row["asset_type"] in ("unlisted_equity", "gold", "other"):
            continue
        idx_key = benchmark.determine_benchmark(row["asset_type"], row["market"], {}, row["name"] or "")
        weights[idx_key] = weights.get(idx_key, 0) + row["value_inr"] / total_value

    blended_bench_return = benchmark.blended_benchmark_return(weights, period=period)

    port_cost = df["cost_inr"].fillna(0).sum()
    port_value = df["value_inr"].fillna(0).sum()
    port_return = ((port_value - port_cost) / port_cost * 100) if port_cost else None

    alpha = None
    if port_return is not None and blended_bench_return is not None:
        alpha = round(port_return - blended_bench_return, 2)

    return {
        "portfolio_return": round(port_return, 2) if port_return is not None else None,
        "benchmark_return": round(blended_bench_return, 2) if blended_bench_return is not None else None,
        "alpha": alpha,
        "index_weights": weights,
    }
