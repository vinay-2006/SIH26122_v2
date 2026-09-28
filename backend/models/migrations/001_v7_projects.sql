-- Migration 001: V7 Projects Domain
-- Description: Establishes the projects entity as the top-level anchor for multi-project isolation.

CREATE TABLE IF NOT EXISTS projects (
  project_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_code TEXT UNIQUE NOT NULL,
  project_name TEXT NOT NULL,
  description TEXT,
  client_name TEXT,
  project_type TEXT,
  location TEXT,
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  geofence_radius_m REAL,
  planned_start DATE,
  planned_finish DATE,
  contract_finish DATE,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_projects_code ON projects (project_code);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects (status);
CREATE INDEX IF NOT EXISTS idx_projects_created_at ON projects (created_at);
