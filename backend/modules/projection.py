"""
projection.py — SIP + lump-sum corpus projection.

DECISION (DECISIONS.md #8): monthly compounding is assumed for the SIP
(a monthly SIP is the overwhelmingly common convention in India), with the
annual return rate converted to an effective monthly rate. The lump sum
(current portfolio value) compounds on the same monthly basis. Year-by-year
values are reported at each 12-month mark so the UI can chart the growth
path, not just hand back a single final number.
"""
from __future__ import annotations

import pandas as pd


def project_corpus(current_value: float, sip_amount: float, annual_return_pct: float, years: int,
                    sip_growth_pct: float = 0.0) -> pd.DataFrame:
    """
    current_value    : lump sum already invested (₹)
    sip_amount       : monthly SIP amount (₹), applied at the start of each month
    annual_return_pct: expected annual return, e.g. 12 for 12%
    years            : investment horizon
    sip_growth_pct   : optional annual step-up in SIP amount (e.g. 10 for a 10%/yr top-up)

    Returns a DataFrame with one row per month: month, year, invested, corpus.
    """
    monthly_rate = (1 + annual_return_pct / 100) ** (1 / 12) - 1
    months = years * 12

    corpus = current_value
    invested = current_value
    sip = sip_amount
    rows = []

    for m in range(1, months + 1):
        corpus = corpus * (1 + monthly_rate) + sip
        invested += sip
        if m % 12 == 0 and sip_growth_pct:
            sip *= (1 + sip_growth_pct / 100)
        rows.append({
            "month": m,
            "year": round(m / 12, 2),
            "invested": round(invested, 2),
            "corpus": round(corpus, 2),
            "gains": round(corpus - invested, 2),
        })

    return pd.DataFrame(rows)


def yearly_summary(monthly_df: pd.DataFrame) -> pd.DataFrame:
    """Collapses the monthly projection to one row per completed year for display."""
    yearly = monthly_df[monthly_df["month"] % 12 == 0].copy()
    yearly["year"] = (yearly["month"] / 12).astype(int)
    return yearly[["year", "invested", "corpus", "gains"]].reset_index(drop=True)
