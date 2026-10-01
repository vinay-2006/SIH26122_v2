"""
Institutional Memory Retrieval Router for SETUAI V7 Phase 11.
Project-scoped institutional memory: search, issue-driven retrieval, and the write path (promote a resolved issue / record a lesson).
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query, status

from backend.context.errors import raise_permission_denied
from backend.context.project import ProjectContext, require_project_context
from backend.memory.retrieval_service import InstitutionalMemoryRetrievalService
from backend.memory.schemas import MemoryFilter, MemoryQuery, MemoryRetrievalResponse
from backend.rbac.permissions import Permission, has_permission
from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.services.memory_write_service import (
    IssueMemoryQuery,
    MemoryCreate,
    MemoryPromote,
    MemoryWriteService,
)

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


@router.post(
    "/api/v1/projects/{project_id}/memory/for-issue",
    response_model=MemoryRetrievalResponse,
    summary="Historical incidents relevant to an issue (same category; own project + lessons shared by other projects)",
)
def memory_for_issue(
    project_id: uuid.UUID = Path(...),
    payload: IssueMemoryQuery = ...,
    context: ProjectContext = Depends(require_project_context),
) -> MemoryRetrievalResponse:
    if not has_permission(context.role, Permission.VIEW_PROJECT):
        raise_permission_denied(Permission.VIEW_PROJECT.value, context.role)
    return InstitutionalMemoryRetrievalService.search_for_issue(
        context=context, category_code=payload.category_code.value, query=payload.query,
        activity_id=payload.activity_id, stage_id=payload.stage_id, top_k=payload.top_k,
    )


@router.get(
    "/api/v1/projects/{project_id}/memory",
    response_model=MemoryRetrievalResponse,
    summary="Browse institutional memory (own project + organisation-shared lessons)",
)
def list_memory(
    project_id: uuid.UUID = Path(...),
    q: str = Query("", max_length=500),
    category: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    context: ProjectContext = Depends(require_project_context),
) -> MemoryRetrievalResponse:
    if not has_permission(context.role, Permission.VIEW_PROJECT):
        raise_permission_denied(Permission.VIEW_PROJECT.value, context.role)
    filters = MemoryFilter(incident_type=category.upper()) if category else None
    return InstitutionalMemoryRetrievalService.search(context=context, query=q, filters=filters, top_k=limit)


@router.post("/api/v1/projects/{project_id}/memory", status_code=201, summary="Record a lesson manually")
def create_memory(
    project_id: uuid.UUID = Path(...),
    payload: MemoryCreate = ...,
    context: ProjectContext = Depends(require_project_context),
):
    return MemoryWriteService.create_record(context, payload)


@router.post(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/issues/{issue_id}/memory",
    summary="Store a resolved issue as institutional knowledge",
)
def promote_issue_to_memory_endpoint(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    issue_id: uuid.UUID = Path(...),
    payload: MemoryPromote = MemoryPromote(),
    context: ScheduleContext = Depends(require_schedule_context),
):
    return MemoryWriteService.promote_issue(context, issue_id, payload)
