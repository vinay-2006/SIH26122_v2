-- 0013 execution workflow (Phase 3A). ADDITIVE: new columns / constraints / functions, plus CREATE OR REPLACE of three guard functions whose
-- new behaviour only ADDS refusals. Compatible with PostgreSQL 15, 16 (and 17: nothing used is newer than 15).
-- Existing data: the v2 schema holds no production data yet, so where a column must be NOT NULL on a ledger/decision table the migration
-- REFUSES to run against rows it cannot back-fill honestly (append-only history is never rewritten) instead of guessing values.
--
--  1. decisions record HOW progress was derived (method), the quantities applied and the result; one FINAL decision per claim
--  2. claims: withdrawal (by the filer only, never after a final decision), linked correction after rejection, freeze after a final state,
--     role guard on every change (PM can never modify a claim)
--  3. claim_quantities: reported values immutable; binding to an assignment of the claim's own activity with a compatible unit
--  4. both ledgers: a linear chain per assignment/activity (prev_entry_id, unique) + per-key advisory lock => concurrent decisions
--     can never fork the chain or double-count; the claim must be APPROVED; approved rows point at the reported quantity they came from
--  5. atomicity: a deferred constraint trigger refuses to COMMIT a decision without its notification + audit record (and, for approvals,
--     its ledger rows)
--  6. evidence/document metadata, issue delay fields, notification types
--  7. as-of rollup functions (actual, planned, SPI basis) = the single timeline implementation

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM planner_decisions) OR EXISTS (SELECT 1 FROM approved_resource_progress) OR EXISTS (SELECT 1 FROM approved_activity_progress) THEN
    RAISE EXCEPTION '0013 needs empty decision / ledger tables: append-only history cannot be back-filled with a decision method or chain links';
  END IF;
END $$;

-- ------------------------------------------------------------------------------------------------ 1. decisions
ALTER TABLE planner_decisions ADD COLUMN method  TEXT  NOT NULL;
ALTER TABLE planner_decisions ADD COLUMN applied JSONB NOT NULL DEFAULT '[]';     -- per-assignment quantities that were applied
ALTER TABLE planner_decisions ADD COLUMN result  JSONB NOT NULL DEFAULT '{}';     -- resulting progress (activity %, flags, inferred start ...)
ALTER TABLE planner_decisions ADD CONSTRAINT planner_decisions_method_check
  CHECK (method IN ('QUANTITIES_AS_CLAIMED','MANUAL_QUANTITIES','APPLY_PCT_TO_ASSIGNMENTS','PCT_ONLY_ACTIVITY','NONE'));
ALTER TABLE planner_decisions ADD CONSTRAINT planner_decisions_method_pairing
  CHECK ((action IN ('APPROVE','EDIT')) = (method <> 'NONE'));                   -- a percentage is never converted without a recorded method
CREATE UNIQUE INDEX uq_decision_final_per_claim ON planner_decisions (event_id) WHERE action IN ('APPROVE','EDIT','REJECT');

-- ------------------------------------------------------------------------------------------------ 2. claims
ALTER TABLE execution_events ADD COLUMN resubmits_event_id UUID;
ALTER TABLE execution_events ADD COLUMN withdrawn_at       TIMESTAMPTZ;
ALTER TABLE execution_events ADD COLUMN withdrawn_reason   TEXT;
ALTER TABLE execution_events DROP CONSTRAINT execution_events_status_check;
ALTER TABLE execution_events ADD CONSTRAINT execution_events_status_check
  CHECK (status IN ('REPORTED','EXTRACTED','MATCHED','VALIDATED','APPROVED','REJECTED','DISPUTED','WITHDRAWN'));
ALTER TABLE execution_events ADD CONSTRAINT chk_event_withdrawn
  CHECK ((status = 'WITHDRAWN') = (withdrawn_at IS NOT NULL) AND (withdrawn_at IS NULL OR length(btrim(coalesce(withdrawn_reason, ''))) >= 3));
ALTER TABLE execution_events ADD CONSTRAINT fk_event_resubmits FOREIGN KEY (project_id, resubmits_event_id)
  REFERENCES execution_events (project_id, event_id);
CREATE UNIQUE INDEX uq_event_one_correction ON execution_events (resubmits_event_id)
  WHERE resubmits_event_id IS NOT NULL AND status <> 'WITHDRAWN';                -- a rejection has at most one live correction
CREATE INDEX idx_events_queue ON execution_events (project_id, priority_score DESC, created_at)
  WHERE status IN ('EXTRACTED','MATCHED','VALIDATED','DISPUTED');

CREATE OR REPLACE FUNCTION trg_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_vstatus text; v_ref execution_events%ROWTYPE; v_actor UUID := app_actor(); v_role text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF project_role_of(NEW.project_id, NEW.filed_by) IS DISTINCT FROM 'SITE_ENGINEER' THEN
      RAISE EXCEPTION 'claims can only be filed by a SITE_ENGINEER of the project (project managers and supervisors cannot)'
        USING ERRCODE = '42501';
    END IF;
    SELECT status INTO v_vstatus FROM schedule_versions WHERE version_id = NEW.filed_in_version_id;
    IF v_vstatus NOT IN ('ACTIVE','SUPERSEDED') THEN
      RAISE EXCEPTION 'claims can only be filed against an activated schedule version (version is %)', v_vstatus USING ERRCODE = '23514';
    END IF;
    IF NEW.status IN ('WITHDRAWN') THEN
      RAISE EXCEPTION 'a claim cannot be created withdrawn' USING ERRCODE = '23514';
    END IF;
    IF NEW.resubmits_event_id IS NOT NULL THEN
      SELECT * INTO v_ref FROM execution_events WHERE project_id = NEW.project_id AND event_id = NEW.resubmits_event_id;
      IF v_ref.status IS DISTINCT FROM 'REJECTED' OR v_ref.filed_by IS DISTINCT FROM NEW.filed_by THEN
        RAISE EXCEPTION 'a correction must be a new claim linked to the engineer''s own REJECTED claim' USING ERRCODE = '23514';
      END IF;
    END IF;
    RETURN NEW;
  END IF;
  -- ---- UPDATE
  IF NEW.filed_in_version_id <> OLD.filed_in_version_id OR NEW.filed_by <> OLD.filed_by
     OR NEW.project_id <> OLD.project_id OR NEW.raw_claim_text <> OLD.raw_claim_text OR NEW.event_date <> OLD.event_date
     OR NEW.resubmits_event_id IS DISTINCT FROM OLD.resubmits_event_id THEN
    RAISE EXCEPTION 'claim provenance (version, filer, text, report date, link to a rejected claim) is immutable' USING ERRCODE = '23514';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
       (OLD.status = 'REPORTED'  AND NEW.status IN ('EXTRACTED','REJECTED','WITHDRAWN')) OR
       (OLD.status = 'EXTRACTED' AND NEW.status IN ('MATCHED','REJECTED','DISPUTED','WITHDRAWN')) OR
       (OLD.status = 'MATCHED'   AND NEW.status IN ('EXTRACTED','VALIDATED','APPROVED','REJECTED','DISPUTED','WITHDRAWN')) OR
       (OLD.status = 'VALIDATED' AND NEW.status IN ('APPROVED','REJECTED','DISPUTED','WITHDRAWN')) OR
       (OLD.status = 'DISPUTED'  AND NEW.status IN ('APPROVED','REJECTED','WITHDRAWN'))) THEN
    RAISE EXCEPTION 'illegal claim status transition % -> %', OLD.status, NEW.status USING ERRCODE = '23514';
  END IF;
  IF OLD.status IN ('APPROVED','REJECTED','WITHDRAWN') AND
     (to_jsonb(NEW) - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - 'updated_at') THEN
    RAISE EXCEPTION 'claim is % and frozen: a final claim is never edited', OLD.status USING ERRCODE = '23514';
  END IF;
  IF NOT app_is_system() THEN
    v_role := project_role_of(NEW.project_id, v_actor);
    IF v_role IS NULL OR NOT (v_role = 'SUPERVISOR' OR (v_role = 'SITE_ENGINEER' AND v_actor = NEW.filed_by)) THEN
      RAISE EXCEPTION 'only the filing site engineer or a supervisor of the project may change a claim (project managers never)' USING ERRCODE = '42501';
    END IF;
    IF NEW.status = 'WITHDRAWN' AND OLD.status <> 'WITHDRAWN' AND v_actor IS DISTINCT FROM NEW.filed_by THEN
      RAISE EXCEPTION 'only the engineer who filed a claim can withdraw it' USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END $$;

-- ------------------------------------------------------------------------------------------------ 3. claim_quantities / evidence
ALTER TABLE claim_quantities ADD CONSTRAINT uq_cq_project UNIQUE (project_id, claim_quantity_id);

CREATE FUNCTION trg_claim_quantities_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_claim execution_events%ROWTYPE; v_actor UUID := app_actor(); v_role text; v_dim_assign text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'claim_quantities rows are never deleted (the reported figures are evidence)' USING ERRCODE = '23514';
  END IF;
  SELECT * INTO v_claim FROM execution_events WHERE project_id = NEW.project_id AND event_id = NEW.event_id;
  IF v_claim.status IN ('APPROVED','REJECTED','WITHDRAWN') THEN
    RAISE EXCEPTION 'claim is % : its quantities are frozen', v_claim.status USING ERRCODE = '23514';
  END IF;
  IF NOT app_is_system() THEN
    v_role := project_role_of(NEW.project_id, v_actor);
    IF v_role IS NULL OR NOT (v_role = 'SUPERVISOR' OR (v_role = 'SITE_ENGINEER' AND v_actor = v_claim.filed_by)) THEN
      RAISE EXCEPTION 'only the filing site engineer or a supervisor may change claim quantities' USING ERRCODE = '42501';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND (NEW.event_id <> OLD.event_id OR NEW.reported_qty <> OLD.reported_qty OR NEW.reported_uom <> OLD.reported_uom
                           OR NEW.qty_basis <> OLD.qty_basis OR NEW.reported_resource IS DISTINCT FROM OLD.reported_resource) THEN
    RAISE EXCEPTION 'reported quantities are immutable; only binding / normalisation may change' USING ERRCODE = '23514';
  END IF;
  IF NEW.assignment_uid IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM assignments a WHERE a.project_id = NEW.project_id AND a.assignment_uid = NEW.assignment_uid
                    AND (a.activity_uid = v_claim.matched_activity_uid
                         OR EXISTS (SELECT 1 FROM claim_activity_splits s WHERE s.event_id = NEW.event_id AND s.activity_uid = a.activity_uid))) THEN
      RAISE EXCEPTION 'the assignment does not belong to the claim''s matched activity' USING ERRCODE = '23514';
    END IF;
    IF NEW.normalized_uom IS NOT NULL THEN
      SELECT u.dimension INTO v_dim_assign FROM baseline_resources br JOIN units_of_measure u ON u.code = br.unit_of_measure
        JOIN schedule_versions v ON v.version_id = br.version_id
       WHERE br.assignment_uid = NEW.assignment_uid AND v.project_id = NEW.project_id ORDER BY v.version_no DESC LIMIT 1;
      IF v_dim_assign IS DISTINCT FROM (SELECT dimension FROM units_of_measure WHERE code = NEW.normalized_uom) THEN
        RAISE EXCEPTION 'unit % is not the same kind of unit as the assignment (%): units are never converted across kinds', NEW.normalized_uom, v_dim_assign
          USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_claim_quantities_guard BEFORE INSERT OR UPDATE OR DELETE ON claim_quantities FOR EACH ROW EXECUTE FUNCTION trg_claim_quantities_guard();

CREATE FUNCTION trg_claim_evidence_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_claim execution_events%ROWTYPE;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'claim evidence links are never changed or removed' USING ERRCODE = '23514';
  END IF;
  SELECT * INTO v_claim FROM execution_events WHERE project_id = NEW.project_id AND event_id = NEW.event_id;
  IF v_claim.status IN ('APPROVED','REJECTED','WITHDRAWN') THEN
    RAISE EXCEPTION 'claim is % : evidence can no longer be attached', v_claim.status USING ERRCODE = '23514';
  END IF;
  IF NOT app_is_system() AND app_actor() IS DISTINCT FROM v_claim.filed_by THEN
    RAISE EXCEPTION 'only the engineer who filed the claim can attach evidence to it' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_claim_evidence_guard BEFORE INSERT OR UPDATE OR DELETE ON claim_evidence FOR EACH ROW EXECUTE FUNCTION trg_claim_evidence_guard();

-- ------------------------------------------------------------------------------------------------ 4. ledgers
ALTER TABLE approved_resource_progress ADD COLUMN claim_quantity_id UUID;
ALTER TABLE approved_resource_progress ADD COLUMN prev_entry_id UUID REFERENCES approved_resource_progress(entry_id);
ALTER TABLE approved_resource_progress ADD CONSTRAINT fk_arp_claim_quantity FOREIGN KEY (project_id, claim_quantity_id)
  REFERENCES claim_quantities (project_id, claim_quantity_id);
CREATE UNIQUE INDEX uq_arp_chain_next ON approved_resource_progress (prev_entry_id) WHERE prev_entry_id IS NOT NULL;     -- no forks
CREATE UNIQUE INDEX uq_arp_chain_root ON approved_resource_progress (assignment_uid) WHERE prev_entry_id IS NULL;        -- one root
ALTER TABLE approved_activity_progress ADD COLUMN prev_entry_id UUID REFERENCES approved_activity_progress(entry_id);
CREATE UNIQUE INDEX uq_aap_chain_next ON approved_activity_progress (prev_entry_id) WHERE prev_entry_id IS NOT NULL;
CREATE UNIQUE INDEX uq_aap_chain_root ON approved_activity_progress (activity_uid) WHERE prev_entry_id IS NULL;

CREATE FUNCTION decision_claim_is_approved(p_project UUID, p_decision UUID) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT e.status = 'APPROVED' FROM planner_decisions d JOIN execution_events e ON e.project_id = d.project_id AND e.event_id = d.event_id
   WHERE d.project_id = p_project AND d.decision_id = p_decision $$;

CREATE OR REPLACE FUNCTION trg_aap_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_action text; v_decided timestamptz; v_prev approved_activity_progress%ROWTYPE; v_version UUID;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('aap:' || NEW.activity_uid::text, 0));       -- serialise writers of one activity's chain
  SELECT action, decided_at INTO v_action, v_decided FROM planner_decisions
   WHERE project_id = NEW.project_id AND decision_id = NEW.decision_id;
  IF v_action NOT IN ('APPROVE','EDIT') THEN
    RAISE EXCEPTION 'progress can only be recorded from an APPROVE/EDIT decision (decision is %)', v_action USING ERRCODE = '23514';
  END IF;
  IF NOT decision_claim_is_approved(NEW.project_id, NEW.decision_id) THEN
    RAISE EXCEPTION 'only an APPROVED claim can create progress: pending, rejected, withdrawn and disputed claims never do' USING ERRCODE = '23514';
  END IF;
  IF NEW.as_of_date > v_decided::date THEN
    RAISE EXCEPTION 'as_of_date % is after the decision date %', NEW.as_of_date, v_decided::date USING ERRCODE = '23514';
  END IF;
  v_version := active_version_of(NEW.project_id);
  IF v_version IS NULL OR NOT EXISTS (SELECT 1 FROM baseline_activities WHERE version_id = v_version AND activity_uid = NEW.activity_uid) THEN
    RAISE EXCEPTION 'activity is not part of the project''s active schedule version' USING ERRCODE = '23514';
  END IF;
  SELECT * INTO v_prev FROM approved_activity_progress WHERE activity_uid = NEW.activity_uid ORDER BY entry_seq DESC LIMIT 1;
  NEW.prev_entry_id := v_prev.entry_id;
  IF v_prev.entry_id IS NOT NULL THEN
    IF NEW.supersedes_entry_id IS NULL THEN
      IF NEW.as_of_date < v_prev.as_of_date THEN
        RAISE EXCEPTION 'stale progress: as_of_date % precedes the latest approved entry (%)', NEW.as_of_date, v_prev.as_of_date USING ERRCODE = '23514';
      END IF;
      NEW.actual_start  := coalesce(NEW.actual_start,  v_prev.actual_start);
      NEW.actual_finish := coalesce(NEW.actual_finish, v_prev.actual_finish);
      NEW.reported_pct  := coalesce(NEW.reported_pct,  v_prev.reported_pct);
      IF NEW.reported_pct < coalesce(v_prev.reported_pct, 0) THEN
        RAISE EXCEPTION 'approved percent cannot decrease without superseding (reopen) the previous entry' USING ERRCODE = '23514';
      END IF;
      IF v_prev.actual_start IS NOT NULL AND NEW.actual_start <> v_prev.actual_start THEN
        RAISE EXCEPTION 'actual_start already approved as %; change requires superseding the entry', v_prev.actual_start USING ERRCODE = '23514';
      END IF;
      IF v_prev.actual_finish IS NOT NULL AND NEW.actual_finish <> v_prev.actual_finish THEN
        RAISE EXCEPTION 'actual_finish already approved as %; change requires superseding the entry', v_prev.actual_finish USING ERRCODE = '23514';
      END IF;
    ELSIF NEW.supersedes_entry_id <> v_prev.entry_id THEN
      RAISE EXCEPTION 'a reopen must supersede the latest approved entry' USING ERRCODE = '23514';
    ELSE
      NEW.actual_start := coalesce(NEW.actual_start, v_prev.actual_start);
    END IF;
  ELSIF NEW.supersedes_entry_id IS NOT NULL THEN
    RAISE EXCEPTION 'nothing to supersede' USING ERRCODE = '23514';
  END IF;
  IF NEW.actual_finish IS NOT NULL AND NEW.actual_start IS NULL THEN
    RAISE EXCEPTION 'actual_finish requires an actual_start' USING ERRCODE = '23514';
  END IF;
  IF NEW.actual_finish > current_date OR NEW.actual_start > current_date THEN
    RAISE EXCEPTION 'actual dates cannot be in the future' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION trg_arp_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_action text; v_decided timestamptz; v_decider UUID; v_ack boolean; v_version UUID; v_event UUID;
  v_prev approved_resource_progress%ROWTYPE; v_base numeric; v_tol numeric;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('arp:' || NEW.assignment_uid::text, 0));     -- serialise writers of one assignment's chain
  SELECT action, decided_at, decided_by, overrun_ack, event_id INTO v_action, v_decided, v_decider, v_ack, v_event FROM planner_decisions
   WHERE project_id = NEW.project_id AND decision_id = NEW.decision_id;
  IF v_action NOT IN ('APPROVE','EDIT') THEN
    RAISE EXCEPTION 'quantities can only be approved by an APPROVE/EDIT decision (decision is %)', v_action USING ERRCODE = '23514';
  END IF;
  IF NOT decision_claim_is_approved(NEW.project_id, NEW.decision_id) THEN
    RAISE EXCEPTION 'only an APPROVED claim can create progress: pending, rejected, withdrawn and disputed claims never do' USING ERRCODE = '23514';
  END IF;
  IF NEW.claim_quantity_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM claim_quantities cq WHERE cq.project_id = NEW.project_id AND cq.claim_quantity_id = NEW.claim_quantity_id
          AND cq.event_id = v_event AND cq.assignment_uid = NEW.assignment_uid) THEN
    RAISE EXCEPTION 'claim_quantity_id must be a quantity of the decided claim, bound to this assignment' USING ERRCODE = '23514';
  END IF;
  IF NEW.as_of_date > v_decided::date THEN
    RAISE EXCEPTION 'as_of_date % is after the decision date %', NEW.as_of_date, v_decided::date USING ERRCODE = '23514';
  END IF;
  v_version := active_version_of(NEW.project_id);
  SELECT br.baseline_qty INTO v_base FROM baseline_resources br
   WHERE br.version_id = v_version AND br.assignment_uid = NEW.assignment_uid;
  IF v_base IS NULL THEN
    RAISE EXCEPTION 'assignment is not part of the project''s active schedule version' USING ERRCODE = '23514';
  END IF;
  IF NEW.baseline_qty_at_entry IS NOT NULL AND NEW.baseline_qty_at_entry <> v_base THEN
    RAISE EXCEPTION 'baseline_qty_at_entry must equal the active baseline quantity (%)', v_base USING ERRCODE = '23514';
  END IF;
  NEW.baseline_qty_at_entry := v_base;

  SELECT * INTO v_prev FROM approved_resource_progress WHERE assignment_uid = NEW.assignment_uid ORDER BY entry_seq DESC LIMIT 1;
  NEW.prev_entry_id := v_prev.entry_id;
  NEW.prev_cumulative_qty := coalesce(v_prev.cumulative_qty, 0);
  IF NEW.incremental_qty IS NOT NULL AND NEW.incremental_qty <> NEW.cumulative_qty - NEW.prev_cumulative_qty THEN
    RAISE EXCEPTION 'incremental_qty % does not reconcile with cumulative % minus previous % (double counting guard)',
      NEW.incremental_qty, NEW.cumulative_qty, NEW.prev_cumulative_qty USING ERRCODE = '23514';
  END IF;
  NEW.incremental_qty := NEW.cumulative_qty - NEW.prev_cumulative_qty;

  IF v_prev.entry_id IS NOT NULL THEN
    IF NEW.supersedes_entry_id IS NULL THEN
      IF NEW.cumulative_qty < v_prev.cumulative_qty THEN
        RAISE EXCEPTION 'cumulative quantity cannot decrease (% < %) without superseding the previous entry',
          NEW.cumulative_qty, v_prev.cumulative_qty USING ERRCODE = '23514';
      END IF;
      IF NEW.as_of_date < v_prev.as_of_date THEN
        RAISE EXCEPTION 'stale quantity: as_of_date % precedes the latest approved entry (%)', NEW.as_of_date, v_prev.as_of_date USING ERRCODE = '23514';
      END IF;
    ELSIF NEW.supersedes_entry_id <> v_prev.entry_id THEN
      RAISE EXCEPTION 'a reopen must supersede the latest approved entry' USING ERRCODE = '23514';
    END IF;
  ELSIF NEW.supersedes_entry_id IS NOT NULL THEN
    RAISE EXCEPTION 'nothing to supersede' USING ERRCODE = '23514';
  END IF;

  SELECT over_baseline_tolerance_pct INTO v_tol FROM project_settings WHERE project_id = NEW.project_id;
  IF NEW.cumulative_qty > v_base * (1 + coalesce(v_tol, 10) / 100.0) THEN
    IF NEW.overrun_ack_by IS DISTINCT FROM v_decider OR NOT v_ack
       OR length(btrim(coalesce(NEW.overrun_ack_note, ''))) < 3 THEN
      RAISE EXCEPTION 'cumulative % exceeds baseline % by more than the % %% tolerance: explicit Supervisor acknowledgement required',
        NEW.cumulative_qty, v_base, coalesce(v_tol, 10) USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;

-- ------------------------------------------------------------------------------------------------ 5. atomicity: complete or not at all
CREATE FUNCTION trg_decision_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_filer UUID;
BEGIN
  SELECT filed_by INTO v_filer FROM execution_events WHERE project_id = NEW.project_id AND event_id = NEW.event_id;
  IF NEW.action IN ('APPROVE','EDIT') AND NOT EXISTS (SELECT 1 FROM approved_resource_progress WHERE decision_id = NEW.decision_id)
     AND NOT EXISTS (SELECT 1 FROM approved_activity_progress WHERE decision_id = NEW.decision_id) THEN
    RAISE EXCEPTION 'decision % was committed without its progress entries: an approval and its ledger writes are one unit', NEW.decision_id
      USING ERRCODE = '23514';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM notifications WHERE project_id = NEW.project_id AND decision_id = NEW.decision_id AND recipient_id = v_filer) THEN
    RAISE EXCEPTION 'decision % was committed without notifying the engineer who filed the claim', NEW.decision_id USING ERRCODE = '23514';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM audit_logs WHERE entity_type = 'PLANNER_DECISION' AND entity_id = NEW.decision_id::text) THEN
    RAISE EXCEPTION 'decision % was committed without its audit record', NEW.decision_id USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trg_decision_complete AFTER INSERT ON planner_decisions
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION trg_decision_complete();

-- ------------------------------------------------------------------------------------------------ 6. metadata, delays, notifications
ALTER TABLE source_documents ADD COLUMN captured_at    TIMESTAMPTZ;
ALTER TABLE source_documents ADD COLUMN gps_lat        DOUBLE PRECISION CHECK (gps_lat BETWEEN -90 AND 90);
ALTER TABLE source_documents ADD COLUMN gps_lon        DOUBLE PRECISION CHECK (gps_lon BETWEEN -180 AND 180);
ALTER TABLE source_documents ADD COLUMN exif           JSONB;
ALTER TABLE source_documents ADD COLUMN page_count     INTEGER CHECK (page_count IS NULL OR page_count >= 0);
ALTER TABLE source_documents ADD COLUMN storage_backend TEXT NOT NULL DEFAULT 'LOCAL' CHECK (storage_backend IN ('LOCAL','SUPABASE'));
ALTER TABLE source_documents ADD CONSTRAINT chk_doc_gps_pair CHECK ((gps_lat IS NULL) = (gps_lon IS NULL));

ALTER TABLE issues ADD COLUMN delay_started_on      DATE;
ALTER TABLE issues ADD COLUMN delay_ended_on        DATE;
ALTER TABLE issues ADD COLUMN impact_days_estimated NUMERIC(8,2) CHECK (impact_days_estimated IS NULL OR impact_days_estimated >= 0);
ALTER TABLE issues ADD COLUMN impact_days_actual    NUMERIC(8,2) CHECK (impact_days_actual IS NULL OR impact_days_actual >= 0);
ALTER TABLE issues ADD CONSTRAINT chk_issue_delay_dates CHECK (delay_ended_on IS NULL OR delay_started_on IS NULL OR delay_ended_on >= delay_started_on);

ALTER TABLE notifications DROP CONSTRAINT notifications_notification_type_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_notification_type_check
  CHECK (notification_type IN ('CLAIM_DECISION','ISSUE_UPDATE','CLAIM_SUBMITTED','CLAIM_CLARIFICATION'));
ALTER TABLE notifications DROP CONSTRAINT notifications_subject_chk;
ALTER TABLE notifications ADD CONSTRAINT notifications_subject_chk CHECK (
    (notification_type = 'CLAIM_DECISION' AND decision_id IS NOT NULL AND event_id IS NOT NULL)
 OR (notification_type = 'ISSUE_UPDATE' AND issue_id IS NOT NULL)
 OR (notification_type IN ('CLAIM_SUBMITTED','CLAIM_CLARIFICATION') AND event_id IS NOT NULL));

-- ------------------------------------------------------------------------------------------------ 7. as-of rollups (actual vs planned)
-- ONE implementation of "progress at a date". Everything reads the approved ledgers only. v_activity_progress (0007) stays as the
-- "now" view; tests assert the two agree for the active version at 'infinity'.
-- PLANNED % IS AN APPROXIMATION: each activity is assumed to progress linearly between its baseline start and finish (a milestone
-- jumps at its finish), combined with the same weights as actual progress. It is NOT cost-based earned value and NOT a CPM time-phased plan.
CREATE FUNCTION version_activity_weights(p_version UUID) RETURNS TABLE (activity_uid UUID, weight NUMERIC, weight_basis TEXT)
LANGUAGE sql STABLE AS $$
  SELECT ba.activity_uid,
         CASE vb.weight_basis WHEN 'MANHOURS' THEN coalesce(m.manhours, 0) WHEN 'DURATION' THEN ba.baseline_duration ELSE 1 END::numeric,
         vb.weight_basis
    FROM baseline_activities ba
    JOIN v_version_weight_basis vb ON vb.version_id = ba.version_id
    LEFT JOIN v_activity_manhours m ON m.version_id = ba.version_id AND m.activity_uid = ba.activity_uid
   WHERE ba.version_id = p_version
$$;

CREATE FUNCTION activity_progress_as_of(p_version UUID, p_asof DATE)
RETURNS TABLE (project_id UUID, version_id UUID, activity_uid UUID, external_activity_id TEXT, activity_name TEXT, wbs_id UUID, wbs_path TEXT,
               discipline_code TEXT, activity_type TEXT, location TEXT, baseline_start DATE, baseline_finish DATE, baseline_duration NUMERIC,
               total_float NUMERIC, actual_start DATE, actual_finish DATE, physical_pct NUMERIC, planned_pct NUMERIC, progress_basis TEXT,
               measured_assignments BIGINT, any_overrun BOOLEAN, max_overrun_pct NUMERIC, weight NUMERIC, weight_basis TEXT,
               execution_state TEXT, start_date_unrecorded BOOLEAN, start_variance_days INTEGER, finish_variance_days INTEGER)
LANGUAGE sql STABLE AS $$
  WITH base AS (
    SELECT ba.*, w.weight AS w_weight, w.weight_basis AS w_basis, sw.wbs_path AS w_path
      FROM baseline_activities ba
      JOIN version_activity_weights(p_version) w ON w.activity_uid = ba.activity_uid
      JOIN schedule_wbs sw ON sw.wbs_id = ba.wbs_id
     WHERE ba.version_id = p_version
  ), qty AS (
    SELECT br.activity_uid, sum(br.progress_weight) AS w,
           sum(br.progress_weight * least(100, coalesce(cr.cum, 0) / br.baseline_qty * 100)) AS wp,
           count(*) AS n, bool_or(coalesce(cr.over, false)) AS any_over, max(cr.opct) AS max_over
      FROM baseline_resources br
      LEFT JOIN LATERAL (SELECT r.cumulative_qty AS cum, r.over_baseline AS over, r.overrun_pct AS opct
                           FROM approved_resource_progress r
                          WHERE r.assignment_uid = br.assignment_uid AND r.as_of_date <= p_asof
                          ORDER BY r.entry_seq DESC LIMIT 1) cr ON TRUE
     WHERE br.version_id = p_version AND br.measures_progress
     GROUP BY br.activity_uid
  ), calc AS (
    SELECT b.*, ca.actual_start AS a_start, ca.actual_finish AS a_finish, ca.reported_pct AS a_pct, q.w, q.wp, q.n, q.any_over, q.max_over
      FROM base b
      LEFT JOIN LATERAL (SELECT a.actual_start, a.actual_finish, a.reported_pct FROM approved_activity_progress a
                          WHERE a.project_id = b.project_id AND a.activity_uid = b.activity_uid AND a.as_of_date <= p_asof
                          ORDER BY a.entry_seq DESC LIMIT 1) ca ON TRUE
      LEFT JOIN qty q ON q.activity_uid = b.activity_uid
  ), pct AS (
    SELECT c.*,
           CASE WHEN c.w IS NOT NULL THEN round((c.wp / c.w)::numeric, 3)
                WHEN c.activity_type = 'MILESTONE' THEN CASE WHEN c.a_finish IS NOT NULL THEN 100 ELSE 0 END
                ELSE coalesce(c.a_pct, 0) END AS pp
      FROM calc c
  )
  SELECT p.project_id, p.version_id, p.activity_uid, p.external_activity_id, p.activity_name, p.wbs_id, p.w_path, p.discipline_code, p.activity_type,
         p.location, p.baseline_start, p.baseline_finish, p.baseline_duration, p.total_float, p.a_start, p.a_finish, p.pp::numeric,
         CASE WHEN p.activity_type = 'MILESTONE' THEN CASE WHEN p_asof >= p.baseline_finish THEN 100 ELSE 0 END
              ELSE round(100 * least(1, greatest(0, (p_asof - p.baseline_start + 1)::numeric / (p.baseline_finish - p.baseline_start + 1))), 3) END,
         CASE WHEN p.w IS NOT NULL THEN 'QUANTITY' WHEN p.activity_type = 'MILESTONE' THEN 'MILESTONE' ELSE 'APPROVED_PCT' END,
         coalesce(p.n, 0), coalesce(p.any_over, false), p.max_over, p.w_weight, p.w_basis,
         CASE WHEN p.a_finish IS NOT NULL THEN 'COMPLETED' WHEN p.a_start IS NOT NULL OR p.pp > 0 THEN 'IN_PROGRESS' ELSE 'NOT_STARTED' END,
         (p.pp > 0 AND p.a_start IS NULL), (p.a_start - p.baseline_start), (p.a_finish - p.baseline_finish)
    FROM pct p
$$;

CREATE FUNCTION project_progress_as_of(p_version UUID, p_asof DATE)
RETURNS TABLE (activities BIGINT, completed BIGINT, in_progress BIGINT, not_started BIGINT, physical_pct NUMERIC, planned_pct NUMERIC,
               spi_approx NUMERIC, weight_basis TEXT, any_overrun BOOLEAN, data_date DATE, as_of DATE)
LANGUAGE sql STABLE AS $$
  WITH a AS (SELECT * FROM activity_progress_as_of(p_version, p_asof)), s AS (
    SELECT count(*) AS n, count(*) FILTER (WHERE execution_state = 'COMPLETED') AS c, count(*) FILTER (WHERE execution_state = 'IN_PROGRESS') AS i,
           count(*) FILTER (WHERE execution_state = 'NOT_STARTED') AS ns, sum(weight) AS sw, sum(weight * physical_pct) AS swa, sum(weight * planned_pct) AS swp,
           min(weight_basis) AS basis, bool_or(any_overrun) AS over FROM a)
  SELECT s.n, s.c, s.i, s.ns,
         CASE WHEN coalesce(s.sw, 0) > 0 THEN round(s.swa / s.sw, 3) ELSE 0 END,
         CASE WHEN coalesce(s.sw, 0) > 0 THEN round(s.swp / s.sw, 3) ELSE 0 END,
         CASE WHEN coalesce(s.sw, 0) > 0 AND s.swp > 0 THEN round((s.swa / s.sw) / (s.swp / s.sw), 3) END,
         s.basis, coalesce(s.over, false), (SELECT v.data_date FROM schedule_versions v WHERE v.version_id = p_version), p_asof
    FROM s
$$;

CREATE FUNCTION wbs_progress_as_of(p_version UUID, p_asof DATE)
RETURNS TABLE (wbs_id UUID, wbs_code TEXT, wbs_name TEXT, node_type TEXT, level SMALLINT, activities BIGINT, physical_pct NUMERIC, planned_pct NUMERIC)
LANGUAGE sql STABLE AS $$
  WITH a AS (SELECT * FROM activity_progress_as_of(p_version, p_asof))
  SELECT n.wbs_id, n.wbs_code, n.wbs_name, n.node_type, n.level, count(a.activity_uid),
         CASE WHEN coalesce(sum(a.weight), 0) > 0 THEN round(sum(a.weight * a.physical_pct) / sum(a.weight), 3) ELSE 0 END,
         CASE WHEN coalesce(sum(a.weight), 0) > 0 THEN round(sum(a.weight * a.planned_pct) / sum(a.weight), 3) ELSE 0 END
    FROM schedule_wbs n
    LEFT JOIN a ON a.wbs_path LIKE n.wbs_path || '%'
   WHERE n.version_id = p_version
   GROUP BY n.wbs_id, n.wbs_code, n.wbs_name, n.node_type, n.level, n.wbs_path
   ORDER BY n.wbs_path
$$;

CREATE FUNCTION discipline_progress_as_of(p_version UUID, p_asof DATE)
RETURNS TABLE (discipline_code TEXT, activities BIGINT, physical_pct NUMERIC, planned_pct NUMERIC)
LANGUAGE sql STABLE AS $$
  SELECT a.discipline_code, count(*),
         CASE WHEN coalesce(sum(a.weight), 0) > 0 THEN round(sum(a.weight * a.physical_pct) / sum(a.weight), 3) ELSE 0 END,
         CASE WHEN coalesce(sum(a.weight), 0) > 0 THEN round(sum(a.weight * a.planned_pct) / sum(a.weight), 3) ELSE 0 END
    FROM activity_progress_as_of(p_version, p_asof) a GROUP BY a.discipline_code ORDER BY a.discipline_code
$$;

CREATE FUNCTION progress_timeline(p_version UUID, p_from DATE, p_to DATE, p_step_days INTEGER)
RETURNS TABLE (as_of DATE, physical_pct NUMERIC, planned_pct NUMERIC, spi_approx NUMERIC)
LANGUAGE sql STABLE AS $$
  SELECT d::date, p.physical_pct, p.planned_pct, p.spi_approx
    FROM generate_series(p_from::timestamp, p_to::timestamp, make_interval(days => greatest(1, p_step_days))) AS d
    CROSS JOIN LATERAL project_progress_as_of(p_version, d::date) p
$$;
