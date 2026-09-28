-- Migration 008: V7 Progress Metadata and Integrity Constraints
-- Description: Adds CHECK constraints and composite project-scoped indexes for progress and EVM calculations.

-- 1. Constraints on weight factor and dependencies
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_activities_weight_factor'
    ) THEN
        ALTER TABLE schedule_activities ADD CONSTRAINT chk_activities_weight_factor CHECK (weight_factor >= 0.0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_dependencies_lag_days'
    ) THEN
        ALTER TABLE schedule_dependencies ADD CONSTRAINT chk_dependencies_lag_days CHECK (lag_days >= -365.0 AND lag_days <= 365.0);
    END IF;
END $$;

-- 2. Performance Composite Indexes for Project/Schedule Scoped Queries
CREATE INDEX IF NOT EXISTS idx_activities_proj_sched ON schedule_activities (project_id, schedule_id);
CREATE INDEX IF NOT EXISTS idx_activities_proj_stage ON schedule_activities (project_id, stage_id);
CREATE INDEX IF NOT EXISTS idx_events_proj_sched ON execution_events (project_id, schedule_id);
CREATE INDEX IF NOT EXISTS idx_actuals_proj_sched ON approved_actuals (project_id, schedule_id);
