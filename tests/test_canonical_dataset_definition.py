"""DB-free checks of the canonical dataset definition (derivations from sample_data/, no I/O against a database)."""
from backend.canonical import dataset as ds
from backend.shared.schedule import parse_schedule_csv


def test_canonical_schedule_parses_with_full_relationship_coverage():
    r = parse_schedule_csv(ds.schedule_csv_text(), "X")
    assert r.is_valid and not r.errors
    assert len(r.activities) == 45 and len(r.dependencies) == 34
    kinds = {d.relationship_type for d in r.dependencies}
    assert kinds == {"FS", "SS", "FF", "SF"}
    lags = {d.lag_days for d in r.dependencies}
    assert min(lags) < 0 < max(lags)


def test_rev_a_is_a_documented_derivation_of_rev_b():
    a = parse_schedule_csv(ds.rev_a_csv_text(), "A")
    b = parse_schedule_csv(ds.schedule_csv_text(), "B")
    assert a.is_valid
    ids_a, ids_b = {x.activity_id for x in a.activities}, {x.activity_id for x in b.activities}
    assert ids_b - ids_a == set(ds.REV_A_CHANGES["drop_activities"]) and not ids_a - ids_b
    assert {x.activity_id: x for x in a.activities}["CIV-PS3-FND-003"].planned_start != {x.activity_id: x for x in b.activities}["CIV-PS3-FND-003"].planned_start


def test_stage_weights_sum_to_exactly_100_and_cover_every_activity():
    w = ds.stage_weights()
    assert round(sum(w.values()), 2) == 100.0 and list(w) == ds.DISCIPLINE_STAGE_ORDER
    assert all(ds.stage_of(r) in w for r in ds.schedule_rows())


def test_every_activity_has_a_gate_and_safety_critical_means_hold():
    m = ds.activity_master()
    for r in ds.schedule_rows():
        aid = r["L6 Task ID"]
        g = ds.gate_spec(aid, m[aid])
        assert g["gate_name"]
        if m[aid]["Safety_Critical"] == "Yes":
            assert g["checkpoint_category"] == "HOLD"
    assert ds.gate_spec("CIV-PS3-FND-002", m["CIV-PS3-FND-002"])["checkpoint_category"] == "HOLD"


def test_work_packages_cover_every_stage_and_reference_known_contractors():
    codes = {c[0] for c in ds.CONTRACTORS}
    assert {w[3] for w in ds.WORK_PACKAGES} == set(ds.DISCIPLINE_STAGE_ORDER)
    assert all(w[2] in codes for w in ds.WORK_PACKAGES)


def test_all_seven_required_roles_have_identities():
    roles = {v[1] for v in ds.IDENTITIES.values()}
    assert {"OWNER", "PROJECT_MANAGER", "PLANNER", "SUPERVISOR", "SITE_ENGINEER", "QUALITY_INSPECTOR", "AUDITOR"} <= roles
