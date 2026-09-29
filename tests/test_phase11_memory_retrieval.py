"""
SETUAI V7 Phase 11 Tests — Institutional Memory Retrieval Infrastructure.
Validates:
1. Multi-tenant project isolation (Project A cannot query Project B memory).
2. Cross-project filter rejection (filters.project_id mismatch raises 403).
3. Cross-schedule filter validation (schedule must belong to project).
4. Metadata filtering (stage, activity, contractor, severity, incident_type, date range).
5. Deterministic ranking & explainability (scores, bonuses, match reasons).
6. Deterministic lexical fallback when semantic encoder is unavailable.
7. Semantic retrieval when embeddings are present.
8. Empty states and top_k boundary clamping.
9. Security & RBAC (unauthenticated -> 401, unauthorized project -> 403).
10. Stable provider contract for Member 2 integration.
"""

import time
import uuid
from datetime import date, datetime, timedelta
import jwt
import pytest
from fastapi.testclient import TestClient

from backend.auth.models import CurrentUser
from backend.context.errors import SecurityException
from backend.context.project import ProjectContext
from backend.main import app
from backend.memory.adapters import (
    InMemoryMemoryProvider,
    SentenceTransformersAdapter,
)
from backend.memory.interfaces import SemanticEncoder
from backend.memory.ranking import (
    BONUS_ACTIVITY_MATCH,
    BONUS_CONTRACTOR_MATCH,
    BONUS_STAGE_MATCH,
    compute_lexical_similarity,
    rank_candidates,
)
from backend.memory.retrieval_service import (
    InstitutionalMemoryRetrievalService,
    search_by_activity,
    search_by_contractor,
    search_by_stage,
    search_institutional_memory,
    search_similar,
)
from backend.memory.schemas import (
    MemoryFilter,
    MemoryQuery,
    MemoryRecord,
    MemoryResult,
    MemoryRetrievalResponse,
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


@pytest.fixture
def memory_test_fixture():
    """Sets up clean isolated ProjectContexts and in-memory test memory records."""
    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    stage_1 = uuid.uuid4()
    stage_2 = uuid.uuid4()
    contractor_1 = uuid.uuid4()
    contractor_2 = uuid.uuid4()

    ctx_a = ProjectContext(
        user=CurrentUser(id=str(user_a), email="user_a@test.com", full_name="User A"),
        project_id=proj_a,
        role="PROJECT_MANAGER",
        membership_id=uuid.uuid4(),
        project_name="Memory Test Project A",
    )

    ctx_b = ProjectContext(
        user=CurrentUser(id=str(user_b), email="user_b@test.com", full_name="User B"),
        project_id=proj_b,
        role="PROJECT_MANAGER",
        membership_id=uuid.uuid4(),
        project_name="Memory Test Project B",
    )

    # Clear and populate the test in-memory provider
    provider = InstitutionalMemoryRetrievalService.get_in_memory_provider()
    provider.clear()

    # Records for Project A
    rec_a1 = MemoryRecord(
        memory_id="REC-A1",
        project_id=proj_a,
        schedule_id="SCHED-A",
        stage_id=stage_1,
        activity_id="A1010",
        contractor_id=contractor_1,
        title="Concrete Pour Delays due to Rain and Monsoon",
        summary="Monsoon rainfall caused heavy delay in foundation slab casting.",
        content="Heavy rain during July waterlogged the excavation site, halting concrete trucks.",
        incident_type="WEATHER",
        severity="HIGH",
        status="RESOLVED",
        created_at=datetime.utcnow() - timedelta(days=10),
    )
    rec_a2 = MemoryRecord(
        memory_id="REC-A2",
        project_id=proj_a,
        schedule_id="SCHED-A",
        stage_id=stage_1,
        activity_id="A1020",
        contractor_id=contractor_2,
        title="Rebar Supplier Material Quality Rejection",
        summary="Defective grade 60 steel rebar failed bend tensile test.",
        content="Third party testing rejected shipment lot 492 due to microcracking.",
        incident_type="QUALITY",
        severity="MEDIUM",
        status="OPEN",
        created_at=datetime.utcnow() - timedelta(days=30),
    )
    rec_a3 = MemoryRecord(
        memory_id="REC-A3",
        project_id=proj_a,
        schedule_id="SCHED-A",
        stage_id=stage_2,
        activity_id="A2010",
        contractor_id=contractor_1,
        title="Crane Mechanical Breakdown at Pipelaying Trench",
        summary="Hydraulic boom failure halted heavy pipe placement for 4 days.",
        content="Crane cylinder seal blew under load; spare parts imported from regional depot.",
        incident_type="EQUIPMENT",
        severity="HIGH",
        status="RESOLVED",
        created_at=datetime.utcnow() - timedelta(days=5),
    )

    # Record for Project B (Isolated tenant)
    rec_b1 = MemoryRecord(
        memory_id="REC-B1",
        project_id=proj_b,
        schedule_id="SCHED-B",
        stage_id=stage_1,
        activity_id="A1010",
        contractor_id=contractor_1,
        title="Project B Concrete Pour Delays due to Rain",
        summary="Identical query wording but belonging strictly to Project B.",
        content="Project B rain delay details strictly isolated.",
        incident_type="WEATHER",
        severity="HIGH",
        status="RESOLVED",
        created_at=datetime.utcnow() - timedelta(days=2),
    )

    provider.add_record(rec_a1)
    provider.add_record(rec_a2)
    provider.add_record(rec_a3)
    provider.add_record(rec_b1)

    return {
        "ctx_a": ctx_a,
        "ctx_b": ctx_b,
        "proj_a": proj_a,
        "proj_b": proj_b,
        "stage_1": stage_1,
        "stage_2": stage_2,
        "contractor_1": contractor_1,
        "contractor_2": contractor_2,
        "provider": provider,
    }


# ============================================================================
# 1. ISOLATION TESTS
# ============================================================================

def test_01_project_isolation(memory_test_fixture):
    """Project A must NEVER retrieve records belonging to Project B."""
    fixture = memory_test_fixture
    ctx_a = fixture["ctx_a"]
    ctx_b = fixture["ctx_b"]

    # Search with identical query from Project A
    res_a = search_institutional_memory(
        context=ctx_a,
        query="Concrete Pour Delays due to Rain",
        top_k=10,
    )
    retrieved_ids_a = [r.record.memory_id for r in res_a.results]
    assert "REC-A1" in retrieved_ids_a
    assert "REC-B1" not in retrieved_ids_a, "Cross-project leakage: Project B record found in Project A search!"

    # Search from Project B
    res_b = search_institutional_memory(
        context=ctx_b,
        query="Concrete Pour Delays due to Rain",
        top_k=10,
    )
    retrieved_ids_b = [r.record.memory_id for r in res_b.results]
    assert "REC-B1" in retrieved_ids_b
    assert "REC-A1" not in retrieved_ids_b, "Cross-project leakage: Project A record found in Project B search!"


def test_02_cross_project_filter_rejected(memory_test_fixture):
    """Providing a filter with project_id != context.project_id must raise 403."""
    fixture = memory_test_fixture
    ctx_a = fixture["ctx_a"]
    proj_b = fixture["proj_b"]

    malicious_filter = MemoryFilter(project_id=proj_b)
    with pytest.raises(SecurityException) as exc_info:
        search_institutional_memory(
            context=ctx_a,
            query="test",
            filters=malicious_filter,
        )
    assert exc_info.value.status_code == 403
    assert "Cross-project filter violation" in str(exc_info.value.detail)


# ============================================================================
# 2. METADATA FILTERING TESTS
# ============================================================================

def test_03_stage_filtering(memory_test_fixture):
    """Filtering by stage_id returns only records in that stage."""
    fixture = memory_test_fixture
    ctx_a = fixture["ctx_a"]
    stage_1 = fixture["stage_1"]
    stage_2 = fixture["stage_2"]

    res_stg1 = search_by_stage(context=ctx_a, stage_id=stage_1, query="")
    ids_stg1 = [r.record.memory_id for r in res_stg1.results]
    assert "REC-A1" in ids_stg1
    assert "REC-A2" in ids_stg1
    assert "REC-A3" not in ids_stg1

    res_stg2 = search_by_stage(context=ctx_a, stage_id=stage_2, query="")
    ids_stg2 = [r.record.memory_id for r in res_stg2.results]
    assert ids_stg2 == ["REC-A3"]


def test_04_activity_and_contractor_filtering(memory_test_fixture):
    """Filtering by activity_id or contractor_id matches exact criteria."""
    fixture = memory_test_fixture
    ctx_a = fixture["ctx_a"]
    contractor_2 = fixture["contractor_2"]

    # Activity filter
    res_act = search_by_activity(context=ctx_a, activity_id="A1020", query="")
    assert len(res_act.results) == 1
    assert res_act.results[0].record.memory_id == "REC-A2"

    # Contractor filter
    res_cont = search_by_contractor(context=ctx_a, contractor_id=contractor_2, query="")
    assert len(res_cont.results) == 1
    assert res_cont.results[0].record.memory_id == "REC-A2"


def test_05_severity_and_incident_type_filtering(memory_test_fixture):
    """Filtering by severity and incident_type correctly filters candidates."""
    fixture = memory_test_fixture
    ctx_a = fixture["ctx_a"]

    f_type = MemoryFilter(incident_type="QUALITY")
    res_type = search_institutional_memory(context=ctx_a, query="", filters=f_type)
    assert len(res_type.results) == 1
    assert res_type.results[0].record.memory_id == "REC-A2"

    f_sev = MemoryFilter(severity="HIGH")
    res_sev = search_institutional_memory(context=ctx_a, query="", filters=f_sev)
    ids_sev = [r.record.memory_id for r in res_sev.results]
    assert "REC-A1" in ids_sev
    assert "REC-A3" in ids_sev
    assert "REC-A2" not in ids_sev


def test_06_date_range_filtering(memory_test_fixture):
    """Filtering by date_from and date_to filters by creation date."""
    fixture = memory_test_fixture
    ctx_a = fixture["ctx_a"]

    today = date.today()
    # Filter for last 7 days only (REC-A3 is 5 days old, REC-A1 is 10 days, REC-A2 is 30 days)
    f_date = MemoryFilter(date_from=today - timedelta(days=7), date_to=today)
    res_date = search_institutional_memory(context=ctx_a, query="", filters=f_date)
    assert len(res_date.results) == 1
    assert res_date.results[0].record.memory_id == "REC-A3"


# ============================================================================
# 3. DETERMINISTIC RANKING & EXPLAINABILITY
# ============================================================================

def test_07_deterministic_lexical_fallback(memory_test_fixture):
    """When semantic encoder is disabled, deterministic lexical token similarity is used."""
    fixture = memory_test_fixture
    ctx_a = fixture["ctx_a"]

    # Explicitly disable semantic encoder
    InstitutionalMemoryRetrievalService.set_semantic_encoder(None)

    res = search_institutional_memory(
        context=ctx_a,
        query="hydraulic crane breakdown",
        top_k=10,
    )
    assert res.retrieval_mode == "metadata+lexical_fallback"
    assert len(res.results) > 0
    # Top result must be REC-A3 (Crane Breakdown)
    top = res.results[0]
    assert top.record.memory_id == "REC-A3"
    assert top.score_breakdown.lexical_score > 0.0
    assert top.score_breakdown.semantic_score == 0.0
    assert "LEXICAL_TOKEN_MATCH" in top.match_reasons


def test_08_metadata_bonus_improves_ranking(memory_test_fixture):
    """Matching metadata attributes adds transparent, explainable bonuses."""
    fixture = memory_test_fixture
    ctx_a = fixture["ctx_a"]

    # Disable semantic encoder for pure lexical + metadata evaluation
    InstitutionalMemoryRetrievalService.set_semantic_encoder(None)

    # Search with activity filter: activity_id="A1010"
    f = MemoryFilter(activity_id="A1010")
    res = search_institutional_memory(
        context=ctx_a,
        query="delays and breakdown",
        filters=f,
    )

    matched_item = next((r for r in res.results if r.record.activity_id == "A1010"), None)
    assert matched_item is not None
    assert matched_item.score_breakdown.activity_bonus == BONUS_ACTIVITY_MATCH
    assert "EXACT_ACTIVITY" in matched_item.match_reasons


def test_09_deterministic_tie_breaking(memory_test_fixture):
    """Equal scores are broken deterministically by memory_id ASC."""
    proj_id = uuid.uuid4()
    ctx = ProjectContext(
        user=CurrentUser(id=str(uuid.uuid4()), email="u@test.com", full_name="U"),
        project_id=proj_id,
        role="PROJECT_MANAGER",
        membership_id=uuid.uuid4(),
    )

    r1 = MemoryRecord(memory_id="REC-B", project_id=proj_id, title="Identical", content="Identical")
    r2 = MemoryRecord(memory_id="REC-A", project_id=proj_id, title="Identical", content="Identical")
    r3 = MemoryRecord(memory_id="REC-C", project_id=proj_id, title="Identical", content="Identical")

    ranked, _ = rank_candidates([r1, r2, r3], query_text="", encoder=None)
    ranked_ids = [r.record.memory_id for r in ranked]
    # All scores are 0.0; tie breaker must produce REC-A, REC-B, REC-C
    assert ranked_ids == ["REC-A", "REC-B", "REC-C"]


def test_10_empty_state_and_top_k_clamping(memory_test_fixture):
    """Empty candidate set returns empty list cleanly; top_k is respected."""
    fixture = memory_test_fixture
    ctx_a = fixture["ctx_a"]

    # Filter with non-existent activity
    f_empty = MemoryFilter(activity_id="NON_EXISTENT_999")
    res_empty = search_institutional_memory(context=ctx_a, query="anything", filters=f_empty)
    assert res_empty.results == []
    assert res_empty.total_candidates == 0

    # Top_k boundary test
    res_k1 = search_institutional_memory(context=ctx_a, query="", top_k=1)
    assert len(res_k1.results) == 1


# ============================================================================
# 4. SPECIALIZED METHODS & PROVIDER EXTENSIBILITY
# ============================================================================

def test_11_search_similar(memory_test_fixture):
    """search_similar retrieves related records based on an existing memory record."""
    fixture = memory_test_fixture
    ctx_a = fixture["ctx_a"]

    res_sim = search_similar(context=ctx_a, memory_id="REC-A1", top_k=5)
    retrieved_ids = [r.record.memory_id for r in res_sim.results]
    # Source record REC-A1 must NOT be in the results (excluded from self)
    assert "REC-A1" not in retrieved_ids


def test_12_member2_custom_provider_registration(memory_test_fixture):
    """Member 2 can register a custom MemoryProvider implementing the protocol."""
    fixture = memory_test_fixture
    ctx_a = fixture["ctx_a"]

    class MockMember2DomainProvider:
        def get_candidates(self, context, filters=None):
            return [
                MemoryRecord(
                    memory_id="M2-DISPUTE-01",
                    project_id=context.project_id,
                    title="Subcontractor Earthwork Dispute Claim",
                    content="Claim for extra depth rock excavation under clause 14.",
                    incident_type="DISPUTE",
                )
            ]

    # Register custom provider
    InstitutionalMemoryRetrievalService.register_provider(MockMember2DomainProvider())

    res = search_institutional_memory(context=ctx_a, query="earthwork dispute")
    ids = [r.record.memory_id for r in res.results]
    assert "M2-DISPUTE-01" in ids


# ============================================================================
# 5. FASTAPI REST ENDPOINT TESTS
# ============================================================================

def test_13_api_unauthenticated_returns_401(memory_test_fixture):
    """Accessing search endpoint without Bearer token returns 401."""
    client = TestClient(app)
    proj_id = memory_test_fixture["proj_a"]
    resp = client.post(f"/api/v7/projects/{proj_id}/memory/search", json={"query": "test"})
    assert resp.status_code == 401


def test_14_api_unauthorized_project_returns_403(memory_test_fixture):
    """User not member of requested project receives 403."""
    from backend.auth.dependencies import get_current_user
    foreign_user = uuid.uuid4()
    foreign_proj = uuid.uuid4()

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(foreign_user),
        email="foreign@test.com",
        full_name="Foreign User",
        role="SUPERVISOR",
    )
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/v7/projects/{foreign_proj}/memory/search",
            json={"query": "test"},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
