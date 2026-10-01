-- Migration 014: canonical V7 project roles in project_memberships.assigned_role
-- Description:
--   The RBAC layer (backend/rbac/roles.py ProjectRole + permissions.py ROLE_PERMISSIONS) defines seven project
--   roles: OWNER, PROJECT_MANAGER, PLANNER, SUPERVISOR, SITE_ENGINEER, QUALITY_INSPECTOR, AUDITOR
--   (PRD v7 Domain A/T4 names SITE_ENGINEER, SUPERVISOR, PROJECT_MANAGER, AUDITOR; the code adds the other three
--   and grants them permissions). The CHECK created in migration 004 allowed only
--   SITE_ENGINEER, SUPERVISOR, PLANNER, PROJECT_MANAGER, VIEWER, CONTRACTOR_REP, so OWNER, QUALITY_INSPECTOR and
--   AUDITOR memberships could not be stored although RBAC grants them permissions.
--   This migration WIDENS the constraint to the union. It never changes a row:
--   every value that was valid stays valid (VIEWER and CONTRACTOR_REP are kept for existing rows; RBAC gives
--   them no permissions). No roles are invented.
-- Idempotent: dropping and re-adding the same named constraint is a no-op on repeat.
ALTER TABLE project_memberships DROP CONSTRAINT IF EXISTS project_memberships_assigned_role_check;
ALTER TABLE project_memberships ADD CONSTRAINT project_memberships_assigned_role_check
    CHECK (assigned_role IN (
        'OWNER', 'PROJECT_MANAGER', 'PLANNER', 'SUPERVISOR', 'SITE_ENGINEER', 'QUALITY_INSPECTOR', 'AUDITOR',
        'VIEWER', 'CONTRACTOR_REP'
    ));
