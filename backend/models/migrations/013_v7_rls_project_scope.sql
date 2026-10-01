-- Migration 013: Close cross-project RLS leaks (pre-E2E hardening)
-- Description:
-- 1. claim_activity_splits: drop the legacy USING (true) SELECT policy (policies are OR-ed, so it
--    defeated v7_activity_splits_select). Member-scoped policies from 011 remain.
-- 2. evidence_links: replace USING (true) with a policy scoped through BOTH linked execution events.
-- 3. execution_summaries: global (project-less) aggregate cache. Drop the authenticated policy so it is
--    service-connection only. Project scoping needs an additive project_id column (proposed, not done here).
-- 4. source_documents: had RLS enabled and no policy. Add a SELECT policy scoped through the records
--    that reference a document (execution_events, quality_evidence, incident_evidence) or own upload.
-- 5. impact_scenarios: codify the INSERT/UPDATE/DELETE policies that exist on the shared DB but in no
--    migration (schema drift), so a clean build matches.
-- Idempotent. No data is modified.

DROP POLICY IF EXISTS "authenticated_activity_splits_select" ON claim_activity_splits;
DROP POLICY IF EXISTS "authenticated_evidence_links_select" ON evidence_links;
DROP POLICY IF EXISTS "authenticated_execution_summaries_select" ON execution_summaries;

DROP POLICY IF EXISTS "v7_evidence_links_select" ON evidence_links;
CREATE POLICY "v7_evidence_links_select" ON evidence_links FOR SELECT TO authenticated
USING (
    EXISTS (SELECT 1 FROM execution_events a
            WHERE a.event_id = evidence_links.event_id_a
              AND a.project_id IS NOT NULL AND is_project_member(a.project_id))
    AND EXISTS (SELECT 1 FROM execution_events b
            WHERE b.event_id = evidence_links.event_id_b
              AND b.project_id IS NOT NULL AND is_project_member(b.project_id))
);

DROP POLICY IF EXISTS "v7_source_documents_select" ON source_documents;
CREATE POLICY "v7_source_documents_select" ON source_documents FOR SELECT TO authenticated
USING (
    uploader_id = auth.uid()
    OR EXISTS (SELECT 1 FROM execution_events ee
               WHERE ee.document_id = source_documents.document_id
                 AND ee.project_id IS NOT NULL AND is_project_member(ee.project_id))
    OR EXISTS (SELECT 1 FROM quality_evidence qe
               JOIN quality_gates qg ON qg.quality_gate_id = qe.quality_gate_id
               WHERE qe.source_document_id = source_documents.document_id
                 AND is_project_member(qg.project_id))
    OR EXISTS (SELECT 1 FROM incident_evidence ie
               JOIN institutional_incidents ii ON ii.incident_id = ie.incident_id
               WHERE ie.source_document_id = source_documents.document_id
                 AND ii.project_id IS NOT NULL AND is_project_member(ii.project_id))
);

DROP POLICY IF EXISTS "v7_scenarios_insert" ON impact_scenarios;
CREATE POLICY "v7_scenarios_insert" ON impact_scenarios FOR INSERT TO authenticated
WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));
DROP POLICY IF EXISTS "v7_scenarios_update" ON impact_scenarios;
CREATE POLICY "v7_scenarios_update" ON impact_scenarios FOR UPDATE TO authenticated
USING (project_id IS NOT NULL AND is_project_member(project_id))
WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));
DROP POLICY IF EXISTS "v7_scenarios_delete" ON impact_scenarios;
CREATE POLICY "v7_scenarios_delete" ON impact_scenarios FOR DELETE TO authenticated
USING (project_id IS NOT NULL AND is_project_member(project_id));
