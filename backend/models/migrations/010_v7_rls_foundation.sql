-- Migration 010: V7 Row Level Security (RLS) Foundation
-- Description: Establishes database-level project isolation ensuring User in Project A cannot access Project B.

-- 1. Helper function for Project Membership verification
CREATE OR REPLACE FUNCTION is_project_member(p_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    IF auth.uid() IS NULL THEN
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

-- 2. Enable RLS on all tables
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE stages ENABLE ROW LEVEL SECURITY;
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
ALTER TABLE claim_activity_splits ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE execution_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE contractors ENABLE ROW LEVEL SECURITY;
ALTER TABLE work_packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE quality_gates ENABLE ROW LEVEL SECURITY;
ALTER TABLE quality_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE institutional_incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE incident_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE contractor_disputes ENABLE ROW LEVEL SECURITY;
ALTER TABLE impact_scenarios ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_briefings ENABLE ROW LEVEL SECURITY;

-- 3. Project isolation policies for Authenticated users
DO $$
BEGIN
    -- Profiles
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_profiles_select') THEN
        CREATE POLICY "v7_profiles_select" ON profiles FOR SELECT TO authenticated USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_profiles_update_own') THEN
        CREATE POLICY "v7_profiles_update_own" ON profiles FOR UPDATE TO authenticated USING (id = auth.uid());
    END IF;

    -- Projects: accessible only if the user has an active membership
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_projects_member_select') THEN
        CREATE POLICY "v7_projects_member_select" ON projects FOR SELECT TO authenticated USING (is_project_member(project_id));
    END IF;

    -- Project Memberships: user can see their own memberships or memberships in projects they belong to
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_memberships_select') THEN
        CREATE POLICY "v7_memberships_select" ON project_memberships FOR SELECT TO authenticated 
        USING (user_id = auth.uid() OR is_project_member(project_id));
    END IF;

    -- Schedules: project member access (or legacy null-project access)
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_schedules_select') THEN
        CREATE POLICY "v7_schedules_select" ON schedules FOR SELECT TO authenticated 
        USING (project_id IS NULL OR is_project_member(project_id));
    END IF;

    -- Stages: project member access
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_stages_select') THEN
        CREATE POLICY "v7_stages_select" ON stages FOR SELECT TO authenticated 
        USING (is_project_member(project_id));
    END IF;

    -- Schedule Activities: project member access
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_activities_select') THEN
        CREATE POLICY "v7_activities_select" ON schedule_activities FOR SELECT TO authenticated 
        USING (project_id IS NULL OR is_project_member(project_id));
    END IF;

    -- Schedule Dependencies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_dependencies_select') THEN
        CREATE POLICY "v7_dependencies_select" ON schedule_dependencies FOR SELECT TO authenticated USING (true);
    END IF;

    -- Contractors & Work Packages: project member access
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_contractors_select') THEN
        CREATE POLICY "v7_contractors_select" ON contractors FOR SELECT TO authenticated 
        USING (is_project_member(project_id));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_contractors_insert') THEN
        CREATE POLICY "v7_contractors_insert" ON contractors FOR INSERT TO authenticated 
        WITH CHECK (is_project_member(project_id));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_contractors_update') THEN
        CREATE POLICY "v7_contractors_update" ON contractors FOR UPDATE TO authenticated 
        USING (is_project_member(project_id));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_work_packages_select') THEN
        CREATE POLICY "v7_work_packages_select" ON work_packages FOR SELECT TO authenticated 
        USING (is_project_member(project_id));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_work_packages_insert') THEN
        CREATE POLICY "v7_work_packages_insert" ON work_packages FOR INSERT TO authenticated 
        WITH CHECK (is_project_member(project_id));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_work_packages_update') THEN
        CREATE POLICY "v7_work_packages_update" ON work_packages FOR UPDATE TO authenticated 
        USING (is_project_member(project_id));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_activities_update') THEN
        CREATE POLICY "v7_activities_update" ON schedule_activities FOR UPDATE TO authenticated 
        USING (project_id IS NULL OR is_project_member(project_id));
    END IF;

    -- Quality Gates & Evidence: project member access
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_quality_gates_select') THEN
        CREATE POLICY "v7_quality_gates_select" ON quality_gates FOR SELECT TO authenticated 
        USING (is_project_member(project_id));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_quality_evidence_select') THEN
        CREATE POLICY "v7_quality_evidence_select" ON quality_evidence FOR SELECT TO authenticated 
        USING (EXISTS (SELECT 1 FROM quality_gates qg WHERE qg.quality_gate_id = quality_evidence.quality_gate_id AND is_project_member(qg.project_id)));
    END IF;

    -- Execution Events: project member access
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_events_select') THEN
        CREATE POLICY "v7_events_select" ON execution_events FOR SELECT TO authenticated 
        USING (project_id IS NULL OR is_project_member(project_id));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_events_insert') THEN
        CREATE POLICY "v7_events_insert" ON execution_events FOR INSERT TO authenticated 
        WITH CHECK (auth.uid() IS NOT NULL);
    END IF;

    -- Approved Actuals & Audit: project member access
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_actuals_select') THEN
        CREATE POLICY "v7_actuals_select" ON approved_actuals FOR SELECT TO authenticated 
        USING (project_id IS NULL OR is_project_member(project_id));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_audit_select') THEN
        CREATE POLICY "v7_audit_select" ON audit_logs FOR SELECT TO authenticated 
        USING (project_id IS NULL OR is_project_member(project_id));
    END IF;

    -- Future intelligence tables
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_incidents_select') THEN
        CREATE POLICY "v7_incidents_select" ON institutional_incidents FOR SELECT TO authenticated 
        USING (is_project_member(project_id));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_disputes_select') THEN
        CREATE POLICY "v7_disputes_select" ON contractor_disputes FOR SELECT TO authenticated 
        USING (is_project_member(project_id));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_scenarios_select') THEN
        CREATE POLICY "v7_scenarios_select" ON impact_scenarios FOR SELECT TO authenticated 
        USING (is_project_member(project_id));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_briefings_select') THEN
        CREATE POLICY "v7_briefings_select" ON agent_briefings FOR SELECT TO authenticated 
        USING (is_project_member(project_id));
    END IF;
END $$;
