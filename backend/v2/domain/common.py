from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional

from ..db import tx
from ..errors import ApiError, forbidden

PENDING_STATUSES = ("REPORTED", "EXTRACTED", "MATCHED", "VALIDATED", "DISPUTED")
DECIDABLE_STATUSES = ("EXTRACTED", "MATCHED", "VALIDATED", "DISPUTED")
FINAL_STATUSES = ("APPROVED", "REJECTED", "WITHDRAWN")
SE, SUP, PM = "SITE_ENGINEER", "SUPERVISOR", "PROJECT_MANAGER"


@dataclass(frozen=True)
class ProjectActor:
    user_id: uuid.UUID
    project_id: uuid.UUID
    role: str
    record_status: str = "ACTIVE"


def resolve_actor(user_id, project_id) -> ProjectActor:
    """identity + the role granted by an ACTIVE membership of THIS project (never a role carried by the caller)"""
    with tx() as c:
        r = c.execute("select m.role, p.record_status from project_memberships m join projects p on p.project_id = m.project_id "
                      "where m.project_id = %s and m.user_id = %s and m.status = 'ACTIVE'", (project_id, user_id)).fetchone()
    if r is None:
        raise forbidden("You are not a member of this project", "NOT_A_MEMBER")
    return ProjectActor(uuid.UUID(str(user_id)), uuid.UUID(str(project_id)), r["role"], r["record_status"])


def require_role(actor: ProjectActor, *roles: str, what: str = "this action") -> None:
    if actor.role not in roles:
        raise forbidden(f"Role {actor.role} may not perform {what}", "PERMISSION_DENIED")


def require_writable(actor: ProjectActor) -> None:
    if actor.record_status != "ACTIVE":
        raise ApiError(409, "PROJECT_ARCHIVED", "The project is archived and read-only")


def active_version(conn, project_id) -> dict:
    v = conn.execute("select version_id, version_no, baseline_name, data_date from schedule_versions where project_id = %s and status = 'ACTIVE'",
                     (project_id,)).fetchone()
    if v is None:
        raise ApiError(409, "NO_ACTIVE_SCHEDULE", "The project has no active schedule: a Project Manager must activate one first")
    return v


def supervisors_of(conn, project_id) -> List[uuid.UUID]:
    return [r["user_id"] for r in conn.execute(
        "select user_id from project_memberships where project_id = %s and role = 'SUPERVISOR' and status = 'ACTIVE'", (project_id,)).fetchall()]


def notify(conn, *, project_id, recipient_id, ntype: str, title: str, body: Optional[str] = None, created_by=None,
           decision_id=None, event_id=None, issue_id=None) -> None:
    conn.execute("insert into notifications (project_id, recipient_id, notification_type, decision_id, event_id, issue_id, title, body, created_by) "
                 "values (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (project_id, recipient_id, ntype, decision_id, event_id, issue_id, title, body, created_by))


def lock_keys(conn, keys: Iterable[str]) -> None:
    """transaction-level advisory locks taken in sorted order (same keys the database triggers use) so concurrent writers queue, never deadlock"""
    for k in sorted(set(keys)):
        conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (k,))


@contextmanager
def actor_tx(actor: ProjectActor, write: bool = False, readonly: bool = False) -> Iterator:
    """A transaction as `actor` that FIRST re-verifies, inside the transaction, that the membership is still ACTIVE with the same role and (for writes)
    that the project is not archived. The ProjectActor is only a snapshot taken earlier in the request; a suspended user or an archived project
    is honoured even by a stale snapshot. (The database role guards check live data again at every write.)"""
    with tx(actor.user_id, readonly=readonly) as c:
        r = c.execute("select m.role, p.record_status from project_memberships m join projects p on p.project_id = m.project_id "
                      "where m.project_id = %s and m.user_id = %s and m.status = 'ACTIVE'", (actor.project_id, actor.user_id)).fetchone()
        if r is None or r["role"] != actor.role:
            raise forbidden("Your project membership changed; sign in again", "MEMBERSHIP_CHANGED")
        if write and r["record_status"] != "ACTIVE":
            raise ApiError(409, "PROJECT_ARCHIVED", "The project is archived and read-only")
        yield c
