"""
asset_classifier.py — Classifies a holding/transaction as stock, ETF, or
mutual fund from its ISIN and/or name.

DECISION: ISIN prefix alone is insufficient. SEBI's ISIN convention uses
the 3rd character as a type code (E=equity/bond-like instruments, F=mutual
fund/AMFI-registered instruments) — this already distinguishes ordinary
stocks from AMFI-registered instruments (see cas_parser.py's own DECISIONS
on this). But ETFs are ALSO issued under the "F"-prefix AMFI convention
(confirmed against real data: e.g. "MIRAE ASSET NIFTY 500 MULTICAP 50:25:25
ETF" has ISIN INF769K01LX9 — an "INF" prefix identical to a traditional
open-ended mutual fund), so ISIN prefix cannot distinguish an ETF from a
regular mutual fund on its own. Name-based keyword matching is the
deciding factor for that specific split.

DECISION: name check runs FIRST (an explicit "ETF" in the name is a strong,
unambiguous signal), then ISIN prefix, then a name-based mutual-fund
keyword fallback for entries with no ISIN at all, defaulting to "stock"
only when nothing else matches — matching this module's error-toward-the-
more-common-case philosophy rather than an "other"/"unknown" bucket, since
"stock" is by far the most common unclassified case in real tradebook data
(single-letter/ambiguous company names that aren't funds).
"""
from __future__ import annotations

import re

_ETF_KEYWORDS = ("etf", "exchange traded fund", "bees")  # "BEES" = legacy Benchmark/Goldman ETF naming (e.g. NIFTYBEES, GOLDBEES)
_MF_KEYWORDS = ("mutual fund", " mf-", "amc ltd", "asset management")

_ISIN_RE = re.compile(r"^IN[A-Z0-9]{10}$")


def classify(isin: str = None, name: str = "") -> str:
    """Returns 'stock', 'etf', or 'mutual_fund'."""
    name_low = (name or "").lower()

    if any(kw in name_low for kw in _ETF_KEYWORDS):
        return "etf"

    if isin and _ISIN_RE.match(isin):
        type_code = isin[2]
        if type_code == "F":
            # AMFI-registered instrument, and name didn't say ETF above ->
            # treat as a regular mutual fund.
            return "mutual_fund"
        if type_code == "E":
            return "stock"

    if any(kw in name_low for kw in _MF_KEYWORDS):
        return "mutual_fund"

    # Some no-ISIN exports (confirmed on a real Angel One-style file) use
    # abbreviated AMC names fused as a suffix of the first token, e.g.
    # "BIRLASLAMC - MOMENTUM" (no space before "AMC"). A trailing "amc" on
    # the first hyphen/space-delimited token is a reasonable signal for
    # this specific naming pattern, even though "amc ltd"/"asset
    # management" above wouldn't catch it.
    first_token = re.split(r"[\s\-]", name_low.strip(), maxsplit=1)[0] if name_low.strip() else ""
    if first_token.endswith("amc") and len(first_token) > 3:
        return "mutual_fund"

    return "stock"
