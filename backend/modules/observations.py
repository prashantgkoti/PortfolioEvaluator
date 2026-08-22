"""
observations.py — Computes the concentration, overlap, and sector-tilt
analytics shown on the "Observations" view. All figures are derived fresh
from the person's actual holdings each request; nothing here references
or fabricates any prior session, review, or stated preference.

DECISION: sector tags are a small hardcoded lookup (common NSE symbols),
not a live classification API (none free exists). Untagged symbols fall
into "Other" rather than being guessed at. This is heuristic, not
authoritative sector data.
"""
from __future__ import annotations

from typing import List

SECTOR_MAP = {
    "SBIN": "Banks & NBFCs", "HDFCBANK": "Banks & NBFCs", "HDFCAMC": "Banks & NBFCs",
    "IOB": "Banks & NBFCs", "LTF": "Banks & NBFCs", "TATACAPITAL": "Banks & NBFCs",
    "SURYODAY": "Banks & NBFCs", "KOTAKBANK": "Banks & NBFCs", "ICICIBANK": "Banks & NBFCs",
    "HAL": "Defence", "PARAS": "Defence", "BEL": "Defence",
    "IREDA": "Green / Clean Energy", "WAAREEENER": "Green / Clean Energy",
    "ASHOKLEY": "Auto & Ancillary", "TATAMOTORS": "Auto & Ancillary", "EXIDEIND": "Auto & Ancillary",
    "GABRIEL": "Auto & Ancillary", "RICOAUTO": "Auto & Ancillary", "MSUMI": "Auto & Ancillary",
    "NTPC": "PSU / Infra", "IOC": "PSU / Infra", "NSDL": "PSU / Infra", "CDSL": "PSU / Infra",
    "IRCTC": "PSU / Infra",
    "RELIANCE": "Conglomerate / Energy", "TCS": "IT Services", "INFY": "IT Services",
    "ITC": "FMCG", "HINDUNILVR": "FMCG",
    "KFINTECH": "Financial Services Tech", "CGPOWER": "Capital Goods / Semiconductor-adjacent",
    "TATASTEEL": "Metals", "TATATECH": "Engineering Services", "TATAPOWER": "Power & Utilities",
    "JIOFIN": "Financial Services", "SONACOMS": "Auto & Ancillary", "LT": "Capital Goods",
    "AEROFLEX": "Industrials", "HFCL": "Telecom Infra", "ETERNAL": "Consumer Internet",
}


def tag_sector(symbol) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        return "Other"
    return SECTOR_MAP.get(symbol.upper(), "Other")


def compute_observations(equity_holdings: List[dict]) -> dict:
    """equity_holdings: list of dicts with at least symbol, name, current_value.
    Returns concentration stats, sector tally, and small-position analysis —
    all computed fresh from this exact input, nothing cached or assumed."""
    rows = [h for h in equity_holdings if (h.get("current_value") or 0) > 0]
    rows.sort(key=lambda h: -(h["current_value"] or 0))
    total = sum(h["current_value"] for h in rows)
    if total == 0:
        return {"total_equity": 0, "position_count": 0, "top5_pct": 0,
                "small_positions": [], "small_positions_pct": 0, "sector_tally": [],
                "top_holding": None}

    top5 = rows[:5]
    top5_value = sum(h["current_value"] for h in top5)
    small = [h for h in rows if h["current_value"] < 20000]
    small_value = sum(h["current_value"] for h in small)

    sector_totals: dict = {}
    for h in rows:
        sec = tag_sector(h.get("symbol"))
        sector_totals.setdefault(sec, {"value": 0.0, "names": []})
        sector_totals[sec]["value"] += h["current_value"]
        sector_totals[sec]["names"].append(h.get("name") or h.get("symbol") or "Unknown")
    sector_tally = sorted(
        [{"sector": k, "value": round(v["value"], 2), "names": v["names"]} for k, v in sector_totals.items()],
        key=lambda x: -x["value"],
    )

    return {
        "total_equity": round(total, 2),
        "position_count": len(rows),
        "top_holding": {"name": rows[0].get("name"), "value": rows[0]["current_value"],
                         "pct": round(rows[0]["current_value"] / total * 100, 2)},
        "second_holding": {"name": rows[1].get("name"), "value": rows[1]["current_value"],
                            "pct": round(rows[1]["current_value"] / total * 100, 2)} if len(rows) > 1 else None,
        "top5_value": round(top5_value, 2),
        "top5_pct": round(top5_value / total * 100, 2),
        "small_positions": [{"name": h.get("name"), "value": h["current_value"]} for h in small],
        "small_positions_count": len(small),
        "small_positions_pct_of_value": round(small_value / total * 100, 2) if total else 0,
        "small_positions_pct_of_count": round(len(small) / len(rows) * 100, 2) if rows else 0,
        "sector_tally": sector_tally,
    }


def find_fund_overlap(mf_holdings: List[dict]) -> List[dict]:
    """Flags mutual fund folios that share an obvious category keyword
    (small cap, mid cap, flexi cap) across different names — a tidiness
    signal, not a performance judgement. Matches with or without a space
    ("Midcap Fund" vs "Mid Cap Fund") since AMCs aren't consistent about it."""
    categories = {"small cap": [], "mid cap": [], "flexi cap": []}
    for h in mf_holdings:
        name_low = (h.get("name") or "").lower().replace("smallcap", "small cap") \
            .replace("midcap", "mid cap").replace("flexicap", "flexi cap")
        for cat in categories:
            if cat in name_low:
                categories[cat].append(h.get("name"))
    return [{"category": k, "funds": v} for k, v in categories.items() if len(v) > 1]
