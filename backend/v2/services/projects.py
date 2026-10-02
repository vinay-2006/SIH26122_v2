"""Projects, settings, members, invitations, platform grants, and SE report uploads. All writes run as the authenticated actor."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import psycopg.errors as pge

from .. import audit
from ..auth import CurrentUser
from ..db import tx
from ..errors import ApiError, forbidden
from ..upload_guard import schedule_like_reason

PROJECT_FIELDS = ("project_name", "description", "client_name", "project_type", "location", "latitude", "longitude", "geofence_radius_m",
                  "planned_start", "planned_finish", "contract_finish", "lifecycle_status")
SETTING_FIELDS = ("over_baseline_tolerance_pct", "completion_threshold_pct", "working_days_per_week", "require_photo_evidence", "extra")
ASSIGNABLE = ("SITE_ENGINEER", "SUPERVISOR")
DOC_KINDS = ("DAILY_REPORT", "SITE_REPORT", "PHOTO", "EVIDENCE", "ISSUE_REPORT")
MAX_UPLOAD = 25 * 1024 * 1024


# ------------------------------------------------------------------------------------------------ projects
def create_project(user: CurrentUser, data: Dict[str, Any]) -> Dict[str, Any]:
    if not user.can("CREATE_PROJECT"):
        raise forbidden("Creating a project needs the CREATE_PROJECT platform grant", "CREATE_PROJECT_REQUIRED")
    with tx(user.id) as c:
        p = c.execute(
            "insert into projects (project_code, project_name, description, client_name, project_type, location, latitude, longitude, geofence_radius_m,"
            " planned_start, planned_finish, contract_finish, lifecycle_status, created_by) values (%(project_code)s,%(project_name)s,%(description)s,"
            "%(client_name)s,%(project_type)s,%(location)s,%(latitude)s,%(longitude)s,%(geofence_radius_m)s,%(planned_start)s,%(planned_finish)s,"
            "%(contract_finish)s,%(lifecycle_status)s,%(created_by)s) returning *",
            {**{k: data.get(k) for k in PROJECT_FIELDS}, "project_code": data["project_code"], "created_by": user.id,
             "lifecycle_status": data.get("lifecycle_status") or "UPCOMING"}).fetchone()
        c.execute("insert into project_memberships (project_id, user_id, role, added_by) values (%s,%s,'PROJECT_MANAGER',%s)",
                  (p["project_id"], user.id, user.id))                    # creator bootstrap (the database allows exactly this one seat)
        audit.log(c, project_id=p["project_id"], actor_id=user.id, role="PROJECT_MANAGER", action="PROJECT_CREATED",
                  entity_type="PROJECT", entity_id=p["project_id"], after={"code": p["project_code"], "name": p["project_name"]})
    return p


def list_projects(user: CurrentUser) -> List[Dict[str, Any]]:
    with tx() as c:
        return c.execute(
            "select p.project_id, p.project_code, p.project_name, p.lifecycle_status, p.record_status, p.location, m.role as my_role, "
            "(select v.version_id from schedule_versions v where v.project_id = p.project_id and v.status = 'ACTIVE') as active_version_id "
            "from project_memberships m join projects p on p.project_id = m.project_id where m.user_id = %s and m.status = 'ACTIVE' "
            "order by p.project_code", (user.id,)).fetchall()


def get_project(project_id) -> Dict[str, Any]:
    with tx() as c:
        p = c.execute("select * from projects where project_id = %s", (project_id,)).fetchone()
        v = c.execute("select version_id, version_no, baseline_name, data_date from schedule_versions where project_id = %s and status = 'ACTIVE'",
                      (project_id,)).fetchone()
    return {**p, "active_version": v}


def update_project(user, project_id, patch: Dict[str, Any]) -> Dict[str, Any]:
    fields = {k: v for k, v in patch.items() if k in PROJECT_FIELDS and v is not None}
    if not fields:
        raise ApiError(422, "NOTHING_TO_UPDATE", "No updatable field was supplied")
    with tx(user.id) as c:
        before = c.execute("select * from projects where project_id = %s for update", (project_id,)).fetchone()
        sets = ", ".join(f"{k} = %({k})s" for k in fields)
        p = c.execute(f"update projects set {sets} where project_id = %(pid)s returning *", {**fields, "pid": project_id}).fetchone()
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="PROJECT_UPDATED", entity_type="PROJECT",
                  entity_id=project_id, before={k: before[k] for k in fields}, after=fields)
    return p


def set_record_status(user, project_id, status: str) -> Dict[str, Any]:
    with tx(user.id) as c:
        p = c.execute("update projects set record_status = %s where project_id = %s returning *", (status, project_id)).fetchone()
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="PROJECT_" + ("ARCHIVED" if status == "ARCHIVED" else "RESTORED"),
                  entity_type="PROJECT", entity_id=project_id)
    return p


def get_settings(project_id) -> Dict[str, Any]:
    with tx() as c:
        return c.execute("select * from project_settings where project_id = %s", (project_id,)).fetchone()


def update_settings(user, project_id, patch: Dict[str, Any]) -> Dict[str, Any]:
    fields = {k: v for k, v in patch.items() if k in SETTING_FIELDS and v is not None}
    if not fields:
        raise ApiError(422, "NOTHING_TO_UPDATE", "No updatable setting was supplied")
    if "extra" in fields:
        from psycopg.types.json import Jsonb
        fields["extra"] = Jsonb(fields["extra"])
    with tx(user.id) as c:
        before = c.execute("select * from project_settings where project_id = %s", (project_id,)).fetchone()
        sets = ", ".join(f"{k} = %({k})s" for k in fields)
        s = c.execute(f"update project_settings set {sets} where project_id = %(pid)s returning *", {**fields, "pid": project_id}).fetchone()
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="PROJECT_SETTINGS_UPDATED", entity_type="PROJECT_SETTINGS",
                  entity_id=project_id, before={k: before[k] for k in fields}, after={k: s[k] for k in fields})
    return s


# ------------------------------------------------------------------------------------------------ members
def list_members(project_id) -> List[Dict[str, Any]]:
    with tx() as c:
        return c.execute("select m.user_id, p.full_name, p.email, m.role, m.status, m.created_at from project_memberships m join profiles p on p.id = m.user_id "
                         "where m.project_id = %s order by (m.role = 'PROJECT_MANAGER') desc, p.full_name", (project_id,)).fetchall()


def add_existing_member(user, project_id, email: str, role: str) -> Dict[str, Any]:
    if role not in ASSIGNABLE:
        raise ApiError(422, "ROLE_NOT_ASSIGNABLE", f"A project manager can assign {', '.join(ASSIGNABLE)}")
    with tx(user.id) as c:
        target = c.execute("select id, full_name, email from profiles where lower(email) = lower(%s) and is_active", (email.strip(),)).fetchone()
        if target is None:
            raise ApiError(404, "USER_NOT_FOUND", "No active registered user with that email; invite them instead")
        cur = c.execute("select status, role from project_memberships where project_id = %s and user_id = %s", (project_id, target["id"])).fetchone()
        if cur and cur["status"] == "ACTIVE":
            raise ApiError(409, "ALREADY_MEMBER", f"{target['email']} is already an active {cur['role']}")
        if cur:
            c.execute("update project_memberships set role = %s, status = 'ACTIVE', added_by = %s where project_id = %s and user_id = %s",
                      (role, user.id, project_id, target["id"]))
        else:
            c.execute("insert into project_memberships (project_id, user_id, role, added_by) values (%s,%s,%s,%s)", (project_id, target["id"], role, user.id))
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="MEMBER_ADDED", entity_type="MEMBERSHIP",
                  entity_id=target["id"], after={"email": target["email"], "role": role})
    return {"user_id": target["id"], "email": target["email"], "full_name": target["full_name"], "role": role, "status": "ACTIVE"}


def change_member(user, project_id, member_id, role: Optional[str], status: Optional[str]) -> Dict[str, Any]:
    if role is not None and role not in ASSIGNABLE:
        raise ApiError(422, "ROLE_NOT_ASSIGNABLE", f"A project manager can assign {', '.join(ASSIGNABLE)}")
    if status is not None and status not in ("ACTIVE", "SUSPENDED", "REMOVED"):
        raise ApiError(422, "BAD_STATUS", "status must be ACTIVE, SUSPENDED or REMOVED")
    if role is None and status is None:
        raise ApiError(422, "NOTHING_TO_UPDATE", "Supply role and/or status")
    with tx(user.id) as c:
        before = c.execute("select role, status from project_memberships where project_id = %s and user_id = %s", (project_id, member_id)).fetchone()
        if before is None:
            raise ApiError(404, "MEMBER_NOT_FOUND", "No such member in this project")
        row = c.execute("update project_memberships set role = coalesce(%s, role), status = coalesce(%s, status) where project_id = %s and user_id = %s "
                        "returning user_id, role, status", (role, status, project_id, member_id)).fetchone()
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="MEMBER_CHANGED", entity_type="MEMBERSHIP",
                  entity_id=member_id, before=before, after={"role": row["role"], "status": row["status"]})
    return row


# ------------------------------------------------------------------------------------------------ invitations
def create_invitation(user, project_id, email: str, role: str, days: int = 7) -> Dict[str, Any]:
    if role not in ASSIGNABLE:
        raise ApiError(422, "ROLE_NOT_ASSIGNABLE", f"Invitations can grant {', '.join(ASSIGNABLE)}")
    token = secrets.token_urlsafe(32)
    with tx(user.id) as c:
        row = c.execute("insert into project_invitations (project_id, email, role, token_hash, delivery, invited_by, expires_at) "
                        "values (%s,%s,%s,%s,'LINK',%s,%s) returning invitation_id, expires_at",
                        (project_id, email.strip(), role, hashlib.sha256(token.encode()).hexdigest(), user.id,
                         datetime.now(timezone.utc) + timedelta(days=days))).fetchone()
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="INVITATION_CREATED", entity_type="INVITATION",
                  entity_id=row["invitation_id"], after={"email": email.strip().lower(), "role": role})
    # the raw token is returned exactly once; only its hash is stored
    return {"invitation_id": row["invitation_id"], "email": email.strip(), "role": role, "expires_at": row["expires_at"], "token": token,
            "accept_path": f"/accept-invitation?token={token}"}


def list_invitations(project_id) -> List[Dict[str, Any]]:
    with tx() as c:
        return c.execute("select invitation_id, email, role, delivery, expires_at, accepted_at, revoked_at, created_at, "
                         "case when accepted_at is not null then 'ACCEPTED' when revoked_at is not null then 'REVOKED' when expires_at < now() then 'EXPIRED' "
                         "else 'PENDING' end as state from project_invitations where project_id = %s order by created_at desc", (project_id,)).fetchall()


def revoke_invitation(user, project_id, invitation_id) -> None:
    with tx(user.id) as c:
        r = c.execute("update project_invitations set revoked_at = now() where project_id = %s and invitation_id = %s and accepted_at is null "
                      "and revoked_at is null returning invitation_id", (project_id, invitation_id)).fetchone()
        if r is None:
            raise ApiError(404, "INVITATION_NOT_FOUND", "No open invitation with that id")
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="INVITATION_REVOKED", entity_type="INVITATION", entity_id=invitation_id)


def accept_invitation(user: CurrentUser, token: str) -> Dict[str, Any]:
    h = hashlib.sha256(token.encode()).hexdigest()
    try:
        with tx(user.id) as c:
            mid = c.execute("select accept_project_invitation(%s, %s) as m", (h, user.id)).fetchone()["m"]
            m = c.execute("select project_id, role from project_memberships where membership_id = %s", (mid,)).fetchone()
            audit.log(c, project_id=m["project_id"], actor_id=user.id, role=m["role"], action="INVITATION_ACCEPTED", entity_type="MEMBERSHIP", entity_id=mid)
    except pge.InvalidParameterValue as e:
        raise ApiError(410, "INVITATION_INVALID", "This invitation is invalid, expired or already used") from e
    except pge.InsufficientPrivilege as e:
        raise ApiError(403, "INVITATION_EMAIL_MISMATCH", "This invitation was issued to a different email address") from e
    except pge.UniqueViolation as e:
        raise ApiError(409, "ALREADY_MEMBER", "You are already an active member of this project") from e
    return {"project_id": m["project_id"], "role": m["role"]}


# ------------------------------------------------------------------------------------------------ platform grants (admin only)
def grant_capability(admin: CurrentUser, email: str, capability: str) -> Dict[str, Any]:
    if not admin.can("PLATFORM_ADMIN"):
        raise forbidden("Only a platform admin can grant platform capabilities", "ADMIN_REQUIRED")
    if capability not in ("CREATE_PROJECT", "PLATFORM_ADMIN"):
        raise ApiError(422, "BAD_CAPABILITY", "capability must be CREATE_PROJECT or PLATFORM_ADMIN")
    with tx(admin.id) as c:
        t = c.execute("select id, email from profiles where lower(email) = lower(%s) and is_active", (email.strip(),)).fetchone()
        if t is None:
            raise ApiError(404, "USER_NOT_FOUND", "No active registered user with that email")
        c.execute("insert into platform_grants (user_id, capability, granted_by) values (%s,%s,%s) on conflict do nothing", (t["id"], capability, admin.id))
        audit.log(c, project_id=None, actor_id=admin.id, role="PLATFORM_ADMIN", action="CAPABILITY_GRANTED", entity_type="PLATFORM_GRANT",
                  entity_id=t["id"], after={"capability": capability})
    return {"user_id": t["id"], "email": t["email"], "capability": capability}


def revoke_capability(admin: CurrentUser, user_id, capability: str) -> None:
    if not admin.can("PLATFORM_ADMIN"):
        raise forbidden("Only a platform admin can revoke platform capabilities", "ADMIN_REQUIRED")
    with tx(admin.id) as c:
        r = c.execute("update platform_grants set revoked_at = now(), revoked_by = %s where user_id = %s and capability = %s and revoked_at is null returning grant_id",
                      (admin.id, user_id, capability)).fetchone()
        if r is None:
            raise ApiError(404, "GRANT_NOT_FOUND", "No active grant to revoke")
        audit.log(c, project_id=None, actor_id=admin.id, role="PLATFORM_ADMIN", action="CAPABILITY_REVOKED", entity_type="PLATFORM_GRANT",
                  entity_id=user_id, after={"capability": capability})


def revoke_sessions(admin: CurrentUser, user_id) -> Dict[str, Any]:
    """Reject every token issued to this user before now (stateless tokens cannot otherwise be revoked)."""
    if not admin.can("PLATFORM_ADMIN"):
        raise forbidden("Only a platform admin can revoke sessions", "ADMIN_REQUIRED")
    with tx(admin.id) as c:
        r = c.execute("update profiles set tokens_valid_after = now() where id = %s returning id, tokens_valid_after", (user_id,)).fetchone()
        if r is None:
            raise ApiError(404, "USER_NOT_FOUND", "No such user")
        audit.log(c, project_id=None, actor_id=admin.id, role="PLATFORM_ADMIN", action="SESSIONS_REVOKED", entity_type="PROFILE", entity_id=user_id)
    return {"user_id": r["id"], "tokens_valid_after": r["tokens_valid_after"]}


# ------------------------------------------------------------------------------------------------ site engineer report / evidence upload
def upload_document(user, project_id, kind: str, filename: str, content: bytes, mime: Optional[str]) -> Dict[str, Any]:
    if kind not in DOC_KINDS:
        raise ApiError(422, "BAD_KIND", f"kind must be one of {', '.join(DOC_KINDS)}")
    if not content:
        raise ApiError(422, "EMPTY_FILE", "The file is empty")
    if len(content) > MAX_UPLOAD:
        raise ApiError(413, "TOO_LARGE", "The file exceeds the 25 MB limit")
    why = schedule_like_reason(filename, content)
    if why:
        raise ApiError(422, "SCHEDULE_FILE_NOT_ALLOWED", why)
    sha = hashlib.sha256(content).hexdigest()
    with tx(user.id) as c:
        try:
            row = c.execute("insert into source_documents (project_id, kind, file_name, mime_type, sha256, size_bytes, uploaded_by, extraction_status) "
                            "values (%s,%s,%s,%s,%s,%s,%s,'PENDING') returning document_id, uploaded_at", (project_id, kind, filename, mime, sha, len(content), user.id)).fetchone()
        except pge.UniqueViolation as e:
            raise ApiError(409, "DUPLICATE_UPLOAD", "This exact file was already uploaded to the project") from e
        audit.log(c, project_id=project_id, actor_id=user.id, role="SITE_ENGINEER", action="DOCUMENT_UPLOADED", entity_type="SOURCE_DOCUMENT",
                  entity_id=row["document_id"], after={"kind": kind, "file": filename, "sha256": sha})
    return {"document_id": row["document_id"], "kind": kind, "sha256": sha, "uploaded_at": row["uploaded_at"]}
