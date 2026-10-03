"""Pure arithmetic: no database."""
from datetime import date
from decimal import Decimal as D

import pytest

from backend.v2.domain import progress_math as m

KM, METRE, TONNE, KG, M3 = (m.Unit("KM", "LENGTH", D(1000)), m.Unit("M", "LENGTH", D(1)), m.Unit("TONNE", "MASS", D(1000)), m.Unit("KG", "MASS", D(1)), m.Unit("M3", "VOLUME", D(1)))


def test_conversion_only_inside_one_unit_kind():
    assert m.convert(2500, METRE, KM) == D("2.500") and m.convert(3, KM, METRE) == D("3000.000") and m.convert(1.5, TONNE, KG) == D("1500.000")
    with pytest.raises(m.UnitMismatch):
        m.convert(10, TONNE, M3)
    with pytest.raises(m.UnitMismatch):
        m.convert(10, KM, KG)


def test_weighted_activity_percentage_ignores_nothing_it_is_given_and_never_mixes_units():
    # 5 of 10 km (weight .6) and 50 of 200 joints (weight .4): units never added, only percentages
    assert m.weighted_pct([(D("0.6"), D(5), D(10)), (D("0.4"), D(50), D(200))]) == D("40.000")
    assert m.weighted_pct([(1, 0, 10)]) == 0 and m.weighted_pct([]) == 0 and m.weighted_pct([(0, 5, 10)]) == 0     # zero weight is 0, never NaN


def test_the_percentage_caps_at_100_but_the_quantity_is_never_clamped():
    assert m.capped_pct(600, 500) == 100 and m.pct_of(600, 500) == D(120)
    assert m.weighted_pct([(1, D(600), D(500))]) == D("100.000")
    pct, over, beyond = m.overrun(600, 500, 10)
    assert (pct, over, beyond) == (D("20.000"), True, True)


@pytest.mark.parametrize("cum,over,beyond", [(500, False, False), (550, True, False), (550.001, True, True), (499, False, False), (540, True, False)])
def test_overrun_flags_and_the_tolerance_boundary(cum, over, beyond):
    _, o, b = m.overrun(D(str(cum)), 500, 10)
    assert (o, b) == (over, beyond)


def test_apply_percentage_is_explicit_and_rounded_to_three_places():
    assert m.apply_percentage(480, 50) == D("240.000") and m.apply_percentage(2000, D("33.3333")) == D("666.666") and m.apply_percentage(10, 100) == D("10.000")


@pytest.mark.parametrize("asof,expected", [(date(2026, 1, 1), 10), (date(2026, 1, 5), 50), (date(2026, 1, 10), 100), (date(2025, 12, 1), 0), (date(2026, 3, 1), 100)])
def test_linear_planned_progress(asof, expected):
    assert m.planned_linear(date(2026, 1, 1), date(2026, 1, 10), asof) == D(expected)


def test_a_milestone_jumps_at_its_finish():
    assert m.planned_linear(date(2026, 1, 10), date(2026, 1, 10), date(2026, 1, 9), milestone=True) == 0
    assert m.planned_linear(date(2026, 1, 10), date(2026, 1, 10), date(2026, 1, 10), milestone=True) == 100


def test_spi_is_none_until_something_is_planned_and_is_a_plain_ratio():
    assert m.spi_approx(10, 0) is None and m.spi_approx(40, 50) == D("0.800") and m.spi_approx(0, 20) == D("0.000")


def test_weighted_mean_handles_zero_total_weight():
    assert m.weighted_mean([(1, 100), (3, 0)]) == D("25.000") and m.weighted_mean([(0, 50)]) == 0
