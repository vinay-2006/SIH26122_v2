-- Migration 003: V7 Stages Domain
-- Description: Establishes hierarchical stages tied to projects and schedule versions for progress and gating.

CREATE TABLE IF NOT EXISTS stages (
  stage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  schedule_id TEXT NOT NULL REFERENCES schedules(schedule_id) ON DELETE RESTRICT,
  parent_stage_id UUID REFERENCES stages(stage_id) ON DELETE RESTRICT,
  stage_code TEXT,
  stage_name TEXT NOT NULL,
  sequence_order INTEGER DEFAULT 1,
  weight_pct REAL CHECK (weight_pct >= 0.0 AND weight_pct <= 100.0),
  status TEXT NOT NULL DEFAULT 'NOT_STARTED' CHECK (status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'BLOCKED', 'ON_HOLD')),
  planned_start DATE,
  planned_finish DATE,
  contract_milestone_date DATE,
  gating_predecessor_stage_id UUID REFERENCES stages(stage_id) ON DELETE RESTRICT,
  completion_rule JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stages_project_id ON stages(project_id);
CREATE INDEX IF NOT EXISTS idx_stages_schedule_id ON stages(schedule_id);
CREATE INDEX IF NOT EXISTS idx_stages_parent_stage_id ON stages(parent_stage_id);
CREATE INDEX IF NOT EXISTS idx_stages_gating_predecessor ON stages(gating_predecessor_stage_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_stages_project_schedule_code ON stages(project_id, schedule_id, stage_code) WHERE stage_code IS NOT NULL;
