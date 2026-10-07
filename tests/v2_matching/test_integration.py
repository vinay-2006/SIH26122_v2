"""Automatic matching inside the real v2 workflow (real Postgres, real services). Matching proposes; it never approves or changes approved progress."""
import json

import pytest

from domainkit import TODAY, assert_consistent, connect, claims, resolve_actor
from v2api import build_and_activate, ledger_fingerprint, seed_progress
from backend.v2.errors import ApiError
from backend.v2.matching import adapter, service as svc
from backend.v2.domain import decisions

pytestmark = pytest.mark.db_write


def fresh(kit, text, **kw):
    return claims.submit_claim(kit.se, event_date=TODAY, raw_text=text, **kw)


def row(claim_id):
    with connect() as c:
        return c.execute("select * from execution_events where event_id = %s", (claim_id,)).fetchone()


def cands(claim_id):
    with connect() as c:
        return c.execute("select cm.*, ba.external_activity_id from candidate_matches cm join baseline_activities ba on ba.activity_uid = cm.activity_uid "
                         "join schedule_versions v on v.version_id = ba.version_id and v.status = 'ACTIVE' where cm.event_id = %s order by rank_order", (claim_id,)).fetchall()


def audits(claim_id, action):
    with connect() as c:
        return c.execute("select * from audit_logs where entity_type = 'CLAIM' and entity_id = %s and action = %s", (str(claim_id), action)).fetchall()


def test_free_text_claim_is_matched_automatically_and_not_approved(kit):
    before = ledger_fingerprint()
    r = fresh(kit, "Welding mainline A2010 - 120 joints done to date", quantities=[{"qty": 120, "uom": "joints", "basis": "CUMULATIVE"}])
    assert r["status"] == "MATCHED" and r["activity_uid"] == kit.uid("A2010")
    assert r["match"]["matched"] and r["match"]["match_tier"] == "EXACT_ID" and r["match"]["composite_confidence"] == 0.97     # engine: an id cited in free text scores 0.97 (1.0 is for an id reported as a field)
    e = row(r["claim_id"])
    assert e["status"] == "MATCHED" and e["matched_activity_uid"] == kit.uid("A2010") and e["field_provenance"]["activity"] == "SCHEDULE_AUTO_FILLED"
    cs = cands(r["claim_id"])
    assert [c["external_activity_id"] for c in cs][0] == "A2010" and cs[0]["rank_order"] == 1 and cs[0]["match_tier"] == "EXACT_ID"
    assert [q["assignment_uid"] is not None for q in r["quantities"]] == [True]                 # the quantity was bound to the measured assignment
    assert ledger_fingerprint() == before and kit.count("approved_resource_progress") == 0 and kit.count("planner_decisions") == 0
    a = audits(r["claim_id"], "CLAIM_AUTO_MATCHED")
    assert len(a) == 1 and json.loads(a[0]["after_state"])["performed_by"].startswith("SYSTEM:") and a[0]["actor_id"] == kit.se.user_id
    assert_consistent(kit)


def test_matched_claim_flows_through_supervisor_approval_like_any_other(kit):
    r = fresh(kit, "Welding mainline A2010", quantities=[{"qty": 100, "uom": "joints", "basis": "CUMULATIVE"}])
    assert kit.count("approved_resource_progress") == 0
    kit.approve(r["claim_id"], method="QUANTITIES_AS_CLAIMED")
    assert kit.count("approved_resource_progress") == 1 and kit.pct("A2010") > 0
    assert_consistent(kit)


def test_unmatched_claim_stays_for_a_supervisor_and_keeps_its_candidates(kit):
    r = fresh(kit, "Temporary rain shelter erected at the laydown area", claimed_pct=10)
    assert r["status"] == "EXTRACTED" and r["activity_uid"] is None and r["match"]["matched"] is False
    e = row(r["claim_id"])
    assert e["matched_activity_uid"] is None
    with connect() as c:
        v = c.execute("select rule_code, severity, description from claim_validations where event_id = %s and rule_code = 'NO_AUTOMATIC_MATCH'", (r["claim_id"],)).fetchall()
    assert len(v) == 1 and v[0]["severity"] == "INFO" and v[0]["description"]
    assert 1 <= len(cands(r["claim_id"])) <= 3
    with pytest.raises(ApiError):                                                              # nothing to decide against until a Supervisor picks an activity
        kit.approve(r["claim_id"], method="PERCENT_AS_CLAIMED")


def test_explicit_activity_choice_is_left_exactly_as_chosen(kit):
    r = fresh(kit, "Pipe stringing progress", activity_uid=kit.uid("A2000"), quantities=[{"qty": 3, "uom": "km", "basis": "CUMULATIVE"}])
    assert r["status"] == "MATCHED" and r["activity_uid"] == kit.uid("A2000") and r["match"] is None
    assert cands(r["claim_id"]) == [] and audits(r["claim_id"], "CLAIM_AUTO_MATCHED") == []
    assert row(r["claim_id"])["field_provenance"].get("activity") == "ENGINEER"


def test_supervisor_manual_override_and_explicit_automatic_rematch(kit):
    r = fresh(kit, "Welding mainline A2010 progress", quantities=[{"qty": 50, "uom": "joints", "basis": "CUMULATIVE"}])
    assert r["activity_uid"] == kit.uid("A2010")
    claims.rematch_claim(kit.sup, r["claim_id"], kit.uid("A2000"))                          # explicit manual override
    assert row(r["claim_id"])["matched_activity_uid"] == kit.uid("A2000")
    out = claims.rematch_claim(kit.sup, r["claim_id"])                                      # explicit automatic re-run puts the engine's answer back, audited
    assert out["matched"] and out["activity_uid"] == kit.uid("A2010") and row(r["claim_id"])["matched_activity_uid"] == kit.uid("A2010")
    assert len(audits(r["claim_id"], "CLAIM_AUTO_MATCHED")) == 2 and len(audits(r["claim_id"], "CLAIM_REMATCHED")) == 1
    assert len(cands(r["claim_id"])) >= 1 and kit.count("approved_resource_progress") == 0
    assert_consistent(kit)


def test_only_a_supervisor_can_rematch(kit):
    r = fresh(kit, "Welding mainline A2010", claimed_pct=5)
    with pytest.raises(ApiError) as e:
        claims.rematch_claim(kit.se, r["claim_id"])
    assert e.value.status == 403
    with pytest.raises(ApiError) as e:
        claims.rematch_claim(kit.pm, r["claim_id"])
    assert e.value.status == 403


def test_an_automatic_rematch_that_finds_nothing_keeps_the_supervisors_choice(kit):
    r = fresh(kit, "Temporary rain shelter erected at the laydown area", claimed_pct=10)
    claims.rematch_claim(kit.sup, r["claim_id"], kit.uid("A2000"))
    out = claims.rematch_claim(kit.sup, r["claim_id"])
    assert out["matched"] is False and row(r["claim_id"])["matched_activity_uid"] == kit.uid("A2000")


def test_candidates_never_come_from_another_project_even_with_identical_activity_ids(kit, api):
    w = kit.world
    build_and_activate(api, w.pm2, w.project2, "csv")                                       # the SAME schedule (same external ids) in another project
    with connect() as c:
        other = {r["activity_uid"] for r in c.execute("select activity_uid from baseline_activities where project_id = %s", (w.project2,)).fetchall()}
        mine = {r["activity_uid"] for r in c.execute("select activity_uid from baseline_activities where project_id = %s", (w.project,)).fetchall()}
    assert other and mine and not (other & mine)
    r = fresh(kit, "Welding mainline A2010 progress", quantities=[{"qty": 10, "uom": "joints", "basis": "CUMULATIVE"}])
    got = {c["activity_uid"] for c in cands(r["claim_id"])}
    assert got and got <= mine and not (got & other)
    assert row(r["claim_id"])["matched_activity_uid"] in mine
    with connect() as c:
        ver = c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (w.project,)).fetchone()["version_id"]
        loaded = adapter.load_activities(c, w.project, ver)
    assert {a["activity_uid"] for a in loaded} == mine and all(a["project_id"] == str(w.project) for a in loaded)


def test_matching_uses_the_active_version_only(kit, api):
    w = kit.world
    with connect() as c:
        old = c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (w.project,)).fetchone()["version_id"]
    from v2api import file_bytes
    rev = file_bytes("nsp.csv").rstrip(b"\n") + b"\nA9999,Punch list closeout,Northern Spur Test Pipeline > Civil & ROW Preparation,Safety,Task,5.0,12-01-2026,16-01-2026,0,\n"
    up = api.post(f"/api/v2/projects/{w.project}/schedule-imports", w.pm, files={"file": ("rev.csv", rev, "text/csv"), "resources_file": ("res.csv", file_bytes("nsp_resources.csv"), "text/csv")},
                  data={"data_date": "2026-01-05", "planned_start": "2026-01-12", "planned_finish": "2026-07-31", "project_name": "Northern Spur Test Pipeline"})
    assert up.status_code == 201, up.text
    b = api.post(f"/api/v2/projects/{w.project}/schedule-imports/{up.json()['import_id']}/build", w.pm)
    assert b.status_code == 201, b.text
    assert api.post(f"/api/v2/projects/{w.project}/schedule-versions/{b.json()['version_id']}/activate", w.pm).status_code == 200      # a NEW version is active, the first superseded
    r = fresh(kit, "Welding mainline A2010 progress", claimed_pct=10)
    with connect() as c:
        new = c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (w.project,)).fetchone()["version_id"]
        used = c.execute("select schedule_version_id from audit_logs where entity_id = %s and action = 'CLAIM_AUTO_MATCHED'", (str(r["claim_id"]),)).fetchone()["schedule_version_id"]
        filed = c.execute("select filed_in_version_id from execution_events where event_id = %s", (r["claim_id"],)).fetchone()["filed_in_version_id"]
    assert new != old and filed == new and used == new and r["match"]["matched"]
    assert row(r["claim_id"])["matched_activity_uid"] == kit.uid("A2010")                    # stable identity: the same activity across versions


def test_completed_activity_is_not_offered_but_a_supervisor_can_still_assign_it_manually(kit):
    w = kit.world
    seed_progress(w.project, "A2010", {"WELD_JOINTS": 2000}, w.sup, w.se, finish=True)
    assert kit.pct("A2010") == 100
    r = fresh(kit, "Welding mainline A2010 repair of 3 joints", claimed_pct=5)
    assert r["match"]["matched"] is False and "A2010" not in [c["external_activity_id"] for c in r["match"]["candidates"]]
    claims.rematch_claim(kit.sup, r["claim_id"], kit.uid("A2010"))                           # manual override is a Supervisor's explicit decision
    assert row(r["claim_id"])["matched_activity_uid"] == kit.uid("A2010")


def test_planned_quantity_comes_only_from_the_single_measuring_assignment(kit):
    with connect() as c:
        ver = c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]
        acts = {a["activity_id"]: a for a in adapter.load_activities(c, kit.project, ver)}
        n = {r["external_activity_id"]: r["n"] for r in c.execute(
            "select ba.external_activity_id, count(*) filter (where br.measures_progress) n from baseline_activities ba left join baseline_resources br on br.activity_uid = ba.activity_uid "
            "and br.version_id = ba.version_id where ba.version_id = %s group by 1", (ver,)).fetchall()}
    assert acts["A2010"]["planned_quantity"] == 2000.0 and acts["A2010"]["uom"] == "JOINT"           # the schedule's controlled unit code, passed through unconverted
    for ext, a in acts.items():
        if n[ext] != 1:
            assert a["planned_quantity"] is None and a["uom"] is None, ext                    # none or several measuring assignments: uncertainty preserved, nothing summed


def test_asset_tag_is_imported_and_drives_the_exact_asset_tier(kit, api):
    w = kit.world
    csv = (b"Activity ID,Activity Name,WBS Path,Discipline,Activity Type,Baseline Duration,Baseline Start,Baseline Finish,Total Float,Predecessors,Asset Tag\n"
           b"T100,Pump skid installation,Plant > Mechanical,Mechanical Works,Task,10,02-02-2026,13-02-2026,0,,P-102\n"
           b"T200,Pump skid grouting,Plant > Mechanical,Mechanical Works,Task,10,16-02-2026,27-02-2026,0,T100FS,P-103\n")
    res = (b"Activity ID,Resource ID,Resource Name,Resource Class,Baseline Qty,Unit\nT100,SKIDS,Skids,Material,2,nos\nT200,GROUT,Grout,Material,20,m3\n")
    r = api.post(f"/api/v2/projects/{w.project2}/schedule-imports", w.pm2, files={"file": ("t.csv", csv, "text/csv"), "resources_file": ("r.csv", res, "text/csv")},
                 data={"data_date": "2026-01-05", "planned_start": "2026-01-12", "planned_finish": "2026-07-31", "project_name": "Tag test"})
    assert r.status_code == 201, r.text
    b = api.post(f"/api/v2/projects/{w.project2}/schedule-imports/{r.json()['import_id']}/build", w.pm2)
    assert b.status_code == 201, b.text
    assert api.post(f"/api/v2/projects/{w.project2}/schedule-versions/{b.json()['version_id']}/activate", w.pm2).status_code == 200
    with connect() as c:
        tags = {x["external_activity_id"]: x["asset_tag"] for x in c.execute("select external_activity_id, asset_tag from baseline_activities where project_id = %s", (w.project2,)).fetchall()}
        assert tags == {"T100": "P-102", "T200": "P-103"}
        assert all(x["asset_tag"] is None for x in c.execute("select asset_tag from baseline_activities where project_id = %s", (w.project,)).fetchall())     # NSP has no tags
    api.post(f"/api/v2/projects/{w.project2}/members", w.pm2, json={"email": w.se2.email, "role": "SITE_ENGINEER"})
    actor2 = resolve_actor(w.se2.id, w.project2)
    out = claims.submit_claim(actor2, event_date=TODAY, raw_text="Skid placed on the plinth", asset_tag="P-102", claimed_pct=40)
    assert out["match"]["matched"] and out["match"]["match_tier"] == "EXACT_ASSET"
    with connect() as c:
        assert c.execute("select external_activity_id from baseline_activities where activity_uid = %s and version_id = (select version_id from schedule_versions where project_id = %s and status='ACTIVE')",
                         (out["activity_uid"], w.project2)).fetchone()["external_activity_id"] == "T100"


def test_a_failing_engine_never_loses_the_report(kit, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine down")
    monkeypatch.setattr(svc, "rank_claim", boom)
    r = fresh(kit, "Welding mainline A2010", claimed_pct=5)
    assert r["status"] == "EXTRACTED" and r["match"]["matched"] is False
    assert row(r["claim_id"]) is not None
    with connect() as c:
        assert c.execute("select count(*) n from claim_validations where event_id = %s and rule_code = 'AUTOMATIC_MATCH_UNAVAILABLE'", (r["claim_id"],)).fetchone()["n"] == 1
    assert_consistent(kit)


def test_semantic_stack_unavailable_degrades_like_the_legacy_pipeline(kit, monkeypatch):
    from backend.v2.matching import index
    monkeypatch.setattr(index, "search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no embeddings")))
    r = fresh(kit, "Welding mainline A2010 progress", claimed_pct=5)
    assert r["match"]["semantic_available"] is False and r["match"]["matched"] and r["match"]["match_tier"] == "EXACT_ID"
    a = audits(r["claim_id"], "CLAIM_AUTO_MATCHED")[0]
    assert json.loads(a["after_state"])["semantic_available"] is False


def test_automatic_matching_never_overwrites_an_explicit_pick_unless_asked(kit):
    from backend.v2.domain.common import actor_tx, active_version
    r = fresh(kit, "Welding mainline A2010 progress", activity_uid=kit.uid("A2000"), claimed_pct=5)
    with actor_tx(kit.se, write=True) as c:
        out = svc.auto_match(c, kit.se, r["claim_id"], active_version(c, kit.project))
    assert out == {"skipped": "ACTIVITY_ALREADY_CHOSEN", "matched": False}
    assert row(r["claim_id"])["matched_activity_uid"] == kit.uid("A2000") and cands(r["claim_id"]) == []
