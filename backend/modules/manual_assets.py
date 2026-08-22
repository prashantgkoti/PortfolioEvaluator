"""
manual_assets.py — Helpers for holdings the CAS never covers:
  - US-market trades (stocks/ETFs bought outside India, e.g. via LRS brokers)
  - Unlisted shares (pre-IPO / ESOPs in private companies, no live price feed)
  - Physical/digital gold (priced per gram, not per "share")
  - Any other off-CAS asset the user wants tracked (bonds, real estate, etc.)

These all land in the same `portfolio_holdings` table as CAS-derived rows
(see db.py), tagged with a distinct `source`, so the rest of the app
(portfolio evaluator, benchmark comparison, allocation charts) treats the
whole net worth as one portfolio without needing special-case logic.
"""
from __future__ import annotations

from typing import Optional

from . import data_fetch, cas_parser, db


def build_us_trade_holding(symbol: str, name: str, quantity: float, avg_cost: float,
                            asset_type: str = "stock") -> dict:
    """asset_type: 'stock' or 'etf'. Price is fetched live via yfinance (US market)."""
    current_price = data_fetch.get_current_price(symbol, market="US")
    current_value = (current_price * quantity) if current_price else None
    return {
        "symbol": symbol.upper(),
        "name": name or symbol.upper(),
        "isin": None,
        "asset_type": asset_type,
        "market": "US",
        "quantity": quantity,
        "avg_cost": avg_cost,
        "unit": "units",
        "current_price": current_price,
        "current_value": current_value,
        "currency": "USD",
        "notes": None,
    }


def build_unlisted_holding(name: str, quantity: float, avg_cost: float,
                            estimated_current_price: Optional[float] = None, notes: str = "") -> dict:
    """No live feed exists for unlisted/private shares — the user supplies their own
    best estimate of current fair value per share (e.g. from a recent funding round
    or secondary transaction); this is clearly labelled as user-estimated in the UI."""
    price = estimated_current_price if estimated_current_price is not None else avg_cost
    return {
        "symbol": None,
        "name": name,
        "isin": None,
        "asset_type": "unlisted_equity",
        "market": "OTHER",
        "quantity": quantity,
        "avg_cost": avg_cost,
        "unit": "units",
        "current_price": price,
        "current_value": price * quantity if price else None,
        "currency": "INR",
        "notes": notes or "User-estimated current price (no live market feed for unlisted shares).",
    }


def build_gold_holding(grams: float, avg_cost_per_gram: float,
                        current_price_per_gram: Optional[float] = None, form: str = "Physical") -> dict:
    """form: 'Physical', 'Digital', 'SGB' (Sovereign Gold Bond), or 'Gold ETF units held outside CAS'."""
    price = current_price_per_gram
    return {
        "symbol": None,
        "name": f"Gold ({form})",
        "isin": None,
        "asset_type": "gold",
        "market": "IN",
        "quantity": grams,
        "avg_cost": avg_cost_per_gram,
        "unit": "grams",
        "current_price": price,
        "current_value": (price * grams) if price else None,
        "currency": "INR",
        "notes": f"Form: {form}. Current price per gram must be entered manually (no automated gold-rate feed in this build).",
    }


def build_other_holding(name: str, asset_type: str, quantity: float, avg_cost: float,
                         current_value: Optional[float] = None, currency: str = "INR", notes: str = "") -> dict:
    return {
        "symbol": None,
        "name": name,
        "isin": None,
        "asset_type": asset_type,
        "market": "OTHER",
        "quantity": quantity,
        "avg_cost": avg_cost,
        "unit": "units",
        "current_price": (current_value / quantity) if (current_value and quantity) else None,
        "current_value": current_value if current_value is not None else (avg_cost * quantity),
        "currency": currency,
        "notes": notes,
    }


def save_manual_batch(holdings: list, source: str, label: str) -> str:
    batch_id = cas_parser.new_batch_id()
    db.save_holdings(holdings, source=source, batch_id=batch_id, label=label)
    return batch_id
