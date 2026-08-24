"""
generic_tabular_parser.py — A format-agnostic fallback for tradebook/ledger
exports that don't match tradebook_parser.py's Zerodha-specific template.

Rather than hardcoding a parser per broker (which needs a real sample file
per broker to build and verify against — see tradebook_parser.py's own
DECISION notes on why that's the approach for Zerodha specifically), this
scans for a header row using fuzzy matching against known synonyms for the
columns any trade record needs: symbol, ISIN, date, quantity, price, and
buy/sell direction. If it finds a plausible header, it extracts rows below
it the same way regardless of the exact column wording or order a given
broker uses.

This sits BETWEEN the Zerodha-specific parser and the LLM fallback in
upload_dispatch.py's chain:
  1. tradebook_parser (Zerodha-exact) — fastest, most precise, zero cost
  2. generic_tabular_parser (this file) — heuristic, no API key needed,
     handles "reasonably standard" tradebook/ledger layouts from other
     brokers (Motilal Oswal, Angel One, etc.) without ever leaving the
     machine
  3. llm_parser (opt-in, needs ANTHROPIC_API_KEY) — last resort for genuinely
     unusual layouts

DECISION: acceptance requires matching symbol + quantity + price, AND at
least one of (date, trade_type) — a deliberately conservative bar, so a
file that only coincidentally has a column called "Price" doesn't get
mis-parsed as a tradebook. If the bar isn't met, this parser reports
"no confident match" rather than guessing, and the caller falls through to
the next tier instead of trusting a low-confidence extraction.
"""
from __future__ import annotations

import io
import re
import uuid
import datetime as dt
from typing import List, Optional

import openpyxl

# --------------------------------------------------------------------------- #
# Column synonym groups — case-insensitive, punctuation/whitespace-normalized
# --------------------------------------------------------------------------- #

SYNONYMS = {
    "symbol": ["symbol", "scrip", "scripname", "stock", "stockname", "security",
               "securityname", "instrument", "tradingsymbol", "company", "companyname", "script"],
    "isin": ["isin", "isincode", "isinno", "isinnumber"],
    "date": ["tradedate", "date", "transactiondate", "orderdate", "txndate",
             "dealdate", "settlementdate"],
    "quantity": ["quantity", "qty", "shares", "units", "noofshares", "nos", "tradedqty"],
    "price": ["price", "rate", "tradeprice", "avgprice", "averageprice", "dealprice",
              "priceperunit", "netrate"],
    "trade_type": ["tradetype", "type", "buysell", "transactiontype", "txntype",
                    "bs", "action", "buyorsell", "orderindicator"],
}

BUY_TOKENS = {"b", "buy", "bought", "purchase"}
SELL_TOKENS = {"s", "sell", "sold", "sale"}

_ISIN_RE = re.compile(r"^IN[A-Z0-9]{10}$")


def _normalize_header(text) -> str:
    if text is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _find_header_row(rows: List[tuple]) -> Optional[dict]:
    """Scans rows for the best-matching header row. Returns a column-name ->
    column-index map if a confident match is found, else None."""
    best_map, best_score = None, 0
    for row in rows:
        col_map = {}
        for idx, cell in enumerate(row):
            norm = _normalize_header(cell)
            if not norm:
                continue
            for field, synonyms in SYNONYMS.items():
                if field in col_map:
                    continue
                if norm in synonyms:
                    col_map[field] = idx
                    break
        score = len(col_map)
        required_core = {"symbol", "quantity", "price"} <= col_map.keys()
        has_context = "date" in col_map or "trade_type" in col_map
        if required_core and has_context and score > best_score:
            best_score, best_map = score, col_map
    return best_map


def _parse_date(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s if s else None  # keep the raw string rather than silently dropping the row


def _parse_trade_type(value) -> Optional[str]:
    if value is None:
        return None
    token = str(value).strip().lower()
    if token in BUY_TOKENS:
        return "buy"
    if token in SELL_TOKENS:
        return "sell"
    return None


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[,\s₹]", "", str(value))
    try:
        return float(s)
    except ValueError:
        return None


def parse_tabular_bytes(filename: str, file_bytes: bytes) -> dict:
    """Returns {"transactions": [...], "warnings": [...], "error": str|None,
    "columns_matched": [...]}. Only supports .xlsx/.xlsm/.csv — the same
    universe tradebook_parser.py covers, since this is specifically a
    tradebook-shaped-data fallback, not a general document parser."""
    lower = filename.lower()
    rows: List[tuple] = []

    try:
        if lower.endswith((".xlsx", ".xlsm")):
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows.extend(list(ws.iter_rows(values_only=True)))
        elif lower.endswith((".csv", ".tsv")):
            import csv
            delimiter = "\t" if lower.endswith(".tsv") else ","
            text = None
            for enc in ("utf-8", "utf-8-sig", "latin-1"):
                try:
                    text = file_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                return {"transactions": [], "warnings": [], "error": "Could not decode this "
                        "file as text (unrecognized character encoding).", "columns_matched": []}
            rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        else:
            return {"transactions": [], "warnings": [], "error": "This parser only handles "
                    ".xlsx, .xlsm, .csv, or .tsv files.", "columns_matched": []}
    except Exception as e:
        return {"transactions": [], "warnings": [], "error": f"Could not read this file: {e}",
                "columns_matched": []}

    col_map = _find_header_row(rows)
    if col_map is None:
        return {"transactions": [], "warnings": [], "error": "No confident tradebook-style "
                "header row found (need at least Symbol + Quantity + Price, plus a Date or "
                "Buy/Sell column). This file's layout doesn't look like trade data, or uses "
                "column names too different from what this parser recognizes.",
                "columns_matched": []}

    header_row_values = None
    for row in rows:
        candidate_map = _find_header_row([row])
        if candidate_map == col_map:
            header_row_values = row
            break
    header_row_idx = rows.index(header_row_values) if header_row_values is not None else -1

    def get(row, field):
        idx = col_map.get(field)
        return row[idx] if idx is not None and idx < len(row) else None

    transactions = []
    skipped = 0
    for row in rows[header_row_idx + 1:]:
        if row is None or get(row, "symbol") is None:
            continue
        symbol = str(get(row, "symbol")).strip()
        if not symbol or _normalize_header(symbol) in {"total", "grandtotal", "subtotal"}:
            continue

        qty = _to_float(get(row, "quantity"))
        price = _to_float(get(row, "price"))
        trade_type = _parse_trade_type(get(row, "trade_type")) if "trade_type" in col_map else None
        trade_date = _parse_date(get(row, "date")) if "date" in col_map else None

        if qty is None or price is None or trade_type is None or trade_date is None:
            skipped += 1
            continue

        isin = str(get(row, "isin")).strip() if get(row, "isin") else None
        if isin and not _ISIN_RE.match(isin):
            isin = None

        transactions.append({
            "symbol": symbol, "isin": isin, "trade_date": trade_date,
            "exchange": None, "segment": None, "trade_type": trade_type,
            "quantity": qty, "price": price, "trade_id": None,
            "order_id": None, "executed_at": None,
        })

    warnings = []
    if not transactions:
        warnings.append("A tradebook-style header was found, but no rows below it had all of "
                         "Symbol/Quantity/Price/Date/Type filled in — nothing was extracted.")
    elif skipped:
        warnings.append(f"{skipped} row(s) below the header were skipped — missing quantity, "
                         "price, a recognizable date, or a recognizable buy/sell value.")

    return {"transactions": transactions, "warnings": warnings, "error": None,
            "columns_matched": sorted(col_map.keys())}


def new_batch_id() -> str:
    return uuid.uuid4().hex[:12]
