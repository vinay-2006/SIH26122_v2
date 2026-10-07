"""
Test-only fixtures for backend/test_m2_intake.py. Provides `client` and
`fake_db`, which the test file references as bare parameters but never
declares itself (no @pytest.fixture in that file).

The auth override here is TEST-ONLY -- it reads X-Dev-User-Id/X-Dev-Role
headers and hands back a UserProfile directly, so intake.py's real
require_role()/get_current_user() role-gating logic still runs unmodified
against it. This does not touch backend/shared/auth.py or change any
production authentication behavior.
"""
import uuid

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from backend.auth.models import CurrentUser
from backend.context import gates
from backend.context.errors import (
    raise_invalid_schedule_context,
    raise_permission_denied,
    raise_resource_not_found,
)
from backend.context.event import EventContext
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.main import app
from backend.rbac.permissions import Permission, has_permission
from backend.shared.auth import UserProfile, get_current_user
from backend.shared.db import get_db
from backend.test_m2_intake import FakeDB

_PROJECT_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _override_get_current_user(request: Request) -> UserProfile:
    user_id = request.headers.get("X-Dev-User-Id")
    role = request.headers.get("X-Dev-Role")
    if not user_id or not role:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Dev-User-Id/X-Dev-Role test auth headers.",
        )
    return UserProfile(id=user_id, full_name=user_id, role=role)


def _project_ctx(request: Request) -> ProjectContext:
    """TEST-ONLY stand-in for membership lookup: the dev headers name the caller's PROJECT role."""
    user = _override_get_current_user(request)
    return ProjectContext(
        user=CurrentUser(id=user.id, full_name=user.full_name, role=user.role),
        project_id=_PROJECT_ID, role=user.role, membership_id=uuid.uuid4(), project_name="fake project",
    )


def _need(request: Request, *permissions: Permission) -> ProjectContext:
    ctx = _project_ctx(request)
    if not any(has_permission(ctx.role, p) for p in permissions):
        raise_permission_denied(" | ".join(p.value for p in permissions), ctx.role)
    return ctx


def _fake_gates(fake_db):
    """Replace the gates' DB lookups with FakeDB-backed equivalents. The permission checks stay real."""
    def project_events_create(request: Request):
        return _need(request, Permission.CREATE_EXECUTION_EVENT)

    def _event_gate(*permissions):
        def dep(request: Request):
            ctx = _need(request, *permissions)
            eid = request.path_params["event_id"]
            ev = next((e for e in fake_db.execution_events if e["event_id"] == eid), None)
            if ev is None:
                raise_resource_not_found("Execution claim", eid)
            return EventContext(project_context=ctx, event_id=eid, schedule_id=ev.get("schedule_id"))
        return dep

    def events_view_schedule(request: Request):
        ctx = _need(request, Permission.VIEW_EXECUTION_EVENTS)
        sid = request.query_params.get("schedule_id") or request.headers.get("X-Schedule-ID")
        if not sid:
            raise_invalid_schedule_context()
        return ScheduleContext(project_context=ctx, schedule_id=sid)

    return {
        gates.project_events_create: project_events_create,
        gates.event_view: _event_gate(Permission.VIEW_EXECUTION_EVENTS),
        gates.event_clarify: _event_gate(Permission.CREATE_EXECUTION_EVENT, Permission.REVIEW_CLAIM),
        gates.events_view: events_view_schedule,
    }


def _fake_resolve_explicit_schedule(fake_db):
    def resolve(project_context, *candidates):
        supplied = [c for c in candidates if c]
        if not supplied:
            raise_invalid_schedule_context("Explicit schedule_id is required")
        sid = supplied[0]
        if sid not in {s["schedule_id"] for s in fake_db.schedules}:
            raise_resource_not_found("Schedule", sid)
        return ScheduleContext(project_context=project_context, schedule_id=sid)
    return resolve


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(fake_db, monkeypatch):
    def _override_get_db():
        yield fake_db

    monkeypatch.setattr("backend.routers.intake.resolve_explicit_schedule", _fake_resolve_explicit_schedule(fake_db))
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    for gate, fake in _fake_gates(fake_db).items():
        app.dependency_overrides[gate] = fake
    tc = TestClient(app)
    tc.headers.update({"X-Schedule-ID": "test-sched-1"})  # explicit schedule, as a V7 client must send
    try:
        yield tc
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        for gate in (gates.project_events_create, gates.event_view, gates.event_clarify, gates.events_view):
            app.dependency_overrides.pop(gate, None)
