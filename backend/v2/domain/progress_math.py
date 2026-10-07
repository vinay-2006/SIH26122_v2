"""Pure arithmetic for quantities and progress (no database). Decimal throughout; 3-decimal rounding matches the SQL views (round half up).

Rules encoded here (and mirrored by database constraints):
* a quantity converts only INSIDE one unit kind (m <-> km, kg <-> tonne); across kinds it is an error, never a guess
* over-baseline quantities are never clamped; only the PERCENTAGE caps at 100
* activity % = weighted mean of per-assignment min(100, cumulative / baseline * 100) over MEASURED assignments only"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterable, List, Optional, Tuple

Q3 = Decimal("0.001")
D = lambda x: x if isinstance(x, Decimal) else Decimal(str(x))


def q3(x) -> Decimal:
    return D(x).quantize(Q3, rounding=ROUND_HALF_UP)


class UnitMismatch(ValueError):
    pass


@dataclass(frozen=True)
class Unit:
    code: str
    dimension: str
    factor: Decimal            # to the dimension's base unit


def convert(qty, from_unit: Unit, to_unit: Unit) -> Decimal:
    if from_unit.dimension != to_unit.dimension:
        raise UnitMismatch(f"{from_unit.code} ({from_unit.dimension}) cannot be expressed in {to_unit.code} ({to_unit.dimension})")
    return q3(D(qty) * from_unit.factor / to_unit.factor)


def pct_of(cumulative, baseline) -> Decimal:
    """uncapped percentage of a baseline quantity (kept for overrun display); the dashboard percentage uses min(100, ...)"""
    return D(cumulative) / D(baseline) * 100


def capped_pct(cumulative, baseline) -> Decimal:
    return min(Decimal(100), pct_of(cumulative, baseline))


def weighted_pct(items: Iterable[Tuple[Decimal, Decimal, Decimal]]) -> Decimal:
    """items: (progress_weight, cumulative, baseline) of MEASURED assignments -> activity % (3 dp)"""
    items = list(items)
    tw = sum((D(w) for w, _, _ in items), Decimal(0))
    if tw <= 0:
        return Decimal(0)
    return q3(sum(D(w) * capped_pct(c, b) for w, c, b in items) / tw)


def overrun(cumulative, baseline, tolerance_pct) -> Tuple[Decimal, bool, bool]:
    """-> (overrun_pct above baseline, over_baseline, beyond_tolerance). 3 dp. Any excess is flagged; only beyond tolerance needs acknowledgement."""
    c, b = D(cumulative), D(baseline)
    over = c > b
    pct = q3((c / b - 1) * 100) if over else Decimal(0)
    return pct, over, c > b * (1 + D(tolerance_pct) / 100)


def apply_percentage(baseline, pct) -> Decimal:
    """APPLY_PCT_TO_ASSIGNMENTS: the quantity that corresponds to `pct` of one assignment's baseline (explicit and recorded, never implicit)"""
    return q3(D(baseline) * D(pct) / 100)


def planned_linear(start: date, finish: date, as_of: date, milestone: bool = False) -> Decimal:
    """APPROXIMATION: planned % of one activity at a date, linear between baseline start and finish (a milestone jumps at its finish)."""
    if milestone:
        return Decimal(100) if as_of >= finish else Decimal(0)
    span = (finish - start).days + 1
    frac = Decimal((as_of - start).days + 1) / Decimal(span)
    return q3(100 * min(Decimal(1), max(Decimal(0), frac)))


def weighted_mean(pairs: Iterable[Tuple[Decimal, Decimal]]) -> Decimal:
    """(weight, value) -> weighted mean (3 dp); 0 when the total weight is 0 (never NaN)"""
    pairs = list(pairs)
    tw = sum((D(w) for w, _ in pairs), Decimal(0))
    return q3(sum(D(w) * D(v) for w, v in pairs) / tw) if tw > 0 else Decimal(0)


def spi_approx(actual, planned) -> Optional[Decimal]:
    """actual / planned, or None while nothing is planned yet. NOT cost-based earned value."""
    return q3(D(actual) / D(planned)) if D(planned) > 0 else None
