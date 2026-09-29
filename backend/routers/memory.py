"""
Institutional Memory Retrieval Router for SETUAI V7 Phase 11.
Provides project-scoped, read-only institutional memory search endpoints.
"""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Path, status

from backend.context.errors import raise_permission_denied
from backend.context.project import ProjectContext, require_project_context
from backend.memory.retrieval_service import InstitutionalMemoryRetrievalService
from backend.memory.schemas import MemoryQuery, MemoryRetrievalResponse
from backend.rbac.permissions import Permission, has_permission

router = APIRouter(tags=["institutional-memory"])


@router.post(
    "/api/v7/projects/{project_id}/memory/search",
    response_model=MemoryRetrievalResponse,
    summary="Search institutional memory records within project context (V7)",
)
@router.post(
    "/api/v1/projects/{project_id}/memory/search",
    response_model=MemoryRetrievalResponse,
    summary="Search institutional memory records within project context (V1 alias)",
)
def search_memory(
    project_id: uuid.UUID = Path(...),
    payload: MemoryQuery = ...,
    context: ProjectContext = Depends(require_project_context),
) -> MemoryRetrievalResponse:
    """
    Two-stage institutional memory retrieval:
    1. Metadata filtering (exact matching by stage, activity, contractor, severity, dates).
    2. Deterministic explainable ranking (semantic similarity if vector model available, else lexical fallback).

    Enforces strict project isolation: caller cannot query or retrieve records outside authorized project.
    """
    if not has_permission(context.role, Permission.VIEW_PROJECT):
        raise_permission_denied(Permission.VIEW_PROJECT.value, context.role)

    return InstitutionalMemoryRetrievalService.search(
        context=context,
        query=payload.query,
        filters=payload.filters,
        top_k=payload.top_k,
    )
