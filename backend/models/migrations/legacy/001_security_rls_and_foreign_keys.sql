-- Migration 001: Supabase RLS Security and Safe Foreign Key Constraints
-- Description:
-- 1. Enables Row Level Security (RLS) on all 14 public tables.
-- 2. Closes anonymous public data leakage via Supabase PostgREST (anon has default deny).
-- 3. Grants appropriate SELECT/INSERT policies to authenticated users matching the system's access model.
-- 4. Adds foreign keys with NOT VALID so existing historical orphan records are preserved without breaking referential integrity for all new writes.

-- ============================================================================
-- 1. ROW LEVEL SECURITY (RLS) ACTIVATION
-- ============================================================================

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE schedule_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE schedule_dependencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE execution_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_references ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_matches ENABLE ROW LEVEL SECURITY;
ALTER TABLE conflict_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE validation_issues ENABLE ROW LEVEL SECURITY;
ALTER TABLE planner_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE approved_actuals ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE claim_wbs_splits ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- 2. AUTHENTICATED USER POLICIES (Matches application architecture)
-- ============================================================================

-- Profiles: Authenticated users can read all profiles; users can update only their own profile
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_profiles_select') THEN
        CREATE POLICY "authenticated_profiles_select" ON profiles FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_profiles_update_own') THEN
        CREATE POLICY "authenticated_profiles_update_own" ON profiles FOR UPDATE TO authenticated USING (id = auth.uid());
    END IF;
END $$;

-- Schedules & Schedule Metadata: Authenticated users can read schedules
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_schedules_select') THEN
        CREATE POLICY "authenticated_schedules_select" ON schedules FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_activities_select') THEN
        CREATE POLICY "authenticated_activities_select" ON schedule_activities FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_dependencies_select') THEN
        CREATE POLICY "authenticated_dependencies_select" ON schedule_dependencies FOR SELECT TO authenticated USING (true);
    END IF;
END $$;

-- Ingestion & Source Documents: Authenticated can read and insert their reports
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_source_docs_select') THEN
        CREATE POLICY "authenticated_source_docs_select" ON source_documents FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_source_docs_insert') THEN
        CREATE POLICY "authenticated_source_docs_insert" ON source_documents FOR INSERT TO authenticated WITH CHECK (auth.uid() IS NOT NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_events_select') THEN
        CREATE POLICY "authenticated_events_select" ON execution_events FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_events_insert') THEN
        CREATE POLICY "authenticated_events_insert" ON execution_events FOR INSERT TO authenticated WITH CHECK (auth.uid() IS NOT NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_source_refs_select') THEN
        CREATE POLICY "authenticated_source_refs_select" ON source_references FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_source_refs_insert') THEN
        CREATE POLICY "authenticated_source_refs_insert" ON source_references FOR INSERT TO authenticated WITH CHECK (auth.uid() IS NOT NULL);
    END IF;
END $$;

-- Decisions, Actuals, Analysis & Audit: Authenticated can read
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_decisions_select') THEN
        CREATE POLICY "authenticated_decisions_select" ON planner_decisions FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_actuals_select') THEN
        CREATE POLICY "authenticated_actuals_select" ON approved_actuals FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_matches_select') THEN
        CREATE POLICY "authenticated_matches_select" ON candidate_matches FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_conflicts_select') THEN
        CREATE POLICY "authenticated_conflicts_select" ON conflict_records FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_validation_select') THEN
        CREATE POLICY "authenticated_validation_select" ON validation_issues FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_wbs_select') THEN
        CREATE POLICY "authenticated_wbs_select" ON claim_wbs_splits FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'authenticated_audit_select') THEN
        CREATE POLICY "authenticated_audit_select" ON audit_logs FOR SELECT TO authenticated USING (true);
    END IF;
END $$;

-- Note: Anonymous role (anon) intentionally has NO policies created above.
-- In PostgreSQL RLS, lack of an applicable policy results in DEFAULT DENY.
-- Direct PostgREST queries with anon key will be denied with HTTP 401/403 or empty set.

-- ============================================================================
-- 3. SAFE FOREIGN KEY CONSTRAINTS (NOT VALID)
-- ============================================================================

-- 1. schedule_activities -> schedules
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_schedule_activities_schedule') THEN
        ALTER TABLE schedule_activities
        ADD CONSTRAINT fk_schedule_activities_schedule
        FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id)
        NOT VALID;
    END IF;
END $$;

-- 2. schedule_dependencies -> schedules
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_schedule_dependencies_schedule') THEN
        ALTER TABLE schedule_dependencies
        ADD CONSTRAINT fk_schedule_dependencies_schedule
        FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id)
        NOT VALID;
    END IF;
END $$;

-- 3. execution_events -> schedules
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_execution_events_schedule') THEN
        ALTER TABLE execution_events
        ADD CONSTRAINT fk_execution_events_schedule
        FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id)
        NOT VALID;
    END IF;
END $$;

-- 4. planner_decisions -> execution_events
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_planner_decisions_event') THEN
        ALTER TABLE planner_decisions
        ADD CONSTRAINT fk_planner_decisions_event
        FOREIGN KEY (event_id) REFERENCES execution_events(event_id)
        NOT VALID;
    END IF;
END $$;

-- 5. approved_actuals -> planner_decisions
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_approved_actuals_decision') THEN
        ALTER TABLE approved_actuals
        ADD CONSTRAINT fk_approved_actuals_decision
        FOREIGN KEY (decision_id) REFERENCES planner_decisions(decision_id)
        NOT VALID;
    END IF;
END $$;

-- 6. approved_actuals -> schedule_activities (composite)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_approved_actuals_activity') THEN
        ALTER TABLE approved_actuals
        ADD CONSTRAINT fk_approved_actuals_activity
        FOREIGN KEY (schedule_id, activity_id) REFERENCES schedule_activities(schedule_id, activity_id)
        NOT VALID;
    END IF;
END $$;

-- 7. candidate_matches -> execution_events
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_candidate_matches_event') THEN
        ALTER TABLE candidate_matches
        ADD CONSTRAINT fk_candidate_matches_event
        FOREIGN KEY (event_id) REFERENCES execution_events(event_id)
        NOT VALID;
    END IF;
END $$;

-- 8. validation_issues -> execution_events
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_validation_issues_event') THEN
        ALTER TABLE validation_issues
        ADD CONSTRAINT fk_validation_issues_event
        FOREIGN KEY (event_id) REFERENCES execution_events(event_id)
        NOT VALID;
    END IF;
END $$;

-- 9. source_references -> execution_events
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_source_references_event') THEN
        ALTER TABLE source_references
        ADD CONSTRAINT fk_source_references_event
        FOREIGN KEY (event_id) REFERENCES execution_events(event_id)
        NOT VALID;
    END IF;
END $$;
