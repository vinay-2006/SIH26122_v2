-- Migration 015: persisted source of truth for the BLOCKED workflow condition
-- Description:
--   WorkflowCondition.BLOCKED had no authoritative persisted source: nothing stored on activities, approved
--   actuals or events could ever produce it. A blocker is an explainable, human-governed fact:
--       activity or stage -> reason/type -> source (field report) -> raised by/at -> resolved by/at -> status
--   One row per blocker. The BLOCKED condition of an activity is DERIVED (never copied) from ACTIVE rows that
--   target the activity, or its stage when activity_id is NULL. Nothing about progress or actuals is stored here.
-- Idempotent. RLS: project members only (same model as every V7 project-owned table).
CREATE TABLE IF NOT EXISTS execution_blockers (
    blocker_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    schedule_id     TEXT NOT NULL REFERENCES schedules(schedule_id) ON DELETE RESTRICT,
    activity_id     TEXT,
    stage_id        UUID REFERENCES stages(stage_id) ON DELETE RESTRICT,
    blocker_type    TEXT NOT NULL CHECK (blocker_type IN
                    ('MATERIAL', 'EQUIPMENT', 'LABOUR', 'ACCESS', 'PERMIT', 'DESIGN', 'WEATHER', 'DEPENDENCY', 'OTHER')),
    reason          TEXT NOT NULL CHECK (length(btrim(reason)) >= 3),
    source_event_id TEXT REFERENCES execution_events(event_id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RESOLVED')),
    created_by      UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_by     UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    resolved_at     TIMESTAMPTZ,
    resolution_notes TEXT,
    -- a blocker targets an activity or a stage (at least one)
    CONSTRAINT execution_blockers_target_chk CHECK (activity_id IS NOT NULL OR stage_id IS NOT NULL),
    -- an activity target must be an activity of that schedule
    CONSTRAINT execution_blockers_activity_fk FOREIGN KEY (schedule_id, activity_id)
        REFERENCES schedule_activities(schedule_id, activity_id) ON DELETE RESTRICT,
    -- lifecycle integrity: resolved rows carry who/when, active rows do not
    CONSTRAINT execution_blockers_lifecycle_chk CHECK (
        (status = 'ACTIVE' AND resolved_by IS NULL AND resolved_at IS NULL)
        OR (status = 'RESOLVED' AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_blockers_active_activity ON execution_blockers (project_id, schedule_id, activity_id) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_blockers_active_stage ON execution_blockers (project_id, schedule_id, stage_id) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_blockers_source_event ON execution_blockers (source_event_id);

-- supports the bulk workflow-condition derivation (quality gates by activity within a schedule version)
CREATE INDEX IF NOT EXISTS idx_quality_gates_activity_state ON quality_gates (project_id, schedule_id, activity_id);

ALTER TABLE execution_blockers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "v7_blockers_select" ON execution_blockers;
CREATE POLICY "v7_blockers_select" ON execution_blockers FOR SELECT TO authenticated
    USING (project_id IS NOT NULL AND is_project_member(project_id));
DROP POLICY IF EXISTS "v7_blockers_insert" ON execution_blockers;
CREATE POLICY "v7_blockers_insert" ON execution_blockers FOR INSERT TO authenticated
    WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));
DROP POLICY IF EXISTS "v7_blockers_update" ON execution_blockers;
CREATE POLICY "v7_blockers_update" ON execution_blockers FOR UPDATE TO authenticated
    USING (project_id IS NOT NULL AND is_project_member(project_id))
    WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));
-- no DELETE policy: a blocker is history, it is resolved, never removed.
