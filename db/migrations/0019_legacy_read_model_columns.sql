-- 0019 Legacy read model: the remaining columns of the original tables (actual_id, decision_id, schedule_id on conflicts / splits, source document link ...) so the original
-- read-only queries run unchanged. Views only (derived, no data): dropped and recreated. Same safety as 0016: scoped by the transaction-local project / version settings, not
-- granted to client roles, not writable.

DROP VIEW IF EXISTS lrm.approved_actuals;
CREATE VIEW lrm.approved_actuals AS
SELECT p.version_id::text AS schedule_id, p.project_id, p.external_activity_id AS activity_id, p.activity_uid, p.activity_uid AS actual_id,
       p.actual_start, p.actual_finish, p.physical_pct::float8 AS actual_pct_complete,
       (SELECT sum(r.cumulative_qty)::float8 FROM (SELECT DISTINCT ON (a.assignment_uid) a.cumulative_qty FROM approved_resource_progress a
                                                    WHERE a.activity_uid = p.activity_uid ORDER BY a.assignment_uid, a.entry_seq DESC) r) AS actual_quantity,
       h.event_id, h.decision_id, NULL::timestamptz AS exported_at, h.created_at, NULL::text AS stage_id,
       EXISTS (SELECT 1 FROM activity_reopens ar WHERE ar.project_id = p.project_id AND ar.activity_uid = p.activity_uid AND ar.status = 'APPROVED') AS is_reopened,
       NULL::text AS rework_notes
  FROM activity_progress_as_of(lrm.version(), current_date) p
  LEFT JOIN LATERAL (SELECT d.event_id, d.decision_id, x.created_at FROM approved_activity_progress x JOIN planner_decisions d ON d.decision_id = x.decision_id
                      WHERE x.activity_uid = p.activity_uid ORDER BY x.entry_seq DESC LIMIT 1) h ON true
 WHERE p.project_id = lrm.project() AND (p.actual_start IS NOT NULL OR p.physical_pct > 0);

DROP VIEW IF EXISTS lrm.conflict_records;
CREATE VIEW lrm.conflict_records AS
SELECT c.conflict_id, c.project_id, lrm.version()::text AS schedule_id, ba.external_activity_id AS activity_id, c.reporting_period, c.event_id_a, c.event_id_b,
       c.value_a::float8 AS value_a, c.value_b::float8 AS value_b, c.variance_pct::float8 AS variance_pct, c.status
  FROM conflict_records c
  LEFT JOIN baseline_activities ba ON ba.activity_uid = c.activity_uid AND ba.version_id = lrm.version()
 WHERE c.project_id = lrm.project();

DROP VIEW IF EXISTS lrm.source_references;
CREATE VIEW lrm.source_references AS
SELECT r.reference_id, r.project_id, r.event_id, r.document_id, d.file_name, r.sheet_name, r.row_cell_ref, r.message_id, r.raw_snippet
  FROM source_references r LEFT JOIN source_documents d ON d.document_id = r.document_id WHERE r.project_id = lrm.project();

DROP VIEW IF EXISTS lrm.claim_activity_splits;
CREATE VIEW lrm.claim_activity_splits AS
SELECT s.split_id, s.project_id, s.event_id, e.filed_in_version_id::text AS schedule_id, ba.external_activity_id AS activity_id, s.split_basis, s.split_pct::float8 AS split_pct, s.wbs_code,
       s.planned_quantity::float8 AS planned_quantity, s.allocated_quantity::float8 AS allocated_quantity, s.uom, s.rationale, s.created_at
  FROM claim_activity_splits s
  JOIN execution_events e ON e.event_id = s.event_id
  JOIN baseline_activities ba ON ba.version_id = e.filed_in_version_id AND ba.activity_uid = s.activity_uid
 WHERE s.project_id = lrm.project();

REVOKE ALL ON ALL TABLES IN SCHEMA lrm FROM PUBLIC;
