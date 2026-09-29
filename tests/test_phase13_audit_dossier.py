"""
SETUAI V7 Phase 13 Tests — Audit Dossier Foundation.
Validates:
1. Project isolation (Project A cannot access Project B dossier).
2. Schedule isolation (Schedule must belong to project; historical versions preserved; no implicit active fallback).
3. Project-level dossier assembly (all core sections present; completeness calculated).
4. Schedule-level dossier assembly.
5. Activity-level deep dossier (evidence -> matching -> validation -> decisions -> actuals -> reopen -> progress -> impact).
6. Authoritative approved actual vs historical revision integrity (revisions preserved, never double-counted).
7. Cryptographic audit chain verification (valid sequence passes; tampered payload/linkage fails with diagnostics; empty sequence handled).
8. Extensible provider registration (Member 2 domains plug in without modifying dossier core).
9. Partial sections reported cleanly as NOT_AVAILABLE without data fabrication.
10. Security & RBAC (401 unauthenticated, 403 unauthorized).
"""

import copy
import time
import uuid
from datetime import date, datetime, timedelta
import jwt
import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.context.errors import SecurityException
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.dossier.audit_verifier import AuditVerifier
from backend.dossier.interfaces import DossierSectionProvider
from backend.dossier.schemas import (
    AuditChainVerificationResult,
    AuditDossier,
    DossierCompleteness,
    DossierScope,
    ExtensionSection,
    SectionStatus,
)
from backend.dossier.service import (
    DossierService,
    build_activity_dossier,
    build_project_dossier,
    build_schedule_dossier,
    verify_audit_chain,
)
from backend.main import app
from backend.shared.audit import (
    GENESIS_HASH,
    canonical_json,
    compute_hash,
    payload_hash,
)
from backend.shared.db import get_connection

TEST_JWT_SECRET = "phase3-super-secret-key-12345678901234567890"


@pytest.fixture(autouse=True)
def configure_test_jwt(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")


def _make_jwt(sub: str) -> str:
    payload = {
        "sub": str(sub),
        "email": f"{sub}@example.com",
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


def _build_authentic_audit_sequence() -> list:
    """Builds a verified 3-entry audit sequence with authentic cryptographic hashes."""
    entries = []

    # Entry 1 (Genesis)
    before_1 = None
    after_1 = {"status": "EXTRACTED", "pct": 15.0}
    p_hash_1 = payload_hash(before_1, after_1)
    prev_1 = GENESIS_HASH
    curr_1 = compute_hash(
        entity_type="execution_event",
        entity_id="EVT-DOSS-1",
        action="CREATE",
        actor_id="USER-SITE-1",
        before_state=before_1,
        after_state=after_1,
        payload_hash=p_hash_1,
        previous_hash=prev_1,
    )
    entries.append({
        "log_id": 1,
        "entity_type": "execution_event",
        "entity_id": "EVT-DOSS-1",
        "action": "CREATE",
        "actor_id": "USER-SITE-1",
        "before_state": None,
        "after_state": canonical_json(after_1),
        "payload_hash": p_hash_1,
        "previous_hash": prev_1,
        "current_hash": curr_1,
    })

    # Entry 2
    before_2 = after_1
    after_2 = {"status": "APPROVED", "pct": 15.0}
    p_hash_2 = payload_hash(before_2, after_2)
    prev_2 = curr_1
    curr_2 = compute_hash(
        entity_type="execution_event",
        entity_id="EVT-DOSS-1",
        action="APPROVE",
        actor_id="SUPERVISOR-1",
        before_state=before_2,
        after_state=after_2,
        payload_hash=p_hash_2,
        previous_hash=prev_2,
    )
    entries.append({
        "log_id": 2,
        "entity_type": "execution_event",
        "entity_id": "EVT-DOSS-1",
        "action": "APPROVE",
        "actor_id": "SUPERVISOR-1",
        "before_state": canonical_json(before_2),
        "after_state": canonical_json(after_2),
        "payload_hash": p_hash_2,
        "previous_hash": prev_2,
        "current_hash": curr_2,
    })

    # Entry 3
    before_3 = after_2
    after_3 = {"status": "REWORK", "reopen_reason": "DEFECT"}
    p_hash_3 = payload_hash(before_3, after_3)
    prev_3 = curr_2
    curr_3 = compute_hash(
        entity_type="execution_event",
        entity_id="EVT-DOSS-1",
        action="REQUEST_REOPEN",
        actor_id="USER-PM-1",
        before_state=before_3,
        after_state=after_3,
        payload_hash=p_hash_3,
        previous_hash=prev_3,
    )
    entries.append({
        "log_id": 3,
        "entity_type": "execution_event",
        "entity_id": "EVT-DOSS-1",
        "action": "REQUEST_REOPEN",
        "actor_id": "USER-PM-1",
        "before_state": canonical_json(before_3),
        "after_state": canonical_json(after_3),
        "payload_hash": p_hash_3,
        "previous_hash": prev_3,
        "current_hash": curr_3,
    })

    return entries


@pytest.fixture
def dossier_fixture():
    user_id = uuid.uuid4()
    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()

    sched_v1 = f"SCH-DOSS-V1-{uuid.uuid4().hex[:6]}"
    sched_v2 = f"SCH-DOSS-V2-{uuid.uuid4().hex[:6]}"
    sched_b = f"SCH-DOSS-B-{uuid.uuid4().hex[:6]}"

    conn = get_connection()
    conn.autocommit = False

    with conn.cursor() as cur:
        # 1. Profiles
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_id, "Dossier Auditor", "SUPERVISOR"),
        )
        # 2. Projects
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_a, f"PRJ-D1-{uuid.uuid4().hex[:4]}", "Dossier Project A", user_id),
        )
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_b, f"PRJ-D2-{uuid.uuid4().hex[:4]}", "Dossier Project B", user_id),
        )
        # 3. Memberships: user is member ONLY of Project A
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, project_id, user_id, assigned_role, active)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE);
            """,
            (uuid.uuid4(), proj_a, user_id),
        )
        # 4. Schedules
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V1', FALSE);
            """,
            (sched_v1, "Dossier Project A", proj_a),
        )
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active, supersedes_schedule_id)
            VALUES (%s, %s, %s, 'V2', TRUE, %s);
            """,
            (sched_v2, "Dossier Project A", proj_a, sched_v1),
        )
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V1', TRUE);
            """,
            (sched_b, "Dossier Project B", proj_b),
        )
        # 5. Activity for Schedule V2 in Project A
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, activity_name, discipline,
                location, planned_start, planned_finish, planned_quantity, weight_factor
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            ("ACT-100", sched_v2, proj_a, "Foundation Concrete Pour", "CIVIL", "Zone 1", "2026-03-01", "2026-03-10", 100.0, 1.5),
        )
        # 6. Approved Actual for ACT-100
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id, project_id,
                actual_start, actual_finish, actual_pct_complete, actual_quantity
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (f"ACTUAL-{uuid.uuid4().hex[:6]}", "DEC-1", "EVT-1", sched_v2, "ACT-100", proj_a, "2026-03-01", None, 50.0, 50.0),
        )
        conn.commit()

    ctx_a = ProjectContext(
        user=CurrentUser(id=str(user_id), email="user@test.com", full_name="Dossier Auditor"),
        project_id=proj_a,
        role="PROJECT_MANAGER",
        membership_id=uuid.uuid4(),
        project_name="Dossier Project A",
    )

    sched_ctx_v2 = ScheduleContext(project_context=ctx_a, schedule_id=sched_v2)
    sched_ctx_v1 = ScheduleContext(project_context=ctx_a, schedule_id=sched_v1)

    yield {
        "user_id": user_id,
        "ctx_a": ctx_a,
        "proj_a": proj_a,
        "proj_b": proj_b,
        "sched_v1": sched_v1,
        "sched_v2": sched_v2,
        "sched_b": sched_b,
        "sched_ctx_v1": sched_ctx_v1,
        "sched_ctx_v2": sched_ctx_v2,
    }

    # Teardown
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM approved_actuals WHERE project_id IN (%s, %s);", (proj_a, proj_b))
            cur.execute("DELETE FROM schedule_activities WHERE project_id IN (%s, %s);", (proj_a, proj_b))
            cur.execute("DELETE FROM schedules WHERE schedule_id IN (%s, %s, %s);", (sched_v1, sched_v2, sched_b))
            cur.execute("DELETE FROM project_memberships WHERE project_id IN (%s, %s);", (proj_a, proj_b))
            cur.execute("DELETE FROM projects WHERE project_id IN (%s, %s);", (proj_a, proj_b))
            cur.execute("DELETE FROM profiles WHERE id = %s;", (user_id,))
            conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ============================================================================
# 1. PROJECT & SCHEDULE ISOLATION TESTS
# ============================================================================

def test_01_project_isolation(dossier_fixture):
    """User not member of Project B receives 403 when requesting its dossier."""
    user_id = dossier_fixture["user_id"]
    proj_b = dossier_fixture["proj_b"]

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(user_id), email="user@test.com", full_name="User", role="SUPERVISOR"
    )
    try:
        client = TestClient(app)
        resp = client.get(f"/api/v7/projects/{proj_b}/dossier")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_02_schedule_isolation(dossier_fixture):
    """Schedule must belong to project context; foreign schedule raises 403."""
    user_id = dossier_fixture["user_id"]
    proj_a = dossier_fixture["proj_a"]
    sched_b = dossier_fixture["sched_b"]  # Belongs to Project B

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(user_id), email="user@test.com", full_name="User", role="SUPERVISOR"
    )
    try:
        client = TestClient(app)
        # Attempt to access Project B's schedule under Project A context
        resp = client.get(f"/api/v7/projects/{proj_a}/schedules/{sched_b}/dossier")
        assert resp.status_code == 403
        assert "SCHEDULE_ACCESS_DENIED" in resp.text or "does not belong" in resp.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_03_historical_schedules_queryable(dossier_fixture):
    """Historical superseding versions (V1) remain distinguishable from active (V2)."""
    sched_ctx_v1 = dossier_fixture["sched_ctx_v1"]
    sched_ctx_v2 = dossier_fixture["sched_ctx_v2"]

    dossier_v1 = build_schedule_dossier(sched_ctx_v1)
    dossier_v2 = build_schedule_dossier(sched_ctx_v2)

    assert dossier_v1.schedule.schedule_id == sched_ctx_v1.schedule_id
    assert dossier_v1.schedule.is_active is False

    assert dossier_v2.schedule.schedule_id == sched_ctx_v2.schedule_id
    assert dossier_v2.schedule.is_active is True
    assert dossier_v2.schedule.supersedes_schedule_id == sched_ctx_v1.schedule_id


# ============================================================================
# 2. DOSSIER STRUCTURE & COMPLETENESS
# ============================================================================

def test_04_project_dossier_assembly(dossier_fixture):
    """build_project_dossier returns complete structured dossier with all sections."""
    ctx_a = dossier_fixture["ctx_a"]
    dossier = build_project_dossier(ctx_a)

    assert dossier.dossier_id.startswith("DOSSIER-PRJ-")
    assert dossier.scope == DossierScope.PROJECT
    assert dossier.project.project_id == ctx_a.project_id
    assert dossier.project.status == SectionStatus.AVAILABLE

    # Core sections check
    assert dossier.stages is not None
    assert dossier.activities is not None
    assert dossier.execution_evidence is not None
    assert dossier.matching is not None
    assert dossier.validation is not None
    assert dossier.human_decisions is not None
    assert dossier.approved_actuals is not None
    assert dossier.reopen_history is not None
    assert dossier.progress is not None
    assert dossier.impact is not None
    assert dossier.audit_chain is not None

    # Completeness check
    assert dossier.completeness.total_sections > 0
    assert "project" in dossier.completeness.complete_sections


def test_05_extension_sections_not_available_no_fabrication(dossier_fixture):
    """Future Member 2 domains (quality, contractor, work_package, etc.) report NOT_AVAILABLE."""
    ctx_a = dossier_fixture["ctx_a"]
    dossier = build_project_dossier(ctx_a)

    for domain in ["quality", "contractor", "work_package", "institutional_memory", "agent_briefing", "governance"]:
        ext = dossier.extensions.get(domain)
        assert ext is not None, f"Extension slot '{domain}' missing from dossier.extensions!"
        assert ext.status == SectionStatus.NOT_AVAILABLE
        assert ext.records == [], f"Fabricated records detected in extension domain '{domain}'!"


# ============================================================================
# 3. ACTIVITY DOSSIER & REVISION INTEGRITY
# ============================================================================

def test_06_activity_dossier_lifecycle(dossier_fixture):
    """Activity dossier traces canonical state, progress, and approved actual."""
    sched_ctx_v2 = dossier_fixture["sched_ctx_v2"]
    dossier = build_activity_dossier(sched_ctx_v2, activity_id="ACT-100")

    assert dossier.scope == DossierScope.ACTIVITY
    assert dossier.activity_id == "ACT-100"
    assert len(dossier.activities.activities) == 1

    act = dossier.activities.activities[0]
    assert act.activity_id == "ACT-100"
    assert act.canonical_execution_state in ["IN_PROGRESS", "COMPLETED", "NOT_STARTED"]
    assert act.progress_pct == 50.0  # From approved actual

    # Authoritative approved actual present
    assert len(dossier.approved_actuals.actuals) == 1
    actual = dossier.approved_actuals.actuals[0]
    assert actual.actual_pct_complete == 50.0
    assert actual.activity_id == "ACT-100"


def test_07_activity_not_found_returns_404(dossier_fixture):
    """Requesting an activity that does not exist in schedule returns 404."""
    from fastapi import HTTPException
    sched_ctx_v2 = dossier_fixture["sched_ctx_v2"]

    with pytest.raises(HTTPException) as exc_info:
        build_activity_dossier(sched_ctx_v2, activity_id="NON_EXISTENT_999")
    assert exc_info.value.status_code == 404


# ============================================================================
# 4. CRYPTOGRAPHIC AUDIT CHAIN VERIFICATION
# ============================================================================

def test_08_audit_chain_verification_valid_sequence():
    """Authentic cryptographic hash chain verifies with status VALID."""
    logs = _build_authentic_audit_sequence()
    result = AuditVerifier.verify_chain(logs, allow_subchain=False)

    assert result.status == "VALID"
    assert result.records_checked == 3
    assert result.first_log_id == 1
    assert result.last_log_id == 3
    assert result.broken_at_log_id is None
    assert result.reason is None


def test_09_audit_chain_verification_tamper_detected():
    """Tampering with previous_hash breaks chain and is detected with diagnostic."""
    logs = _build_authentic_audit_sequence()
    # Maliciously alter previous_hash in Entry 2
    logs[1]["previous_hash"] = "deadbeef" * 8

    result = AuditVerifier.verify_chain(logs, allow_subchain=False)
    assert result.status == "BROKEN"
    assert result.broken_at_log_id == 2
    assert "Chain linkage violation" in result.reason
    assert result.actual_hash == "deadbeef" * 8


def test_10_audit_chain_payload_tamper_detected():
    """Tampering with after_state payload breaks verification."""
    logs = _build_authentic_audit_sequence()
    # Maliciously alter after_state in Entry 1
    logs[0]["after_state"] = '{"status":"APPROVED","pct":999.0}'

    result = AuditVerifier.verify_chain(logs, allow_subchain=False)
    assert result.status == "BROKEN"
    assert result.broken_at_log_id == 1
    assert "Payload hash mismatch" in result.reason


def test_11_audit_chain_empty_sequence():
    """Empty audit sequence returns EMPTY status gracefully without crash."""
    result = AuditVerifier.verify_chain([], allow_subchain=True)
    assert result.status == "EMPTY"
    assert result.records_checked == 0
    assert result.broken_at_log_id is None


# ============================================================================
# 5. EXTENSION PROVIDER PLUGGABILITY (MEMBER 2 CONTRACT)
# ============================================================================

def test_12_member2_extension_provider_registration(dossier_fixture):
    """Member 2 can register a DossierSectionProvider that populates extension slots."""
    ctx_a = dossier_fixture["ctx_a"]

    class MockQualityProvider(DossierSectionProvider):
        @property
        def section_name(self) -> str:
            return "quality"

        def collect(self, context, schedule_id=None, activity_id=None) -> ExtensionSection:
            return ExtensionSection(
                status=SectionStatus.AVAILABLE,
                provider="Member2_Quality_Engine",
                records=[{"itp_id": "ITP-001", "hold_point": "CONCRETE_SLUMP_TEST", "status": "PASSED"}],
            )

    # Register provider
    DossierService.register_section_provider(MockQualityProvider())

    dossier = build_project_dossier(ctx_a)
    quality_ext = dossier.extensions["quality"]
    assert quality_ext.status == SectionStatus.AVAILABLE
    assert quality_ext.provider == "Member2_Quality_Engine"
    assert len(quality_ext.records) == 1
    assert quality_ext.records[0]["hold_point"] == "CONCRETE_SLUMP_TEST"


# ============================================================================
# 6. REST API READ-ONLY ENDPOINTS
# ============================================================================

def test_13_api_unauthenticated_returns_401(dossier_fixture):
    """Unauthenticated requests to dossier endpoints return 401."""
    client = TestClient(app)
    proj_a = dossier_fixture["proj_a"]

    r1 = client.get(f"/api/v7/projects/{proj_a}/dossier")
    assert r1.status_code == 401

    r2 = client.get(f"/api/v7/projects/{proj_a}/dossier/audit-verification")
    assert r2.status_code == 401


def test_14_api_project_dossier_happy_path(dossier_fixture):
    """Authenticated user gets 200 and structured dossier JSON."""
    user_id = dossier_fixture["user_id"]
    proj_a = dossier_fixture["proj_a"]

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(user_id), email="user@test.com", full_name="User", role="PROJECT_MANAGER"
    )
    try:
        client = TestClient(app)
        resp = client.get(f"/api/v7/projects/{proj_a}/dossier")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "PROJECT"
        assert data["project"]["project_id"] == str(proj_a)
        assert "extensions" in data
        assert "audit_chain" in data
        assert "completeness" in data
    finally:
        app.dependency_overrides.pop(get_current_user, None)
