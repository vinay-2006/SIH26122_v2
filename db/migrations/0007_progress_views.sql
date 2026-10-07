-- 0007 derived views. EVERYTHING here is derived from baseline tables + the approved-progress ledgers (the single source of truth).

-- Canonical PROJECT_MASTER shape: project identity + its locked baseline boundaries.
CREATE VIEW project_master AS
SELECT p.project_id, p.project_code, p.project_name, p.lifecycle_status, p.record_status, p.location,
       v.baseline_name, v.data_date, v.planned_start_date, v.planned_finish_date, v.locked_at AS baseline_locked_at
  FROM projects p
  LEFT JOIN schedule_versions v ON v.project_id = p.project_id AND v.kind = 'BASELINE';

CREATE VIEW v_current_activity_progress AS
SELECT DISTINCT ON (activity_uid) project_id, activity_uid, entry_id, entry_seq, as_of_date,
       actual_start, actual_finish, reported_pct, decision_id
  FROM approved_activity_progress ORDER BY activity_uid, entry_seq DESC;

CREATE VIEW v_current_resource_progress AS
SELECT DISTINCT ON (assignment_uid) project_id, activity_uid, assignment_uid, entry_id, entry_seq, as_of_date,
       cumulative_qty, overrun_pct, over_baseline, decision_id
  FROM approved_resource_progress ORDER BY assignment_uid, entry_seq DESC;

-- Weighting basis is chosen ONCE per version so activity weights share one scale (never mixing manhours with days):
--   MANHOURS if every weighted activity has baseline manhours, else DURATION if all have a duration, else UNIT (weight 1).
CREATE VIEW v_activity_manhours AS
SELECT br.version_id, br.activity_uid, sum(br.baseline_qty) AS manhours
  FROM baseline_resources br JOIN units_of_measure u ON u.code = br.unit_of_measure AND u.dimension = 'EFFORT'
 GROUP BY br.version_id, br.activity_uid;

CREATE VIEW v_version_weight_basis AS
SELECT v.version_id,
       CASE WHEN count(*) FILTER (WHERE ba.activity_type <> 'MILESTONE') > 0
                 AND count(*) FILTER (WHERE ba.activity_type <> 'MILESTONE' AND m.manhours IS NULL) = 0 THEN 'MANHOURS'
            WHEN count(*) FILTER (WHERE ba.activity_type <> 'MILESTONE') > 0
                 AND count(*) FILTER (WHERE ba.activity_type <> 'MILESTONE' AND ba.baseline_duration <= 0) = 0 THEN 'DURATION'
            ELSE 'UNIT' END AS weight_basis
  FROM schedule_versions v
  LEFT JOIN baseline_activities ba ON ba.version_id = v.version_id
  LEFT JOIN v_activity_manhours m ON m.version_id = ba.version_id AND m.activity_uid = ba.activity_uid
 GROUP BY v.version_id;

-- Physical progress per activity of the ACTIVE version.
--   quantity-based: weighted mean of per-assignment min(100, cumulative/baseline*100) over measures_progress assignments
--   no measuring assignment: approved reported_pct; milestone: 100 once finished else 0.
-- Only APPROVED ledger rows are ever read.
CREATE VIEW v_activity_progress AS
WITH act AS (
  SELECT ba.*, vb.weight_basis, m.manhours
    FROM baseline_activities ba
    JOIN schedule_versions v ON v.version_id = ba.version_id AND v.status = 'ACTIVE'
    JOIN v_version_weight_basis vb ON vb.version_id = ba.version_id
    LEFT JOIN v_activity_manhours m ON m.version_id = ba.version_id AND m.activity_uid = ba.activity_uid
), qty AS (
  SELECT br.version_id, br.activity_uid,
         sum(br.progress_weight) AS w,
         sum(br.progress_weight * least(100, coalesce(cr.cumulative_qty, 0) / br.baseline_qty * 100)) AS wp,
         count(*) AS measured_assignments,
         bool_or(cr.over_baseline) AS any_overrun,
         max(cr.overrun_pct) AS max_overrun_pct
    FROM baseline_resources br
    LEFT JOIN v_current_resource_progress cr ON cr.assignment_uid = br.assignment_uid
   WHERE br.measures_progress
   GROUP BY br.version_id, br.activity_uid
), calc AS (
  SELECT a.project_id, a.version_id, a.activity_uid, a.activity_row_id, a.external_activity_id, a.activity_name,
         a.wbs_id, a.discipline_code, a.activity_type, a.location,
         a.baseline_start, a.baseline_finish, a.baseline_duration, a.total_float,
         ca.actual_start, ca.actual_finish,
         CASE WHEN q.w IS NOT NULL THEN round((q.wp / q.w)::numeric, 3)
              WHEN a.activity_type = 'MILESTONE' THEN CASE WHEN ca.actual_finish IS NOT NULL THEN 100 ELSE 0 END
              ELSE coalesce(ca.reported_pct, 0) END AS physical_pct,
         CASE WHEN q.w IS NOT NULL THEN 'QUANTITY' WHEN a.activity_type = 'MILESTONE' THEN 'MILESTONE' ELSE 'APPROVED_PCT' END AS progress_basis,
         coalesce(q.measured_assignments, 0) AS measured_assignments,
         coalesce(q.any_overrun, FALSE) AS any_overrun,
         q.max_overrun_pct,
         a.weight_basis,
         CASE a.weight_basis WHEN 'MANHOURS' THEN coalesce(a.manhours, 0)
                             WHEN 'DURATION' THEN a.baseline_duration ELSE 1 END AS weight
    FROM act a
    LEFT JOIN qty q ON q.version_id = a.version_id AND q.activity_uid = a.activity_uid
    LEFT JOIN v_current_activity_progress ca ON ca.project_id = a.project_id AND ca.activity_uid = a.activity_uid
)
SELECT c.*,
       CASE WHEN c.actual_finish IS NOT NULL THEN 'COMPLETED'
            WHEN c.actual_start IS NOT NULL OR c.physical_pct > 0 THEN 'IN_PROGRESS' ELSE 'NOT_STARTED' END AS execution_state,
       (c.physical_pct > 0 AND c.actual_start IS NULL) AS start_date_unrecorded,   -- data-quality flag: progress approved, no actual start
       (c.actual_start  - c.baseline_start)  AS start_variance_days,
       (c.actual_finish - c.baseline_finish) AS finish_variance_days
  FROM calc c;

-- Rollups: weighted mean of descendant activity progress. WBS: every node over its subtree (stage = STAGE node).
CREATE VIEW v_wbs_progress AS
SELECT n.project_id, n.version_id, n.wbs_id, n.wbs_code, n.wbs_name, n.node_type, n.level,
       count(ap.activity_uid) AS activities,
       CASE WHEN coalesce(sum(ap.weight), 0) > 0 THEN round((sum(ap.weight * ap.physical_pct) / sum(ap.weight))::numeric, 3) ELSE 0 END AS physical_pct,
       min(ap.weight_basis) AS weight_basis
  FROM schedule_wbs n
  JOIN v_activity_progress ap ON ap.version_id = n.version_id
  JOIN schedule_wbs an ON an.wbs_id = ap.wbs_id AND an.wbs_path LIKE n.wbs_path || '%'
 GROUP BY n.project_id, n.version_id, n.wbs_id, n.wbs_code, n.wbs_name, n.node_type, n.level;

CREATE VIEW v_discipline_progress AS
SELECT project_id, version_id, discipline_code, count(*) AS activities,
       CASE WHEN coalesce(sum(weight), 0) > 0 THEN round((sum(weight * physical_pct) / sum(weight))::numeric, 3) ELSE 0 END AS physical_pct,
       min(weight_basis) AS weight_basis
  FROM v_activity_progress GROUP BY project_id, version_id, discipline_code;

CREATE VIEW v_project_progress AS
SELECT project_id, version_id, count(*) AS activities,
       count(*) FILTER (WHERE execution_state = 'COMPLETED') AS completed,
       count(*) FILTER (WHERE execution_state = 'IN_PROGRESS') AS in_progress,
       count(*) FILTER (WHERE execution_state = 'NOT_STARTED') AS not_started,
       CASE WHEN coalesce(sum(weight), 0) > 0 THEN round((sum(weight * physical_pct) / sum(weight))::numeric, 3) ELSE 0 END AS physical_pct,
       min(weight_basis) AS weight_basis,
       bool_or(any_overrun) AS any_overrun
  FROM v_activity_progress GROUP BY project_id, version_id;

-- Pending (NOT authoritative) claims, shown separately and never added to progress.
CREATE VIEW v_pending_claims AS
SELECT project_id, matched_activity_uid AS activity_uid, status, count(*) AS claims, max(claimed_pct) AS max_claimed_pct
  FROM execution_events WHERE status IN ('REPORTED','EXTRACTED','MATCHED','VALIDATED','DISPUTED') GROUP BY 1, 2, 3;

-- Baseline vs revised comparison by stable identity (planned scope change, separate from execution).
CREATE FUNCTION version_activity_diff(p_old UUID, p_new UUID)
RETURNS TABLE (activity_uid UUID, old_external_id TEXT, new_external_id TEXT, change_kind TEXT,
               start_shift_days INTEGER, finish_shift_days INTEGER)
LANGUAGE sql STABLE AS $$
  SELECT coalesce(n.activity_uid, o.activity_uid), o.external_activity_id, n.external_activity_id,
         CASE WHEN o.activity_uid IS NULL THEN 'ADDED'
              WHEN n.activity_uid IS NULL THEN 'REMOVED'
              WHEN o.baseline_start <> n.baseline_start OR o.baseline_finish <> n.baseline_finish
                   OR o.activity_name <> n.activity_name OR o.external_activity_id <> n.external_activity_id
                   OR o.baseline_duration <> n.baseline_duration OR o.discipline_code <> n.discipline_code THEN 'CHANGED'
              ELSE 'UNCHANGED' END,
         n.baseline_start - o.baseline_start, n.baseline_finish - o.baseline_finish
    FROM (SELECT * FROM baseline_activities WHERE version_id = p_old) o
    FULL JOIN (SELECT * FROM baseline_activities WHERE version_id = p_new) n ON n.activity_uid = o.activity_uid
$$;

CREATE FUNCTION version_assignment_diff(p_old UUID, p_new UUID)
RETURNS TABLE (assignment_uid UUID, activity_uid UUID, old_qty NUMERIC, new_qty NUMERIC, change_kind TEXT)
LANGUAGE sql STABLE AS $$
  SELECT coalesce(n.assignment_uid, o.assignment_uid), coalesce(n.activity_uid, o.activity_uid), o.baseline_qty, n.baseline_qty,
         CASE WHEN o.assignment_uid IS NULL THEN 'ADDED' WHEN n.assignment_uid IS NULL THEN 'REMOVED'
              WHEN o.baseline_qty <> n.baseline_qty OR o.unit_of_measure <> n.unit_of_measure THEN 'CHANGED' ELSE 'UNCHANGED' END
    FROM (SELECT * FROM baseline_resources WHERE version_id = p_old) o
    FULL JOIN (SELECT * FROM baseline_resources WHERE version_id = p_new) n ON n.assignment_uid = o.assignment_uid
$$;
