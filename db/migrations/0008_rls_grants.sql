-- 0008 RLS + grants. Deny by default. The backend uses a privileged connection and enforces authorization itself;
-- RLS is the second wall for any direct (PostgREST / authenticated-role) access. The frontend never reads tables directly.

-- Nothing is granted to anon; authenticated gets SELECT only (RLS-filtered). No client-side writes exist.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION is_project_member(uuid), project_role_of(uuid, uuid) TO authenticated;

-- Claim visibility: a SUPERVISOR sees every claim of the project; a SITE_ENGINEER only their own; a PROJECT_MANAGER none.
CREATE FUNCTION can_read_claim(p_project UUID, p_filed_by UUID) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT auth.uid() IS NOT NULL AND (
    project_role_of(p_project, auth.uid()) = 'SUPERVISOR'
    OR (project_role_of(p_project, auth.uid()) = 'SITE_ENGINEER' AND p_filed_by = auth.uid())) $$;
CREATE FUNCTION can_read_claim_by_id(p_project UUID, p_event UUID) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT can_read_claim(p_project, (SELECT filed_by FROM execution_events WHERE project_id = p_project AND event_id = p_event)) $$;
CREATE FUNCTION is_pm(p_project UUID) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT auth.uid() IS NOT NULL AND project_role_of(p_project, auth.uid()) = 'PROJECT_MANAGER' $$;
CREATE FUNCTION is_supervisor_or_pm(p_project UUID) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT auth.uid() IS NOT NULL AND project_role_of(p_project, auth.uid()) IN ('SUPERVISOR','PROJECT_MANAGER') $$;
CREATE FUNCTION is_member_of_any_project() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT auth.uid() IS NOT NULL AND EXISTS (SELECT 1 FROM project_memberships WHERE user_id = auth.uid() AND status = 'ACTIVE') $$;
CREATE FUNCTION shares_project_with(p_user UUID) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT auth.uid() IS NOT NULL AND EXISTS (
    SELECT 1 FROM project_memberships a JOIN project_memberships b ON a.project_id = b.project_id
     WHERE a.user_id = auth.uid() AND b.user_id = p_user AND a.status = 'ACTIVE' AND b.status = 'ACTIVE') $$;
GRANT EXECUTE ON FUNCTION can_read_claim(uuid, uuid), can_read_claim_by_id(uuid, uuid), is_pm(uuid),
  is_supervisor_or_pm(uuid), is_member_of_any_project(), shares_project_with(uuid) TO authenticated;

DO $$
DECLARE t text;
  member_tables text[] := ARRAY['upload_batches','source_documents','schedule_versions','activities','assignments','schedule_wbs',
    'wbs_stage_rules','baseline_activities','schedule_dependencies','project_resources','baseline_resources',
    'approved_activity_progress','approved_resource_progress','root_causes','issues','issue_evidence','project_settings'];
  claim_child_tables text[] := ARRAY['claim_quantities','source_references','claim_evidence','candidate_matches',
    'claim_activity_splits','claim_validations','evidence_links'];
  ref_tables text[] := ARRAY['disciplines','discipline_aliases','units_of_measure','issue_categories'];
BEGIN
  FOREACH t IN ARRAY member_tables LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO authenticated USING (is_project_member(project_id))', t || '_select', t);
    EXECUTE format('GRANT SELECT ON %I TO authenticated', t);
  END LOOP;
  FOREACH t IN ARRAY claim_child_tables LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO authenticated USING (can_read_claim_by_id(project_id, %s))', t || '_select', t,
                   CASE t WHEN 'evidence_links' THEN 'event_id_a' ELSE 'event_id' END);
    EXECUTE format('GRANT SELECT ON %I TO authenticated', t);
  END LOOP;
  FOREACH t IN ARRAY ref_tables LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO authenticated USING (auth.uid() IS NOT NULL)', t || '_select', t);
    EXECUTE format('GRANT SELECT ON %I TO authenticated', t);
  END LOOP;
END $$;

-- Tables with bespoke visibility
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY profiles_select ON profiles FOR SELECT TO authenticated USING (id = auth.uid() OR shares_project_with(id));
ALTER TABLE platform_grants ENABLE ROW LEVEL SECURITY;
CREATE POLICY platform_grants_select ON platform_grants FOR SELECT TO authenticated USING (user_id = auth.uid());
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
CREATE POLICY projects_select ON projects FOR SELECT TO authenticated USING (is_project_member(project_id));
ALTER TABLE project_memberships ENABLE ROW LEVEL SECURITY;
CREATE POLICY memberships_select ON project_memberships FOR SELECT TO authenticated
  USING (user_id = auth.uid() OR is_project_member(project_id));
ALTER TABLE project_invitations ENABLE ROW LEVEL SECURITY;
CREATE POLICY invitations_select ON project_invitations FOR SELECT TO authenticated USING (is_pm(project_id));
ALTER TABLE schedule_imports ENABLE ROW LEVEL SECURITY;
CREATE POLICY imports_select ON schedule_imports FOR SELECT TO authenticated USING (is_pm(project_id));
ALTER TABLE activity_lineage ENABLE ROW LEVEL SECURITY;
CREATE POLICY lineage_select ON activity_lineage FOR SELECT TO authenticated USING (is_pm(project_id));
ALTER TABLE execution_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY events_select ON execution_events FOR SELECT TO authenticated USING (can_read_claim(project_id, filed_by));
ALTER TABLE planner_decisions ENABLE ROW LEVEL SECURITY;
CREATE POLICY decisions_select ON planner_decisions FOR SELECT TO authenticated USING (can_read_claim_by_id(project_id, event_id));
ALTER TABLE conflict_records ENABLE ROW LEVEL SECURITY;
CREATE POLICY conflicts_select ON conflict_records FOR SELECT TO authenticated USING (project_role_of(project_id, auth.uid()) = 'SUPERVISOR');
ALTER TABLE institutional_memory ENABLE ROW LEVEL SECURITY;
CREATE POLICY memory_select ON institutional_memory FOR SELECT TO authenticated
  USING (is_project_member(project_id) OR (visibility = 'ORGANISATION' AND is_member_of_any_project()));
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
CREATE POLICY notifications_select ON notifications FOR SELECT TO authenticated
  USING (recipient_id = auth.uid() AND is_project_member(project_id));
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_select ON audit_logs FOR SELECT TO authenticated USING (project_id IS NOT NULL AND is_supervisor_or_pm(project_id));

GRANT SELECT ON profiles, platform_grants, projects, project_memberships, project_invitations, schedule_imports, activity_lineage,
  execution_events, planner_decisions, conflict_records, institutional_memory, notifications, audit_logs TO authenticated;

-- Views run with the CALLER's rights, so the policies above apply to them too.
DO $$
DECLARE v text;
BEGIN
  FOR v IN SELECT viewname FROM pg_views WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER VIEW %I SET (security_invoker = true)', v);
    EXECUTE format('GRANT SELECT ON %I TO authenticated', v);
  END LOOP;
END $$;
