-- Migration 007: V7 Core Table Extensions
-- Description: Extends schedule_activities, dependencies, execution_events, approved_actuals, and audit_logs with explicit V7 domain context.

-- 1. schedule_activities extensions
ALTER TABLE schedule_activities ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(project_id) ON DELETE RESTRICT;
ALTER TABLE schedule_activities ADD COLUMN IF NOT EXISTS stage_id UUID REFERENCES stages(stage_id) ON DELETE SET NULL;
ALTER TABLE schedule_activities ADD COLUMN IF NOT EXISTS contractor_id UUID REFERENCES contractors(contractor_id) ON DELETE SET NULL;
ALTER TABLE schedule_activities ADD COLUMN IF NOT EXISTS work_package_id UUID REFERENCES work_packages(work_package_id) ON DELETE SET NULL;
ALTER TABLE schedule_activities ADD COLUMN IF NOT EXISTS weight_factor REAL DEFAULT 1.0;
ALTER TABLE schedule_activities ADD COLUMN IF NOT EXISTS weight_basis TEXT DEFAULT 'PLANNED_COST';
ALTER TABLE schedule_activities ADD COLUMN IF NOT EXISTS quality_gate_required BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_schedule_activities_project_id ON schedule_activities(project_id);
CREATE INDEX IF NOT EXISTS idx_schedule_activities_stage_id ON schedule_activities(stage_id);
CREATE INDEX IF NOT EXISTS idx_schedule_activities_contractor_id ON schedule_activities(contractor_id);
CREATE INDEX IF NOT EXISTS idx_schedule_activities_work_package_id ON schedule_activities(work_package_id);

-- 1b. schedule foreign keys
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_activities_schedule') THEN
        ALTER TABLE schedule_activities ADD CONSTRAINT fk_activities_schedule FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_events_schedule') THEN
        ALTER TABLE execution_events ADD CONSTRAINT fk_events_schedule FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_dependencies_schedule') THEN
        ALTER TABLE schedule_dependencies ADD CONSTRAINT fk_dependencies_schedule FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id) ON DELETE CASCADE;
    END IF;
END $$;

-- 2. schedule_dependencies extensions
ALTER TABLE schedule_dependencies ADD COLUMN IF NOT EXISTS lag_days REAL DEFAULT 0.0;
CREATE INDEX IF NOT EXISTS idx_schedule_dependencies_schedule ON schedule_dependencies(schedule_id);

-- 3. execution_events extensions
ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(project_id) ON DELETE RESTRICT;
ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS stage_id UUID REFERENCES stages(stage_id) ON DELETE SET NULL;
ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS contractor_id UUID REFERENCES contractors(contractor_id) ON DELETE SET NULL;
ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS work_package_id UUID REFERENCES work_packages(work_package_id) ON DELETE SET NULL;
ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS quality_gate_id UUID REFERENCES quality_gates(quality_gate_id) ON DELETE SET NULL;
ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS reopen_status TEXT DEFAULT 'NONE' CHECK (reopen_status IN ('NONE', 'REQUESTED', 'APPROVED', 'REJECTED'));
ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS reopened_from_actual_id TEXT;
ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS reopen_justification TEXT;
ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS reopen_requested_by UUID REFERENCES profiles(id) ON DELETE SET NULL;
ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS reopen_decided_by UUID REFERENCES profiles(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_execution_events_project_id ON execution_events(project_id);
CREATE INDEX IF NOT EXISTS idx_execution_events_stage_id ON execution_events(stage_id);
CREATE INDEX IF NOT EXISTS idx_execution_events_contractor_id ON execution_events(contractor_id);
CREATE INDEX IF NOT EXISTS idx_execution_events_work_package_id ON execution_events(work_package_id);

-- 4. approved_actuals extensions
ALTER TABLE approved_actuals ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(project_id) ON DELETE RESTRICT;
ALTER TABLE approved_actuals ADD COLUMN IF NOT EXISTS stage_id UUID REFERENCES stages(stage_id) ON DELETE SET NULL;
ALTER TABLE approved_actuals ADD COLUMN IF NOT EXISTS is_reopened BOOLEAN DEFAULT FALSE;
ALTER TABLE approved_actuals ADD COLUMN IF NOT EXISTS reopened_at TIMESTAMPTZ;
ALTER TABLE approved_actuals ADD COLUMN IF NOT EXISTS reopened_by UUID REFERENCES profiles(id) ON DELETE SET NULL;
ALTER TABLE approved_actuals ADD COLUMN IF NOT EXISTS rework_notes TEXT;

CREATE INDEX IF NOT EXISTS idx_approved_actuals_project_id ON approved_actuals(project_id);
CREATE INDEX IF NOT EXISTS idx_approved_actuals_stage_id ON approved_actuals(stage_id);

-- 5. audit_logs extensions
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(project_id) ON DELETE RESTRICT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS schedule_id TEXT REFERENCES schedules(schedule_id) ON DELETE SET NULL;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS role TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS entity_context JSONB DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_audit_logs_project_id ON audit_logs(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_schedule_id ON audit_logs(schedule_id);
