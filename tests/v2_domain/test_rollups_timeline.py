"""Rollups, the as-of functions, the timeline and the revision flow. Numbers are derived from raw ledger rows by an independent recomputation."""
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from backend.v2.domain import rollups, timeline as tl
from backend.v2.errors import ApiError
from domainkit import TODAY, assert_consistent
from v2api import connect, ledger_fingerprint, seed_progress
from test_api_revision_reconciliation import P, resolve_all, stage_rev
from test_issues_service import raises

FAR = date(2100, 1, 1)


def active_version(kit):
    with connect() as c:
        return c.execute("select version_id from schedule_versions where project_id=%s and status='ACTIVE'", (kit.project,)).fetchone()["version_id"]


def progress(kit, ext, quantities, **kw):
    return seed_progress(kit.project, ext, quantities, kit.world.sup, kit.world.se, **kw)


def test_the_view_and_the_as_of_function_agree_for_every_activity_and_the_project(kit):
    kit.approve(kit.submit("A2010", 500)["claim_id"])
    progress(kit, "A1000", {"ROW_SURVEY_KM": 24}); progress(kit, "A2000", {"PIPE_STRUNG_KM": 12})
    v = active_version(kit)
    with connect() as c:
        view = {r["activity_uid"]: r for r in c.execute("select * from v_activity_progress where version_id=%s", (v,)).fetchall()}
        fn = {r["activity_uid"]: r for r in c.execute("select * from activity_progress_as_of(%s,%s)", (v, FAR)).fetchall()}
        assert view.keys() == fn.keys() and len(fn) > 10
        for u, r in view.items():
            for col in ("physical_pct", "weight", "weight_basis", "execution_state", "actual_start", "actual_finish", "any_overrun"):
                assert r[col] == fn[u][col], (r["external_activity_id"], col)
        pv = c.execute("select * from v_project_progress where version_id=%s", (v,)).fetchone()
        pf = c.execute("select * from project_progress_as_of(%s,%s)", (v, FAR)).fetchone()
        for col in ("physical_pct", "activities", "completed", "in_progress", "not_started", "weight_basis", "any_overrun"):
            assert pv[col] == pf[col], col
    assert_consistent(kit)


def test_a_project_with_no_execution_history_is_zero_everywhere(kit):
    s = rollups.project_summary(kit.pm)
    assert s["physical_pct"] == 0 and s["activities"]["completed"] == 0 and s["activities"]["in_progress"] == 0 and s["activities"]["not_started"] == s["activities"]["total"] > 0
    assert all(r["physical_pct"] == 0 for r in rollups.activity_progress(kit.pm, limit=2000))
    assert all(r["physical_pct"] == 0 for r in rollups.wbs_progress(kit.pm)) and all(r["physical_pct"] == 0 for r in rollups.discipline_progress(kit.pm))
    assert all(p["physical_pct"] == 0 for p in rollups.timeline(kit.pm, step_days=30)["points"])
    assert kit.ledger() == ([], [])


def test_planned_progress_is_the_documented_linear_approximation_and_spi_is_labelled(kit):
    v = active_version(kit)
    with connect() as c:
        a = c.execute("select baseline_start s, baseline_finish f, activity_uid u from baseline_activities where version_id=%s and external_activity_id='A2010'", (v,)).fetchone()
        mid = a["s"] + (a["f"] - a["s"]) / 2
        at = lambda d: c.execute("select planned_pct from activity_progress_as_of(%s,%s) where activity_uid=%s", (v, d, a["u"])).fetchone()["planned_pct"]
        assert at(a["s"] - timedelta(days=1)) == 0 and at(a["f"]) == 100 and at(a["f"] + timedelta(days=50)) == 100
        n = (a["f"] - a["s"]).days
        k = max(1, n // 2)
        assert abs(at(a["s"] + timedelta(days=k)) - D(100 * k) / D(n)) < D("5")      # linear in calendar days, up to the documented rounding
        assert 0 < at(a["s"] + timedelta(days=max(1, n // 2))) < 100
        early = c.execute("select * from project_progress_as_of(%s,%s)", (v, a["s"] - timedelta(days=4000))).fetchone()
    assert early["planned_pct"] == 0 and early["spi_approx"] is None              # no division by zero: SPI is undefined before anything is planned
    s = rollups.project_summary(kit.pm, as_of=a["f"])
    assert "APPROXIMATION" in s["planned_method_note"] and "not a cost-based earned-value index" in s["spi_note"] and s["data_date"] is not None and s["as_of"] == a["f"]


def test_spi_is_actual_over_approximate_planned(kit):
    progress(kit, "A1000", {"ROW_SURVEY_KM": 24})
    s = rollups.project_summary(kit.pm, as_of=FAR)
    assert s["planned_pct"] == 100 and s["physical_pct"] > 0
    assert abs(s["spi_approx"] - s["physical_pct"] / s["planned_pct"]) < D("0.01")


def test_as_of_respects_the_dates_on_the_ledger(kit):
    r1 = kit.approve(kit.submit("A2010", 200, days_ago=20)["claim_id"])
    r2 = kit.approve(kit.submit("A2010", 600, days_ago=5)["claim_id"])
    v = active_version(kit)
    with connect() as c:
        pct = lambda d: c.execute("select physical_pct p from activity_progress_as_of(%s,%s) where external_activity_id='A2010'", (v, d)).fetchone()["p"]
        assert pct(TODAY - timedelta(days=30)) == 0
        assert pct(TODAY - timedelta(days=10)) == D("10.000") and pct(TODAY) == D("30.000")
    assert r1["status"] == r2["status"] == "APPROVED" and kit.pct("A2010") == D("30.000")


def test_wbs_and_discipline_rollups_are_weighted_means_of_their_activities(kit):
    progress(kit, "A1000", {"ROW_SURVEY_KM": 24}, finish=True); progress(kit, "A1010", {"CLEARED_ROW_KM": 12})
    acts = rollups.activity_progress(kit.pm, limit=2000)
    for d in rollups.discipline_progress(kit.pm):
        mine = [a for a in acts if a["discipline_code"] == d["discipline_code"]]
        sw = sum(a["weight"] for a in mine)
        want = (sum(a["weight"] * a["physical_pct"] for a in mine) / sw).quantize(D("0.001")) if sw else D(0)
        assert d["activities"] == len(mine) and d["physical_pct"] == want, d["discipline_code"]
    wbs = rollups.wbs_progress(kit.pm)
    root = min(wbs, key=lambda r: r["level"])
    assert root["activities"] == len(acts) and root["physical_pct"] == rollups.project_summary(kit.pm)["physical_pct"]
    assert {s["node_type"] for s in rollups.stage_progress(kit.pm)} == {"STAGE"}
    assert [a["external_activity_id"] for a in rollups.activity_progress(kit.pm, state="COMPLETED")] == ["A1000"]
    civil = rollups.activity_progress(kit.pm, discipline=acts[0]["discipline_code"])
    assert civil and {a["discipline_code"] for a in civil} == {acts[0]["discipline_code"]}


def test_weight_basis_is_one_scale_per_version_and_explained(kit):
    s = rollups.project_summary(kit.pm)
    assert s["weight_basis"] in rollups.WEIGHT_BASIS_EXPLANATION and s["weight_basis_explanation"] == rollups.WEIGHT_BASIS_EXPLANATION[s["weight_basis"]]
    assert {a["weight_basis"] for a in rollups.activity_progress(kit.pm, limit=2000)} == {s["weight_basis"]}


def test_summary_claims_are_aggregate_for_the_pm_and_own_for_the_engineer(kit):
    kit.submit("A2010", 100)
    pm, se, sup = (rollups.project_summary(a)["claims"] for a in (kit.pm, kit.se, kit.sup))
    assert pm["scope"] == "aggregate" and sup["scope"] == "aggregate" and se["scope"] == "own"
    flat = repr(pm)
    assert "A2010" not in flat and "raw_claim_text" not in flat and "claim_id" not in flat


def test_timeline_points_ranges_and_limits(kit):
    t = rollups.timeline(kit.pm, step_days=14)
    pts = t["points"]
    assert len(pts) > 3 and pts[0]["as_of"] < pts[-1]["as_of"] and all(b["as_of"] - a["as_of"] == timedelta(days=14) for a, b in zip(pts, pts[1:]))
    assert pts[0]["planned_pct"] <= pts[-1]["planned_pct"] and "APPROXIMATION" in t["planned_method_note"]
    with raises("BAD_RANGE", 422):
        rollups.timeline(kit.pm, date_from=date(2030, 1, 2), date_to=date(2030, 1, 1))
    with raises("TOO_MANY_POINTS", 422):
        rollups.timeline(kit.pm, date_from=date(2000, 1, 1), date_to=date(2030, 1, 1), step_days=1)
    assert len(rollups.timeline(kit.pm, date_from=date(2000, 1, 1), date_to=date(2030, 1, 1), step_days=30)["points"]) > 300


def test_an_unknown_version_is_not_found(kit):
    import uuid
    with raises("VERSION_NOT_FOUND", 404):
        rollups.wbs_progress(kit.pm, version_id=uuid.uuid4())


# ---------------------------------------------------------------------------------------------- per-activity timeline, role filtered
def test_the_activity_timeline_is_role_filtered(kit):
    mine = kit.submit("A2010", 300, text="my welds")
    other = kit.submit("A2010", 100, text="their welds", by=kit.se2)
    kit.approve(mine["claim_id"])
    u = kit.uid("A2010")
    sup, se, se2, pm = (tl.activity_timeline(a, u) for a in (kit.sup, kit.se, kit.se2, kit.pm))
    assert {c["event_id"] for c in sup["claims"]} == {mine["claim_id"], other["claim_id"]} and len(sup["decisions"]) == 1
    assert {c["event_id"] for c in se["claims"]} == {mine["claim_id"]} and {c["event_id"] for c in se2["claims"]} == {other["claim_id"]} and se2["decisions"] == []
    assert pm["claims"] == [] and pm["decisions"] == [] and "aggregate" in pm["claims_note"]
    assert len(pm["quantity_entries"]) == len(sup["quantity_entries"]) == 1 and pm["versions"] and "raw_claim_text" not in repr(pm)
    with raises("ACTIVITY_NOT_FOUND", 404):
        import uuid
        tl.activity_timeline(kit.pm, uuid.uuid4())


def test_the_audit_trail_is_readable_by_supervisor_and_pm_only_and_its_chain_verifies(kit):
    kit.approve(kit.submit("A2010", 100)["claim_id"])
    for a in (kit.sup, kit.pm):
        assert tl.audit_trail(a, entity_type="PLANNER_DECISION") and tl.verify_audit_chain(a)["valid"] is True
    with raises("PERMISSION_DENIED", 403):
        tl.audit_trail(kit.se)
    with raises("PERMISSION_DENIED", 403):
        tl.verify_audit_chain(kit.se)


# ---------------------------------------------------------------------------------------------- revisions never transfer progress
@pytest.fixture
def revised(kit, api):
    w = kit.world
    ids = {ext: progress(kit, ext, q) for ext, q in {"A1000": {"ROW_SURVEY_KM": 24}, "A1010": {"CLEARED_ROW_KM": 12}, "A1020": {"CONCRETE_M3": 240},
                                                      "A2000": {"PIPE_STRUNG_KM": 12}, "A2030": {"BACKFILL_M3": 2000}, "A3000": {"TEST_SECTIONS": 1}}.items()}
    w.v1, w.uid, w.before = active_version(kit), ids, ledger_fingerprint()
    imp = stage_rev(api, w)
    resolve_all(api, w, imp)
    v2 = api.post(f"{P}/{w.project}/schedule-imports/{imp['import_id']}/build", w.pm).json()
    assert api.post(f"{P}/{w.project}/schedule-versions/{v2['version_id']}/activate", w.pm, json={"reason": "Client approved revision 1"}).status_code == 200
    kit.v1, kit.v2, kit.ids = w.v1, v2["version_id"], ids
    return kit


def test_split_merge_and_retire_never_move_progress_automatically(revised):
    k = revised
    assert ledger_fingerprint() == k.world.before                                      # activation did not touch the ledgers
    with connect() as c:
        new = {r["external_activity_id"]: r for r in c.execute("select * from activity_progress_as_of(%s,%s)", (k.v2, FAR)).fetchall()}
    assert all(new[e]["physical_pct"] == 0 for e in ("A2000-1", "A2000-2", "A3000M"))   # split children and merge target start empty
    assert new["CG-1010"]["physical_pct"] > 0 and new["A1000"]["physical_pct"] == 100   # renamed / unchanged keep their own history by stable identity
    assert "A2030" not in new and "A2000" not in new and "A3000" not in new


def test_compare_versions_lists_retired_scope_with_its_history_and_transfers_nothing(revised):
    k = revised
    c = rollups.compare_versions(k.pm, k.v1, k.v2, as_of=FAR)
    retired = {r["external_id"]: r for r in c["retired_scope"]}
    assert {"A2000", "A2030", "A3000"} <= set(retired) and all(r["progress_transferred"] is False for r in retired.values()) and all(retired[e]["has_approved_history"] for e in ("A2000", "A2030", "A3000")) and retired["A3001"]["has_approved_history"] is False
    assert retired["A2030"]["last_actual_pct"] > 0
    assert {a["external_id"] for a in c["added_scope"]} >= {"A2000-1", "A2000-2", "A3000M", "A4000"} and all(a["actual_pct"] == 0 for a in c["added_scope"] if a["external_id"] != "A4000")
    assert c["old"]["version_no"] == 1 and c["new"]["version_no"] == 2 and "Nothing is transferred" in c["note"]
    assert {l["relation"] for l in c["lineage"]} == {"RENAMED", "SPLIT", "MERGED", "RETIRED"}


def test_the_old_version_still_reports_its_own_history_and_a_retired_activity_cannot_be_decided(revised):
    k = revised
    old = rollups.project_summary(k.pm, as_of=FAR, version_id=k.v1)
    new = rollups.project_summary(k.pm, as_of=FAR)
    assert old["version"]["version_no"] == 1 and new["version"]["version_no"] == 2 and old["physical_pct"] > 0
    with connect() as c:
        assert c.execute("select physical_pct p from activity_progress_as_of(%s,%s) where external_activity_id='A2030'", (k.v1, FAR)).fetchone()["p"] > 0
    with raises("ACTIVITY_NOT_IN_ACTIVE_SCHEDULE"):
        k.submit("A2030", 100, "m3")
    assert tl.activity_timeline(k.pm, k.ids["A2030"])["quantity_entries"]               # the history of the retired activity remains readable
