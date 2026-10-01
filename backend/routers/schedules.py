"""M1 Schedule API layer.

Thin FastAPI wrapper over the Phase 1 parser/validator
(backend.shared.schedule) and Phase 2 repository (backend.shared.schedule_repository):

    POST /api/v1/schedules                                   request -> generate schedule_id -> parser -> repository -> PostgreSQL -> active FAISS index
    GET  /api/v1/schedules                                    list schedules from PostgreSQL
    GET  /api/v1/schedules/{schedule_id}                       one schedule from PostgreSQL
    GET  /api/v1/schedules/{schedule_id}/activities             a schedule's activities from PostgreSQL
    GET  /api/v1/schedules/{schedule_id}/activities/{activity_id}  one activity from PostgreSQL
    GET  /api/v1/schedules/{schedule_id}/dependencies            a schedule's dependency pairs from PostgreSQL
    GET  /api/v1/schedules/{schedule_id}/wbs-tree                 activities grouped by wbs_code (Feature #30, M1 half)

No matching (EXACT_ID/EXACT_ASSET/HYBRID_FALLBACK/tiering), embeddings-as-a-
service, OCR, or LLM extraction here — those belong to later phases.

ID contract (per the project's non-negotiable "ID format" rule):
schedule_id is a system-generated entity — a lowercase uuid.uuid4() string
generated here, server-side, never accepted from the client. activity_id is
the one exception in the whole system and is preserved verbatim from the
uploaded CSV (see backend.shared.schedule).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.context.project import ProjectContext
from backend.context import gates
from backend.context.schedule import ScheduleContext
from backend.rbac.dependencies import require_permission
from backend.rbac.permissions import Permission
from backend.shared import schedule_index
from backend.shared.schedule import parse_schedule_csv
from backend.shared.schedule_repository import (
    ScheduleAlreadyExistsError,
    SchedulePersistenceError,
    list_active_schedules_for_project,
    get_schedule,
    get_schedule_activity,
    list_schedule_activities,
    list_schedule_dependencies,
    list_schedule_wbs_activities,
    list_schedules,
    save_schedule,
)
from backend.shared.schemas import (
    Schedule,
    ScheduleActivity,
    ScheduleDependency,
    WBSGroup,
    WBSGroupActivity,
    WBSTreeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])



@router.get("/health")
def health():
    return {"router": "schedules", "status": "ok"}


class ScheduleCreateRequest(BaseModel):
    # No schedule_id field: schedule_id is a system-generated identifier
    # (see module docstring) and is never accepted from the client.
    project_name: str
    data_date: Optional[date] = None
    source_format: Optional[str] = "csv"
    csv_content: str


class ScheduleCreateResponse(BaseModel):
    schedule_id: str
    project_name: str
    data_date: Optional[date] = None
    source_format: Optional[str] = None
    activity_count: int
    dependency_count: int
    indexed: bool
    index_error: Optional[str] = None


def _parse_errors_detail(message: str, errors) -> dict:
    return {
        "message": message,
        "errors": [
            {
                "row": e.row_number,
                "field": e.field_name,
                "message": e.message,
                "activity_id": e.activity_id,
            }
            for e in errors
        ],
    }


@router.post("", response_model=ScheduleCreateResponse, status_code=status.HTTP_201_CREATED)
def create_schedule(
    request: ScheduleCreateRequest,
    project_context: ProjectContext = Depends(require_permission(Permission.MANAGE_SCHEDULE)),
) -> ScheduleCreateResponse:
    # schedule_id is generated here, server-side — never accepted from the
    # client. This is what makes duplicate-schedule protection operate on a
    # genuinely system-assigned identity rather than one the caller could
    # collide (accidentally or otherwise).
    schedule_id = str(uuid.uuid4())

    parse_result = parse_schedule_csv(request.csv_content, schedule_id=schedule_id)

    if not parse_result.is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_parse_errors_detail("schedule CSV failed validation", parse_result.errors),
        )

    if not parse_result.activities:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_parse_errors_detail("schedule CSV contains no activities", []),
        )

    schedule = Schedule(
        schedule_id=schedule_id,
        project_name=request.project_name,
        data_date=request.data_date,
        source_format=request.source_format,
    )

    try:
        has_active = bool(list_active_schedules_for_project(str(project_context.project_id)))
        activity_count = save_schedule(
            schedule,
            parse_result,
            project_id=str(project_context.project_id),
            activate=not has_active,  # never silently displace or duplicate the project's active schedule
        )
    except ScheduleAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SchedulePersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    # PostgreSQL is authoritative and already committed at this point.
    # FAISS indexing is best-effort: a failure here must not undo the
    # successful persistence, so it is reported back rather than raised.
    # On success, this schedule becomes the single active (in-memory) FAISS
    # index, replacing whichever schedule was active before — see
    # backend.shared.schedule_index for the single-active-schedule contract.
    indexed = False
    index_error: Optional[str] = None
    try:
        schedule_index.build_index(schedule.schedule_id)
        indexed = True
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any indexing failure must surface, not crash the request
        index_error = str(exc)
        logger.error(
            "FAISS indexing failed for schedule_id=%s after successful persistence: %s",
            schedule.schedule_id, exc,
        )

    return ScheduleCreateResponse(
        schedule_id=schedule.schedule_id,
        project_name=schedule.project_name,
        data_date=schedule.data_date,
        source_format=schedule.source_format,
        activity_count=activity_count,
        dependency_count=len(parse_result.dependencies),
        indexed=indexed,
        index_error=index_error,
    )


@router.get("", response_model=list[Schedule])
def get_schedules(
    project_context: ProjectContext = Depends(require_permission(Permission.VIEW_SCHEDULE)),
) -> list[Schedule]:
    return list_schedules(project_id=str(project_context.project_id))


@router.get("/active", response_model=Schedule)
def get_active_schedule_endpoint(
    project_context: ProjectContext = Depends(require_permission(Permission.VIEW_SCHEDULE)),
) -> Schedule:
    """The caller's PROJECT's active schedule. Requires explicit project context (never a global
    'most recent'). Zero active -> 404; more than one -> 409, never a silent pick.
    Declared before "/{schedule_id}" so "active" is not read as a schedule id."""
    active = list_active_schedules_for_project(str(project_context.project_id))
    if not active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project has no active schedule")
    if len(active) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project has multiple active schedules; select one explicitly by schedule_id",
        )
    return active[0]


@router.get("/{schedule_id}", response_model=Schedule)
def get_schedule_by_id(
    schedule_id: str,
    _context: ScheduleContext = Depends(gates.schedule_view),
) -> Schedule:
    schedule = get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"schedule_id {schedule_id!r} not found",
        )
    return schedule


@router.get("/{schedule_id}/activities", response_model=list[ScheduleActivity])
def get_activities_for_schedule(
    schedule_id: str,
    _context: ScheduleContext = Depends(gates.schedule_view),
) -> list[ScheduleActivity]:
    schedule = get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"schedule_id {schedule_id!r} not found",
        )
    return list_schedule_activities(schedule_id)


@router.get("/{schedule_id}/activities/{activity_id}", response_model=ScheduleActivity)
def get_activity_by_id(
    schedule_id: str, activity_id: str,
    _context: ScheduleContext = Depends(gates.schedule_view),
) -> ScheduleActivity:
    schedule = get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"schedule_id {schedule_id!r} not found",
        )

    activity = get_schedule_activity(schedule_id, activity_id)
    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"activity_id {activity_id!r} not found in schedule_id {schedule_id!r}",
        )
    return activity


@router.get("/{schedule_id}/dependencies", response_model=list[ScheduleDependency])
def get_dependencies_for_schedule(
    schedule_id: str,
    _context: ScheduleContext = Depends(gates.schedule_view),
) -> list[ScheduleDependency]:
    schedule = get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"schedule_id {schedule_id!r} not found",
        )
    return list_schedule_dependencies(schedule_id)


@router.get("/{schedule_id}/wbs-tree", response_model=WBSTreeResponse)
def get_wbs_tree(
    schedule_id: str,
    _context: ScheduleContext = Depends(gates.schedule_view),
) -> WBSTreeResponse:
    """M1 half of Feature #30 (WBS Granularity Bridge): a read-only grouping
    of this schedule's activities by wbs_code, for M3's decomposition
    analysis. No role gate, same as this router's other read endpoints.

    Activities with a NULL/empty/whitespace-only wbs_code are excluded
    (never bucketed as "unassigned") — see
    backend.shared.schedule_repository.list_schedule_wbs_activities. Group
    and within-group ordering is deterministic (by wbs_code, then
    activity_id) because the repository query is already sorted that way.
    """
    schedule = get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"schedule_id {schedule_id!r} not found",
        )

    rows = list_schedule_wbs_activities(schedule_id)

    groups: dict[str, list[WBSGroupActivity]] = {}
    for row in rows:
        groups.setdefault(row["wbs_code"], []).append(
            WBSGroupActivity(
                activity_id=row["activity_id"],
                planned_quantity=row["planned_quantity"],
            )
        )

    wbs_groups = [
        WBSGroup(wbs_code=wbs_code, activities=activities)
        for wbs_code, activities in groups.items()
    ]

    return WBSTreeResponse(schedule_id=schedule_id, wbs_groups=wbs_groups)
