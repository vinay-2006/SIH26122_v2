"""DB-free: the schedule generator and the history plan are valid, deterministic and consistent with the rules the services enforce."""
from collections import Counter
from datetime import date, timedelta

import pytest

from backend.v2.schedule_import.csv_parser import parse_csv
from backend.v2.schedule_import.mapping import RefData
from backend.v2.schedule_import.validate import validate
from backend.v2.seed import history_plan as hp, projects_spec as ps
from backend.v2.seed.schedule_gen import is_work, to_csv

ANCHOR = date(2026, 9, 30)
SPECS = {s.code: s for s in ps.all_projects()}


def ref():
    from pathlib import Path
    r = RefData.builtin()
    r.uoms.update({"HR": "MACHINE_TIME", "MH": "EFFORT", "M": "LENGTH", "KM": "LENGTH", "M2": "AREA", "M3": "VOLUME", "TONNE": "MASS", "NOS": "COUNT", "SET": "COUNT", "JOINT": "WELD_JOINT"})
    return r


def test_the_four_projects_are_defined_with_the_expected_shape():
    assert list(SPECS) == ["NNB-CRUDE", "AEC-OFFSHORE", "NRL-EXPANSION", "SMP-PIPE"]
    assert {c: s.lifecycle for c, s in SPECS.items()} == {"NNB-CRUDE": "COMPLETED", "AEC-OFFSHORE": "ONGOING", "NRL-EXPANSION": "ONGOING", "SMP-PIPE": "UPCOMING"}
    for s in SPECS.values():
        assert 30 <= len(s.acts) <= 80 and s.finish > s.start and s.data_date <= s.start + timedelta(days=60)
        assert s.pm in ps.PEOPLE and all(h in ps.PEOPLE for h in s.supervisors + s.engineers)
        assert "CREATE_PROJECT" in ps.PEOPLE[s.pm][1]
    assert SPECS["SMP-PIPE"].start > ANCHOR                                   # D really is in the future


def test_ids_are_unique_and_the_logic_network_is_acyclic_and_ordered():
    for s in SPECS.values():
        ids = [a.id for a in s.acts]
        assert len(ids) == len(set(ids))
        seen = set()
        for a in s.acts:
            assert all(p in seen for p, _, _ in a.preds), f"{s.code} {a.id}: predecessor defined after its successor"
            seen.add(a.id)


def test_schedule_dates_are_consistent_working_days_and_float_is_not_negative():
    for s in SPECS.values():
        for a in s.acts:
            assert is_work(a.start) and is_work(a.finish) and a.finish >= a.start and a.total_float >= 0
            for pid, typ, lag in a.preds:
                p = next(x for x in s.acts if x.id == pid)
                assert a.start >= (p.finish if typ == "FS" else p.start), f"{a.id} starts before its {typ} predecessor {pid}"


@pytest.mark.parametrize("code", list(SPECS))
def test_generated_files_parse_and_validate_with_no_errors_and_no_warnings(code):
    s = SPECS[code]
    acts_csv, res_csv = to_csv(s.root, s.acts)
    parsed = parse_csv(acts_csv, res_csv, s.name)
    parsed.project.data_date = s.data_date
    parsed.project.planned_start, parsed.project.planned_finish = s.start, s.finish
    report = validate(parsed, ref())
    assert report["valid"], report["errors"][:5]
    assert report["warnings"] == [], report["warnings"][:5]
    assert len(parsed.activities) == len(s.acts)


def test_generation_is_byte_for_byte_deterministic():
    a = {c: to_csv(s.root, s.acts) for c, s in ((s.code, s) for s in ps.all_projects())}
    b = {c: to_csv(s.root, s.acts) for c, s in ((s.code, s) for s in ps.all_projects())}
    assert a == b
    assert hp.build_plan(ps.project_c(), ANCHOR).steps == hp.build_plan(ps.project_c(), ANCHOR).steps


def test_units_are_never_mixed_within_a_resource():
    for s in SPECS.values():
        units = {}
        for a in s.acts:
            for code, _, cls, qty, unit in a.res:
                assert qty > 0
                assert units.setdefault(code, unit) == unit, f"{s.code}: resource {code} used with two units"
    meas_units = {r[4] for a in SPECS["NNB-CRUDE"].acts for r in a.res if r[2] == "Material"}
    assert {"km", "joints", "m3", "tonne", "m", "nos"} <= meas_units         # pipe length AND weld-joint counts, side by side
    w = next(a for a in SPECS["NNB-CRUDE"].acts if a.id == "NNB-3120")
    assert {r[4] for r in w.res if r[2] == "Material"} == {"joints", "km"}


def test_project_a_plans_every_activity_to_completion_with_valid_claims():
    pl = hp.build_plan(SPECS["NNB-CRUDE"], ANCHOR)
    assert set(pl.state.values()) == {"COMPLETE"}
    by = {}
    for s in pl.steps:
        by.setdefault(s.act_id, []).append(s)
    assert set(by) == {a.id for a in SPECS["NNB-CRUDE"].acts}
    for aid, steps in by.items():
        assert steps[-1].final and steps[-1].fraction == 1.0
        assert all(s.on < ANCHOR for s in steps)
        for x, y in zip(steps, steps[1:]):
            assert y.on > x.on
            for code in y.qty:
                assert y.qty[code] > x.qty[code]
        if steps[0].mode != "DATES":
            assert steps[0].start is not None and steps[0].start <= steps[0].on
        if steps[-1].mode != "DATES":
            assert steps[-1].finish == steps[-1].on


def test_ongoing_plans_follow_the_logic_network_and_never_look_into_the_future():
    for code in ("AEC-OFFSHORE", "NRL-EXPANSION"):
        s = SPECS[code]
        pl = hp.build_plan(s, ANCHOR)
        assert all(st.on <= ANCHOR - timedelta(days=1) for st in pl.steps)
        for a in s.acts:
            if pl.state[a.id] != "NONE":
                for pid, typ, _ in a.preds:
                    assert pl.state[pid] == "COMPLETE" if typ == "FS" else pl.state[pid] != "NONE", f"{code} {a.id} progressed before its predecessor {pid}"
        assert Counter(pl.state.values()).keys() == {"COMPLETE", "PARTIAL", "NONE"}
        for aid in {st.act_id for st in pl.steps}:
            ss = [st for st in pl.steps if st.act_id == aid]
            assert [x.seq for x in ss] == list(range(1, len(ss) + 1))


def test_dispositions_cover_the_workflows_each_ongoing_project_must_show():
    b = Counter(s.disposition for s in hp.build_plan(SPECS["AEC-OFFSHORE"], ANCHOR).steps)
    c = Counter(s.disposition for s in hp.build_plan(SPECS["NRL-EXPANSION"], ANCHOR).steps)
    for need in ("PENDING", "EDIT", "REJECT_CORRECT", "REJECT_ONLY", "REJECT_CORRECT_PENDING", "HOLD_OPEN", "HOLD_LOOP"):
        assert b[need] >= 1 and c[need] >= 1, need
    assert c["APPROVE_PCT"] == 3 and c["HOLD_ANSWERED"] >= 1 and c["HOLD_LOOP"] >= 2
    assert any(s.overrun > 1.10 for s in hp.build_plan(SPECS["NRL-EXPANSION"], ANCHOR).steps)          # an over-tolerance approval with acknowledgement
    assert all(s.overrun <= 1.10 for s in hp.build_plan(SPECS["AEC-OFFSHORE"], ANCHOR).steps)
    assert len(hp.build_plan(SPECS["AEC-OFFSHORE"], ANCHOR).withdrawals) == 2 and len(hp.build_plan(SPECS["NRL-EXPANSION"], ANCHOR).withdrawals) == 2


def test_project_d_has_no_plan_at_all():
    pl = hp.build_plan(SPECS["SMP-PIPE"], ANCHOR)
    assert pl.steps == [] and pl.issues == [] and pl.withdrawals == [] and set(pl.state.values()) == {"NONE"}


def test_issue_plans_reference_real_activities_in_a_state_that_makes_sense():
    for code in ("NNB-CRUDE", "AEC-OFFSHORE", "NRL-EXPANSION"):
        pl = hp.build_plan(SPECS[code], ANCHOR)       # build_issues raises for unknown activities or activities with no progress
        assert pl.issues and all(i.reported_on <= ANCHOR for i in pl.issues)
        for i in pl.issues:
            if i.resolved_days is not None:
                assert i.reported_on + timedelta(days=i.resolved_days) <= ANCHOR
        rc_keys = {r.key for r in pl.root_causes}
        assert {i.root_cause for i in pl.issues if i.root_cause} <= rc_keys
    assert sum(1 for i in hp.build_plan(SPECS["AEC-OFFSHORE"], ANCHOR).issues if i.stage and i.blocks and i.resolved_days is None) == 1
    assert sum(1 for i in hp.build_plan(SPECS["NNB-CRUDE"], ANCHOR).issues if i.resolved_days is None) == 0


def test_user_and_project_ids_are_fixed_by_name():
    assert ps.user_id("anita.bora") == ps.user_id("anita.bora") and ps.user_id("anita.bora") != ps.user_id("rohit.menon")
    assert str(ps.project_uuid("NNB-CRUDE")) == str(ps.project_uuid("NNB-CRUDE"))
    assert all(ps.email(h).endswith("@seed.setuai.local") for h in ps.PEOPLE)
