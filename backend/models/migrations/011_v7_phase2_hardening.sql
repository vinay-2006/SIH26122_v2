-- Migration 011: V7 Phase 2 Security Hardening & Schema Reconciliation
-- Description:
-- 1. Hardens RLS policies to eliminate 'project_id IS NULL' bypass.
-- 2. Enforces strict project isolation for SELECT, INSERT, UPDATE, and DELETE on all project-owned tables.
-- 3. Enables RLS and policies on claim_wbs_splits, claim_activity_splits, incident_evidence, and schedule_dependencies.
-- 4. Reconciles missing foreign keys on approved_actuals, claim_activity_splits, and claim_wbs_splits.
-- 5. Reconciles missing performance and integrity indexes.
-- 6. Backfills legacy orphan NULL project_id records where deterministic project context exists.

-- ============================================================================
-- 1. HARDEN MEMBERSHIP HELPER FUNCTION
-- ============================================================================
CREATE OR REPLACE FUNCTION is_project_member(p_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    IF p_id IS NULL OR auth.uid() IS NULL THEN
        RETURN FALSE;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM project_memberships
        WHERE project_id = p_id
          AND user_id = auth.uid()
          AND active = TRUE
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;

-- ============================================================================
-- 2. BACKFILL KNOWN LEGACY NULL RECORDS
-- ============================================================================
UPDATE schedule_activities sa
SET project_id = s.project_id
FROM schedules s
WHERE sa.schedule_id = s.schedule_id
  AND sa.project_id IS NULL
  AND s.project_id IS NOT NULL;

-- ============================================================================
-- 3. RECONCILE MISSING FOREIGN KEYS
-- ============================================================================
DO $$
BEGIN
    -- approved_actuals -> schedules
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_actuals_schedule') THEN
        ALTER TABLE approved_actuals
        ADD CONSTRAINT fk_actuals_schedule
        FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id)
        ON DELETE CASCADE;
    END IF;

    -- claim_activity_splits -> execution_events
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_activity_splits_event') THEN
        ALTER TABLE claim_activity_splits
        ADD CONSTRAINT fk_activity_splits_event
        FOREIGN KEY (event_id) REFERENCES execution_events(event_id)
        ON DELETE CASCADE;
    END IF;

    -- claim_wbs_splits -> execution_events
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_wbs_splits_event') THEN
        ALTER TABLE claim_wbs_splits
        ADD CONSTRAINT fk_wbs_splits_event
        FOREIGN KEY (event_id) REFERENCES execution_events(event_id)
        ON DELETE CASCADE;
    END IF;
END $$;

-- ============================================================================
-- 4. RECONCILE MISSING PERFORMANCE & INTEGRITY INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_claim_activity_splits_event ON claim_activity_splits (event_id);
CREATE INDEX IF NOT EXISTS idx_claim_wbs_splits_event ON claim_wbs_splits (event_id);
CREATE INDEX IF NOT EXISTS idx_evidence_links_a ON evidence_links (event_id_a);
CREATE INDEX IF NOT EXISTS idx_evidence_links_b ON evidence_links (event_id_b);
CREATE INDEX IF NOT EXISTS idx_approved_actuals_schedule_id ON approved_actuals (schedule_id);

-- ============================================================================
-- 5. ROW LEVEL SECURITY (RLS) ACTIVATION ON ALL TABLES
-- ============================================================================
ALTER TABLE claim_wbs_splits ENABLE ROW LEVEL SECURITY;
ALTER TABLE claim_activity_splits ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE execution_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE incident_evidence ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- 6. DROP INSECURE / BYPASS POLICIES
-- ============================================================================
DROP POLICY IF EXISTS "v7_schedules_select" ON schedules;
DROP POLICY IF EXISTS "v7_activities_select" ON schedule_activities;
DROP POLICY IF EXISTS "v7_dependencies_select" ON schedule_dependencies;
DROP POLICY IF EXISTS "v7_events_select" ON execution_events;
DROP POLICY IF EXISTS "v7_events_insert" ON execution_events;
DROP POLICY IF EXISTS "v7_actuals_select" ON approved_actuals;
DROP POLICY IF EXISTS "v7_audit_select" ON audit_logs;
DROP POLICY IF EXISTS "authenticated_activity_splits_select" ON claim_activity_splits;
DROP POLICY IF EXISTS "authenticated_evidence_links_select" ON evidence_links;
DROP POLICY IF EXISTS "authenticated_execution_summaries_select" ON execution_summaries;
DROP POLICY IF EXISTS "authenticated_wbs_select" ON claim_wbs_splits;

-- ============================================================================
-- 7. CREATE STRICT PROJECT ISOLATION POLICIES (NO NULL BYPASS)
-- ============================================================================

-- A. Schedules
CREATE POLICY "v7_schedules_select" ON schedules FOR SELECT TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_schedules_insert" ON schedules FOR INSERT TO authenticated
  WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_schedules_update" ON schedules FOR UPDATE TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id))
  WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_schedules_delete" ON schedules FOR DELETE TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id));

-- B. Schedule Activities
CREATE POLICY "v7_activities_select" ON schedule_activities FOR SELECT TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_activities_insert" ON schedule_activities FOR INSERT TO authenticated
  WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_activities_update" ON schedule_activities FOR UPDATE TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id))
  WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_activities_delete" ON schedule_activities FOR DELETE TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id));

-- C. Schedule Dependencies (scoped via parent schedule's project context)
CREATE POLICY "v7_dependencies_select" ON schedule_dependencies FOR SELECT TO authenticated
  USING (EXISTS (
    SELECT 1 FROM schedules s 
    WHERE s.schedule_id = schedule_dependencies.schedule_id 
      AND s.project_id IS NOT NULL 
      AND is_project_member(s.project_id)
  ));

CREATE POLICY "v7_dependencies_insert" ON schedule_dependencies FOR INSERT TO authenticated
  WITH CHECK (EXISTS (
    SELECT 1 FROM schedules s 
    WHERE s.schedule_id = schedule_dependencies.schedule_id 
      AND s.project_id IS NOT NULL 
      AND is_project_member(s.project_id)
  ));

CREATE POLICY "v7_dependencies_update" ON schedule_dependencies FOR UPDATE TO authenticated
  USING (EXISTS (
    SELECT 1 FROM schedules s 
    WHERE s.schedule_id = schedule_dependencies.schedule_id 
      AND s.project_id IS NOT NULL 
      AND is_project_member(s.project_id)
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM schedules s 
    WHERE s.schedule_id = schedule_dependencies.schedule_id 
      AND s.project_id IS NOT NULL 
      AND is_project_member(s.project_id)
  ));

CREATE POLICY "v7_dependencies_delete" ON schedule_dependencies FOR DELETE TO authenticated
  USING (EXISTS (
    SELECT 1 FROM schedules s 
    WHERE s.schedule_id = schedule_dependencies.schedule_id 
      AND s.project_id IS NOT NULL 
      AND is_project_member(s.project_id)
  ));

-- D. Execution Events
CREATE POLICY "v7_events_select" ON execution_events FOR SELECT TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_events_insert" ON execution_events FOR INSERT TO authenticated
  WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_events_update" ON execution_events FOR UPDATE TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id))
  WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_events_delete" ON execution_events FOR DELETE TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id));

-- E. Approved Actuals
CREATE POLICY "v7_actuals_select" ON approved_actuals FOR SELECT TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_actuals_insert" ON approved_actuals FOR INSERT TO authenticated
  WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_actuals_update" ON approved_actuals FOR UPDATE TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id))
  WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_actuals_delete" ON approved_actuals FOR DELETE TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id));

-- F. Audit Logs
CREATE POLICY "v7_audit_select" ON audit_logs FOR SELECT TO authenticated
  USING (project_id IS NOT NULL AND is_project_member(project_id));

CREATE POLICY "v7_audit_insert" ON audit_logs FOR INSERT TO authenticated
  WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));

-- G. Incident Evidence
CREATE POLICY "v7_incident_evidence_select" ON incident_evidence FOR SELECT TO authenticated
  USING (EXISTS (
    SELECT 1 FROM institutional_incidents ii 
    WHERE ii.incident_id = incident_evidence.incident_id 
      AND ii.project_id IS NOT NULL 
      AND is_project_member(ii.project_id)
  ));

CREATE POLICY "v7_incident_evidence_insert" ON incident_evidence FOR INSERT TO authenticated
  WITH CHECK (EXISTS (
    SELECT 1 FROM institutional_incidents ii 
    WHERE ii.incident_id = incident_evidence.incident_id 
      AND ii.project_id IS NOT NULL 
      AND is_project_member(ii.project_id)
  ));

CREATE POLICY "v7_incident_evidence_update" ON incident_evidence FOR UPDATE TO authenticated
  USING (EXISTS (
    SELECT 1 FROM institutional_incidents ii 
    WHERE ii.incident_id = incident_evidence.incident_id 
      AND ii.project_id IS NOT NULL 
      AND is_project_member(ii.project_id)
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM institutional_incidents ii 
    WHERE ii.incident_id = incident_evidence.incident_id 
      AND ii.project_id IS NOT NULL 
      AND is_project_member(ii.project_id)
  ));

CREATE POLICY "v7_incident_evidence_delete" ON incident_evidence FOR DELETE TO authenticated
  USING (EXISTS (
    SELECT 1 FROM institutional_incidents ii 
    WHERE ii.incident_id = incident_evidence.incident_id 
      AND ii.project_id IS NOT NULL 
      AND is_project_member(ii.project_id)
  ));

-- H. Claim Splits (Activity & WBS)
CREATE POLICY "v7_activity_splits_select" ON claim_activity_splits FOR SELECT TO authenticated
  USING (EXISTS (
    SELECT 1 FROM execution_events ee 
    WHERE ee.event_id = claim_activity_splits.event_id 
      AND ee.project_id IS NOT NULL 
      AND is_project_member(ee.project_id)
  ));

CREATE POLICY "v7_activity_splits_insert" ON claim_activity_splits FOR INSERT TO authenticated
  WITH CHECK (EXISTS (
    SELECT 1 FROM execution_events ee 
    WHERE ee.event_id = claim_activity_splits.event_id 
      AND ee.project_id IS NOT NULL 
      AND is_project_member(ee.project_id)
  ));

CREATE POLICY "v7_wbs_splits_select" ON claim_wbs_splits FOR SELECT TO authenticated
  USING (EXISTS (
    SELECT 1 FROM execution_events ee 
    WHERE ee.event_id = claim_wbs_splits.event_id 
      AND ee.project_id IS NOT NULL 
      AND is_project_member(ee.project_id)
  ));

CREATE POLICY "v7_wbs_splits_insert" ON claim_wbs_splits FOR INSERT TO authenticated
  WITH CHECK (EXISTS (
    SELECT 1 FROM execution_events ee 
    WHERE ee.event_id = claim_wbs_splits.event_id 
      AND ee.project_id IS NOT NULL 
      AND is_project_member(ee.project_id)
  ));

-- I. Candidate Matches, Conflict Records, Validation Issues, Planner Decisions, Source References
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_candidate_matches_select') THEN
        CREATE POLICY "v7_candidate_matches_select" ON candidate_matches FOR SELECT TO authenticated
          USING (EXISTS (
            SELECT 1 FROM execution_events ee 
            WHERE ee.event_id = candidate_matches.event_id 
              AND ee.project_id IS NOT NULL 
              AND is_project_member(ee.project_id)
          ));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_conflict_records_select') THEN
        CREATE POLICY "v7_conflict_records_select" ON conflict_records FOR SELECT TO authenticated
          USING (EXISTS (
            SELECT 1 FROM schedules s 
            WHERE s.schedule_id = conflict_records.schedule_id 
              AND s.project_id IS NOT NULL 
              AND is_project_member(s.project_id)
          ));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_validation_issues_select') THEN
        CREATE POLICY "v7_validation_issues_select" ON validation_issues FOR SELECT TO authenticated
          USING (EXISTS (
            SELECT 1 FROM execution_events ee 
            WHERE ee.event_id = validation_issues.event_id 
              AND ee.project_id IS NOT NULL 
              AND is_project_member(ee.project_id)
          ));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_planner_decisions_select') THEN
        CREATE POLICY "v7_planner_decisions_select" ON planner_decisions FOR SELECT TO authenticated
          USING (EXISTS (
            SELECT 1 FROM execution_events ee 
            WHERE ee.event_id = planner_decisions.event_id 
              AND ee.project_id IS NOT NULL 
              AND is_project_member(ee.project_id)
          ));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_source_references_select') THEN
        CREATE POLICY "v7_source_references_select" ON source_references FOR SELECT TO authenticated
          USING (EXISTS (
            SELECT 1 FROM execution_events ee 
            WHERE ee.event_id = source_references.event_id 
              AND ee.project_id IS NOT NULL 
              AND is_project_member(ee.project_id)
          ));
    END IF;

    -- Stages Write Policies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_stages_insert') THEN
        CREATE POLICY "v7_stages_insert" ON stages FOR INSERT TO authenticated
        WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_stages_update') THEN
        CREATE POLICY "v7_stages_update" ON stages FOR UPDATE TO authenticated
        USING (project_id IS NOT NULL AND is_project_member(project_id))
        WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_stages_delete') THEN
        CREATE POLICY "v7_stages_delete" ON stages FOR DELETE TO authenticated
        USING (project_id IS NOT NULL AND is_project_member(project_id));
    END IF;
END $$;
