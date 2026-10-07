import io
import os
import uuid
import hashlib
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from fastapi import Request
from backend.main import app
from backend.shared.auth import UserProfile, get_current_user
from backend.shared.db import get_connection

ENGINEER_ID = "811a1e0f-976d-42ea-a37f-1096186daf36"
SUPERVISOR_ID = "8b89e4f9-c059-4212-828b-cb3e987bc556"

def _override_get_current_user(request: Request) -> UserProfile:
    user_id = request.headers.get("X-Dev-User-Id", ENGINEER_ID)
    role = request.headers.get("X-Dev-Role", "SITE_ENGINEER")
    return UserProfile(id=user_id, full_name=user_id, role=role)



@pytest.fixture(autouse=True)
def _dev_header_auth_override():
    """Scoped to this module's tests. It used to be assigned at import time, which leaked a
    header-driven auth override into every other test in the session."""
    previous = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = previous


client = TestClient(app)

ENG_HEADERS = {"X-Dev-User-Id": ENGINEER_ID, "X-Dev-Role": "SITE_ENGINEER"}
SUP_HEADERS = {"X-Dev-User-Id": SUPERVISOR_ID, "X-Dev-Role": "SUPERVISOR"}


_STATE: dict = {}


def _get_or_create_schedule():
    """This module's OWN project + schedule + activity (created once by the module fixture below).
    Intake now requires an explicit project (X-Project-ID) and schedule (X-Schedule-ID) and the caller must be
    a member; nothing here reuses 'the latest schedule' of some other project any more."""
    return _STATE["schedule_id"]


@pytest.fixture(autouse=True, scope="module")
def _p0_project_and_schedule():
    project_id, schedule_id = str(uuid.uuid4()), f"P0-{uuid.uuid4().hex[:10]}"
    with get_connection() as conn:
        for pid, name, role in ((ENGINEER_ID, "P0 Engineer", "SITE_ENGINEER"), (SUPERVISOR_ID, "P0 Supervisor", "SUPERVISOR")):
            conn.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING", (pid, name, role))
        conn.execute(
            "INSERT INTO projects (project_id, project_code, project_name, status) VALUES (%s, %s, 'V7-INTEG p0', 'ACTIVE')",
            (project_id, f"V7-INTEG-P0-{uuid.uuid4().hex[:6]}"),
        )
        for pid, role in ((ENGINEER_ID, "SITE_ENGINEER"), (SUPERVISOR_ID, "SUPERVISOR")):
            conn.execute(
                "INSERT INTO project_memberships (user_id, project_id, assigned_role, active, status) VALUES (%s, %s, %s, TRUE, 'ACTIVE')",
                (pid, project_id, role),
            )
        conn.execute("INSERT INTO schedules (schedule_id, project_name, project_id) VALUES (%s, 'P0 Test Schedule', %s)", (schedule_id, project_id))
        conn.execute(
            """INSERT INTO schedule_activities (
                schedule_id, activity_id, activity_name, discipline, location,
                planned_start, planned_finish, baseline_pct_complete, project_id
            ) VALUES (%s, %s, %s, %s, %s, CURRENT_DATE, CURRENT_DATE + 10, 0.0, %s)""",
            (schedule_id, "CIV-PS3-FND-001", "Pump P-101/P-102 Foundation Blinding", "CIVIL", "Pump Station 3", project_id),
        )
        conn.commit()
    _STATE.update(project_id=project_id, schedule_id=schedule_id)
    for h in (ENG_HEADERS, SUP_HEADERS):
        h.update({"X-Project-ID": project_id, "X-Schedule-ID": schedule_id})
    yield
    with get_connection() as conn:
        conn.execute("DELETE FROM validation_issues WHERE event_id IN (SELECT event_id FROM execution_events WHERE schedule_id = %s)", (schedule_id,))
        conn.execute("DELETE FROM candidate_matches WHERE schedule_id = %s", (schedule_id,))
        conn.execute("DELETE FROM source_references WHERE event_id IN (SELECT event_id FROM execution_events WHERE schedule_id = %s)", (schedule_id,))
        conn.execute("DELETE FROM audit_logs WHERE project_id = %s", (project_id,))
        conn.execute("DELETE FROM execution_events WHERE schedule_id = %s", (schedule_id,))
        conn.execute("DELETE FROM schedule_activities WHERE schedule_id = %s", (schedule_id,))
        conn.execute("DELETE FROM schedules WHERE schedule_id = %s", (schedule_id,))
        conn.execute("DELETE FROM project_memberships WHERE project_id = %s", (project_id,))
        conn.execute("DELETE FROM projects WHERE project_id = %s", (project_id,))
        conn.commit()


def test_typed_claim_with_evidence_photo():
    """P0-1: Typed progress claim with attached evidence photo persists end-to-end."""
    _get_or_create_schedule()
    photo_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    filename = "p0_blinding_evidence.png"

    r = client.post(
        "/api/v1/claims/file",
        files={"file": (filename, io.BytesIO(photo_bytes), "image/png")},
        data={
            "purpose": "EVIDENCE_PHOTO",
            "raw_claim_text": "CIV-PS3-FND-001 Foundation blinding is 20% complete at Pump Station 3.",
        },
        headers=ENG_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list) and len(data) == 1
    event = data[0]
    event_id = event["event_id"]
    doc_id = event["document_id"]
    photo_path = event["photo_path"]

    assert event_id is not None
    assert doc_id is not None
    assert photo_path is not None
    assert filename in photo_path
    assert event["input_channel"] == "TYPED_TEXT"

    # Verify DB records
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. source_documents
            cur.execute("SELECT * FROM source_documents WHERE document_id = %s", (doc_id,))
            doc_row = cur.fetchone()
            assert doc_row is not None
            assert doc_row["file_name"] == filename
            assert doc_row["file_hash"] == hashlib.sha256(photo_bytes).hexdigest()

            # 2. execution_events
            cur.execute("SELECT * FROM execution_events WHERE event_id = %s", (event_id,))
            ev_row = cur.fetchone()
            assert ev_row is not None
            assert ev_row["document_id"] == doc_id
            assert ev_row["photo_path"] == photo_path
            assert ev_row["input_channel"] == "TYPED_TEXT"

            # 3. source_references
            cur.execute("SELECT * FROM source_references WHERE event_id = %s", (event_id,))
            ref_rows = cur.fetchall()
            assert len(ref_rows) == 1
            assert ref_rows[0]["file_name"] == filename
            assert "CIV-PS3-FND-001" in ref_rows[0]["raw_snippet"]

    # Verify photo download endpoint returns identical bytes
    r_photo = client.get(f"/api/v1/claims/{event_id}/photo", headers=ENG_HEADERS)
    assert r_photo.status_code == 200
    assert r_photo.content == photo_bytes


def test_typed_claim_with_evidence_document_pdf():
    """P0-1: Typed claim with document evidence (e.g. PDF inspection cert) stores QC evidence without misparsing as DPR."""
    _get_or_create_schedule()
    pdf_bytes = b"%PDF-1.4 dummy inspection certificate for foundation blinding"
    filename = "qa_inspection_cert.pdf"

    r = client.post(
        "/api/v1/claims/file",
        files={"file": (filename, io.BytesIO(pdf_bytes), "application/pdf")},
        data={
            "purpose": "EVIDENCE_PHOTO",
            "raw_claim_text": "CIV-PS3-FND-001 Foundation blinding is 20% complete at Pump Station 3.",
        },
        headers=ENG_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list) and len(data) == 1
    event = data[0]

    assert event["photo_path"] is not None
    assert filename in event["photo_path"]
    assert event["input_channel"] == "TYPED_TEXT"
    assert event["claimed_pct"] == 20.0

    # Verify source_references has filename
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM source_references WHERE event_id = %s", (event["event_id"],))
            ref_rows = cur.fetchall()
            assert len(ref_rows) == 1
            assert ref_rows[0]["file_name"] == filename


def test_clarification_end_to_end_flow():
    """P0-2: Missing field triggers PENDING, blocks matching, clarifies to ANSWERED on same event, resumes pipeline."""
    _get_or_create_schedule()
    photo_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    filename = "pending_blinding.png"

    # Step 1: Submit claim missing progress
    r = client.post(
        "/api/v1/claims/file",
        files={"file": (filename, io.BytesIO(photo_bytes), "image/png")},
        data={
            "purpose": "EVIDENCE_PHOTO",
            "raw_claim_text": "CIV-PS3-FND-001 Foundation blinding work is progressing at Pump Station 3.",
        },
        headers=ENG_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 1
    event = data[0]
    canonical_event_id = event["event_id"]
    initial_doc_id = event["document_id"]
    initial_photo = event["photo_path"]

    assert event["clarification_status"] == "PENDING"
    assert event["clarification_question"] is not None

    # Step 2: Verify matching is BLOCKED while clarification is pending
    r_match_blocked = client.post(f"/api/v1/claims/{canonical_event_id}/match", headers=SUP_HEADERS)
    assert r_match_blocked.status_code == 400
    assert "clarification is pending" in r_match_blocked.text

    # Step 3: Engineer answers the clarification using canonical { "answer": "20%" }
    clarify_payload = {"answer": "20%"}
    r_clarify = client.post(
        f"/api/v1/claims/{canonical_event_id}/clarify",
        json=clarify_payload,
        headers=ENG_HEADERS,
    )
    assert r_clarify.status_code == 200, r_clarify.text
    clarified = r_clarify.json()

    # Invariant: SAME canonical event ID, no duplicate event!
    assert clarified["event_id"] == canonical_event_id
    assert clarified["document_id"] == initial_doc_id
    assert clarified["photo_path"] == initial_photo
    assert clarified["clarification_status"] == "ANSWERED"
    assert clarified["clarification_answer"] == "20%"
    assert clarified["claimed_pct"] == 20.0
    assert "Clarification: 20%" in clarified["raw_claim_text"]

    # Invariant: Verify database row before/after
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM execution_events WHERE event_id = %s", (canonical_event_id,))
            db_event = cur.fetchone()
            assert db_event["clarification_status"] == "ANSWERED"
            assert db_event["clarification_answer"] == "20%"
            assert db_event["claimed_pct"] == 20.0
            assert db_event["photo_path"] == initial_photo
            assert db_event["document_id"] == initial_doc_id

    # Step 4: Resume matching now that clarification is answered
    r_match = client.post(f"/api/v1/claims/{canonical_event_id}/match", headers=SUP_HEADERS)
    assert r_match.status_code == 200, r_match.text
    match_data = r_match.json()
    assert match_data["status"] in ("MATCHED", "UNMATCHED")

    # Step 5: Resume checking
    r_check = client.post(f"/api/v1/claims/{canonical_event_id}/check", headers=SUP_HEADERS)
    assert r_check.status_code == 200, r_check.text


def test_clarification_tolerant_legacy_payload():
    """Verify backend accepts legacy clarification_answer gracefully if sent."""
    _get_or_create_schedule()
    photo_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("test_legacy.png", io.BytesIO(photo_bytes), "image/png")},
        data={
            "purpose": "EVIDENCE_PHOTO",
            "raw_claim_text": "CIV-PS3-FND-001 Ongoing excavation at Pump Station 3.",
        },
        headers=ENG_HEADERS,
    )
    data = r.json()[0]
    event_id = data["event_id"]

    r_clarify = client.post(
        f"/api/v1/claims/{event_id}/clarify",
        json={"clarification_answer": "45%"},
        headers=ENG_HEADERS,
    )
    assert r_clarify.status_code == 200
    clarified = r_clarify.json()
    assert clarified["clarification_status"] == "ANSWERED"
    assert clarified["clarification_answer"] == "45%"
    assert clarified["claimed_pct"] == 45.0


def test_clarification_without_evidence():
    """Missing field triggers PENDING on typed claim with NO evidence, blocks matching, clarifies to ANSWERED on same event, keeps photo_path=NULL."""
    _get_or_create_schedule()

    # Step 1: Submit claim missing progress with NO evidence
    r = client.post(
        "/api/v1/claims/text",
        json={
            "raw_claim_text": "CIV-PS3-FND-001 Foundation blinding work is progressing without photo at Pump Station 3.",
            "input_channel": "TYPED_TEXT",
        },
        headers=ENG_HEADERS,
    )
    assert r.status_code == 200, r.text
    event = r.json()
    canonical_event_id = event["event_id"]
    assert event["photo_path"] is None
    assert event["document_id"] is None
    assert event["clarification_status"] == "PENDING"
    assert event["clarification_question"] is not None

    # Step 2: Verify matching is BLOCKED while clarification is pending
    r_match_blocked = client.post(f"/api/v1/claims/{canonical_event_id}/match", headers=SUP_HEADERS)
    assert r_match_blocked.status_code == 400
    assert "clarification is pending" in r_match_blocked.text

    # Step 3: Answer clarification
    r_clarify = client.post(
        f"/api/v1/claims/{canonical_event_id}/clarify",
        json={"answer": "20%"},
        headers=ENG_HEADERS,
    )
    assert r_clarify.status_code == 200, r_clarify.text
    clarified = r_clarify.json()

    assert clarified["event_id"] == canonical_event_id
    assert clarified["document_id"] is None
    assert clarified["photo_path"] is None
    assert clarified["clarification_status"] == "ANSWERED"
    assert clarified["clarification_answer"] == "20%"
    assert clarified["claimed_pct"] == 20.0

    # Step 4: Verify DB row retains NULL photo_path and NULL document_id
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM execution_events WHERE event_id = %s", (canonical_event_id,))
            db_event = cur.fetchone()
            assert db_event["photo_path"] is None
            assert db_event["document_id"] is None
            assert db_event["clarification_status"] == "ANSWERED"

    # Step 5: Matching and checking resume
    r_match = client.post(f"/api/v1/claims/{canonical_event_id}/match", headers=SUP_HEADERS)
    assert r_match.status_code == 200
    r_check = client.post(f"/api/v1/claims/{canonical_event_id}/check", headers=SUP_HEADERS)
    assert r_check.status_code == 200


def test_multiple_reports_same_activity_no_cross_linking():
    """Verify 3 separate reports on the same activity have strictly isolated evidence records:
    Report 1 -> 20% -> NO evidence
    Report 2 -> 55% -> evidence_002.png
    Report 3 -> 100% -> evidence_003.pdf
    Expected: zero cross-linking.
    """
    schedule_id = _get_or_create_schedule()
    activity_id = "CIV-PS3-FND-001"

    # Report 1: 20% - NO evidence (typed text endpoint)
    r1 = client.post(
        "/api/v1/claims/text",
        json={
            "raw_claim_text": f"{activity_id} Report 1: 20% progress foundation blinding at Pump Station 3.",
            "input_channel": "TYPED_TEXT",
        },
        headers=ENG_HEADERS,
    )
    assert r1.status_code == 200, r1.text
    ev1 = r1.json()
    assert ev1["photo_path"] is None
    assert ev1["document_id"] is None

    # Report 2: 55% - image evidence
    r2 = client.post(
        "/api/v1/claims/file",
        files={"file": ("evidence_002.png", io.BytesIO(b"png_evidence_bytes"), "image/png")},
        data={"purpose": "EVIDENCE_PHOTO", "raw_claim_text": f"{activity_id} Report 2: 55% progress foundation blinding at Pump Station 3."},
        headers=ENG_HEADERS,
    )
    assert r2.status_code == 200, r2.text
    ev2 = r2.json()[0]
    assert ev2["photo_path"] is not None
    assert "evidence_002.png" in ev2["photo_path"]

    # Report 3: 100% - PDF evidence
    r3 = client.post(
        "/api/v1/claims/file",
        files={"file": ("evidence_003.pdf", io.BytesIO(b"%PDF-1.4 dummy pdf bytes"), "application/pdf")},
        data={"purpose": "EVIDENCE_PHOTO", "raw_claim_text": f"{activity_id} Report 3: 100% complete foundation blinding at Pump Station 3."},
        headers=ENG_HEADERS,
    )
    assert r3.status_code == 200, r3.text
    ev3 = r3.json()[0]
    assert ev3["photo_path"] is not None
    assert "evidence_003.pdf" in ev3["photo_path"]

    created_events = [ev1, ev2, ev3]
    for ev in created_events:
        client.post(f"/api/v1/claims/{ev['event_id']}/match", headers=SUP_HEADERS)
        client.post(f"/api/v1/claims/{ev['event_id']}/check", headers=SUP_HEADERS)

    event_ids = [e["event_id"] for e in created_events]
    assert len(set(event_ids)) == 3, "Events must be distinct"

    # Query Activity History timeline from backend
    r_hist = client.get(
        f"/api/v1/activities/{activity_id}/history",
        params={"schedule_id": schedule_id},
        headers=SUP_HEADERS,
    )
    assert r_hist.status_code == 200, r_hist.text
    timeline = r_hist.json().get("timeline", [])
    timeline_events = {item["event_id"]: item for item in timeline if item.get("event_id") in event_ids}
    assert len(timeline_events) == 3

    # Report 1: NO evidence
    item1 = timeline_events[ev1["event_id"]]
    assert item1["photo_path"] is None
    assert len(item1.get("source_references", [])) == 0, "Report 1 must have NO source_references"

    # Report 2: evidence_002.png
    item2 = timeline_events[ev2["event_id"]]
    assert "evidence_002.png" in item2["photo_path"]
    refs2 = item2.get("source_references", [])
    assert len(refs2) == 1
    assert refs2[0]["file_name"] == "evidence_002.png"

    # Report 3: evidence_003.pdf
    item3 = timeline_events[ev3["event_id"]]
    assert "evidence_003.pdf" in item3["photo_path"]
    refs3 = item3.get("source_references", [])
    assert len(refs3) == 1
    assert refs3[0]["file_name"] == "evidence_003.pdf"


def test_optional_evidence_without_file():
    """Verify when user does not attach evidence:
    - Claim succeeds via normal pipeline
    - photo_path = NULL, document_id = NULL
    - Before vs after DB count: 0 source_documents created, 0 source_references created
    - No fake files on disk
    - GET /claims/{event_id}/photo returns 404
    """
    _get_or_create_schedule()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM source_documents")
            doc_count_before = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM source_references")
            ref_count_before = cur.fetchone()["cnt"]

    r = client.post(
        "/api/v1/claims/text",
        json={
            "raw_claim_text": "CIV-PS3-FND-001 Excavation ongoing without site photo at Pump Station 3.",
            "input_channel": "TYPED_TEXT",
        },
        headers=ENG_HEADERS,
    )
    assert r.status_code == 200, r.text
    event = r.json()
    assert event["photo_path"] is None
    assert event["document_id"] is None
    canonical_event_id = event["event_id"]

    # Verify before vs after: ZERO new source_documents, ZERO new source_references
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM source_documents")
            doc_count_after = cur.fetchone()["cnt"]
            assert doc_count_after == doc_count_before, f"Expected {doc_count_before} docs, got {doc_count_after}"

            cur.execute("SELECT COUNT(*) as cnt FROM source_references")
            ref_count_after = cur.fetchone()["cnt"]
            assert ref_count_after == ref_count_before, f"Expected {ref_count_before} refs, got {ref_count_after}"

            cur.execute("SELECT photo_path, document_id FROM execution_events WHERE event_id = %s", (canonical_event_id,))
            db_event = cur.fetchone()
            assert db_event["photo_path"] is None
            assert db_event["document_id"] is None

            # Verify no fake files on disk
            upload_dir = Path("backend/uploads")
            fake_files = list(upload_dir.glob("*file_not_uploaded*"))
            assert len(fake_files) == 0, f"Found fake placeholder files: {fake_files}"

    # Photo endpoint MUST return 404
    r_photo = client.get(f"/api/v1/claims/{canonical_event_id}/photo", headers=ENG_HEADERS)
    assert r_photo.status_code == 404


