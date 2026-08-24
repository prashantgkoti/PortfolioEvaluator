"""
xirr_calc.py — Computes XIRR (Extended Internal Rate of Return) from actual
transaction cash flows, plus a plain absolute-return-% companion metric for
positions held less than a year (see module docstring below for why both
are shown).

============================================================================
THE FORMULA
============================================================================
XIRR finds the single annualized rate r that makes the net present value of
every cash flow — each on its own real date, not assumed evenly spaced —
equal to zero:

    0 = Σ CFᵢ / (1 + r)^((dᵢ − d₀) / 365)

  CFᵢ  = cash flow i (negative = money out, e.g. a buy; positive = money
         in, e.g. a sell, or a notional "sell at today's price" for
         anything still held)
  dᵢ   = the actual date of cash flow i
  d₀   = the date of the first cash flow (the anchor)

There is no closed-form solution — r is found iteratively. This module
uses Newton-Raphson (the same method Excel's own XIRR() uses internally),
with a bisection fallback for cases where Newton-Raphson fails to converge
(flat/near-flat NPV curves, poor initial guesses, or cash-flow patterns
with no real root in a sane range).

Verified against Microsoft's own published XIRR example (5 cash flows,
documented expected result ≈37.34%) before being used on any real data —
see the test in the accompanying conversation, not just asserted here.

============================================================================
WHY BOTH XIRR AND A PLAIN ABSOLUTE RETURN %
============================================================================
XIRR is mathematically valid for any holding period, including days — it's
a common misconception that it "needs" a year of history. What actually
happens for short holdings is that XIRR *annualizes* the return, which can
look extreme: a position up 2% in 10 days annualizes to roughly +1000%,
which is correct math but not a meaningful number to act on. Rather than
avoid XIRR for short holdings, this module computes a plain, un-annualized
absolute return % (total gain ÷ total invested) alongside it for every
position, so short-duration holdings have an intuitive figure available
too, not just an inflated annualized one.
"""
from __future__ import annotations

import datetime as dt
from typing import List, Optional, Tuple

CashFlow = Tuple[dt.date, float]


def _npv(rate: float, cashflows: List[CashFlow], d0: dt.date) -> float:
    total = 0.0
    for date, amount in cashflows:
        years = (date - d0).days / 365.0
        try:
            total += amount / ((1 + rate) ** years)
        except OverflowError:
            # A runaway rate estimate during iteration (Newton-Raphson can
            # briefly diverge to an extreme value before correcting, or
            # never correct for ill-conditioned cash-flow patterns) — treat
            # as an enormous NPV in the direction the amount's sign implies,
            # so the solver is pushed away from this rate rather than
            # crashing on it.
            total += float("inf") if amount > 0 else float("-inf")
    return total


def _npv_derivative(rate: float, cashflows: List[CashFlow], d0: dt.date) -> float:
    total = 0.0
    for date, amount in cashflows:
        years = (date - d0).days / 365.0
        if years == 0:
            continue
        try:
            total += -years * amount / ((1 + rate) ** (years + 1))
        except OverflowError:
            total += 0.0  # contributes nothing usable at this extreme rate; let other terms dominate
    return total


def xirr(cashflows: List[CashFlow], guess: float = 0.15) -> Optional[float]:
    """Returns the annualized rate as a decimal (0.15 = 15%), or None if no
    solution could be found (e.g. all cash flows are the same sign — there's
    no rate that makes an all-negative or all-positive series NPV to zero).
    Requires at least 2 cash flows with at least one negative and one
    positive value."""
    if len(cashflows) < 2:
        return None
    amounts = [c[1] for c in cashflows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        return None

    cashflows_sorted = sorted(cashflows, key=lambda c: c[0])
    d0 = cashflows_sorted[0][0]

    # Newton-Raphson
    rate = guess
    for _ in range(100):
        npv = _npv(rate, cashflows_sorted, d0)
        if npv in (float("inf"), float("-inf")) or npv != npv:  # inf or NaN
            break  # abandon Newton-Raphson, fall through to bisection below
        if abs(npv) < 1e-6:
            return rate
        deriv = _npv_derivative(rate, cashflows_sorted, d0)
        if deriv == 0:
            break
        new_rate = rate - npv / deriv
        if new_rate <= -0.999:  # rate can't go below -100%
            new_rate = (rate - 0.999) / 2
        new_rate = max(-0.999, min(new_rate, 1000))  # clamp to a sane range so a
        rate = new_rate                              # bad step can't diverge to overflow

    # Bisection fallback — search a wide, sane bracket for a sign change
    lo, hi = -0.999, 10.0
    npv_lo, npv_hi = _npv(lo, cashflows_sorted, d0), _npv(hi, cashflows_sorted, d0)
    if npv_lo * npv_hi > 0:
        return None  # no sign change in bracket -> no root found
    for _ in range(200):
        mid = (lo + hi) / 2
        npv_mid = _npv(mid, cashflows_sorted, d0)
        if abs(npv_mid) < 1e-6:
            return mid
        if npv_lo * npv_mid < 0:
            hi, npv_hi = mid, npv_mid
        else:
            lo, npv_lo = mid, npv_mid
    return (lo + hi) / 2


def build_position_cashflows(transactions: List[dict], current_value: Optional[float],
                              as_of: Optional[dt.date] = None) -> List[CashFlow]:
    """Builds the cash-flow series for one position: each buy as a negative
    flow, each sell as a positive flow, plus (if still holding anything) a
    final notional positive flow of current_value as of today — as if the
    position were liquidated right now. If current_value is None (no live
    price available and the position is still open), no final flow is
    added and XIRR will reflect realized cash flows only, which understates
    true return for an open position — callers should treat that case's
    XIRR as a lower bound, not a precise figure.

    Uses each transaction's `matched_quantity` (from
    tradebook_parser.compute_fifo_positions), not its raw quantity — a sell
    that exceeds what FIFO actually had available to match (confirmed on
    real data: several same-day sells summing to more than any known prior
    buy, meaning the shares predate the uploaded trade history) would
    otherwise be counted as a real cash inflow for shares this app never
    saw a purchase for, distorting the cash-flow timeline."""
    as_of = as_of or dt.date.today()
    flows: List[CashFlow] = []
    for t in transactions:
        try:
            date = dt.datetime.strptime(t["trade_date"][:10], "%Y-%m-%d").date()
        except (ValueError, KeyError, TypeError):
            continue
        qty = t.get("matched_quantity", t["quantity"])
        if qty <= 1e-9:
            continue
        amount = qty * t["price"]
        flows.append((date, -amount if t["trade_type"] == "buy" else amount))
    if current_value is not None and current_value > 0:
        flows.append((as_of, current_value))
    return flows


def absolute_return_pct(transactions: List[dict], current_value: Optional[float]) -> Optional[dict]:
    """Plain, un-annualized return: (money back − money in) / money in.
    Meaningful regardless of holding period length, and doesn't distort
    short holdings the way an annualized figure can. Also uses
    matched_quantity — see build_position_cashflows for why."""
    invested = sum(t.get("matched_quantity", t["quantity"]) * t["price"]
                    for t in transactions if t["trade_type"] == "buy")
    realized = sum(t.get("matched_quantity", t["quantity"]) * t["price"]
                    for t in transactions if t["trade_type"] == "sell")
    total_back = realized + (current_value or 0)
    if invested == 0:
        return None
    return {
        "invested": round(invested, 2), "realized": round(realized, 2),
        "current_value": round(current_value, 2) if current_value else 0.0,
        "total_back": round(total_back, 2),
        "return_pct": round((total_back - invested) / invested * 100, 2),
    }
