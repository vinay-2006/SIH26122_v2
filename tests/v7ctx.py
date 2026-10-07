"""
Test helper: act as a member of a project with a given PROJECT ROLE, without a database.

Only the two context *resolvers* are replaced (they would otherwise look up membership/schedule rows);
every permission gate (`backend.context.gates`, `require_permission`, ...) runs for real, so RBAC behaviour is
what is under test. Unauthenticated requests are simply made without calling `act_as` (real 401).
"""
import uuid
from contextlib import contextmanager

from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.context import gates
from backend.context.event import EventContext
from backend.context.project import ProjectContext, require_project_context
from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.main import app

PROJECT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


@contextmanager
def act_as(role: str, schedule_id: "str | None" = "SCH-1", user_id: str = "22222222-2222-4222-8222-222222222222"):
    """schedule_id=None replaces only the project resolver, so the REAL schedule resolution/validation runs
    (use with `fake_schedules`)."""
    user = CurrentUser(id=user_id, full_name=f"test {role}", role="SUPERVISOR")
    pctx = ProjectContext(user=user, project_id=PROJECT_ID, role=role, membership_id=uuid.uuid4(), project_name="test")
    keys = (get_current_user, require_project_context, require_schedule_context)
    saved = {k: app.dependency_overrides.get(k) for k in keys}
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[require_project_context] = lambda: pctx
    if schedule_id is not None:
        app.dependency_overrides[require_schedule_context] = lambda: ScheduleContext(project_context=pctx, schedule_id=schedule_id)
    try:
        yield pctx
    finally:
        for k, v in saved.items():
            if v is None:
                app.dependency_overrides.pop(k, None)
            else:
                app.dependency_overrides[k] = v


class _Cur:
    def __init__(self, rows): self._rows, self._row = rows, None
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=()):
        self._row = self._rows.get(params[0]) if params else None
    def fetchone(self): return self._row


class _Conn:
    def __init__(self, rows): self._rows = rows
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return _Cur(self._rows)


def fake_schedules(monkeypatch, mapping: dict):
    """Replace the schedule lookup used by the real schedule validation: {schedule_id: project_uuid_or_None}."""
    rows = {sid: {"schedule_id": sid, "project_id": pid} for sid, pid in mapping.items()}
    monkeypatch.setattr("backend.context.schedule.get_connection", lambda: _Conn(rows))


@contextmanager
def act_as_event(role: str = "SUPERVISOR", event_id: str = "EVT", schedule_id: "str | None" = "SCH-1", project_id=None):
    """Replace every event-scoped gate by a constant EventContext (for tests that mock the DB layer)."""
    user = CurrentUser(id="22222222-2222-4222-8222-222222222222", full_name=f"test {role}", role="SUPERVISOR")
    pid = uuid.UUID(str(project_id)) if project_id else PROJECT_ID
    pctx = ProjectContext(user=user, project_id=pid, role=role, membership_id=uuid.uuid4(), project_name="test")
    ectx = EventContext(project_context=pctx, event_id=event_id, schedule_id=schedule_id)
    saved = {g: app.dependency_overrides.get(g) for g in gates.EVENT_GATES}
    for g in gates.EVENT_GATES:
        app.dependency_overrides[g] = lambda ectx=ectx: ectx
    try:
        yield ectx
    finally:
        for g, v in saved.items():
            if v is None:
                app.dependency_overrides.pop(g, None)
            else:
                app.dependency_overrides[g] = v


_KEEP_ALIVE: list = []


def set_event_ctx(role: str = "SUPERVISOR", schedule_id: "str | None" = "SCH-1", project_id=None):
    """Non-context-manager variant: caller (or a fixture) clears app.dependency_overrides afterwards."""
    cm = act_as_event(role, schedule_id=schedule_id, project_id=project_id)
    _KEEP_ALIVE.append(cm)  # a garbage-collected context manager would run its cleanup immediately
    return cm.__enter__()
