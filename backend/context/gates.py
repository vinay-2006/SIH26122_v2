"""
Module-level permission gates for the legacy (V6-origin) routers.

Each gate is a single dependency object so a router and a test refer to the SAME callable
(`app.dependency_overrides[gates.review_claim] = ...`). All gates require EXPLICIT project context
(X-Project-ID / path / query) validated against active membership; schedule gates additionally require an
explicit schedule that belongs to that project. Nothing here ever selects an active/latest schedule.
"""
from __future__ import annotations

from fastapi import Depends

from backend.context.event import require_event_permission
from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.context.errors import raise_permission_denied
from backend.rbac.dependencies import require_any_permission, require_permission
from backend.rbac.permissions import Permission, has_permission

# ---- project-scoped (no schedule needed)
project_view = require_permission(Permission.VIEW_PROJECT)
audit_view = require_permission(Permission.VIEW_AUDIT)


def _schedule_gate(*permissions: Permission):
    def dependency(context: ScheduleContext = Depends(require_schedule_context)) -> ScheduleContext:
        if not any(has_permission(context.role, p) for p in permissions):
            raise_permission_denied(permission=" | ".join(p.value for p in permissions), role=context.role)
        return context

    return dependency


# ---- schedule-scoped
schedule_view = _schedule_gate(Permission.VIEW_SCHEDULE)
events_view = _schedule_gate(Permission.VIEW_EXECUTION_EVENTS)
events_create = _schedule_gate(Permission.CREATE_EXECUTION_EVENT)          # site engineer / PM / owner
claim_review_schedule = _schedule_gate(Permission.REVIEW_CLAIM)            # supervisor / planner / PM / owner
approve_schedule = _schedule_gate(Permission.APPROVE_ACTUAL)               # supervisor / PM / owner
review_or_view_schedule = _schedule_gate(Permission.REVIEW_CLAIM, Permission.VIEW_EXECUTION_EVENTS)

# ---- event-scoped (event must belong to the explicit project)
event_view = require_event_permission(Permission.VIEW_EXECUTION_EVENTS)
event_review = require_event_permission(Permission.REVIEW_CLAIM)
event_clarify = require_event_permission(Permission.CREATE_EXECUTION_EVENT, Permission.REVIEW_CLAIM)
event_process = require_event_permission(Permission.CREATE_EXECUTION_EVENT, Permission.REVIEW_CLAIM)  # match / check
event_approve = require_event_permission(Permission.APPROVE_ACTUAL)

# ---- project-scoped, permission-specific
project_events_create = require_permission(Permission.CREATE_EXECUTION_EVENT)
project_review = require_permission(Permission.REVIEW_CLAIM)
project_approve = require_permission(Permission.APPROVE_ACTUAL)


# every event-scoped gate (tests replace them all at once via tests/v7ctx.act_as_event)
EVENT_GATES = (event_view, event_review, event_clarify, event_process, event_approve)
