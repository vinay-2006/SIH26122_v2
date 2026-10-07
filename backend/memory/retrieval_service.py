"""
Authoritative Institutional Memory Retrieval Service for SETUAI V7 Phase 11.
Provides generic retrieval, metadata filtering, semantic ranking, deterministic fallback,
and multi-tenant project isolation.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from backend.context.errors import (
    raise_invalid_project_context,
    raise_project_denied,
    raise_schedule_denied,
)
from backend.context.project import ProjectContext
from backend.memory.adapters import (
    CompositeMemoryProvider,
    DatabaseMemoryProvider,
    InMemoryMemoryProvider,
    SentenceTransformersAdapter,
)
from backend.memory.interfaces import MemoryProvider, SemanticEncoder
from backend.memory.ranking import rank_candidates
from backend.memory.schemas import (
    MemoryFilter,
    MemoryRecord,
    MemoryResult,
    MemoryRetrievalResponse,
)
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


class InstitutionalMemoryRetrievalService:
    """
    Authoritative generic retrieval layer for institutional memory.
    Ensures strict project isolation and deterministic ranking.
    """

    # Global default provider and encoder instances
    _in_memory_provider: InMemoryMemoryProvider = InMemoryMemoryProvider()
    _database_provider: DatabaseMemoryProvider = DatabaseMemoryProvider()
    _composite_provider: CompositeMemoryProvider = CompositeMemoryProvider(
        [_in_memory_provider, _database_provider]
    )
    _semantic_encoder: Optional[SemanticEncoder] = SentenceTransformersAdapter()

    @classmethod
    def register_provider(cls, provider: MemoryProvider) -> None:
        """Register an additional provider (e.g. from Member 2's domain)."""
        cls._composite_provider.add_provider(provider)

    @classmethod
    def get_in_memory_provider(cls) -> InMemoryMemoryProvider:
        """Access in-memory provider for test seeding or mocking."""
        return cls._in_memory_provider

    @classmethod
    def set_semantic_encoder(cls, encoder: Optional[SemanticEncoder]) -> None:
        """Configure or disable the semantic encoder."""
        cls._semantic_encoder = encoder

    # =========================================================================
    # CORE VALIDATION & ISOLATION
    # =========================================================================

    @classmethod
    def _validate_filter_isolation(
        cls,
        context: ProjectContext,
        filters: Optional[MemoryFilter],
    ) -> None:
        """
        Enforce strict project boundary:
        1. If filters.project_id is set, it MUST match context.project_id.
        2. If filters.schedule_id is set, the schedule MUST belong to context.project_id.
        """
        if not filters:
            return

        if filters.project_id is not None:
            if str(filters.project_id) != str(context.project_id):
                raise_project_denied(
                    f"Cross-project filter violation: filter requested project '{filters.project_id}' "
                    f"but caller is authorized only for '{context.project_id}'"
                )

        if filters.schedule_id is not None:
            sched_id = filters.schedule_id.strip()
            try:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT project_id FROM schedules WHERE schedule_id = %s;",
                            (sched_id,),
                        )
                        row = cur.fetchone()
            except Exception as e:
                logger.warning("Database error validating schedule in memory filter: %s", e)
                row = None

            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Schedule '{sched_id}' referenced in filter does not exist.",
                )

            sched_proj_id = row["project_id"] if isinstance(row, dict) else row[0]
            if str(sched_proj_id) != str(context.project_id):
                raise_schedule_denied(
                    f"Schedule '{sched_id}' does not belong to authorized project context '{context.project_id}'"
                )

    # =========================================================================
    # GENERIC RETRIEVAL PIPELINE
    # =========================================================================

    @classmethod
    def search(
        cls,
        context: ProjectContext,
        query: str,
        filters: Optional[MemoryFilter] = None,
        top_k: int = 10,
    ) -> MemoryRetrievalResponse:
        """
        Main two-stage retrieval pipeline:
        Stage A: Validate project context and retrieve candidate records via metadata filters.
        Stage B: Deterministic explainable ranking (semantic similarity if available, else lexical fallback).
        """
        if context is None or context.project_id is None:
            raise_invalid_project_context("Valid ProjectContext is required for memory retrieval.")

        # 1. Enforce isolation constraints on incoming filters
        cls._validate_filter_isolation(context, filters)

        # 2. Retrieve candidates scoped to context.project_id
        candidates: List[MemoryRecord] = cls._composite_provider.get_candidates(
            context=context,
            filters=filters,
        )

        # 3. Deterministic explainable ranking
        ranked_results, retrieval_mode = rank_candidates(
            candidates=candidates,
            query_text=query,
            filters=filters,
            encoder=cls._semantic_encoder,
            top_k=max(1, min(top_k, 100)),
        )

        # 4. Serialize applied filters for transparency
        applied_dict = filters.model_dump(exclude_none=True) if filters else {}

        return MemoryRetrievalResponse(
            query=query,
            results=ranked_results,
            total_candidates=len(candidates),
            retrieval_mode=retrieval_mode,
            filters_applied=applied_dict,
        )

    # =========================================================================
    # SPECIALIZED SEARCH METHODS (DELEGATE TO GENERIC PIPELINE)
    # =========================================================================

    @classmethod
    def search_by_activity(
        cls,
        context: ProjectContext,
        activity_id: str,
        query: str = "",
        filters: Optional[MemoryFilter] = None,
        top_k: int = 10,
    ) -> MemoryRetrievalResponse:
        """Search memory with an explicit activity_id filter and bonus."""
        f = filters.model_copy() if filters else MemoryFilter()
        f.activity_id = activity_id
        return cls.search(context=context, query=query, filters=f, top_k=top_k)

    @classmethod
    def search_by_stage(
        cls,
        context: ProjectContext,
        stage_id: uuid.UUID,
        query: str = "",
        filters: Optional[MemoryFilter] = None,
        top_k: int = 10,
    ) -> MemoryRetrievalResponse:
        """Search memory scoped to a specific stage."""
        f = filters.model_copy() if filters else MemoryFilter()
        f.stage_id = stage_id
        return cls.search(context=context, query=query, filters=f, top_k=top_k)

    @classmethod
    def search_by_contractor(
        cls,
        context: ProjectContext,
        contractor_id: uuid.UUID,
        query: str = "",
        filters: Optional[MemoryFilter] = None,
        top_k: int = 10,
    ) -> MemoryRetrievalResponse:
        """Search memory scoped to a specific contractor."""
        f = filters.model_copy() if filters else MemoryFilter()
        f.contractor_id = contractor_id
        return cls.search(context=context, query=query, filters=f, top_k=top_k)

    @classmethod
    def search_for_issue(
        cls,
        context: ProjectContext,
        *,
        category_code: str,
        query: str = "",
        activity_id: Optional[str] = None,
        stage_id: Optional[uuid.UUID] = None,
        top_k: int = 5,
    ) -> MemoryRetrievalResponse:
        """
        Historical incidents relevant to an issue: same category (this project's own records and lessons other projects
        chose to share), ranked by text similarity with bonuses for the same activity / stage in this project.
        Candidate selection uses the category only; activity/stage are ranking signals, never exclusions, so a lesson
        from a similar delay elsewhere is still found.
        """
        if context is None or context.project_id is None:
            raise_invalid_project_context("Valid ProjectContext is required for memory retrieval.")
        candidates = cls._composite_provider.get_candidates(
            context=context, filters=MemoryFilter(incident_type=category_code)
        )
        rank_filters = MemoryFilter(incident_type=category_code, activity_id=activity_id, stage_id=stage_id)
        ranked, mode = rank_candidates(
            candidates=candidates, query_text=query, filters=rank_filters,
            encoder=cls._semantic_encoder, top_k=max(1, min(top_k, 50)),
        )
        return MemoryRetrievalResponse(
            query=query, results=ranked, total_candidates=len(candidates), retrieval_mode=mode,
            filters_applied={"category_code": category_code, **({"activity_id": activity_id} if activity_id else {}),
                             **({"stage_id": str(stage_id)} if stage_id else {})},
        )

    @classmethod
    def search_similar(
        cls,
        context: ProjectContext,
        memory_id: str,
        top_k: int = 10,
    ) -> MemoryRetrievalResponse:
        """
        Find memory records similar to an existing memory record.
        Uses the target record's title and summary as the query text.
        """
        candidates = cls._composite_provider.get_candidates(context=context)
        target = next((r for r in candidates if r.memory_id == memory_id), None)
        if not target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target memory record '{memory_id}' not found in project '{context.project_id}'",
            )

        query_text = f"{target.title} {target.summary or ''}"
        # Filter out the source record itself
        filtered_candidates = [r for r in candidates if r.memory_id != memory_id]

        ranked_results, retrieval_mode = rank_candidates(
            candidates=filtered_candidates,
            query_text=query_text,
            encoder=cls._semantic_encoder,
            top_k=max(1, min(top_k, 100)),
        )

        return MemoryRetrievalResponse(
            query=query_text,
            results=ranked_results,
            total_candidates=len(filtered_candidates),
            retrieval_mode=retrieval_mode,
            filters_applied={"similar_to_memory_id": memory_id},
        )


# ============================================================================
# STABLE CONVENIENCE FUNCTIONS FOR MEMBER 2 CONSUMPTION
# ============================================================================

def search_institutional_memory(
    context: ProjectContext,
    query: str,
    filters: Optional[MemoryFilter] = None,
    top_k: int = 10,
) -> MemoryRetrievalResponse:
    """Stable service entrypoint for Member 2."""
    return InstitutionalMemoryRetrievalService.search(
        context=context,
        query=query,
        filters=filters,
        top_k=top_k,
    )


def search_by_activity(
    context: ProjectContext,
    activity_id: str,
    query: str = "",
    filters: Optional[MemoryFilter] = None,
    top_k: int = 10,
) -> MemoryRetrievalResponse:
    return InstitutionalMemoryRetrievalService.search_by_activity(
        context=context,
        activity_id=activity_id,
        query=query,
        filters=filters,
        top_k=top_k,
    )


def search_by_stage(
    context: ProjectContext,
    stage_id: uuid.UUID,
    query: str = "",
    filters: Optional[MemoryFilter] = None,
    top_k: int = 10,
) -> MemoryRetrievalResponse:
    return InstitutionalMemoryRetrievalService.search_by_stage(
        context=context,
        stage_id=stage_id,
        query=query,
        filters=filters,
        top_k=top_k,
    )


def search_by_contractor(
    context: ProjectContext,
    contractor_id: uuid.UUID,
    query: str = "",
    filters: Optional[MemoryFilter] = None,
    top_k: int = 10,
) -> MemoryRetrievalResponse:
    return InstitutionalMemoryRetrievalService.search_by_contractor(
        context=context,
        contractor_id=contractor_id,
        query=query,
        filters=filters,
        top_k=top_k,
    )


def search_similar(
    context: ProjectContext,
    memory_id: str,
    top_k: int = 10,
) -> MemoryRetrievalResponse:
    return InstitutionalMemoryRetrievalService.search_similar(
        context=context,
        memory_id=memory_id,
        top_k=top_k,
    )
