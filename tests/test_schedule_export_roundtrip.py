"""Export -> re-import round trip for the canonical schedule (DB-free): CSV and XER carry the same activities and dependency logic."""
import pytest

from backend.canonical import dataset as ds
from backend.shared.schedule import parse_schedule_csv
from backend.shared.schedule_export import export_schedule_csv, export_schedule_xer
from backend.shared.xer_parser import parse_schedule_xer


def rows(parsed):
    return [a.model_dump() for a in parsed.activities], [d.model_dump() for d in parsed.dependencies]


def dep_set(deps):
    return {(d["predecessor_activity_id"], d["successor_activity_id"], d["relationship_type"], round(float(d["lag_days"]), 2)) for d in deps}


@pytest.fixture(scope="module")
def source():
    return rows(parse_schedule_csv(ds.schedule_csv_text(), "SRC"))


def test_csv_round_trip_is_lossless(source):
    acts, deps = source
    out = parse_schedule_csv(export_schedule_csv(acts, deps), "RT")
    assert out.is_valid, out.errors
    a2, d2 = rows(out)
    key = lambda r: r["activity_id"]
    for x, y in zip(sorted(acts, key=key), sorted(a2, key=key)):
        for f in ("activity_id", "activity_name", "wbs_code", "discipline", "location", "planned_start", "planned_finish", "total_float", "is_critical", "planned_quantity"):
            assert x[f] == y[f], (x["activity_id"], f, x[f], y[f])
    assert dep_set(deps) == dep_set(d2) and len(d2) == 34


def test_xer_round_trip_keeps_logic_dates_and_disciplines(source):
    acts, deps = source
    xer = export_schedule_xer(acts, deps, project_short_name="SIH26122_NFU", data_date="2026-08-10")
    out = parse_schedule_xer(xer.encode(), "RT")
    assert out.is_valid, out.errors
    a2, d2 = rows(out)
    assert len(a2) == 45
    key = lambda r: r["activity_id"]
    for x, y in zip(sorted(acts, key=key), sorted(a2, key=key)):
        for f in ("activity_id", "activity_name", "wbs_code", "discipline", "planned_start", "planned_finish", "total_float", "is_critical"):
            assert x[f] == y[f], (x["activity_id"], f, x[f], y[f])
    assert dep_set(deps) == dep_set(d2)
    assert {d["relationship_type"] for d in d2} == {"FS", "SS", "FF", "SF"}


def test_export_is_deterministic(source):
    acts, deps = source
    assert export_schedule_csv(acts, deps) == export_schedule_csv(list(reversed(acts)), list(reversed(deps)))
