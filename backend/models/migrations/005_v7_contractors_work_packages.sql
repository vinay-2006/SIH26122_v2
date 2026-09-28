-- Migration 005: V7 Contractors and Work Packages
-- Description: Establishes contractor entities and work packages for project attribution and tracking.

CREATE TABLE IF NOT EXISTS contractors (
  contractor_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  contractor_code TEXT NOT NULL,
  company_name TEXT NOT NULL,
  type_or_category TEXT,
  contract_reference TEXT,
  contact_email TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED', 'TERMINATED')),
  active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_contractors_project_code UNIQUE (project_id, contractor_code)
);

CREATE INDEX IF NOT EXISTS idx_contractors_project_id ON contractors(project_id);
CREATE INDEX IF NOT EXISTS idx_contractors_code ON contractors(project_id, contractor_code);

CREATE TABLE IF NOT EXISTS work_packages (
  work_package_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  contractor_id UUID REFERENCES contractors(contractor_id) ON DELETE SET NULL,
  stage_id UUID REFERENCES stages(stage_id) ON DELETE SET NULL,
  package_code TEXT,
  package_name TEXT NOT NULL,
  discipline TEXT,
  description TEXT,
  planned_start DATE,
  planned_finish DATE,
  status TEXT NOT NULL DEFAULT 'NOT_STARTED' CHECK (status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'ON_HOLD', 'CANCELLED')),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_work_packages_project_code UNIQUE (project_id, package_code)
);

CREATE INDEX IF NOT EXISTS idx_work_packages_project_id ON work_packages(project_id);
CREATE INDEX IF NOT EXISTS idx_work_packages_contractor_id ON work_packages(contractor_id);
CREATE INDEX IF NOT EXISTS idx_work_packages_stage_id ON work_packages(stage_id);
