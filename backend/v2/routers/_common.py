from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import Query

from ..auth import ProjectAccess
from ..domain.common import ProjectActor

MAX_PAGE = 200
LIMIT = Query(50, ge=1, le=MAX_PAGE, description="Page size (1-200)")
OFFSET = Query(0, ge=0, le=1_000_000, description="Rows to skip")

ERRORS = {
    401: {"description": "Missing, invalid or revoked token"},
    403: {"description": "Not a member of this project, or the role may not do this"},
    404: {"description": "No such record (also returned for records the caller may not see)"},
    409: {"description": "State conflict (already decided, archived project, duplicate)"},
    422: {"description": "Validation error: {error: {code, message, details}}"},
}


def actor_of(access: ProjectAccess) -> ProjectActor:
    """The domain services re-verify membership, role and archive state inside every transaction; this is only the request's snapshot."""
    return ProjectActor(access.user.id, access.project_id, access.role, access.record_status)


def page(fetch: Callable[[int, int], List[Any]], limit: int, offset: int) -> Dict[str, Any]:
    rows = fetch(limit + 1, offset)
    more = len(rows) > limit
    return {"items": rows[:limit], "limit": limit, "offset": offset, "next_offset": offset + limit if more else None}
