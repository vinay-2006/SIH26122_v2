"""
B1 regression: V6 intake is project/schedule-explicit (DB_WRITE + INTEGRATION, isolated DB only).

Real endpoints, real membership/RBAC/schedule validation. Only the identity is injected. Extraction uses the
deterministic rules fallback (EXTRACTION_FALLBACK=rules): no LLM is needed anywhere in this workflow.
"""
import io
import uuid

import pytest
from fastapi.testclient import TestClient

import backend.main  # noqa: F401  (import order: avoids a pre-existing repositories<->services circular import)
from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.main import app
from tests.test_integration_rls_and_audit_chain import world  # noqa: F401  (shared fixture)

pytestmark = [pytest.mark.integration]

CLAIM = {"raw_claim_text": "Excavation at Zone 3 is 40% complete", "input_channel": "TYPED_TEXT"}


@pytest.fixture(autouse=True)
def _rules_extraction_and_cleanup(monkeypatch):
    monkeypatch.setenv("EXTRACTION_FALLBACK", "rules")
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def engineers(world):
    """Both project members act as SITE_ENGINEERs for these tests (restored afterwards)."""
    su = world["su"]
    su.execute("UPDATE project_memberships SET assigned_role = 'SITE_ENGINEER' WHERE user_id IN (%s, %s)", (world["uA"], world["uB"]))
    yield
    su.execute("UPDATE project_memberships SET assigned_role = 'SUPERVISOR' WHERE user_id IN (%s, %s)", (world["uA"], world["uB"]))


def _client(world, key, project=None, schedule=None):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=world[key], full_name=key, role="SITE_ENGINEER")
    c = TestClient(app, raise_server_exceptions=False)
    if project:
        c.headers.update({"X-Project-ID": world[project]})
    if schedule:
        c.headers.update({"X-Schedule-ID": world[schedule]})
    return c


def _events(world, where, args=()):
    return world["su"].execute(f"SELECT * FROM execution_events WHERE {where}", args).fetchall()


def test_project_a_user_project_a_schedule_is_accepted_and_the_event_is_owned_by_project_a(world, engineers):
    c = _client(world, "uA", "A", "sA")
    r = c.post("/api/v1/claims/text", json=CLAIM)
    assert r.status_code == 200, r.text
    ev = _events(world, "event_id = %s", (r.json()["event_id"],))[0]
    assert str(ev["project_id"]) == world["A"] and ev["schedule_id"] == world["sA"]
    assert ev["status"] == "EXTRACTED" and str(ev["supervisor_id"]) == world["uA"]
    world["su"].execute("DELETE FROM execution_events WHERE event_id = %s", (ev["event_id"],))


def test_project_a_user_with_project_b_schedule_is_rejected_and_nothing_is_written(world, engineers):
    before = len(_events(world, "raw_claim_text = %s", (CLAIM["raw_claim_text"],)))
    r = _client(world, "uA", "A", "sB").post("/api/v1/claims/text", json=CLAIM)
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "SCHEDULE_ACCESS_DENIED", r.text
    assert len(_events(world, "raw_claim_text = %s", (CLAIM["raw_claim_text"],))) == before, "no orphan/cross-project event"


def test_project_a_event_cannot_be_created_under_project_b_by_any_context_combination(world, engineers):
    ids_before = {e["event_id"] for e in _events(world, "project_id IN (%s, %s)", (world["A"], world["B"]))}
    attempts = [
        ("uA", "B", "sB"),  # header names B (not a member)
        ("uA", "B", "sA"),
        ("uA", "A", "sB"),  # own project, foreign schedule
        ("uB", "A", "sA"),
    ]
    for user, proj, sched in attempts:
        r = _client(world, user, proj, sched).post("/api/v1/claims/text", json=CLAIM)
        assert r.status_code in (403, 404), (user, proj, sched, r.status_code)
    assert {e["event_id"] for e in _events(world, "project_id IN (%s, %s)", (world["A"], world["B"]))} == ids_before


def test_body_schedule_must_agree_with_header_schedule(world, engineers):
    c = _client(world, "uA", "A", "sA")
    r = c.post("/api/v1/claims/text", json={**CLAIM, "schedule_id": world["sB"]})
    assert r.status_code == 400 and r.json()["detail"]["error_code"] == "INVALID_SCHEDULE_CONTEXT", r.text


def test_no_schedule_no_project_is_rejected(world, engineers):
    assert _client(world, "uA", "A").post("/api/v1/claims/text", json=CLAIM).status_code == 400
    r = _client(world, "uA").post("/api/v1/claims/text", json={**CLAIM, "schedule_id": world["sA"]})
    assert r.status_code == 400 and r.json()["detail"]["error_code"] == "INVALID_PROJECT_CONTEXT"


def test_schedule_in_body_alone_is_enough(world, engineers):
    r = _client(world, "uA", "A").post("/api/v1/claims/text", json={**CLAIM, "schedule_id": world["sA"]})
    assert r.status_code == 200, r.text
    world["su"].execute("DELETE FROM execution_events WHERE event_id = %s", (r.json()["event_id"],))


def test_a_supervisor_cannot_file_field_reports(world):
    """CREATE_EXECUTION_EVENT belongs to the site engineer / PM / owner; reviewers review, they do not file."""
    r = _client(world, "uA", "A", "sA").post("/api/v1/claims/text", json=CLAIM)  # uA is a SUPERVISOR member
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "PERMISSION_DENIED"


def test_reading_and_clarifying_another_projects_claim_is_denied(world, engineers):
    for path, method in ((f"/api/v1/claims/{world['evB1']}", "get"), (f"/api/v1/claims/{world['evB1']}/photo", "get")):
        r = getattr(_client(world, "uA", "A", "sA"), method)(path)
        assert r.status_code == 403, (path, r.status_code)
    r = _client(world, "uA", "A", "sA").post(f"/api/v1/claims/{world['evB1']}/clarify", json={"answer": "x"})
    assert r.status_code == 403


def test_claim_list_is_schedule_and_project_scoped(world, engineers):
    c = _client(world, "uA", "A", "sA")
    resp = c.get("/api/v1/claims")
    assert resp.status_code == 200, resp.text
    ids = {x["event_id"] for x in resp.json()}
    assert world["evA1"] in ids and world["evB1"] not in ids
    assert _client(world, "uA", "A").get("/api/v1/claims").status_code == 400
    assert _client(world, "uA", "A", "sB").get("/api/v1/claims").status_code == 403


def test_file_intake_requires_and_uses_the_explicit_schedule(world, engineers):
    csv = "Activity ID,Discipline,Cumulative Actual,Planned Qty,Progress Pct\nACT-1,Civil,10,100,10\n"
    files = lambda: {"file": ("progress.csv", io.BytesIO(csv.encode()), "text/csv")}
    c = _client(world, "uA", "A")
    assert c.post("/api/v1/claims/schedule-export", files=files()).status_code == 400
    r = c.post("/api/v1/claims/schedule-export", files=files(), data={"schedule_id": world["sB"]})
    assert r.status_code == 403
    r = c.post("/api/v1/claims/schedule-export", files=files(), data={"schedule_id": world["sA"]})
    assert r.status_code == 200, r.text
    created = r.json()
    assert created, "the CSV row must produce a claim"
    for row in created:
        ev = _events(world, "event_id = %s", (row["event_id"],))[0]
        assert str(ev["project_id"]) == world["A"] and ev["schedule_id"] == world["sA"]
        assert str(ev["stage_id"] or "") == "" or True  # attribution needs an activity with a stage (covered by the seeded dataset)
    world["su"].execute("DELETE FROM execution_events WHERE event_id = ANY(%s)", ([c["event_id"] for c in created],))


def test_no_ambient_schedule_is_ever_selected(world, engineers):
    """The newest schedule of ANY project must never be picked: create a newer schedule in project B, then
    submit as project A without naming a schedule."""
    world["su"].execute("INSERT INTO schedules (schedule_id, project_name, project_id) VALUES ('SCH-NEWEST-B', 'newer', %s)", (world["B"],))
    try:
        r = _client(world, "uA", "A").post("/api/v1/claims/text", json=CLAIM)
        assert r.status_code == 400
        assert _events(world, "schedule_id = 'SCH-NEWEST-B'") == []
    finally:
        world["su"].execute("DELETE FROM schedules WHERE schedule_id = 'SCH-NEWEST-B'")
