"""Workflow 6: a schedule revision (rename, split, merge, retire, add) against real approved progress. Nothing moves between activities automatically."""
from decimal import Decimal as D

from apikit import TODAY
from v2api import connect, file_bytes, ledger_fingerprint


def file_and_approve(s, ext, resource_qty, uom, text):
    """a Site Engineer files cumulative quantities, a Supervisor approves them as claimed"""
    body = {"event_date": str(TODAY), "raw_text": text, "activity_uid": s.uid(ext), "quantities": [{"qty": q, "uom": uom, "basis": "CUMULATIVE", "resource_hint": r} for r, q in resource_qty.items()]}
    r = s.api.post(s.url("/claims"), s.se, json=body)
    assert r.status_code == 201, r.text
    s.decide(r.json()["claim_id"])


def test_workflow_6_revision_never_moves_progress_between_activities(story):
    s, api = story, story.api
    # real approved progress through the API on the activities the revision will rename / split / merge / retire
    file_and_approve(s, "A1000", {"ROW_SURVEY_KM": 24}, "km", "Survey complete")
    file_and_approve(s, "A1010", {"CLEARED_ROW_KM": 12}, "km", "Half the ROW cleared")
    file_and_approve(s, "A2000", {"PIPE_STRUNG_KM": 12}, "km", "Half the pipe strung")
    file_and_approve(s, "A2030", {"BACKFILL_M3": 2000}, "m3", "Some backfill done")
    file_and_approve(s, "A3000", {"TEST_SECTIONS": 1}, "nos", "First hydrotest section passed")
    before = ledger_fingerprint()
    v1 = s.version
    sum_v1 = s.summary()
    assert sum_v1["physical_pct"] > 0 and s.pct("A1000") == 100 and s.pct("A2000") == 50

    # stage the revision: the PM sees what is proposed and what blocks the build
    files = {"file": ("rev1.csv", file_bytes("nsp_rev1.csv"), "text/csv"), "resources_file": ("rev1_res.csv", file_bytes("nsp_rev1_resources.csv"), "text/csv")}
    imp = api.post(s.url("/schedule-imports"), s.pm, files=files, data={"data_date": "2026-02-02", "planned_start": "2026-01-12", "planned_finish": "2026-07-31", "project_name": "E2E Northern Spur Pipeline"})
    assert imp.status_code == 201, imp.text
    imp = imp.json()
    rec = imp["reconciliation"]
    assert imp["base_version_id"] == v1 and {b["code"] for b in rec["blockers"]} == {"UNRESOLVED_MATCH", "UNRESOLVED_PROGRESS"}
    assert {b["ref"] for b in rec["blockers"] if b["code"] == "UNRESOLVED_PROGRESS"} == {"A1010", "A2000", "A3000", "A2030"}      # exactly the executed activities that vanish or change identity
    blocked = api.post(s.url(f"/schedule-imports/{imp['import_id']}/build"), s.pm)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "RECONCILIATION_INCOMPLETE"
    assert ledger_fingerprint() == before
    # the PM decides everything explicitly: rename, split 50/50, merge, retire
    a3001 = next(a["activity_uid"] for a in api.get(s.url(f"/schedule-versions/{v1}/activities"), s.pm).json() if a["external_activity_id"] == "A3001")
    dec = {"reconcile": {"accept": {"CG-1010": s.uid("A1010")},
                         "split": [{"from": s.uid("A2000"), "to": [{"ext": "A2000-1", "fraction": 0.5}, {"ext": "A2000-2", "fraction": 0.5}]}],
                         "merge": [{"from": [s.uid("A3000"), a3001], "to": "A3000M"}], "retire": [s.uid("A2030")]}}
    d = api.put(s.url(f"/schedule-imports/{imp['import_id']}/decisions"), s.pm, json=dec)
    assert d.status_code == 200 and d.json()["reconciliation"]["blockers"] == []
    b = api.post(s.url(f"/schedule-imports/{imp['import_id']}/build"), s.pm)
    assert b.status_code == 201 and b.json()["kind"] == "REVISION" and b.json()["version_no"] == 2
    v2 = b.json()["version_id"]
    assert ledger_fingerprint() == before                                                         # building a revision touches no history
    assert api.post(s.url(f"/schedule-versions/{v2}/activate"), s.se).status_code == 403
    assert api.post(s.url(f"/schedule-versions/{v2}/activate"), s.pm).status_code == 200
    assert ledger_fingerprint() == before                                                         # activation neither

    # compare: retired scope is listed, with its history kept and NOTHING transferred
    cmp_ = api.get(s.url("/dashboard/compare"), s.pm, params={"old": v1, "new": v2, "as_of": "2100-01-01"}).json()
    retired = {r["external_id"]: r for r in cmp_["retired_scope"]}
    assert {"A2000", "A2030", "A3000"} <= set(retired) and all(r["progress_transferred"] is False for r in retired.values())
    assert all(retired[e]["has_approved_history"] for e in ("A2000", "A2030", "A3000")) and retired["A3001"]["has_approved_history"] is False
    assert {a["external_id"] for a in cmp_["added_scope"]} >= {"A2000-1", "A2000-2", "A3000M", "A4000"}
    assert all(a["actual_pct"] == 0 for a in cmp_["added_scope"])                                  # split children, the merge target and new work start EMPTY
    assert "Nothing is transferred" in cmp_["note"] and {l["relation"] for l in cmp_["lineage"]} == {"RENAMED", "SPLIT", "MERGED", "RETIRED"}

    # the active schedule now: identity-preserved activities keep their history, restructured ones start at zero
    new_active = {a["external_activity_id"]: a["physical_pct"] for a in api.get(s.url("/dashboard/activities"), s.pm, params={"as_of": "2100-01-01", "limit": 200}).json()["items"]}
    assert new_active["A1000"] == 100 and new_active["CG-1010"] == 50                              # unchanged / renamed: same activity_uid, same ledger
    assert new_active["A2000-1"] == new_active["A2000-2"] == new_active["A3000M"] == new_active["A4000"] == 0
    assert "A2000" not in new_active and "A2030" not in new_active and "A3000" not in new_active
    old_view = api.get(s.url("/dashboard/summary"), s.pm, params={"as_of": "2100-01-01", "version_id": v1}).json()
    new_view = api.get(s.url("/dashboard/summary"), s.pm, params={"as_of": "2100-01-01"}).json()
    assert old_view["version"]["version_no"] == 1 and old_view["physical_pct"] == sum_v1["physical_pct"] and new_view["version"]["version_no"] == 2
    assert new_view["physical_pct"] != old_view["physical_pct"]                                    # different scope and denominators; the quantities themselves are unchanged

    # a claim on a retired activity is refused; one on a split child is an ordinary pending claim that moves only that child
    retired_claim = api.post(s.url("/claims"), s.se, json={"event_date": str(TODAY), "raw_text": "More backfill", "activity_uid": s.uid("A2030"), "quantities": [{"qty": 10, "uom": "m3", "basis": "CUMULATIVE"}]})
    assert retired_claim.status_code == 409 and retired_claim.json()["error"]["code"] == "ACTIVITY_NOT_IN_ACTIVE_SCHEDULE"
    kids = {a["external_activity_id"]: a for a in api.get(s.url("/activities"), s.se, params={"limit": 200}).json()["items"]}
    c = api.post(s.url("/claims"), s.se, json={"event_date": str(TODAY), "raw_text": "Section 1 stringing: 3 km", "activity_uid": kids["A2000-1"]["activity_uid"],
                                              "quantities": [{"qty": 3, "uom": "km", "basis": "CUMULATIVE", "resource_hint": "PIPE_STRUNG_KM"}]})
    assert c.status_code == 201
    assert ledger_fingerprint() == before                                                         # the pending claim has not touched the ledgers
    s.decide(c.json()["claim_id"])
    after = {a["external_activity_id"]: a["physical_pct"] for a in api.get(s.url("/dashboard/activities"), s.pm, params={"as_of": "2100-01-01", "limit": 200}).json()["items"]}
    assert after["A2000-1"] == D("25") and after["A2000-2"] == 0 and after["A1000"] == 100

    # history of a retired activity stays readable
    tl = api.get(s.url(f"/activities/{s.uid('A2030')}/timeline"), s.pm).json()
    assert tl["quantity_entries"] and tl["quantity_entries"][0]["cumulative_qty"] == 2000 and {v["version_no"] for v in tl["versions"]} == {1}
    # the previous schedule can be restored (rollback) only with a reason, and then its own history is back in force
    assert api.post(s.url(f"/schedule-versions/{v1}/activate"), s.pm, json={}).status_code in (409, 422)
