-- 0016 Legacy read model: READ-ONLY views, in the vocabulary of the original SetuAI demo, over the v2 tables.
--
-- Why: the original review/validation/intelligence code (conflict detection, sequence checks, evidence fusion, forecasts, impact ...) is written against the demo's table
-- names. Instead of rewriting that logic, the v2 server lets it read these views. They translate vocabulary only (external activity id <-> activity_uid, version id <-> schedule id,
-- v2 claim status <-> demo status, numeric -> double precision); they hold no data and no rule of their own, so v2 stays the single source of truth.
--
-- Safety: every view is scoped to ONE project through the transaction-local setting app.lrm_project (set by the backend after it has authorised the caller); with the setting
-- absent a view returns no rows. Views that depend on a schedule version also read app.lrm_version. Views are not updatable and carry no INSTEAD OF triggers, so the original
-- code cannot write through them: every write still goes through the v2 domain services and the database guards.
-- Purely additive (a new schema, no change to any existing object).

CREATE SCHEMA IF NOT EXISTS lrm;

CREATE FUNCTION lrm.project() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.lrm_project', true), '')::uuid $$;
CREATE FUNCTION lrm.version() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.lrm_version', true), '')::uuid $$;

-- ---------------------------------------------------------------- schedule_activities (one schedule version)
CREATE VIEW lrm.schedule_activities AS
SELECT ba.external_activity_id AS activity_id, ba.version_id::text AS schedule_id, ba.project_id, ba.activity_uid,
       ba.activity_name, sw.wbs_code, ba.discipline_code AS discipline, coalesce(ba.location, '') AS location, ba.asset_tag,
       ba.baseline_start AS planned_start, ba.baseline_finish AS planned_finish,
       CASE WHEN q.n = 1 THEN q.qty END::float8 AS planned_quantity, CASE WHEN q.n = 1 THEN q.uom END AS uom,
       0.0::float8 AS baseline_pct_complete, ba.total_float::float8 AS total_float, ba.is_critical, ba.description,
       st.wbs_uid::text AS stage_id, st.wbs_name AS stage_name, 1.0::float8 AS weight_factor, false AS quality_gate_required,
       NULL::text AS contractor_id, NULL::text AS work_package_id, ba.sequence
  FROM baseline_activities ba
  JOIN schedule_wbs sw ON sw.wbs_id = ba.wbs_id
  LEFT JOIN LATERAL (SELECT st.wbs_uid, st.wbs_name FROM schedule_wbs st
                      WHERE st.version_id = ba.version_id AND st.node_type = 'STAGE' AND sw.wbs_path LIKE st.wbs_path || '%'
                      ORDER BY length(st.wbs_path) DESC LIMIT 1) st ON true
  LEFT JOIN LATERAL (SELECT count(*) AS n, min(br.baseline_qty) AS qty, min(br.unit_of_measure) AS uom FROM baseline_resources br
                      WHERE br.version_id = ba.version_id AND br.activity_uid = ba.activity_uid AND br.measures_progress) q ON true
 WHERE ba.project_id = lrm.project() AND ba.version_id = lrm.version();

CREATE VIEW lrm.schedule_dependencies AS
SELECT d.dependency_id, d.version_id::text AS schedule_id, d.project_id, p.external_activity_id AS predecessor_activity_id, s.external_activity_id AS successor_activity_id,
       d.relationship_type, d.lag_days::float8 AS lag_days
  FROM schedule_dependencies d
  JOIN baseline_activities p ON p.version_id = d.version_id AND p.activity_uid = d.predecessor_uid
  JOIN baseline_activities s ON s.version_id = d.version_id AND s.activity_uid = d.successor_uid
 WHERE d.project_id = lrm.project() AND d.version_id = lrm.version();

-- ---------------------------------------------------------------- approved_actuals (approved progress of the version, from the append-only ledgers)
CREATE VIEW lrm.approved_actuals AS
SELECT p.version_id::text AS schedule_id, p.project_id, p.external_activity_id AS activity_id, p.activity_uid,
       p.actual_start, p.actual_finish, p.physical_pct::float8 AS actual_pct_complete,
       (SELECT sum(r.cumulative_qty)::float8 FROM (SELECT DISTINCT ON (a.assignment_uid) a.cumulative_qty FROM approved_resource_progress a
                                                    WHERE a.activity_uid = p.activity_uid ORDER BY a.assignment_uid, a.entry_seq DESC) r) AS actual_quantity,
       (SELECT d.event_id FROM approved_activity_progress h JOIN planner_decisions d ON d.decision_id = h.decision_id
         WHERE h.activity_uid = p.activity_uid ORDER BY h.entry_seq DESC LIMIT 1) AS event_id,
       false AS is_reopened
  FROM activity_progress_as_of(lrm.version(), current_date) p
 WHERE p.project_id = lrm.project() AND (p.actual_start IS NOT NULL OR p.physical_pct > 0);

-- ---------------------------------------------------------------- execution_events (claims, demo vocabulary)
CREATE VIEW lrm.execution_events AS
SELECT e.event_id, e.project_id, e.document_id, e.filed_in_version_id::text AS schedule_id, e.event_date, e.raw_claim_text,
       CASE e.input_channel WHEN 'TYPED' THEN 'TYPED_TEXT' WHEN 'VOICE_TRANSCRIPT' THEN 'VOICE' WHEN 'SCANNED' THEN 'SCANNED_OCR' WHEN 'IMAGE' THEN 'SCANNED_OCR'
            WHEN 'API' THEN 'SCHEDULE_EXPORT' ELSE 'FILE_UPLOAD' END AS input_channel,
       e.language_detected, e.reported_activity_ref AS reported_activity_id, ba.external_activity_id AS matched_activity_id, e.matched_activity_uid,
       e.discipline_code AS discipline, NULL::text AS action,
       CASE e.event_type WHEN 'PROGRESS' THEN 'PROGRESS_UPDATE' WHEN 'START' THEN 'ACTUAL_START' WHEN 'FINISH' THEN 'ACTUAL_FINISH' ELSE 'DELAY' END AS event_type,
       CASE e.claim_mode WHEN 'CUMULATIVE_PCT' THEN 'CUMULATIVE_PCT' ELSE 'INCREMENTAL_QUANTITY' END AS claim_mode,
       e.asset_tag, e.location, q.qty::float8 AS claimed_quantity, q.uom AS claimed_uom, e.claimed_pct::float8 AS claimed_pct, e.delay_reason,
       NULL::text AS supervisor_id, NULL::text AS photo_path,
       CASE e.status
         WHEN 'REPORTED' THEN 'EXTRACTED'
         WHEN 'EXTRACTED' THEN CASE WHEN he.x THEN 'REVIEW_REQUIRED' WHEN e.matched_activity_uid IS NULL AND (nm.x OR hc.x) THEN 'UNMATCHED' ELSE 'EXTRACTED' END
         WHEN 'MATCHED' THEN CASE WHEN he.x THEN 'REVIEW_REQUIRED' ELSE 'MATCHED' END
         WHEN 'VALIDATED' THEN CASE WHEN he.x THEN 'REVIEW_REQUIRED' ELSE 'VALIDATED' END
         WHEN 'APPROVED' THEN CASE WHEN la.action = 'EDIT' THEN 'EDITED' ELSE 'APPROVED' END
         WHEN 'DISPUTED' THEN 'HOLD'
         ELSE e.status END AS status,
       e.created_at, CASE WHEN e.clarification_status = 'ASKED' AND e.status = 'REPORTED' THEN 'PENDING' WHEN e.clarification_status = 'ASKED' THEN 'NONE' ELSE e.clarification_status END AS clarification_status,
       e.clarification_question, e.clarification_answer, e.priority_score::float8 AS priority_score, e.priority_reasons, e.field_provenance,
       NULL::text AS stage_id, NULL::text AS contractor_id, NULL::text AS work_package_id, NULL::text AS reopened_from_actual_id, NULL::text AS reopen_status, e.filed_by
  FROM execution_events e
  LEFT JOIN baseline_activities ba ON ba.version_id = e.filed_in_version_id AND ba.activity_uid = e.matched_activity_uid
  LEFT JOIN LATERAL (SELECT cq.reported_qty AS qty, cq.reported_uom AS uom FROM claim_quantities cq WHERE cq.event_id = e.event_id
                      AND (SELECT count(*) FROM claim_quantities c2 WHERE c2.event_id = e.event_id) = 1) q ON true
  LEFT JOIN LATERAL (SELECT d.action FROM planner_decisions d WHERE d.event_id = e.event_id ORDER BY d.decided_at DESC LIMIT 1) la ON true
  LEFT JOIN LATERAL (SELECT EXISTS (SELECT 1 FROM claim_validations v WHERE v.event_id = e.event_id AND v.rule_code = 'NO_AUTOMATIC_MATCH') AS x) nm ON true
  LEFT JOIN LATERAL (SELECT EXISTS (SELECT 1 FROM candidate_matches cm WHERE cm.event_id = e.event_id) AS x) hc ON true
  LEFT JOIN LATERAL (SELECT EXISTS (SELECT 1 FROM claim_validations v WHERE v.event_id = e.event_id AND v.rule_code = 'REVIEW_REQUIRED') AS x) he ON true
 WHERE e.project_id = lrm.project();

CREATE VIEW lrm.planner_decisions AS
SELECT d.decision_id, d.event_id, d.project_id, ba.external_activity_id AS selected_activity_id, d.action, d.approved_pct::float8 AS approved_pct,
       (SELECT sum(a.incremental_qty)::float8 FROM approved_resource_progress a WHERE a.decision_id = d.decision_id) AS approved_qty,
       d.decided_by AS planner_id, d.justification, d.decided_at
  FROM planner_decisions d
  JOIN execution_events e ON e.event_id = d.event_id
  LEFT JOIN baseline_activities ba ON ba.version_id = e.filed_in_version_id AND ba.activity_uid = d.selected_activity_uid
 WHERE d.project_id = lrm.project();

CREATE VIEW lrm.candidate_matches AS
SELECT cm.candidate_id, cm.event_id, cm.project_id, e.filed_in_version_id::text AS schedule_id, ba.external_activity_id AS activity_id, cm.rank_order, cm.match_tier,
       cm.composite_confidence::float8 AS composite_confidence, cm.semantic_score::float8 AS semantic_score, cm.fuzzy_score::float8 AS fuzzy_score,
       cm.location_score::float8 AS location_score, cm.discipline_score::float8 AS discipline_score, cm.supporting_signals, cm.disqualifying_signals
  FROM candidate_matches cm
  JOIN execution_events e ON e.event_id = cm.event_id
  JOIN baseline_activities ba ON ba.version_id = e.filed_in_version_id AND ba.activity_uid = cm.activity_uid
 WHERE cm.project_id = lrm.project();

CREATE VIEW lrm.conflict_records AS
SELECT c.conflict_id, c.project_id, ba.external_activity_id AS activity_id, c.reporting_period, c.event_id_a, c.event_id_b, c.value_a::float8 AS value_a, c.value_b::float8 AS value_b,
       c.variance_pct::float8 AS variance_pct, c.status
  FROM conflict_records c
  LEFT JOIN baseline_activities ba ON ba.activity_uid = c.activity_uid AND ba.version_id = lrm.version()
 WHERE c.project_id = lrm.project();

CREATE VIEW lrm.validation_issues AS
SELECT v.validation_id AS issue_id, v.event_id, v.project_id, v.rule_code, v.severity, v.description FROM claim_validations v WHERE v.project_id = lrm.project();

CREATE VIEW lrm.evidence_links AS
SELECT l.link_id, l.project_id, l.event_id_a, l.event_id_b, l.relation_type, l.confidence::float8 AS confidence, l.rationale, l.created_at
  FROM evidence_links l WHERE l.project_id = lrm.project();

CREATE VIEW lrm.source_references AS
SELECT r.reference_id, r.project_id, r.event_id, d.file_name, r.sheet_name, r.row_cell_ref, r.message_id, r.raw_snippet
  FROM source_references r LEFT JOIN source_documents d ON d.document_id = r.document_id WHERE r.project_id = lrm.project();

CREATE VIEW lrm.source_documents AS
SELECT d.document_id, d.project_id, d.file_name, d.mime_type, d.sha256 AS file_hash, d.uploaded_by AS uploader_id, d.uploaded_at, d.captured_at, d.gps_lat, d.gps_lon,
       CASE d.kind WHEN 'PHOTO' THEN 'SCANNED_DIARY' WHEN 'EVIDENCE' THEN 'QC_INSPECTION' WHEN 'DAILY_REPORT' THEN 'DPR' WHEN 'SITE_REPORT' THEN 'DPR' ELSE d.kind END AS document_type
  FROM source_documents d WHERE d.project_id = lrm.project();

CREATE VIEW lrm.claim_activity_splits AS
SELECT s.split_id, s.project_id, s.event_id, ba.external_activity_id AS activity_id, s.split_basis, s.split_pct::float8 AS split_pct, s.wbs_code,
       s.planned_quantity::float8 AS planned_quantity, s.allocated_quantity::float8 AS allocated_quantity, s.uom, s.rationale, s.created_at
  FROM claim_activity_splits s
  JOIN execution_events e ON e.event_id = s.event_id
  JOIN baseline_activities ba ON ba.version_id = e.filed_in_version_id AND ba.activity_uid = s.activity_uid
 WHERE s.project_id = lrm.project();

-- The views are for the API's own database role only. They are deliberately NOT granted to the client-facing roles (anon / authenticated): a view filtered by a session
-- setting would be bypassable by anyone who could set that setting, whereas the API sets it only after authorising the caller for exactly one project.
REVOKE ALL ON SCHEMA lrm FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA lrm FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA lrm FROM PUBLIC;
