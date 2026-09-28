-- Migration 004: V7 Project Memberships
-- Description: Establishes explicit user-to-project assignment and role authorization for project isolation.

CREATE TABLE IF NOT EXISTS project_memberships (
  membership_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  assigned_role TEXT NOT NULL CHECK (assigned_role IN ('SITE_ENGINEER', 'SUPERVISOR', 'PLANNER', 'PROJECT_MANAGER', 'VIEWER', 'CONTRACTOR_REP')),
  contractor_id UUID,
  active BOOLEAN DEFAULT TRUE,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_project_memberships_user_project UNIQUE (user_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_project_memberships_user_proj ON project_memberships(user_id, project_id);
CREATE INDEX IF NOT EXISTS idx_project_memberships_proj_user ON project_memberships(project_id, user_id);
CREATE INDEX IF NOT EXISTS idx_project_memberships_role ON project_memberships(assigned_role);
