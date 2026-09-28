-- Migration 002: V7 Schedule Versions
-- Description: Extends schedules with explicit project context, versioning, supersession, and source provenance.

ALTER TABLE schedules ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(project_id) ON DELETE RESTRICT;
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS version_code TEXT;
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS version_metadata JSONB DEFAULT '{}';
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS source_hash TEXT;
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE;
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS supersedes_schedule_id TEXT REFERENCES schedules(schedule_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_schedules_project_id ON schedules(project_id);
CREATE INDEX IF NOT EXISTS idx_schedules_project_active ON schedules(project_id, active);
CREATE INDEX IF NOT EXISTS idx_schedules_supersedes ON schedules(supersedes_schedule_id);
CREATE INDEX IF NOT EXISTS idx_schedules_source_hash ON schedules(source_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uq_schedules_project_version ON schedules(project_id, version_code) WHERE version_code IS NOT NULL;
