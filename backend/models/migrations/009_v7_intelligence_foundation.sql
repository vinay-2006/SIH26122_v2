-- Migration 009: V7 Intelligence Foundation
-- Description: Establishes structural storage tables for incidents, disputes, scenarios, and agent briefings.

CREATE TABLE IF NOT EXISTS institutional_incidents (
  incident_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  stage_id UUID REFERENCES stages(stage_id) ON DELETE SET NULL,
  activity_id TEXT,
  discipline TEXT,
  contractor_id UUID REFERENCES contractors(contractor_id) ON DELETE SET NULL,
  incident_type TEXT,
  title TEXT NOT NULL,
  narrative TEXT,
  root_cause TEXT,
  delay_days REAL DEFAULT 0.0,
  cost_impact REAL,
  corrective_action TEXT,
  lessons_learned TEXT,
  recorded_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
  recorded_at TIMESTAMPTZ DEFAULT now(),
  status TEXT DEFAULT 'OPEN'
);

CREATE INDEX IF NOT EXISTS idx_incidents_project_id ON institutional_incidents(project_id);
CREATE INDEX IF NOT EXISTS idx_incidents_stage_id ON institutional_incidents(stage_id);
CREATE INDEX IF NOT EXISTS idx_incidents_contractor_id ON institutional_incidents(contractor_id);

CREATE TABLE IF NOT EXISTS incident_evidence (
  incident_evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id UUID NOT NULL REFERENCES institutional_incidents(incident_id) ON DELETE CASCADE,
  source_document_id TEXT REFERENCES source_documents(document_id) ON DELETE SET NULL,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_incident_evidence_incident ON incident_evidence(incident_id);

CREATE TABLE IF NOT EXISTS contractor_disputes (
  dispute_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  contractor_id UUID NOT NULL REFERENCES contractors(contractor_id) ON DELETE RESTRICT,
  incident_id UUID REFERENCES institutional_incidents(incident_id) ON DELETE SET NULL,
  claimed_extension_days REAL DEFAULT 0.0,
  granted_extension_days REAL DEFAULT 0.0,
  settlement_terms TEXT,
  status TEXT DEFAULT 'OPEN',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_disputes_project_id ON contractor_disputes(project_id);
CREATE INDEX IF NOT EXISTS idx_disputes_contractor_id ON contractor_disputes(contractor_id);

CREATE TABLE IF NOT EXISTS impact_scenarios (
  scenario_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  schedule_id TEXT NOT NULL REFERENCES schedules(schedule_id) ON DELETE RESTRICT,
  name TEXT NOT NULL,
  created_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  inputs JSONB NOT NULL,
  results JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scenarios_project_id ON impact_scenarios(project_id);
CREATE INDEX IF NOT EXISTS idx_scenarios_schedule_id ON impact_scenarios(schedule_id);

CREATE TABLE IF NOT EXISTS agent_briefings (
  briefing_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  trigger_type TEXT,
  severity TEXT,
  title TEXT NOT NULL,
  briefing_text TEXT NOT NULL,
  evidence_refs JSONB DEFAULT '[]',
  recommended_action TEXT,
  generated_at TIMESTAMPTZ DEFAULT now(),
  status TEXT DEFAULT 'ACTIVE'
);

CREATE INDEX IF NOT EXISTS idx_briefings_project_id ON agent_briefings(project_id);
