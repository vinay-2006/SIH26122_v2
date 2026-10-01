"""
Integrity of the CANONICAL V7 dataset built by `python -m backend.canonical.build` (isolated DB only).
The fixture builds it when absent (guarded: the whole DB test-suite refuses to run outside the isolated DB),
then every assertion goes through the real API with real HS256 JWTs.
"""
import os

import psycopg
import psycopg.rows
import pytest

import backend.main  # noqa: F401  (import order: avoids a pre-existing repositories<->services circular import)
from backend.canonical import build as cb
from backend.canonical import dataset as ds

pytestmark = [pytest.mark.integration]


@pytest.fixture(scope="module")
def world(monkeypatch_module):
    from fastapi.testclient import TestClient
    from backend.main import app
    monkeypatch_module.setenv("SUPABASE_JWT_SECRET", cb.JWT_SECRET)
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row, autocommit=True)
    with TestClient(app) as client:
        cb.build(client, conn)  # idempotent: no-op when the project exists
        p = conn.execute("SELECT project_id FROM projects WHERE project_code = %s", (ds.PROJECT_CODE,)).fetchone()["project_id"]
        pid = str(p)
        rows = conn.execute("SELECT schedule_id, version_code, active FROM schedules WHERE project_id = %s", (pid,)).fetchall()
        api = cb.Api(client)
        api.project_id = pid
        ver = {r["version_code"]: str(r["schedule_id"]) for r in rows}
        api.schedule_id = ver["REV-B"]
        yield {"api": api, "conn": conn, "pid": pid, "ver": ver, "rows": rows}
    conn.close()


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def q(w, sql, *a):
    return w["conn"].execute(sql, a).fetchall()


def test_two_versions_one_active_and_counts(world):
    w = world
    assert {r["version_code"]: r["active"] for r in w["rows"]} == {"REV-A": False, "REV-B": True}
    n = lambda sid, t: q(w, f"SELECT count(*) n FROM {t} WHERE schedule_id = %s", sid)[0]["n"]
    assert n(w["ver"]["REV-B"], "schedule_activities") == 45 and n(w["ver"]["REV-A"], "schedule_activities") == 44
    assert n(w["ver"]["REV-B"], "schedule_dependencies") == 34
    assert n(w["ver"]["REV-B"], "stages") == 6 == n(w["ver"]["REV-A"], "stages")


def test_no_row_lacks_project_or_attribution(world):
    w = world
    assert q(w, "SELECT count(*) n FROM schedule_activities WHERE project_id IS NULL AND schedule_id = ANY(%s)", list(w["ver"].values()))[0]["n"] == 0
    bad = q(w, "SELECT count(*) n FROM schedule_activities WHERE project_id = %s AND (stage_id IS NULL OR work_package_id IS NULL OR contractor_id IS NULL)", w["pid"])
    assert bad[0]["n"] == 0
    for t in ("stages", "quality_gates", "approved_actuals", "execution_events", "audit_logs"):
        assert q(w, f"SELECT count(*) n FROM {t} WHERE project_id = %s", w["pid"])[0]["n"] > 0


def test_stage_weights_sum_to_100_per_version(world):
    w = world
    for sid in w["ver"].values():
        assert round(float(q(w, "SELECT sum(weight_pct) s FROM stages WHERE schedule_id = %s", sid)[0]["s"]), 2) == 100.0


def test_every_activity_has_exactly_one_gate_and_history_is_human_approved(world):
    w = world
    assert q(w, "SELECT count(*) n FROM quality_gates WHERE project_id = %s AND schedule_id = %s", w["pid"], w["ver"]["REV-B"])[0]["n"] == 45
    orphans = q(w, "SELECT count(*) n FROM approved_actuals a LEFT JOIN planner_decisions d ON d.decision_id = a.decision_id WHERE a.project_id = %s AND d.decision_id IS NULL", w["pid"])
    assert orphans[0]["n"] == 0
    assert all(r["schedule_id"] and str(r["schedule_id"]) == w["ver"]["REV-B"] for r in q(w, "SELECT schedule_id FROM approved_actuals WHERE project_id = %s", w["pid"]))


def test_audit_chain_is_valid_for_the_auditor(world):
    w, api = world, world["api"]
    r = api.call("auditor", "GET", f"/api/v1/projects/{w['pid']}/dossier/audit-verification")
    assert r["status"] == "VALID" and r["legacy_records"] == 0 and r["v7_records"] > 100


def test_role_permissions_match_the_matrix(world):
    w, api = world, world["api"]
    perms = {who: set(api.call(who, "GET", f"/api/v1/projects/{w['pid']}/me").get("permissions", [])) for who in
             ("owner", "pm", "planner", "supervisor", "engineer", "inspector", "auditor")}
    assert "APPROVE_ACTUAL" in perms["supervisor"] and "APPROVE_ACTUAL" not in perms["engineer"] and "APPROVE_ACTUAL" not in perms["auditor"]
    assert "CREATE_EXECUTION_EVENT" in perms["engineer"] and "CREATE_EXECUTION_EVENT" not in perms["auditor"]


def test_outsider_and_sibling_are_isolated(world):
    w, api = world, world["api"]
    for path in (f"/api/v1/projects/{w['pid']}/progress", f"/api/v1/projects/{w['pid']}/me"):
        r = api.c.get(path, headers={"Authorization": f"Bearer {cb.token('outsider')}", "X-Project-ID": w["pid"]})
        assert r.status_code in (403, 404), (path, r.status_code)
    r = api.c.get(f"/api/v1/projects/{w['pid']}/progress", headers={"X-Project-ID": w["pid"]})
    assert r.status_code == 401
    r = api.c.get(f"/api/v1/projects/{w['pid']}/progress", headers={"Authorization": f"Bearer {cb.token('pm', exp_in=-60)}", "X-Project-ID": w["pid"]})
    assert r.status_code == 401
    r = api.call("supervisor", "POST", f"/api/v1/projects/{w['pid']}/memory/search", json={"query": "confidential sibling project narrative"}, expect=(200, 404, 422))
    assert "Confidential sibling-project narrative" not in str(r)


def test_completed_wld024_awaits_quality_release_and_blocker_is_visible(world):
    w, api = world, world["api"]
    b = api.call("supervisor", "GET", f"/api/v1/projects/{w['pid']}/schedules/{w['ver']['REV-B']}/progress/breakdown", schedule=True)
    acts = {a["activity_id"]: a for s in b["stages"] for a in s["activities"]}
    assert acts["PIP-PS3-WLD-024"]["canonical_state"] == "COMPLETED" and acts["PIP-PS3-WLD-024"]["workflow_condition"] == "QUALITY_HOLD"
    assert acts["INS-PS3-FGS-001"]["workflow_condition"] == "BLOCKED"
    assert acts["CIV-PS3-FND-001"]["workflow_condition"] in ("NONE", "QUALITY_HOLD")


def test_prompt_injection_text_is_stored_as_data_and_never_acted_on(world):
    w = world
    assert q(w, "SELECT count(*) n FROM institutional_incidents WHERE project_id = %s AND narrative ILIKE '%%IGNORE ALL PREVIOUS%%'", w["pid"])[0]["n"] == 1
    assert q(w, "SELECT count(*) n FROM quality_gates WHERE project_id = %s AND status = 'PASSED'", w["pid"])[0]["n"] == 9


def test_rebuild_is_idempotent(world):
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        assert cb.build(c, world["conn"])["status"] == "EXISTS"


def _export(w, who, fmt, sid=None, pid=None):
    sid, pid = sid or w["ver"]["REV-B"], pid or w["pid"]
    return w["api"].c.get(f"/api/v1/projects/{pid}/schedules/{sid}/export?format={fmt}",
                          headers={"Authorization": f"Bearer {cb.token(who)}", "X-Project-ID": pid, "X-Schedule-ID": sid})


def test_export_from_the_database_round_trips_csv_and_xer(world):
    from backend.shared.schedule import parse_schedule_csv
    from backend.shared.xer_parser import parse_schedule_xer
    w = world
    src = parse_schedule_csv(ds.schedule_csv_text(), "S")
    dep = lambda r: {(d.predecessor_activity_id, d.successor_activity_id, d.relationship_type, round(float(d.lag_days), 2)) for d in r.dependencies}
    for fmt, parse in (("csv", lambda t: parse_schedule_csv(t, "R")), ("xer", lambda t: parse_schedule_xer(t.encode(), "R"))):
        r = _export(w, "auditor", fmt)
        assert r.status_code == 200, r.text[:200]
        out = parse(r.text)
        assert out.is_valid, out.errors
        assert {a.activity_id: (a.planned_start, a.planned_finish) for a in out.activities} == {a.activity_id: (a.planned_start, a.planned_finish) for a in src.activities}
        assert dep(out) == dep(src)


def test_export_is_scoped_to_the_schedule_and_the_project(world):
    w = world
    rev_a = _export(w, "pm", "csv", sid=w["ver"]["REV-A"])
    assert rev_a.status_code == 200 and "HSE-PS3-AUD-001" not in rev_a.text  # the version, not "the latest"
    assert _export(w, "outsider", "csv").status_code in (403, 404)
    other = str(q(w, "SELECT project_id FROM projects WHERE project_code = %s", ds.SIBLING_CODE)[0]["project_id"])
    assert _export(w, "pm", "csv", pid=other).status_code in (403, 404)  # NFU schedule via a project the caller is not in
    assert _export(w, "pm", "pdf").status_code == 422


def test_impact_watchlist_lists_problem_activities_with_real_reach(world):
    w, api = world, world["api"]
    rows = api.call("auditor", "GET", f"/api/v1/projects/{w['pid']}/schedules/{w['ver']['REV-B']}/impact/watchlist", schedule=True)
    by = {r["activity_id"]: r for r in rows}
    assert by["INS-PS3-FGS-001"]["workflow_condition"] == "BLOCKED"
    assert by["PIP-PS3-WLD-024"]["workflow_condition"] == "QUALITY_HOLD"
    assert all(r["workflow_condition"] != "NONE" for r in rows)
    assert all(r["total_downstream_count"] >= r["direct_successor_count"] for r in rows)
    rank = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    assert [rank[r["severity"]] for r in rows] == sorted((rank[r["severity"]] for r in rows), reverse=True)
    api.call("outsider", "GET", f"/api/v1/projects/{w['pid']}/schedules/{w['ver']['REV-B']}/impact/watchlist", schedule=True, expect=(403, 404))


def test_quality_gate_and_reopen_lists_are_schedule_scoped(world):
    w, api = world, world["api"]
    base = f"/api/v1/projects/{w['pid']}/schedules"
    gates = api.call("inspector", "GET", f"{base}/{w['ver']['REV-B']}/quality-gates", schedule=True)
    assert len(gates) == 45 and {g["schedule_id"] for g in gates} == {w["ver"]["REV-B"]}
    passed = api.call("auditor", "GET", f"{base}/{w['ver']['REV-B']}/quality-gates?status=PASSED", schedule=True)
    assert len(passed) == 9 and all(g["status"] == "PASSED" for g in passed)
    assert api.call("auditor", "GET", f"{base}/{w['ver']['REV-A']}/quality-gates") == []  # gates belong to REV-B only
    api.call("outsider", "GET", f"{base}/{w['ver']['REV-B']}/quality-gates", schedule=True, expect=(403, 404))
    assert api.call("pm", "GET", f"{base}/{w['ver']['REV-B']}/reopen-requests", schedule=True) == []
    api.call("outsider", "GET", f"{base}/{w['ver']['REV-B']}/reopen-requests", schedule=True, expect=(403, 404))


def test_local_login_issues_a_real_jwt_only_for_known_identities(world, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app
    monkeypatch.setenv("SETUAI_LOCAL_DEMO_AUTH", "1")
    c = TestClient(app)
    ok = c.post("/api/v1/auth/local-login", json={"email": "Inspector@nfu.setuai.test", "password": "Demo123456!"})
    assert ok.status_code == 200
    me = c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"})
    assert me.status_code == 200 and me.json()["id"] == cb.uid("inspector")
    assert c.post("/api/v1/auth/local-login", json={"email": "inspector@nfu.setuai.test", "password": "wrong"}).status_code == 401
    assert c.post("/api/v1/auth/local-login", json={"email": "nobody@nfu.setuai.test", "password": "Demo123456!"}).status_code == 401


def test_required_hold_point_cannot_be_released_without_a_pass_record(world):
    w, api = world, world["api"]
    gate = q(w, "SELECT quality_gate_id, status FROM quality_gates WHERE project_id = %s AND activity_id = 'HSE-PS3-IND-001'", w["pid"])[0]
    assert gate["status"] == "PENDING"
    r = api.c.post(f"/api/v1/projects/{w['pid']}/quality-gates/{gate['quality_gate_id']}/pass", json={},
                   headers={"Authorization": f"Bearer {cb.token('inspector')}", "X-Project-ID": w["pid"]})
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "EVIDENCE_REQUIRED"
    # the read-only roles cannot release either, evidence or not
    for who in ("auditor", "engineer"):
        r = api.c.post(f"/api/v1/projects/{w['pid']}/quality-gates/{gate['quality_gate_id']}/pass", json={},
                       headers={"Authorization": f"Bearer {cb.token(who)}", "X-Project-ID": w["pid"]})
        assert r.status_code == 403, (who, r.status_code)
    assert q(w, "SELECT status FROM quality_gates WHERE quality_gate_id = %s", gate["quality_gate_id"])[0]["status"] == "PENDING"


def test_agent_briefing_agrees_with_the_derived_state_and_uses_one_schedule(world):
    """The supervising agent reports the SAME facts as the state engine: one schedule version, no double counting."""
    w, api = world, world["api"]
    base = f"/api/v1/projects/{w['pid']}"
    prog = api.call("supervisor", "GET", f"{base}/schedules/{w['ver']['REV-B']}/progress/breakdown", schedule=True)
    conds = [a["workflow_condition"] for s in prog["stages"] for a in s["activities"]]
    briefing = api.call("supervisor", "GET", f"{base}/agent/briefing")
    # every finding about an activity is about an activity of the ACTIVE version, once
    activity_findings = [f for f in briefing["findings"] if f["category"] == "DEPENDENCY_BLOCK"]
    ids = [f["affected_entity_id"] for f in activity_findings]
    assert len(ids) == len(set(ids)), "an activity must not be reported twice (REV-A and REV-B both counted)"
    held_or_blocked = sum(1 for c in conds if c in ("BLOCKED", "QUALITY_HOLD"))
    assert len(ids) == min(held_or_blocked, 10)  # the briefing shows a capped sample
    assert f"{sum(1 for c in conds if c == 'BLOCKED')} blocked activities" in briefing["summary"] or briefing["agent_status"] != "DEGRADED"
    queue = api.call("supervisor", "GET", f"{base}/agent/review-queue")
    event_ids = [c["event_id"] for c in queue["claims"]]
    assert len(event_ids) == len(set(event_ids))
    assert all(c.get("event_type") != "REOPEN_REQUEST" for c in queue["claims"])
