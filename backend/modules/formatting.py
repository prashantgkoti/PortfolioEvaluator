"""
formatting.py — Indian numbering system (Lakh/Crore) helpers, used
throughout the app instead of the Western thousand/million convention,
since this is an India-first tool (user request: "as elegant and business
standard as possible" — Indian finance apps universally show ₹ in L/Cr,
not M/B).

DECISION (DECISIONS.md #17): chart axes and headline metrics use a single
compact unit (Cr if the series max is ≥1 crore, else L, else plain rupees)
so an entire axis/metric group stays in one consistent unit rather than
switching per-value. Table cells keep full Indian-grouped digits (e.g.
"1,04,11,513") since exact figures matter more than compactness there.
"""
from __future__ import annotations

from typing import Optional

LAKH = 100_000
CRORE = 10_000_000


def indian_grouped_digits(value: float, decimals: int = 0) -> str:
    """Formats a number with Indian digit grouping (last 3 digits, then
    groups of 2): 10411513 -> '1,04,11,513'."""
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    value = abs(value)
    whole = int(value)
    s = str(whole)
    if len(s) <= 3:
        grouped = s
    else:
        last3, rest = s[-3:], s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        grouped = ",".join(parts) + "," + last3
    if decimals > 0:
        grouped += f".{value - whole:.{decimals}f}".split(".")[1]
    return sign + grouped


def format_inr_compact(value: Optional[float], decimals: int = 2) -> str:
    """Headline/metric format: ₹1.04 Cr / ₹10.41 L / ₹8,240."""
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= CRORE:
        return f"{sign}₹{v / CRORE:.{decimals}f} Cr"
    if v >= LAKH:
        return f"{sign}₹{v / LAKH:.{decimals}f} L"
    return f"{sign}₹{indian_grouped_digits(v)}"


def format_inr_tick(value: float) -> str:
    """Chart-axis tick format: walks the actual Indian place-value system
    (units/tens/hundreds/thousands/ten-thousands as plain grouped rupees,
    then Lakh, Ten-Lakh, Crore, Ten-Crore as the value grows) rather than
    forcing one unit across the whole axis. Trailing zeros are trimmed so
    round ticks read as '5 L' rather than '5.00 L'."""
    if value == 0:
        return "₹0"
    sign = "-" if value < 0 else ""
    v = abs(value)

    def trimmed(num: float) -> str:
        s = f"{num:.2f}"
        return s.rstrip("0").rstrip(".") if "." in s else s

    if v >= CRORE:
        return f"{sign}₹{trimmed(v / CRORE)} Cr"
    if v >= LAKH:
        return f"{sign}₹{trimmed(v / LAKH)} L"
    # Below 1 Lakh: plain Indian-grouped rupees — this already covers units,
    # tens, hundreds, thousands, and ten-thousands correctly (e.g. 50,000).
    return f"{sign}₹{indian_grouped_digits(v)}"


def _nice_step(rough: float) -> float:
    """Rounds a raw step size to a clean 1/2/5×10^n value, the standard
    'nice numbers' algorithm for evenly-spaced, human-readable axis ticks."""
    import math
    if rough <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(rough))
    residual = rough / magnitude
    if residual <= 1:
        nice = 1
    elif residual <= 2:
        nice = 2
    elif residual <= 5:
        nice = 5
    else:
        nice = 10
    return nice * magnitude


def indian_axis_ticks(max_value: float, num_ticks: int = 6) -> tuple[list, list]:
    """Generates (tickvals, ticktext) spanning 0..max_value with clean,
    evenly-spaced steps, each labelled through the proper Indian
    hundreds/thousands/lakh/crore progression via format_inr_tick — for use
    with Plotly's tickmode='array' so every tick is correctly Indian-styled
    regardless of the chart's scale, instead of relying on Plotly's default
    Western K/M/B auto-formatting."""
    if max_value <= 0:
        return [0], ["₹0"]
    step = _nice_step(max_value / num_ticks)
    vals = []
    v = 0.0
    while v <= max_value * 1.02:
        vals.append(round(v, 2))
        v += step
    return vals, [format_inr_tick(v) for v in vals]


def format_inr_full(value: Optional[float]) -> str:
    """Table/detail format with full Indian digit grouping: ₹1,04,11,513."""
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}₹{indian_grouped_digits(abs(value))}"


def choose_axis_unit(max_abs_value: float) -> tuple[float, str]:
    """Picks one consistent divisor + label for a whole chart axis."""
    if max_abs_value >= CRORE:
        return CRORE, "₹ Cr"
    if max_abs_value >= LAKH:
        return LAKH, "₹ L"
    return 1.0, "₹"
