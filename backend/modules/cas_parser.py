"""
cas_parser.py — Parses NSDL / CDSL / CAMS-KFintech Consolidated Account
Statement (CAS) PDFs into structured holdings.

DECISION (DECISIONS.md #4, revised): Real CAS PDFs vary a lot by
depository/RTA, and — importantly — asset-class headers like "Equities (E)"
/ "Mutual Funds (M)" are frequently rendered as background graphics rather
than extractable text, so section-header tracking alone is unreliable.
This parser instead recognizes three concrete NSDL table layouts directly,
using the ISIN itself (not headers) to anchor each entry, and a state
machine to walk the multi-line rows pdfplumber produces:

  1. **Detailed demat holdings table** (the bulk of most NSDL CAS files):
     "<ISIN> <name...> <current_bal> <safekeep_bal> <pledged_bal> <price> <value>"
     followed by up to 2 more lines of "<name...> <free/locked/earmarked or
     lent/pledgesetup/pledgee balances>", then optional pure-name
     continuation lines. Asset type (stock vs mutual fund) is inferred from
     the ISIN prefix: "INE" = equity, "INF" = mutual fund — this is the
     official SEBI ISIN convention, not a guess.
  2. **Simple equity table** (seen on some NSDL demat sub-accounts): a
     compact "<ISIN> <name> <face_value> <qty> <price> <value>" row
     immediately followed by a "<SYMBOL>.NSE" or "<SYMBOL>.BSE" line —
     this is the one layout that actually gives us a usable ticker symbol.
  3. **Mutual Fund Folios (F) table** (non-demat SOA folios): the richest
     data, including real cost basis: "<ISIN> <scheme name...> <folio_no>
     <units> <avg_cost> <total_cost> <nav> <current_value> <unrealised_pl>".

Each pattern is tried in this order for every ISIN found; the first that
matches wins. This still won't parse every CAS variant ever produced (CDSL
statements, older NSDL templates, and CAMS/KFintech statements can differ
further) — a known limitation, flagged in README.md. If parsing yields zero
holdings, the app says so explicitly rather than showing a silently empty
or wrong portfolio.
"""
from __future__ import annotations

import re
import uuid
from typing import List, Optional

import pdfplumber


# DECISION (DECISIONS.md #4b): CAS statements identify equities primarily by
# ISIN, and most NSDL table layouts (pattern 1 above) don't include a ticker
# symbol at all — only pattern 2 does, incidentally. There's no free,
# comprehensive ISIN->NSE-symbol mapping API, so this module ships a lookup
# table covering common large/mid-cap names. Unmapped ISINs are left with
# `symbol=None`; the UI then asks the user to fill in the correct NSE symbol
# for a scored verdict on that specific holding. Best-effort guesses here
# are safe even if occasionally wrong: an incorrect symbol just yields "no
# market data" gracefully rather than any incorrect analysis.
ISIN_TO_SYMBOL = {
    "INE002A01018": "RELIANCE", "INE467B01029": "TCS", "INE040A01034": "HDFCBANK",
    "INE009A01021": "INFY", "INE154A01025": "ITC", "INE062A01020": "SBIN",
    "INE030A01027": "HINDUNILVR", "INE237A01028": "KOTAKBANK", "INE397D01024": "BHARTIARTL",
    "INE565A01014": "IOB", "INE457A01014": "MAHABANK", "INE024001021": "AEROFLEX",
    "INE208A01029": "ASHOKLEY", "INE067A01029": "CGPOWER", "INE758T01015": "ETERNAL",
    "INE302A01020": "EXIDEIND", "INE524A01029": "GABRIEL", "INE127D01025": "HDFCAMC",
    "INE548A01028": "HFCL", "INE066F01020": "HAL", "INE242A01010": "IOC",
    "INE335Y01020": "IRCTC", "INE202E01016": "IREDA", "INE758E01017": "JIOFIN",
    "INE138Y01010": "KFINTECH", "INE498L01015": "LTF", "INE018A01030": "LT",
    "INE0FS801015": "MSUMI", "INE301O01023": "NSDL", "INE733E01010": "NTPC",
    "INE045601023": "PARAS", "INE209B01025": "RICOAUTO", "INE073K01018": "SONACOMS",
    "INE428Q01011": "SURYODAY", "INE976I01016": "TATACAPITAL", "INE1TAE01010": "TATAMOTORS",
    "INE081A01020": "TATASTEEL", "INE142M01025": "TATATECH", "INE245A01021": "TATAPOWER",
    "INE377N01017": "WAAREEENER", "INE263A01024": "BEL", "INE736A01011": "CDSL",
}

# DECISION: broadened to any ISIN prefix (not just INE/INF) so government
# securities (ISIN prefix "IN0", e.g. Sovereign Gold Bonds) that happen to
# use the same detailed-table layout as equities/MFs are also captured,
# tagged as asset_type="other" via the ISIN's official type character.
ISIN_LINE_RE = re.compile(r"^(?P<isin>IN[A-Z0-9]{10})\s+(?P<rest>.+)$")

# Pattern 1 (detailed demat table): first line has name + 3 balance columns
# (3-decimal each) + market price (2-decimal) + value (2-decimal).
DETAILED_LINE1_RE = re.compile(
    r"^(?P<name>.*?)\s+(?P<v1>[\d,]+\.\d{3})\s+(?P<v2>[\d,]+\.\d{3})\s+(?P<v3>[\d,]+\.\d{3})\s+"
    r"(?P<price>[\d,]+\.\d{2})\s+(?P<value>[\d,]+\.\d{2})\s*$"
)
# Continuation lines: optional name fragment + exactly 3 balance columns.
DETAILED_NUMLINE_RE = re.compile(
    r"^(?P<name>.*?)\s*(?P<v1>[\d,]+\.\d{3})\s+(?P<v2>[\d,]+\.\d{3})\s+(?P<v3>[\d,]+\.\d{3})\s*$"
)

# Pattern 2 (simple equity table): name + face value (2dp) + qty (int,
# commas) + price (2dp) + value (2dp), with the NSE/BSE symbol on the row
# immediately after.
SIMPLE_EQUITY_LINE1_RE = re.compile(
    r"^(?P<name>.*?)\s+(?P<facevalue>[\d,]+\.\d{2})\s+(?P<qty>[\d,]+)\s+"
    r"(?P<price>[\d,]+\.\d{2})\s+(?P<value>[\d,]+\.\d{2})\s*$"
)
SYMBOL_LINE_RE = re.compile(r"^(?P<symbol>[A-Z0-9&\-\.]+)\.(?:NSE|BSE)\s*$")

# Pattern 3 (Mutual Fund Folios): name + folio no + units (2-3dp) + avg cost
# (usually 4dp) + total cost (2dp) + NAV (usually 4dp) + current value (2dp)
# + unrealised P/L (2dp, may be negative).
MF_FOLIO_LINE1_RE = re.compile(
    r"^(?P<name>.*?)\s+(?P<folio>\d{4,15})\s+(?P<units>[\d,]+\.\d{2,4})\s+"
    r"(?P<avgcost>[\d,]+\.\d{2,4})\s+(?P<totalcost>[\d,]+\.\d{2})\s+"
    r"(?P<nav>[\d,]+\.\d{2,4})\s+(?P<currval>[\d,]+\.\d{2})\s+"
    r"(?P<pl>-?[\d,]+\.\d{2})\s*$"
)
MF_UCC_LINE_RE = re.compile(r"^(?P<ucc>NOT AVAILABLE|[\w/]+)\s*(?P<name2>.*)$")

STOP_WORDS = ("Sub Total", "Total", "National Pension System", "***End of Statement***")


def _to_float(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _is_stop_line(line: str) -> bool:
    return any(line.startswith(sw) for sw in STOP_WORDS)


def _parse_holdings_from_lines(lines: List[str]) -> List[dict]:
    holdings = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = ISIN_LINE_RE.match(line)
        if not m:
            i += 1
            continue

        isin = m.group("isin")
        rest = m.group("rest")

        # --- Try Pattern 3: Mutual Fund Folios (richest data, check first) ---
        m3 = MF_FOLIO_LINE1_RE.match(rest)
        if m3 and isin.startswith("INF"):
            name_parts = [m3.group("name")]
            folio = m3.group("folio")
            units = _to_float(m3.group("units"))
            avg_cost = _to_float(m3.group("avgcost"))
            nav = _to_float(m3.group("nav"))
            current_value = _to_float(m3.group("currval"))
            i += 1
            if i < n:
                m_ucc = MF_UCC_LINE_RE.match(lines[i])
                if m_ucc and not ISIN_LINE_RE.match(lines[i]) and not _is_stop_line(lines[i]):
                    if m_ucc.group("name2"):
                        name_parts.append(m_ucc.group("name2"))
                    i += 1
            while i < n and not ISIN_LINE_RE.match(lines[i]) and not _is_stop_line(lines[i]) \
                    and not re.search(r"\d", lines[i]):
                name_parts.append(lines[i])
                i += 1
            holdings.append({
                "symbol": None, "name": " ".join(name_parts).strip(" -"), "isin": isin,
                "asset_type": "mutual_fund", "market": "IN", "quantity": units,
                "avg_cost": avg_cost, "current_price": nav, "current_value": current_value,
                "currency": "INR", "notes": f"Folio {folio}",
            })
            continue

        # --- Try Pattern 2: simple equity table (has a following SYMBOL.NSE line) ---
        m2 = SIMPLE_EQUITY_LINE1_RE.match(rest)
        if m2 and i + 1 < n and SYMBOL_LINE_RE.match(lines[i + 1]):
            symbol = SYMBOL_LINE_RE.match(lines[i + 1]).group("symbol")
            holdings.append({
                "symbol": symbol, "name": m2.group("name").strip(" -"), "isin": isin,
                "asset_type": "stock", "market": "IN",
                "quantity": _to_float(m2.group("qty")), "avg_cost": None,
                "current_price": _to_float(m2.group("price")),
                "current_value": _to_float(m2.group("value")), "currency": "INR",
            })
            i += 2
            continue

        # --- Try Pattern 1: detailed demat holdings table ---
        m1 = DETAILED_LINE1_RE.match(rest)
        if m1:
            name_parts = [m1.group("name")]
            # v1 = "Current Bal." = the actual quantity currently held (verified against
            # known holdings: qty * price == value exactly for every row in this layout).
            quantity = _to_float(m1.group("v1"))
            price = _to_float(m1.group("price"))
            value = _to_float(m1.group("value"))
            i += 1
            consumed = 1
            while consumed < 3 and i < n:
                mnum = DETAILED_NUMLINE_RE.match(lines[i])
                if mnum:
                    if mnum.group("name"):
                        name_parts.append(mnum.group("name"))
                    consumed += 1
                    i += 1
                else:
                    break
            while i < n and not ISIN_LINE_RE.match(lines[i]) and not _is_stop_line(lines[i]) \
                    and not re.search(r"\d", lines[i]):
                name_parts.append(lines[i])
                i += 1
            # ISIN's 3rd character is SEBI's official type code: E=equity, F=mutual fund,
            # anything else (e.g. "0" for govt securities/SGBs) -> treated as a generic
            # "other" holding, since this app doesn't score bonds/SGBs against a benchmark.
            type_code = isin[2] if len(isin) > 2 else ""
            asset_type = "mutual_fund" if type_code == "F" else ("stock" if type_code == "E" else "other")
            name = " ".join(name_parts).strip(" -")
            holdings.append({
                "symbol": ISIN_TO_SYMBOL.get(isin) if asset_type == "stock" else None,
                "name": name, "isin": isin, "asset_type": asset_type, "market": "IN",
                "quantity": quantity,
                "avg_cost": None,  # NSDL CAS doesn't track equity cost basis; only MF Folios (Pattern 3) do
                "current_price": price, "current_value": value, "currency": "INR",
            })
            continue

        i += 1

    return holdings


def parse_cas_bytes(file_bytes: bytes) -> dict:
    """Returns {"holdings": [...], "warnings": [...], "error": str|None}."""
    try:
        import io
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as e:
        return {"holdings": [], "warnings": [], "error": f"Could not open PDF: {e}. "
                "If this CAS is password-protected, please remove the password first "
                "(open it once in a PDF reader with your PAN-based password and re-save)."}

    if not full_text.strip():
        return {"holdings": [], "warnings": [], "error": "No extractable text found in this PDF "
                "(it may be a scanned image). Automated parsing isn't supported for scanned CAS files."}

    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    holdings = _parse_holdings_from_lines(lines)

    warnings = []
    if not holdings:
        warnings.append(
            "Zero holdings were extracted. This CAS layout may differ from the supported "
            "patterns (see DECISIONS.md #4 and README's Known Limitations). "
            "You can still evaluate your portfolio by adding holdings manually."
        )
    else:
        n_no_cost = sum(1 for h in holdings if h["asset_type"] == "stock" and h.get("avg_cost") is None)
        if n_no_cost:
            warnings.append(
                f"{n_no_cost} equity holding(s) have no purchase-cost data — NSDL/CDSL CAS statements "
                "don't track cost basis for demat equity holdings (only mutual fund folios include it), "
                "so gain/loss can't be computed for these until you edit them with your actual buy price."
            )
        n_no_symbol = sum(1 for h in holdings if h["asset_type"] == "stock" and not h.get("symbol"))
        if n_no_symbol:
            warnings.append(
                f"{n_no_symbol} stock holding(s) don't have a mapped NSE ticker symbol, so they'll "
                "show as 'N/A' for scored verdicts until you edit them with the correct symbol."
            )

    return {"holdings": holdings, "warnings": warnings, "error": None}


TREND_LINE_RE = re.compile(
    r"^(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(?P<year>\d{4})\s+(?P<value>[\d,]+\.\d{2})"
)


def parse_trend_bytes(file_bytes: bytes) -> List[dict]:
    """Extracts the CAS's own 'Monthly movement of your Consolidated Portfolio
    Value' table (typically ~13 months) — free historical data NSDL already
    includes, so the app can show a trend line immediately without needing
    13 months of separate uploads to build one up."""
    try:
        import io
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return []
    points = []
    for ln in full_text.splitlines():
        m = TREND_LINE_RE.match(ln.strip())
        if m:
            points.append({
                "label": f"{m.group('mon').title()}{m.group('year')[2:]}",
                "month": m.group("mon"), "year": int(m.group("year")),
                "value": _to_float(m.group("value")),
            })
    return points


NPS_LINE_RE = re.compile(
    r"^(?P<tier>TIER\s+[I]+)\s+(?P<contribution>[\d,]+\.\d{2})\s+(?P<withdrawal>[\d,]+\.\d{2})\s+"
    r"(?P<value>[\d,]+\.\d{2})\s+(?P<gain>[\d,]+\.\d{2})\s+(?P<xirr>[\d.]+)\s*$"
)


def parse_nps_bytes(file_bytes: bytes) -> Optional[dict]:
    """Extracts the NPS Tier I summary line (contribution, current value,
    notional gain, XIRR) from the 'Your NPS account' table. Returns None if
    the CAS has no NPS section — not every investor holds NPS."""
    try:
        import io
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return None
    for ln in full_text.splitlines():
        m = NPS_LINE_RE.match(ln.strip())
        if m:
            return {
                "tier": m.group("tier"), "contribution": _to_float(m.group("contribution")),
                "withdrawal": _to_float(m.group("withdrawal")), "value": _to_float(m.group("value")),
                "gain": _to_float(m.group("gain")), "xirr": float(m.group("xirr")),
            }
    return None


def new_batch_id() -> str:
    return uuid.uuid4().hex[:12]
