"""
Prototype alignment, backend (DB_WRITE + INTEGRATION, isolated DB): the four-project dataset and the flows built on it.

Run against the isolated test DB (it creates issues / decisions); the seeded demo DB is only read by the dataset checks:

    SETUAI_ALLOW_DB_TESTS=1 SETUAI_TEST_ENV=integration \
    DATABASE_URL=postgresql://postgres@127.0.0.1:54329/setuai_integ pytest tests/test_integration_prototype_dataset.py

Covers: project/stage/discipline progress from the database (completed = 100, upcoming = 0, ongoing varied, equal to the
dataset definition), issue reporting -> root-cause analysis, supervisor decision -> site-engineer notification
(persisted, recipient-only), and institutional-memory write + retrieval (including cross-project sharing rules).
"""
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

import backend.main  # noqa: F401  (import order: avoids a pre-existing repositories<->services circular import)
from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.main import app
from backend.prototype_seed import data, seed as seeder
from backend.shared.db import get_connection

pytestmark = [pytest.mark.integration]

CODES = {p.code: p for p in data.PROJECTS}


def _purge_test_rows(su, project_ids):
    """Remove everything these tests create (and anything left by an earlier failed run); deterministic seed rows stay.
    Audit rows are append-only history and are intentionally left."""
    seed_issues = [seeder._id(f"issue:{p.code}:{i.key}") for p in data.PROJECTS for i in p.issues]
    stray = [str(r["issue_id"]) for r in su.execute(
        "SELECT issue_id FROM issues WHERE project_id = ANY(%s::uuid[]) AND NOT (issue_id::text = ANY(%s))", (project_ids, seed_issues)).fetchall()]
    su.execute("DELETE FROM notifications WHERE event_id LIKE 'EV-NT-%%' OR issue_id::text = ANY(%s)", (stray,))
    su.execute("DELETE FROM institutional_incidents WHERE project_id = ANY(%s::uuid[]) AND (source = 'MANUAL' OR issue_id::text = ANY(%s))", (project_ids, stray))
    su.execute("DELETE FROM issue_evidence WHERE issue_id::text = ANY(%s)", (stray,))
    su.execute("DELETE FROM source_documents WHERE project_id = ANY(%s::uuid[]) AND document_type = 'ISSUE_EVIDENCE'", (project_ids,))
    su.execute("DELETE FROM issues WHERE issue_id::text = ANY(%s)", (stray,))
    su.execute("DELETE FROM planner_decisions WHERE event_id LIKE 'EV-NT-%%'")
    su.execute("DELETE FROM execution_events WHERE event_id LIKE 'EV-NT-%%'")


@pytest.fixture(scope="module")
def demo():
    with seeder._connect() as conn:
        seeder.seed(conn)
    su = get_connection()
    su.autocommit = True
    d = {"su": su, "pid": {}, "sid": {}}
    for code, p in CODES.items():
        d["pid"][code] = str(su.execute("SELECT project_id FROM projects WHERE project_code = %s", (code,)).fetchone()["project_id"])
        d["sid"][code] = p.schedule_id
    ids = list(d["pid"].values())
    _purge_test_rows(su, ids)
    yield d
    app.dependency_overrides.pop(get_current_user, None)
    _purge_test_rows(su, ids)


class _As:
    """A client bound to one user. The auth override is app-global, so it is re-applied on every call: two clients
    for two users can safely be used interleaved."""

    def __init__(self, who):
        self.who = who
        self.role = "SITE_ENGINEER" if who == "engineer" else "SUPERVISOR"
        self.http = TestClient(app)

    def _bind(self):
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=seeder.UID[self.who], full_name=self.who, role=self.role)

    def get(self, *a, **k):
        self._bind()
        return self.http.get(*a, **k)

    def post(self, *a, **k):
        self._bind()
        return self.http.post(*a, **k)


def as_user(who):
    return _As(who)


def base(d, code):
    return f"/api/v1/projects/{d['pid'][code]}/schedules/{d['sid'][code]}"


def dashboard(d, code, who="supervisor"):
    r = as_user(who).get(base(d, code) + "/dashboard")
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ dataset / integrity
def test_exactly_these_projects_exist_with_their_lifecycle(demo):
    rows = {r["project_code"]: r["lifecycle_status"] for r in demo["su"].execute("SELECT project_code, lifecycle_status FROM projects").fetchall()}
    for code, p in CODES.items():
        assert rows.get(code) == p.lifecycle
    assert {p.lifecycle for p in CODES.values()} == {"COMPLETED", "ONGOING", "UPCOMING"}


def test_dataset_definition_is_internally_consistent():
    assert data.validate() == []


def test_hierarchy_is_enforced_by_foreign_keys_and_every_activity_has_a_discipline(demo):
    su = demo["su"]
    ids = list(demo["pid"].values())  # the test DB also holds other suites' fixtures; check the prototype projects
    assert su.execute("SELECT count(*) n FROM schedule_activities WHERE project_id = ANY(%s::uuid[]) AND (discipline IS NULL OR stage_id IS NULL)", (ids,)).fetchone()["n"] == 0
    # stage must belong to the activity's own project and schedule version
    bad = su.execute(
        "SELECT count(*) n FROM schedule_activities a JOIN stages s ON s.stage_id = a.stage_id "
        "WHERE a.project_id = ANY(%s::uuid[]) AND (s.project_id <> a.project_id OR s.schedule_id <> a.schedule_id)", (ids,)).fetchone()["n"]
    assert bad == 0
    # discipline is a real FK to the reference table and spellings are normalised on write: 'Piping' / ' piping ' /
    # 'Piping Works' all become PIPING, and an unmapped label goes to the OTHER bucket with its source text kept
    where = ("project_id = %s AND activity_id = 'NRL-PEI-010'", (demo["pid"]["NRL-EXP-01"],))
    read = lambda: su.execute(f"SELECT discipline, discipline_source FROM schedule_activities WHERE {where[0]}", where[1]).fetchone()
    try:
        for spelling in ("Piping", " piping ", "Piping Works"):
            su.execute(f"UPDATE schedule_activities SET discipline = %s WHERE {where[0]}", (spelling, *where[1]))
            assert read()["discipline"] == "PIPING"
        su.execute(f"UPDATE schedule_activities SET discipline = 'Plumbing' WHERE {where[0]}", where[1])
        row = read()
        assert row["discipline"] == "OTHER" and row["discipline_source"] == "Plumbing", "an unmapped label is bucketed, never stored as free text"
    finally:  # leave the seeded row exactly as seeded
        su.execute(f"UPDATE schedule_activities SET discipline = 'PIPING' WHERE {where[0]}", where[1])
    for p in CODES.values():
        assert len(p.stages) >= 5 and all(len(s.acts) >= 6 for s in p.stages)


# ---------------------------------------------------------------------------------------- dashboard: DB-derived progress
def test_completed_project_is_100_percent_everywhere(demo):
    dash = dashboard(demo, "NNB-COP-01")
    assert dash["lifecycle_status"] == "COMPLETED"
    assert dash["overall"]["actual_pct"] == 100.0 and dash["overall"]["completed_count"] == dash["overall"]["activity_count"] == 34
    assert [s["actual_pct"] for s in dash["stages"]] == [100.0] * 5
    assert all(x["actual_pct"] == 100.0 for x in dash["disciplines"])
    n = demo["su"].execute("SELECT count(*) n FROM approved_actuals WHERE project_id = %s AND actual_pct_complete = 100", (demo["pid"]["NNB-COP-01"],)).fetchone()["n"]
    assert n == 34, "100 percent is carried by real approved actuals"


def test_upcoming_project_is_planned_but_has_no_execution(demo):
    dash = dashboard(demo, "SMP-CCP-01")
    assert dash["lifecycle_status"] == "UPCOMING"
    assert dash["overall"]["actual_pct"] == 0.0 and dash["overall"]["not_started_count"] == 32
    assert all(s["actual_pct"] == 0.0 for s in dash["stages"]) and all(x["actual_pct"] == 0.0 for x in dash["disciplines"])
    assert demo["su"].execute("SELECT count(*) n FROM approved_actuals WHERE project_id = %s", (demo["pid"]["SMP-CCP-01"],)).fetchone()["n"] == 0
    assert dash["overall"]["planned_pct"] == 0.0, "nothing is due before the plan starts"


@pytest.mark.parametrize("code", ["AND-ODC-01", "NRL-EXP-01"])
def test_ongoing_projects_have_varied_progress_that_matches_the_dataset(demo, code):
    p, dash = CODES[code], dashboard(demo, code)
    got = {s["stage_code"]: s["actual_pct"] for s in dash["stages"]}
    want = {s.code: data.rollup(s.acts) for s in p.stages}
    assert got == pytest.approx(want, abs=0.01)
    assert len(set(got.values())) >= 5, "stages are not all at the same percentage"
    assert max(got.values()) - min(got.values()) > 40
    assert dash["overall"]["actual_pct"] == pytest.approx(data.rollup(data.all_acts(p)), abs=0.01)
    assert 0 < dash["overall"]["actual_pct"] < 100
    disc = {x["discipline"]: x["actual_pct"] for x in dash["disciplines"]}
    assert len(set(disc.values())) >= 4 and all(k in data.DISCIPLINES for k in disc)
    assert dash["overall"]["planned_pct"] > 0 and dash["as_of_date"] == p.data_date.isoformat()


def test_dashboard_progress_agrees_with_the_progress_engine(demo):
    code = "NRL-EXP-01"
    dash = dashboard(demo, code)
    r = as_user("supervisor").get(base(demo, code) + "/progress")
    assert r.status_code == 200, r.text
    assert r.json()["progress_pct"] == pytest.approx(dash["overall"]["actual_pct"], abs=0.05)


def test_dashboard_is_project_scoped(demo):
    other = as_user("supervisor").get(f"/api/v1/projects/{uuid.uuid4()}/schedules/{demo['sid']['NRL-EXP-01']}/dashboard")
    assert other.status_code in (403, 404)
    cross = as_user("supervisor").get(f"/api/v1/projects/{demo['pid']['NRL-EXP-01']}/schedules/{demo['sid']['AND-ODC-01']}/dashboard")
    assert cross.status_code in (403, 404), "a schedule of another project must not be readable through this project"


# ---------------------------------------------------------------------------------------------------- issues / root cause
def test_site_engineer_reports_an_issue_that_is_persisted_with_stage_and_activity(demo):
    c = as_user("engineer")
    code = "NRL-EXP-01"
    r = c.post(base(demo, code) + "/issues", json={
        "activity_id": "NRL-PEI-040", "category_code": "LABOUR_SHORTAGE", "severity": "HIGH",
        "title": "Cable gangs short at the north cable corridor", "description": "Only 9 of 16 cable pullers on site for three days.",
        "expected_duration_days": 6, "reported_date": "2026-09-28", "blocks_work": False})
    assert r.status_code == 201, r.text
    iss = r.json()
    assert iss["status"] == "ACTIVE" and iss["stage_name"] == "Piping, Electrical & Instrumentation"
    assert iss["discipline"] == "ELECTRICAL" and iss["reported_by_name"] == "Demo Site Engineer"
    row = demo["su"].execute("SELECT stage_id, activity_id, category_code FROM issues WHERE issue_id = %s", (iss["issue_id"],)).fetchone()
    assert row["activity_id"] == "NRL-PEI-040" and row["stage_id"] is not None
    assert any(i["issue_id"] == iss["issue_id"] for i in c.get(base(demo, code) + "/issues?mine=true").json())
    demo["issue"] = iss


def test_evidence_can_be_attached_to_an_issue_and_is_visible_to_the_project(demo):
    code = "NRL-EXP-01"
    iid = demo["issue"]["issue_id"]
    r = as_user("engineer").post(base(demo, code) + f"/issues/{iid}/evidence", files={"file": ("crew_roster.txt", b"roster: 9 of 16 present")}, data={"notes": "crew roster"})
    assert r.status_code == 200, r.text
    ev = r.json()["evidence"]
    assert len(ev) == 1 and ev[0]["file_name"] == "crew_roster.txt"
    row = demo["su"].execute("SELECT d.project_id, d.document_type, ie.notes FROM issue_evidence ie JOIN source_documents d ON d.document_id = ie.document_id WHERE ie.issue_id = %s", (iid,)).fetchone()
    assert str(row["project_id"]) == demo["pid"][code] and row["document_type"] == "ISSUE_EVIDENCE" and row["notes"] == "crew roster"
    # the supervisor sees it; an empty file is rejected cleanly
    assert as_user("supervisor").get(base(demo, code) + f"/issues/{iid}").json()["evidence"][0]["file_name"] == "crew_roster.txt"
    assert as_user("engineer").post(base(demo, code) + f"/issues/{iid}/evidence", files={"file": ("empty.txt", b"")}).status_code == 422


def test_engineer_cannot_resolve_or_analyse_into_root_causes_but_supervisor_can(demo):
    code = "NRL-EXP-01"
    iid = demo["issue"]["issue_id"]
    assert as_user("engineer").post(base(demo, code) + f"/issues/{iid}/resolve", json={"resolution_notes": "fixed"}).status_code == 403
    assert as_user("engineer").post(base(demo, code) + "/root-causes", json={"category_code": "LABOUR_SHORTAGE", "title": "x" * 5}).status_code == 403
    # an engineer may not report against a schedule/activity outside the project
    r = as_user("engineer").post(base(demo, code) + "/issues", json={
        "activity_id": "AND-DRL-030", "category_code": "OTHER", "title": "wrong project", "description": "not in this project"})
    assert r.status_code == 404


def test_root_cause_analysis_surfaces_the_repeated_labour_shortage_pattern(demo):
    code = "NRL-EXP-01"
    r = as_user("supervisor").get(base(demo, code) + "/root-cause-analysis")
    assert r.status_code == 200, r.text
    a = r.json()
    cats = {c["category_code"]: c for c in a["categories"]}
    labour = cats["LABOUR_SHORTAGE"]
    assert labour["is_repeated_pattern"] and labour["activity_count"] >= 4 and labour["stage_count"] >= 3
    assert labour["issue_count"] >= 5 and labour["open_count"] >= 4
    assert a["categories"][0]["category_code"] == "LABOUR_SHORTAGE", "most frequent cause first"
    titles = {rc["title"] for rc in a["root_causes"]}
    assert "Skilled-labour shortage across civil, erection and piping" in titles
    rc = next(x for x in a["root_causes"] if x["category_code"] == "LABOUR_SHORTAGE")
    assert rc["issue_count"] >= 4 and len(rc["activity_ids"]) >= 4
    assert a["delayed_stages"] and a["delayed_stages"][0]["open_expected_delay_days"] >= a["delayed_stages"][-1]["open_expected_delay_days"]
    # the new engineer-reported issue can be grouped under the existing root cause
    linked = as_user("supervisor").post(base(demo, code) + f"/root-causes/{rc['root_cause_id']}/issues", json={"issue_ids": [demo["issue"]["issue_id"]]})
    assert linked.status_code == 200 and linked.json()["issue_count"] == rc["issue_count"] + 1


def test_blocking_issue_drives_the_blocked_condition_and_resolving_clears_it(demo):
    code = "NRL-EXP-01"
    r = as_user("engineer").post(base(demo, code) + "/issues", json={
        "activity_id": "NRL-PEI-060", "category_code": "MATERIAL_SHORTAGE", "title": "Tubing and fittings exhausted",
        "description": "No impulse tubing left; instrument installation has stopped.", "blocks_work": True, "severity": "HIGH"})
    assert r.status_code == 201, r.text
    iid = r.json()["issue_id"]

    def cond():
        items = as_user("supervisor").get(base(demo, code) + "/progress/breakdown").json()
        return {a["activity_id"]: a["workflow_condition"] for s in items["stages"] for a in s["activities"]}["NRL-PEI-060"]

    assert cond() == "BLOCKED"
    res = as_user("supervisor").post(base(demo, code) + f"/issues/{iid}/resolve", json={
        "resolution_notes": "Emergency purchase from the Guwahati stockist.", "outcome": "Installation resumed after two days."})
    assert res.status_code == 200 and res.json()["status"] == "RESOLVED"
    assert cond() == "NONE"
    assert as_user("supervisor").post(base(demo, code) + f"/issues/{iid}/resolve", json={"resolution_notes": "again"}).status_code == 409


def test_resolution_becomes_institutional_memory_and_notifies_the_reporter(demo):
    code = "NRL-EXP-01"
    iid = demo["issue"]["issue_id"]
    res = as_user("supervisor").post(base(demo, code) + f"/issues/{iid}/resolve", json={
        "resolution_notes": "Cable-pulling subcontractor added a second gang from Jorhat.", "cause": "Parallel expansion absorbed local cable crews",
        "outcome": "Pulling caught up in nine days.", "share_with_organisation": True})
    assert res.status_code == 200, res.text
    assert res.json()["memory_incident_id"], "the resolved issue is linked to its memory record"
    m = demo["su"].execute("SELECT * FROM institutional_incidents WHERE issue_id = %s", (iid,)).fetchone()
    assert m["category_code"] == "LABOUR_SHORTAGE" and m["visibility"] == "ORGANISATION" and m["source"] == "ISSUE_RESOLUTION"
    assert m["corrective_action"].startswith("Cable-pulling") and m["outcome"] == "Pulling caught up in nine days."
    assert str(m["project_id"]) == demo["pid"][code] and m["activity_id"] == "NRL-PEI-040" and m["stage_id"] is not None
    notes = as_user("engineer").get(f"/api/v1/projects/{demo['pid'][code]}/notifications").json()
    assert any(n["notification_type"] == "ISSUE_UPDATE" and n["issue_id"] == iid for n in notes["items"])


# ------------------------------------------------------------------------------------------ decision -> notification
def _claim(demo, code, activity, pct):
    ev = f"EV-NT-{uuid.uuid4().hex[:10]}"
    su = demo["su"]
    su.execute(
        "INSERT INTO execution_events (event_id, schedule_id, project_id, event_date, raw_claim_text, input_channel, reported_activity_id, "
        "matched_activity_id, discipline, claim_mode, claimed_pct, status, supervisor_id) "
        "VALUES (%s, %s, %s, %s, %s, 'TYPED_TEXT', %s, %s, 'PIPING', 'CUMULATIVE_PCT', %s, 'VALIDATED', %s)",
        (ev, demo["sid"][code], demo["pid"][code], date(2026, 9, 29), f"{activity} {pct} percent", activity, activity, pct, seeder.UID["engineer"]))
    return ev


def test_supervisor_decision_is_persisted_and_the_site_engineer_is_notified(demo):
    code = "NRL-EXP-01"
    pid = demo["pid"][code]
    sup, eng = as_user("supervisor"), as_user("engineer")
    ev_reject, ev_hold = _claim(demo, code, "NRL-PEI-030", 40), _claim(demo, code, "NRL-PEI-020", 60)
    hdr = {"X-Project-ID": pid}
    r1 = sup.post("/api/v1/decisions", headers=hdr, json={"event_id": ev_reject, "action": "REJECT", "justification": "Radiography reports missing for the counted welds."})
    r2 = sup.post("/api/v1/decisions", headers=hdr, json={"event_id": ev_hold, "action": "HOLD", "justification": "Attach weld map showing the counted welds."})
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)

    row = demo["su"].execute(
        "SELECT n.*, pd.action, pd.justification FROM notifications n JOIN planner_decisions pd ON pd.decision_id = n.decision_id "
        "WHERE n.event_id = %s", (ev_reject,)).fetchone()
    assert row is not None and str(row["recipient_id"]) == seeder.UID["engineer"] and row["read_at"] is None
    assert row["action"] == "REJECT" and row["title"].startswith("Claim rejected")

    got = eng.get(f"/api/v1/projects/{pid}/notifications").json()
    mine = {n["event_id"]: n for n in got["items"]}
    assert mine[ev_reject]["decision_action"] == "REJECT" and mine[ev_reject]["claim_status"] == "REJECTED"
    assert mine[ev_reject]["decision_comment"] == "Radiography reports missing for the counted welds."
    assert mine[ev_reject]["decided_by_name"] == "Demo Supervisor" and mine[ev_reject]["decided_at"]
    assert mine[ev_hold]["decision_action"] == "HOLD" and mine[ev_hold]["claim_status"] == "HOLD"
    assert got["unread_count"] >= 2

    # recipient-only: the supervisor never sees the engineer's notifications
    assert not any(n["event_id"] in (ev_reject, ev_hold) for n in sup.get(f"/api/v1/projects/{pid}/notifications").json()["items"])

    # my-claims shows the claim with its decision, and read state persists
    claims = {c["event_id"]: c for c in eng.get(f"/api/v1/projects/{pid}/my-claims").json()}
    assert claims[ev_reject]["status"] == "REJECTED" and claims[ev_reject]["decision_comment"].startswith("Radiography")
    nid = mine[ev_reject]["notification_id"]
    assert eng.post(f"/api/v1/projects/{pid}/notifications/{nid}/read").status_code == 200
    after = eng.get(f"/api/v1/projects/{pid}/notifications").json()
    assert after["unread_count"] == got["unread_count"] - 1
    assert demo["su"].execute("SELECT read_at FROM notifications WHERE notification_id = %s", (nid,)).fetchone()["read_at"] is not None


def test_seeded_decisions_are_visible_to_the_engineer_with_comment_and_time(demo):
    pid = demo["pid"]["NRL-EXP-01"]
    claims = {c["activity_id"]: c for c in as_user("engineer").get(f"/api/v1/projects/{pid}/my-claims?limit=300").json()}
    rej = claims["NRL-CIV-030"]
    assert rej["decision_action"] in ("REJECT", "APPROVE") and rej["decided_at"]
    notes = as_user("engineer").get(f"/api/v1/projects/{pid}/notifications?limit=200").json()["items"]
    actions = {n["decision_action"] for n in notes if n["notification_type"] == "CLAIM_DECISION"}
    assert {"REJECT", "EDIT", "HOLD"} <= actions


# ------------------------------------------------------------------------------------------------------ memory retrieval
def test_memory_for_a_new_issue_finds_a_shared_lesson_from_a_completed_project(demo):
    code = "NRL-EXP-01"
    pid = demo["pid"][code]
    r = as_user("supervisor").post(f"/api/v1/projects/{pid}/memory/for-issue", json={
        "category_code": "MATERIAL_DELIVERY_DELAY", "query": "structural steel consignment delayed vendor logistics", "top_k": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    top = body["results"][0]["record"]
    assert top["title"] == "Line pipe delivery delayed by mill logistics"
    assert top["metadata"]["scope"] == "ORGANISATION" and top["metadata"]["shared_from_other_project"] is True
    assert top["metadata"]["project_name"].startswith("Naharkatiya")
    assert "Alternate supplier" in top["metadata"]["resolution"] and "28 days" in top["metadata"]["outcome"]
    assert top["metadata"]["root_cause"] and body["results"][0]["score"] > 0


def test_memory_keeps_unshared_records_inside_their_project(demo):
    pid = demo["pid"]["NRL-EXP-01"]
    # NNB's river-bank access incident was NOT shared; Andaman's weather incidents were NOT shared
    access = as_user("supervisor").post(f"/api/v1/projects/{pid}/memory/for-issue", json={"category_code": "SITE_ACCESS", "query": "river bank access"}).json()
    assert access["results"] == []
    weather = as_user("supervisor").post(f"/api/v1/projects/{pid}/memory/for-issue", json={"category_code": "WEATHER", "query": "monsoon sea state"}).json()
    titles = {x["record"]["title"] for x in weather["results"]}
    assert "Monsoon flooding of open trench" in titles, "NNB shared this lesson"
    assert "Cyclonic disturbance — rig secured, operations suspended" in titles, "Andaman shared this lesson"
    assert not titles & {"Swell above 3 m halted supply-vessel transfers", "High sea state delayed anchor handling"}, "Andaman kept these private"
    # a project always sees its own records, shared or not
    own = as_user("supervisor").post(f"/api/v1/projects/{demo['pid']['AND-ODC-01']}/memory/for-issue", json={"category_code": "WEATHER", "query": "cyclone swell anchor"}).json()
    own_projects = {x["record"]["project_id"] for x in own["results"]}
    assert demo["pid"]["AND-ODC-01"] in own_projects


def test_memory_can_be_recorded_manually_and_retrieved_by_activity_context(demo):
    code = "AND-ODC-01"
    pid = demo["pid"][code]
    sup = as_user("supervisor")
    r = sup.post(f"/api/v1/projects/{pid}/memory", json={
        "category_code": "EQUIPMENT_SHORTAGE", "title": "Annular preventer element failure", "narrative": "Element failed the 5000 psi test on the first stack-up.",
        "root_cause": "No spare element held on the rig", "resolution": "Spare air-freighted from the vendor base", "outcome": "Retest passed after five days",
        "activity_id": "AND-MOB-060", "schedule_id": demo["sid"][code]})
    assert r.status_code == 201, r.text
    found = sup.post(f"/api/v1/projects/{pid}/memory/for-issue", json={
        "category_code": "EQUIPMENT_SHORTAGE", "query": "BOP annular preventer failed pressure test spare", "activity_id": "AND-MOB-060"}).json()
    assert found["results"][0]["record"]["activity_id"] == "AND-MOB-060"
    assert "EXACT_ACTIVITY" in found["results"][0]["match_reasons"]
    assert as_user("engineer").post(f"/api/v1/projects/{pid}/memory", json={
        "category_code": "OTHER", "title": "not allowed", "narrative": "engineers cannot write memory"}).status_code == 403
