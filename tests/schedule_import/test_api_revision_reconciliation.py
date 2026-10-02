"""Schedule revision against real approved progress: reconciliation, stable identities, activation, rollback. The ledger is fingerprinted
before and after every version change and must be byte-for-byte identical."""
import uuid
from decimal import Decimal

import pytest

from v2api import build_and_activate, connect, file_bytes, ledger_fingerprint, seed_progress, upload
from backend.v2 import audit

P = "/api/v2/projects"
LIN = lambda c: {r["relation"]: r["n"] for r in c.execute("select relation, count(*) n from activity_lineage group by relation").fetchall()}


@pytest.fixture
def running(world, api):
    """baseline built from CSV and ACTIVE, with approved progress on the activities that the revision will rename/split/merge/retire"""
    iid, v1 = build_and_activate(api, world.pm, world.project, "csv")
    ids = {}
    for ext, qty in {"A1000": {"ROW_SURVEY_KM": 24}, "A1010": {"CLEARED_ROW_KM": 12}, "A1020": {"CONCRETE_M3": 240},
                     "A2000": {"PIPE_STRUNG_KM": 12}, "A2030": {"BACKFILL_M3": 2000}, "A3000": {"TEST_SECTIONS": 1}}.items():
        ids[ext] = seed_progress(world.project, ext, qty, world.sup, world.se)
    world.v1, world.uid, world.before = v1, ids, ledger_fingerprint()
    return world


def stage_rev(api, w, fmt="csv"):
    r = upload(api, w.pm, w.project, fmt, tag="nsp_rev1")
    assert r.status_code == 201, r.text
    return r.json()


def by_new(imp):
    return {i["new"]: i for i in imp["reconciliation"]["items"]}


def test_the_baseline_progress_is_what_we_think_it_is(running, api):
    with connect() as c:
        r = {x["external_activity_id"]: x["physical_pct"] for x in c.execute("select external_activity_id, physical_pct from v_activity_progress").fetchall()}
    assert r["A1000"] == 100 and r["A1010"] == 50 and r["A1020"] == 25 and r["A2000"] == 50 and r["A2030"] == Decimal("14.286") and r["A3000"] == 50


def test_reconciliation_report_classifies_every_scenario_and_blocks_the_build(running, api):
    w = running
    imp = stage_rev(api, w)
    rec = imp["reconciliation"]
    assert imp["base_version_id"] == w.v1
    o = by_new(imp)
    assert {k: o[k]["outcome"] for k in ("A0100", "A1000", "A1020", "A2010", "A3010", "A3020", "A3030")} == {k: "SAME" for k in ("A0100", "A1000", "A1020", "A2010", "A3010", "A3020", "A3030")}
    assert all(o[k]["uid"] == str(w.uid[k]) for k in ("A1000", "A1020"))                  # identity carried over automatically
    assert o["CG-1010"]["outcome"] == "PROPOSED_RENAME" and o["CG-1010"]["old_uid"] == str(w.uid["A1010"]) and o["CG-1010"]["uid"] is None
    assert o["A4000"]["outcome"] == "NEW"
    assert [s["from_external_id"] for s in rec["split_proposals"]] == ["A2000"] and [m["to"] for m in rec["merge_proposals"]] == ["A3000M"]
    chg = {c["new"]: c for c in rec["changes"]}
    assert chg["A1000"]["fields"] == ["dates"] and chg["A1020"]["quantities"][0]["new"] == 600
    assert {b["code"] for b in rec["blockers"]} == {"UNRESOLVED_MATCH", "UNRESOLVED_PROGRESS"}
    blocked = {b["ref"] for b in rec["blockers"] if b["code"] == "UNRESOLVED_PROGRESS"}
    assert blocked == {"A1010", "A2000", "A3000", "A2030"}                               # exactly the executed activities that vanished
    r = api.post(f"{P}/{w.project}/schedule-imports/{imp['import_id']}/build", w.pm)
    assert r.status_code == 409 and r.json()["error"]["code"] == "RECONCILIATION_INCOMPLETE"
    with connect() as c:
        assert c.execute("select count(*) n from schedule_versions").fetchone()["n"] == 1      # still only the baseline
    assert ledger_fingerprint() == w.before


def resolve_all(api, w, imp):
    o = by_new(imp)
    uid = lambda ext: o[ext].get("old_uid") or w.uid[ext]
    dec = {"reconcile": {
        "accept": {"CG-1010": str(w.uid["A1010"])},
        "split": [{"from": str(w.uid["A2000"]), "to": [{"ext": "A2000-1", "fraction": 0.5}, {"ext": "A2000-2", "fraction": 0.5}]}],
        "merge": [{"from": [str(w.uid["A3000"]), str(imp_uid_of(imp, "A3001", w))], "to": "A3000M"}],
        "retire": [str(w.uid["A2030"])]}}
    r = api.put(f"{P}/{w.project}/schedule-imports/{imp['import_id']}/decisions", w.pm, json=dec)
    assert r.status_code == 200, r.text
    return r.json()


def imp_uid_of(imp, ext, w):
    return next(m for m in imp["reconciliation"]["merge_proposals"])["from_uids"][1] if ext == "A3001" else w.uid[ext]


def test_full_revision_preserves_identity_and_history(running, api):
    w = running
    imp = stage_rev(api, w)
    after = resolve_all(api, w, imp)
    assert after["reconciliation"]["blockers"] == [], after["reconciliation"]["blockers"]
    b = api.post(f"{P}/{w.project}/schedule-imports/{imp['import_id']}/build", w.pm)
    assert b.status_code == 201, b.text
    v2 = b.json()
    assert v2["kind"] == "REVISION" and v2["version_no"] == 2 and v2["status"] == "VALIDATED"
    assert ledger_fingerprint() == w.before                                            # building a revision does not touch history

    with connect() as c:
        new = {r["external_activity_id"]: r for r in c.execute("select * from baseline_activities where version_id = %s", (v2["version_id"],)).fetchall()}
        old = {r["external_activity_id"]: r for r in c.execute("select * from baseline_activities where version_id = %s", (w.v1,)).fetchall()}
        # identity: same activity_uid for unchanged / renamed; fresh for new + split children + merge target
        for same in ("A0100", "A1000", "A1020", "A2010", "A3010", "A3020", "A3030"):
            assert new[same]["activity_uid"] == old[same]["activity_uid"], same
        assert new["CG-1010"]["activity_uid"] == old["A1010"]["activity_uid"]
        fresh = {new[k]["activity_uid"] for k in ("A4000", "A2000-1", "A2000-2", "A3000M")}
        assert len(fresh) == 4 and not (fresh & {r["activity_uid"] for r in old.values()})
        assert "A2030" not in new and "A2000" not in new and "A3000" not in new
        # assignment identity survives (quantity history keeps its meaning), including the one whose quantity changed
        sel = ("select p.resource_code, r.assignment_uid, r.baseline_qty from baseline_resources r join project_resources p on p.resource_id = r.resource_id "
               "where r.version_id = %s and r.activity_uid = %s")
        a_old = {x["resource_code"]: x for x in c.execute(sel, (w.v1, w.uid["A1010"])).fetchall()}
        a_new = {x["resource_code"]: x for x in c.execute(sel, (v2["version_id"], w.uid["A1010"])).fetchall()}
        assert {k: v["assignment_uid"] for k, v in a_old.items()} == {k: v["assignment_uid"] for k, v in a_new.items()}
        c_old, c_new = c.execute(sel, (w.v1, w.uid["A1020"])).fetchall(), c.execute(sel, (v2["version_id"], w.uid["A1020"])).fetchall()
        q = lambda rows: {x["resource_code"]: (x["assignment_uid"], x["baseline_qty"]) for x in rows}
        assert q(c_old)["CONCRETE_M3"][0] == q(c_new)["CONCRETE_M3"][0] and (q(c_old)["CONCRETE_M3"][1], q(c_new)["CONCRETE_M3"][1]) == (480, 600)
        # WBS identity
        wo = {r["wbs_code"]: r["wbs_uid"] for r in c.execute("select wbs_code, wbs_uid from schedule_wbs where version_id = %s", (w.v1,)).fetchall()}
        wn = {r["wbs_code"]: r["wbs_uid"] for r in c.execute("select wbs_code, wbs_uid from schedule_wbs where version_id = %s", (v2["version_id"],)).fetchall()}
        assert wo == wn and len(wo) == 7
        assert LIN(c) == {"RENAMED": 1, "SPLIT": 2, "MERGED": 2, "RETIRED": 1}
        assert c.execute("select retired_in_version_id from activities where activity_uid = %s", (w.uid["A2030"],)).fetchone()["retired_in_version_id"] == uuid.UUID(v2["version_id"])
        assert c.execute("select count(*) n from activity_lineage where confirmed_by is null").fetchone()["n"] == 0

    cmp_ = api.get(f"{P}/{w.project}/schedule-versions/compare?old={w.v1}&new={v2['version_id']}", w.pm).json()
    kinds = {}
    for a in cmp_["activities"]:
        kinds[a["change_kind"]] = kinds.get(a["change_kind"], 0) + 1
    # identity-based: A1000 (dates) and CG-1010 (external id) changed; A1020's quantity change shows in the assignment diff below;
    # 4 added (2 split children, merge target, A4000); 4 removed (A2000, A2030, A3000, A3001)
    assert kinds == {"CHANGED": 2, "ADDED": 4, "REMOVED": 4} and cmp_["summary"]["UNCHANGED"] == 6
    assert any(a["assignment_uid"] and a["change_kind"] == "CHANGED" and a["old_qty"] == 480 and a["new_qty"] == 600 for a in cmp_["assignments"])


def test_activation_never_rewrites_history_and_progress_follows_the_stable_identity(running, api):
    w = running
    imp = stage_rev(api, w)
    resolve_all(api, w, imp)
    v2 = api.post(f"{P}/{w.project}/schedule-imports/{imp['import_id']}/build", w.pm).json()["version_id"]
    with connect() as c:
        claim_versions = {str(r["filed_in_version_id"]) for r in c.execute("select filed_in_version_id from execution_events").fetchall()}
    assert claim_versions == {w.v1}

    a = api.post(f"{P}/{w.project}/schedule-versions/{v2}/activate", w.pm)
    assert a.status_code == 200 and a.json()["previous_active_version_id"] == w.v1 and a.json()["rollback"] is False
    assert ledger_fingerprint() == w.before                                           # nothing erased, nothing rewritten, nothing re-attributed

    with connect() as c:
        st = {str(r["version_id"]): r["status"] for r in c.execute("select version_id, status from schedule_versions").fetchall()}
        assert st == {w.v1: "SUPERSEDED", v2: "ACTIVE"}
        prog = {r["external_activity_id"]: r for r in c.execute("select * from v_activity_progress").fetchall()}
        # the renamed activity keeps its progress under its NEW external id
        assert prog["CG-1010"]["physical_pct"] == 50 and prog["CG-1010"]["activity_uid"] == w.uid["A1010"]
        # quantity-changed activity: same approved quantity, new denominator => honest new percentage
        # A1020 has two progress-measuring assignments (concrete + steel, equal weight): 240/600 m3 = 40 %, steel 0 % => 20 %
        assert prog["A1020"]["physical_pct"] == 20
        assert c.execute("select cumulative_qty, baseline_qty_at_entry from approved_resource_progress where activity_uid = %s", (w.uid["A1020"],)).fetchone() == {
            "cumulative_qty": 240, "baseline_qty_at_entry": 480}                        # what was approved, against what it was approved
        assert prog["A1000"]["physical_pct"] == 100 and prog["A1000"]["start_variance_days"] is not None
        # split children / merge target start at zero: progress is never silently transferred
        assert prog["A2000-1"]["physical_pct"] == 0 and prog["A2000-2"]["physical_pct"] == 0 and prog["A3000M"]["physical_pct"] == 0
        # retired / split-source / merge-source activities are out of the active scope but their history is intact
        assert not ({"A2030", "A2000", "A3000"} & set(prog))
        assert c.execute("select count(*) n from approved_resource_progress where activity_uid in (%s,%s,%s)", (w.uid["A2030"], w.uid["A2000"], w.uid["A3000"])).fetchone()["n"] == 3
        assert c.execute("select physical_pct from v_project_progress").fetchone()["physical_pct"] > 0
        assert audit.verify_chain(c)["valid"]


def test_rollback_to_the_previous_version_is_lossless_and_needs_a_reason(running, api):
    w = running
    imp = stage_rev(api, w)
    resolve_all(api, w, imp)
    v2 = api.post(f"{P}/{w.project}/schedule-imports/{imp['import_id']}/build", w.pm).json()["version_id"]
    assert api.post(f"{P}/{w.project}/schedule-versions/{v2}/activate", w.pm).status_code == 200
    r = api.post(f"{P}/{w.project}/schedule-versions/{w.v1}/activate", w.pm, json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "REASON_REQUIRED"
    assert api.post(f"{P}/{w.project}/schedule-versions/{w.v1}/activate", w.pm, json={"reason": "  "}).status_code == 422
    ok = api.post(f"{P}/{w.project}/schedule-versions/{w.v1}/activate", w.pm, json={"reason": "Client rejected revision 1"})
    assert ok.status_code == 200 and ok.json()["rollback"] is True and ok.json()["previous_active_version_id"] == v2
    assert ledger_fingerprint() == w.before
    with connect() as c:
        prog = {r["external_activity_id"]: r["physical_pct"] for r in c.execute("select external_activity_id, physical_pct from v_activity_progress").fetchall()}
        assert prog["A1010"] == 50 and prog["A1020"] == 25 and prog["A2000"] == 50 and "CG-1010" not in prog
        row = c.execute("select action, entity_context from audit_logs where action = 'SCHEDULE_VERSION_ROLLBACK'").fetchone()
        assert "Client rejected" in str(row["entity_context"])
    # and forward again (a superseded version always needs a stated reason)
    assert api.post(f"{P}/{w.project}/schedule-versions/{v2}/activate", w.pm).status_code == 422
    assert api.post(f"{P}/{w.project}/schedule-versions/{v2}/activate", w.pm, json={"reason": "Client approved revision 1 after all"}).status_code == 200
    assert ledger_fingerprint() == w.before


def test_you_cannot_skip_a_decision_for_executed_work(running, api):
    w = running
    imp = stage_rev(api, w)
    url = f"{P}/{w.project}/schedule-imports/{imp['import_id']}"
    # reject the rename: the old A1010 (50 % done) would be orphaned
    r = api.put(f"{url}/decisions", w.pm, json={"reconcile": {"new": ["CG-1010"], "split": [], "merge": [], "retire": [str(w.uid["A2030"])]}})
    codes = {(b["code"], b["ref"]) for b in r.json()["reconciliation"]["blockers"]}
    assert ("UNRESOLVED_PROGRESS", "A1010") in codes
    assert api.post(f"{url}/build", w.pm).status_code == 409
    # bad decision shapes are refused
    assert api.put(f"{url}/decisions", w.pm, json={"reconcile": {"split": [{"from": "x", "to": [{"ext": "a", "fraction": 1}]}]}}).status_code == 422
    assert api.put(f"{url}/decisions", w.pm, json={"reconcile": {"merge": [{"from": ["x"], "to": "y"}]}}).status_code == 422
    assert api.put(f"{url}/decisions", w.pm, json={"reconcile": {"teleport": []}}).status_code == 422
    # a split whose fractions do not add up is a blocker, not a silent normalisation
    bad = {"reconcile": {"accept": {"CG-1010": str(w.uid["A1010"])}, "retire": [str(w.uid["A2030"]), str(w.uid["A3000"])],
                         "split": [{"from": str(w.uid["A2000"]), "to": [{"ext": "A2000-1", "fraction": 0.5}, {"ext": "A2000-2", "fraction": 0.3}]}]}}
    r = api.put(f"{url}/decisions", w.pm, json=bad).json()["reconciliation"]["blockers"]
    assert "SPLIT_FRACTIONS" in {b["code"] for b in r}
    assert ledger_fingerprint() == w.before


def test_dropping_a_resource_that_has_approved_quantity_is_blocked(world, api):
    iid, v1 = build_and_activate(api, world.pm, world.project, "csv")
    seed_progress(world.project, "A1010", {"CLEARED_ROW_KM": 12}, world.sup, world.se)
    res = file_bytes("nsp_resources.csv").decode().splitlines()
    res = [l for l in res if not (l.startswith("A1010,CLEARED_ROW_KM"))]                    # the revision forgets the quantity-measured resource
    files = {"file": ("nsp.csv", file_bytes("nsp.csv").replace(b"Clear and grade", b"Clear and grade "), "text/csv"),
             "resources_file": ("r2.csv", "\r\n".join(res).encode(), "text/csv")}
    r = api.post(f"{P}/{world.project}/schedule-imports", world.pm, files=files,
                 data={"data_date": "2026-01-05", "planned_start": "2026-01-12", "planned_finish": "2026-07-31", "baseline_name": "Rev 1"})
    assert r.status_code == 201, r.text
    codes = {b["code"] for b in r.json()["reconciliation"]["blockers"]}
    assert "ASSIGNMENT_DROPPED_WITH_PROGRESS" in codes
    assert api.post(f"{P}/{world.project}/schedule-imports/{r.json()['import_id']}/build", world.pm).status_code == 409


def test_a_revision_exported_by_a_different_tool_reconciles_to_the_same_identities(running, api):
    """baseline came from CSV; the revision arrives as a Primavera .xer (different WBS codes) and still maps to the same activities"""
    w = running
    imp = stage_rev(api, w, "xer")
    o = by_new(imp)
    assert o["A1000"]["uid"] == str(w.uid["A1000"]) and o["CG-1010"]["outcome"] == "PROPOSED_RENAME" and o["CG-1010"]["old_uid"] == str(w.uid["A1010"])
    assert [s["from_external_id"] for s in imp["reconciliation"]["split_proposals"]] == ["A2000"]
    resolve_all(api, w, imp)
    v2 = api.post(f"{P}/{w.project}/schedule-imports/{imp['import_id']}/build", w.pm)
    assert v2.status_code == 201, v2.text
    with connect() as c:
        wo = {r["wbs_uid"] for r in c.execute("select wbs_uid from schedule_wbs where version_id = %s", (w.v1,)).fetchall()}
        wn = {r["wbs_uid"] for r in c.execute("select wbs_uid from schedule_wbs where version_id = %s", (v2.json()["version_id"],)).fetchall()}
        assert wo == wn, "WBS identity must survive a change of export tool (matched by name and parent)"
    assert ledger_fingerprint() == w.before


def test_activation_is_blocked_by_the_database_if_the_service_were_bypassed(running):
    """defence in depth: even a direct activation of a revision that drops executed work is refused by the database gate"""
    import psycopg
    w = running
    with connect(system=True) as c:
        v2 = c.execute("insert into schedule_versions (project_id, version_no, kind, baseline_name, data_date, planned_start_date, planned_finish_date, parent_version_id, created_by) "
                       "select project_id, 2, 'REVISION', 'Rev X', data_date, planned_start_date, planned_finish_date, version_id, created_by from schedule_versions where version_id = %s returning version_id", (w.v1,)).fetchone()["version_id"]
        c.execute("update schedule_versions set status = 'VALIDATED', locked_at = now(), locked_by = created_by where version_id = %s", (v2,))
        c.execute("update schedule_versions set status = 'SUPERSEDED' where version_id = %s", (w.v1,))
        with pytest.raises(psycopg.errors.CheckViolation, match="approved progress exists"):
            c.execute("update schedule_versions set status = 'ACTIVE' where version_id = %s", (v2,))
