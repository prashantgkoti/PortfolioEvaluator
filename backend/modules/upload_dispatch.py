"""
upload_dispatch.py — Routes an uploaded file to the right parser based on
its extension, and transparently unpacks a .zip so the person can push a
whole folder of statements at once (one CAS PDF, several years of tradebook
exports, or any mix) instead of uploading files one at a time.

DECISION: file type is determined by extension, not content-sniffing —
simpler, and the underlying parsers already fail safely on a mismatch
(cas_parser errors on a PDF it can't extract text from; tradebook_parser
errors when it can't find a "Symbol"/"ISIN" header row), so a wrong
extension still surfaces a clear per-file error rather than silently
misparsing something.

DECISION: cost-basis reconciliation (matching tradebook-derived FIFO
positions against CAS holdings) runs exactly once per upload request, after
every file in a zip has been ingested — not per-file — so a zip containing
several years of tradebooks reconciles against their combined trade
history, not each year in isolation.
"""
from __future__ import annotations

import io
import zipfile
from typing import List

from . import cas_parser, tradebook_parser, db, portfolio

JUNK_MARKERS = ("__MACOSX", ".DS_Store")


def _is_junk(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return name.endswith("/") or base.startswith(".") or any(m in name for m in JUNK_MARKERS)


def process_cas(filename: str, file_bytes: bytes) -> dict:
    result = cas_parser.parse_everything(file_bytes)
    if result["error"]:
        return {"filename": filename, "type": "cas", "ok": False, "error": result["error"]}

    holdings = list(result["holdings"])
    nps = result["nps"]
    if nps and nps.get("value"):
        holdings.append({
            "symbol": None, "name": f"NPS ({nps['tier']})", "isin": None,
            "asset_type": "nps", "market": "IN", "quantity": 1.0,
            "avg_cost": nps["contribution"], "current_price": nps["value"],
            "current_value": nps["value"], "currency": "INR",
            "notes": f"XIRR {nps['xirr']}% · contribution ₹{nps['contribution']:,.2f}",
        })

    batch_id = cas_parser.new_batch_id()
    if holdings:
        db.save_holdings(holdings, source="cas", batch_id=batch_id, label=filename)
    if result["trend_points"]:
        db.save_trend_points(result["trend_points"], batch_id=batch_id)
    if nps:
        db.save_nps_snapshot(nps, batch_id=batch_id)

    return {
        "filename": filename, "type": "cas", "ok": True, "error": None,
        "batch_id": batch_id, "holdings_count": len(holdings),
        "trend_points_found": len(result["trend_points"]), "nps_found": nps is not None,
        "warnings": result["warnings"],
    }


def process_tradebook(filename: str, file_bytes: bytes) -> dict:
    result = tradebook_parser.parse_tradebook_bytes(file_bytes)
    if result["error"]:
        return {"filename": filename, "type": "tradebook", "ok": False, "error": result["error"]}

    batch_id = tradebook_parser.new_batch_id()
    inserted = db.save_transactions(result["transactions"], batch_id=batch_id)

    return {
        "filename": filename, "type": "tradebook", "ok": True, "error": None,
        "batch_id": batch_id, "client_id": result["client_id"], "title": result["title"],
        "transactions_found": len(result["transactions"]), "transactions_inserted": inserted,
        "transactions_skipped_duplicate": len(result["transactions"]) - inserted,
        "warnings": result["warnings"],
    }


def process_single_file(filename: str, file_bytes: bytes) -> dict:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return process_cas(filename, file_bytes)
    if lower.endswith(".xlsx"):
        return process_tradebook(filename, file_bytes)
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "unknown"
    return {"filename": filename, "type": "unsupported", "ok": False,
            "error": f"Unsupported file type (.{ext}). Only .pdf (CAS) and .xlsx (tradebook) "
                     "are supported — this file was skipped."}


def expand_files(filename: str, file_bytes: bytes) -> List[tuple]:
    """Returns [(display_name, bytes), ...] — one entry for a plain file, or
    one entry per supported file found inside a .zip."""
    if filename.lower().endswith(".zip") or zipfile.is_zipfile(io.BytesIO(file_bytes)):
        try:
            zf = zipfile.ZipFile(io.BytesIO(file_bytes))
        except zipfile.BadZipFile:
            return []
        entries = [n for n in zf.namelist() if not _is_junk(n)]
        out = []
        for name in entries:
            try:
                out.append((name.rsplit("/", 1)[-1], zf.read(name)))
            except Exception:
                out.append((name.rsplit("/", 1)[-1], None))
        return out
    return [(filename, file_bytes)]


def reconcile_cost_basis() -> dict:
    """Recomputes FIFO positions over every transaction currently stored
    (across all tradebook uploads ever made) and applies the resulting
    avg_cost to matching holdings by ISIN. Flags holdings where the FIFO
    quantity doesn't match the actual current quantity — meaning the
    uploaded trade history is incomplete for that position — so the applied
    cost basis is understood as a best-effort approximation, not a precise
    figure, in that case."""
    all_txns = db.get_all_transactions()
    positions = tradebook_parser.compute_fifo_positions(all_txns)
    if not positions:
        return {"holdings_cost_basis_updated": 0, "partial_coverage": [], "warnings": []}

    holdings = db.get_all_holdings()
    df = portfolio.holdings_to_dataframe(holdings)

    holdings_updated = 0
    partial_coverage = []
    for isin, pos in positions.items():
        if pos["avg_cost"] is None:
            continue
        holdings_updated += db.update_holding_cost_basis(isin, pos["avg_cost"])
        if not df.empty:
            match = df[df["isin"] == isin]
            if not match.empty:
                actual_qty = match["quantity"].sum()
                if actual_qty and abs(actual_qty - pos["quantity"]) > max(0.5, actual_qty * 0.02):
                    partial_coverage.append({
                        "symbol": pos["symbol"], "isin": isin,
                        "tradebook_quantity": pos["quantity"], "actual_holding_quantity": round(actual_qty, 2),
                    })

    warnings = []
    if partial_coverage:
        names = ", ".join(f"{p['symbol']} (history implies {p['tradebook_quantity']:g}, "
                           f"currently holding {p['actual_holding_quantity']:g})"
                           for p in partial_coverage[:5])
        more = f" and {len(partial_coverage) - 5} more" if len(partial_coverage) > 5 else ""
        warnings.append(
            f"{len(partial_coverage)} holding(s) show a quantity mismatch between what the uploaded "
            f"trade history implies and what's actually held now — meaning trades outside the "
            f"uploaded file(s) (earlier purchases, later sales, bonus/split adjustments, or transfers) "
            f"affected the position: {names}{more}. The computed cost basis is still applied as the "
            f"best available approximation, but upload the missing period(s) for a precise figure."
        )

    return {"holdings_cost_basis_updated": holdings_updated, "partial_coverage": partial_coverage,
            "warnings": warnings}


def process_upload(filename: str, file_bytes: bytes) -> dict:
    """Top-level entry point for POST /api/upload. Handles a single CAS PDF,
    a single tradebook XLSX, or a .zip containing any mix of both — expands
    a zip, processes every supported file inside, then reconciles cost basis
    once across everything that was just added."""
    expanded = expand_files(filename, file_bytes)
    if not expanded:
        return {"files": [], "cost_basis": None, "summary": {
            "total_files": 0, "successful": 0, "failed": 0,
            "error": "This .zip file is empty, corrupted, or contains only hidden/system files."
        }}

    results = []
    for name, content in expanded:
        if content is None:
            results.append({"filename": name, "type": "unknown", "ok": False,
                             "error": "Could not read this entry from the zip file."})
            continue
        results.append(process_single_file(name, content))

    any_tradebook_ok = any(r["ok"] for r in results if r["type"] == "tradebook")
    cost_basis = reconcile_cost_basis() if any_tradebook_ok else None

    return {
        "files": results,
        "cost_basis": cost_basis,
        "summary": {
            "total_files": len(results),
            "successful": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "total_holdings_added": sum(r.get("holdings_count", 0) for r in results if r["ok"]),
            "total_transactions_added": sum(r.get("transactions_inserted", 0) for r in results if r["ok"]),
        },
    }
