-- 0005 execution: claims (execution_events), supervisor decisions (planner_decisions) and the two append-only approved-progress
-- ledgers. Reported / extracted / matched / approved / rejected-disputed are DISTINCT states; only the ledgers count as progress.

ALTER TABLE assignments ADD CONSTRAINT uq_assignment_project UNIQUE (project_id, assignment_uid);

CREATE FUNCTION trg_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP USING ERRCODE = '23514';
END $$;

CREATE FUNCTION trg_batches_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF project_role_of(NEW.project_id, NEW.uploaded_by) IS DISTINCT FROM 'SITE_ENGINEER' THEN
    RAISE EXCEPTION 'upload batches can only be created by a SITE_ENGINEER of the project' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_batches_uploader BEFORE INSERT ON upload_batches FOR EACH ROW EXECUTE FUNCTION trg_batches_guard();

-- ------------------------------------------------------------------ claims
CREATE TABLE execution_events (
  event_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id            UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  filed_in_version_id   UUID NOT NULL,                      -- immutable schedule-version provenance
  document_id           UUID,
  batch_id              UUID,
  event_date            DATE NOT NULL,                      -- the date the report is about
  raw_claim_text        TEXT NOT NULL CHECK (length(btrim(raw_claim_text)) > 0),
  input_channel         TEXT NOT NULL CHECK (input_channel IN
                          ('TYPED','VOICE_TRANSCRIPT','TXT','CSV','XLSX','PDF','SCANNED','IMAGE','API')),
  language_detected     TEXT,
  reported_activity_ref TEXT,                               -- what the report literally said (id / partial name / location)
  matched_activity_uid  UUID,
  discipline_code       TEXT REFERENCES disciplines(code),
  event_type            TEXT NOT NULL DEFAULT 'PROGRESS' CHECK (event_type IN ('PROGRESS','START','FINISH','DELAY_NOTE')),
  claim_mode            TEXT NOT NULL DEFAULT 'CUMULATIVE_PCT' CHECK (claim_mode IN ('CUMULATIVE_PCT','CUMULATIVE_QTY','INCREMENTAL_QTY')),
  asset_tag             TEXT,
  location              TEXT,
  claimed_pct           NUMERIC(6,3) CHECK (claimed_pct IS NULL OR claimed_pct BETWEEN 0 AND 100),
  claimed_start         DATE,
  claimed_finish        DATE,
  delay_reason          TEXT,
  filed_by              UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  status                TEXT NOT NULL DEFAULT 'REPORTED' CHECK (status IN
                          ('REPORTED','EXTRACTED','MATCHED','VALIDATED','APPROVED','REJECTED','DISPUTED')),
  clarification_status  TEXT NOT NULL DEFAULT 'NONE' CHECK (clarification_status IN ('NONE','ASKED','ANSWERED')),
  clarification_question TEXT,
  clarification_answer  TEXT,
  field_provenance      JSONB NOT NULL DEFAULT '{}',
  priority_score        NUMERIC(8,3) NOT NULL DEFAULT 0,
  priority_reasons      TEXT,
  claim_fingerprint     TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_event_version  FOREIGN KEY (project_id, filed_in_version_id) REFERENCES schedule_versions (project_id, version_id),
  CONSTRAINT fk_event_document FOREIGN KEY (project_id, document_id) REFERENCES source_documents (project_id, document_id),
  CONSTRAINT fk_event_batch    FOREIGN KEY (project_id, batch_id) REFERENCES upload_batches (project_id, batch_id),
  CONSTRAINT fk_event_activity FOREIGN KEY (project_id, matched_activity_uid) REFERENCES activities (project_id, activity_uid),
  CONSTRAINT uq_event_project UNIQUE (project_id, event_id),
  CHECK (claimed_finish IS NULL OR claimed_start IS NULL OR claimed_finish >= claimed_start),
  CHECK (event_date <= created_at::date + 1)
);
CREATE UNIQUE INDEX uq_event_fingerprint ON execution_events (project_id, claim_fingerprint) WHERE claim_fingerprint IS NOT NULL;
CREATE INDEX idx_events_project_status ON execution_events (project_id, status, event_date DESC);
CREATE INDEX idx_events_activity ON execution_events (matched_activity_uid, event_date);
CREATE INDEX idx_events_filer ON execution_events (filed_by, created_at DESC);
CREATE TRIGGER trg_events_touch BEFORE UPDATE ON execution_events FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE FUNCTION trg_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_vstatus text;
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
    IF NEW.status NOT IN ('REPORTED','EXTRACTED','MATCHED','VALIDATED','APPROVED','REJECTED','DISPUTED') THEN
      RAISE EXCEPTION 'bad status'; END IF;
    RETURN NEW;
  END IF;
  -- UPDATE
  IF NEW.filed_in_version_id <> OLD.filed_in_version_id OR NEW.filed_by <> OLD.filed_by
     OR NEW.project_id <> OLD.project_id OR NEW.raw_claim_text <> OLD.raw_claim_text OR NEW.event_date <> OLD.event_date THEN
    RAISE EXCEPTION 'claim provenance (version, filer, text, report date) is immutable' USING ERRCODE = '23514';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
       (OLD.status = 'REPORTED'  AND NEW.status IN ('EXTRACTED','REJECTED')) OR
       (OLD.status = 'EXTRACTED' AND NEW.status IN ('MATCHED','REJECTED','DISPUTED')) OR
       (OLD.status = 'MATCHED'   AND NEW.status IN ('EXTRACTED','VALIDATED','APPROVED','REJECTED','DISPUTED')) OR
       (OLD.status = 'VALIDATED' AND NEW.status IN ('APPROVED','REJECTED','DISPUTED')) OR
       (OLD.status = 'DISPUTED'  AND NEW.status IN ('APPROVED','REJECTED'))) THEN
    RAISE EXCEPTION 'illegal claim status transition % -> %', OLD.status, NEW.status USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_events_guard BEFORE INSERT OR UPDATE ON execution_events FOR EACH ROW EXECUTE FUNCTION trg_events_guard();
CREATE TRIGGER trg_events_nodelete BEFORE DELETE ON execution_events FOR EACH ROW EXECUTE FUNCTION trg_append_only();

-- Quantities as REPORTED, per resource assignment, preserved exactly (never clamped or rewritten by approval).
CREATE TABLE claim_quantities (
  claim_quantity_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id           UUID NOT NULL,
  event_id             UUID NOT NULL,
  assignment_uid       UUID,
  reported_resource    TEXT,
  qty_basis            TEXT NOT NULL CHECK (qty_basis IN ('CUMULATIVE','INCREMENTAL')),
  reported_qty         NUMERIC(18,3) NOT NULL CHECK (reported_qty >= 0),
  reported_uom         TEXT NOT NULL,
  normalized_uom       TEXT REFERENCES units_of_measure(code),
  normalized_qty       NUMERIC(18,3) CHECK (normalized_qty IS NULL OR normalized_qty >= 0),
  CONSTRAINT fk_cq_event FOREIGN KEY (project_id, event_id) REFERENCES execution_events (project_id, event_id) ON DELETE CASCADE,
  CONSTRAINT fk_cq_assignment FOREIGN KEY (project_id, assignment_uid) REFERENCES assignments (project_id, assignment_uid),
  CHECK ((normalized_qty IS NULL) = (normalized_uom IS NULL))
);
CREATE INDEX idx_cq_event ON claim_quantities (event_id);
CREATE INDEX idx_cq_assignment ON claim_quantities (assignment_uid);

CREATE TABLE source_references (
  reference_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID NOT NULL,
  event_id      UUID NOT NULL,
  document_id   UUID,
  sheet_name    TEXT,
  row_cell_ref  TEXT,
  message_id    TEXT,
  raw_snippet   TEXT NOT NULL,
  CONSTRAINT fk_sr_event FOREIGN KEY (project_id, event_id) REFERENCES execution_events (project_id, event_id) ON DELETE CASCADE,
  CONSTRAINT fk_sr_document FOREIGN KEY (project_id, document_id) REFERENCES source_documents (project_id, document_id)
);
CREATE INDEX idx_sr_event ON source_references (event_id);

CREATE TABLE claim_evidence (
  project_id  UUID NOT NULL,
  event_id    UUID NOT NULL,
  document_id UUID NOT NULL,
  PRIMARY KEY (event_id, document_id),
  CONSTRAINT fk_ce_event FOREIGN KEY (project_id, event_id) REFERENCES execution_events (project_id, event_id) ON DELETE CASCADE,
  CONSTRAINT fk_ce_document FOREIGN KEY (project_id, document_id) REFERENCES source_documents (project_id, document_id)
);

CREATE TABLE candidate_matches (
  candidate_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id           UUID NOT NULL,
  event_id             UUID NOT NULL,
  activity_uid         UUID NOT NULL,
  rank_order           SMALLINT NOT NULL CHECK (rank_order BETWEEN 1 AND 3),
  match_tier           TEXT,
  composite_confidence NUMERIC(5,4) NOT NULL CHECK (composite_confidence BETWEEN 0 AND 1),
  semantic_score       NUMERIC(5,4), fuzzy_score NUMERIC(5,4), location_score NUMERIC(5,4), discipline_score NUMERIC(5,4),
  supporting_signals   TEXT,
  disqualifying_signals TEXT,
  CONSTRAINT fk_cm_event FOREIGN KEY (project_id, event_id) REFERENCES execution_events (project_id, event_id) ON DELETE CASCADE,
  CONSTRAINT fk_cm_activity FOREIGN KEY (project_id, activity_uid) REFERENCES activities (project_id, activity_uid),
  CONSTRAINT uq_cm_rank UNIQUE (event_id, rank_order),
  CONSTRAINT uq_cm_activity UNIQUE (event_id, activity_uid)
);

CREATE TABLE claim_activity_splits (
  split_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id         UUID NOT NULL,
  event_id           UUID NOT NULL,
  activity_uid       UUID NOT NULL,
  split_basis        TEXT NOT NULL CHECK (split_basis IN ('EQUAL','WBS_WEIGHTED','MANUAL')),
  split_pct          NUMERIC(7,6) NOT NULL CHECK (split_pct >= 0 AND split_pct <= 1),
  wbs_code           TEXT,
  planned_quantity   NUMERIC(18,3),
  allocated_quantity NUMERIC(18,3),
  uom                TEXT,
  rationale          TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_cs_event FOREIGN KEY (project_id, event_id) REFERENCES execution_events (project_id, event_id) ON DELETE CASCADE,
  CONSTRAINT fk_cs_activity FOREIGN KEY (project_id, activity_uid) REFERENCES activities (project_id, activity_uid),
  CONSTRAINT uq_cs UNIQUE (event_id, activity_uid)
);

CREATE TABLE claim_validations (
  validation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID NOT NULL,
  event_id      UUID NOT NULL,
  rule_code     TEXT NOT NULL,
  severity      TEXT NOT NULL CHECK (severity IN ('INFO','WARNING','ERROR')),
  description   TEXT NOT NULL,
  CONSTRAINT fk_cv_event FOREIGN KEY (project_id, event_id) REFERENCES execution_events (project_id, event_id) ON DELETE CASCADE
);
CREATE INDEX idx_cv_event ON claim_validations (event_id);

CREATE TABLE conflict_records (
  conflict_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id       UUID NOT NULL,
  activity_uid     UUID NOT NULL,
  reporting_period DATE NOT NULL,
  event_id_a       UUID NOT NULL,
  event_id_b       UUID NOT NULL,
  value_a          NUMERIC(18,3) NOT NULL,
  value_b          NUMERIC(18,3) NOT NULL,
  variance_pct     NUMERIC(10,3) NOT NULL,
  status           TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED')),
  CONSTRAINT fk_cr_a FOREIGN KEY (project_id, event_id_a) REFERENCES execution_events (project_id, event_id) ON DELETE CASCADE,
  CONSTRAINT fk_cr_b FOREIGN KEY (project_id, event_id_b) REFERENCES execution_events (project_id, event_id) ON DELETE CASCADE,
  CONSTRAINT fk_cr_act FOREIGN KEY (project_id, activity_uid) REFERENCES activities (project_id, activity_uid),
  CHECK (event_id_a <> event_id_b)
);

CREATE TABLE evidence_links (
  link_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID NOT NULL,
  event_id_a    UUID NOT NULL,
  event_id_b    UUID NOT NULL,
  relation_type TEXT NOT NULL CHECK (relation_type IN ('CORROBORATES','CONTRADICTS')),
  confidence    NUMERIC(5,4),
  rationale     TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_el_a FOREIGN KEY (project_id, event_id_a) REFERENCES execution_events (project_id, event_id) ON DELETE CASCADE,
  CONSTRAINT fk_el_b FOREIGN KEY (project_id, event_id_b) REFERENCES execution_events (project_id, event_id) ON DELETE CASCADE,
  CHECK (event_id_a <> event_id_b)
);

-- ------------------------------------------------------------------ supervisor decisions (append-only)
CREATE TABLE planner_decisions (
  decision_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id           UUID NOT NULL,
  event_id             UUID NOT NULL,
  selected_activity_uid UUID,
  action               TEXT NOT NULL CHECK (action IN ('APPROVE','EDIT','REJECT','HOLD')),
  approved_pct         NUMERIC(6,3) CHECK (approved_pct IS NULL OR approved_pct BETWEEN 0 AND 100),
  justification        TEXT NOT NULL CHECK (length(btrim(justification)) >= 3),
  decided_by           UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  decided_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  overrun_ack          BOOLEAN NOT NULL DEFAULT FALSE,
  overrun_ack_note     TEXT,
  CONSTRAINT fk_pd_event FOREIGN KEY (project_id, event_id) REFERENCES execution_events (project_id, event_id),
  CONSTRAINT fk_pd_activity FOREIGN KEY (project_id, selected_activity_uid) REFERENCES activities (project_id, activity_uid),
  CONSTRAINT uq_pd_project UNIQUE (project_id, decision_id),
  CHECK (NOT overrun_ack OR length(btrim(coalesce(overrun_ack_note, ''))) >= 3)
);
CREATE INDEX idx_pd_event ON planner_decisions (event_id, decided_at);
CREATE INDEX idx_pd_decider ON planner_decisions (decided_by, decided_at DESC);
CREATE TRIGGER trg_pd_append_only BEFORE UPDATE OR DELETE ON planner_decisions FOR EACH ROW EXECUTE FUNCTION trg_append_only();

CREATE FUNCTION trg_pd_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_created timestamptz; v_status text;
BEGIN
  IF project_role_of(NEW.project_id, NEW.decided_by) IS DISTINCT FROM 'SUPERVISOR' THEN
    RAISE EXCEPTION 'only a SUPERVISOR of the project can decide on claims (project managers and site engineers cannot)'
      USING ERRCODE = '42501';
  END IF;
  SELECT created_at, status INTO v_created, v_status FROM execution_events
   WHERE project_id = NEW.project_id AND event_id = NEW.event_id;
  IF NEW.decided_at < v_created THEN
    RAISE EXCEPTION 'a decision cannot precede the claim it decides (decided_at % < claim created_at %)', NEW.decided_at, v_created
      USING ERRCODE = '23514';
  END IF;
  IF v_status NOT IN ('EXTRACTED','MATCHED','VALIDATED','DISPUTED') THEN
    RAISE EXCEPTION 'claim in status % cannot be decided', v_status USING ERRCODE = '23514';
  END IF;
  IF NEW.action IN ('APPROVE','EDIT') AND NEW.selected_activity_uid IS NULL
     AND NOT EXISTS (SELECT 1 FROM claim_activity_splits s WHERE s.event_id = NEW.event_id) THEN
    RAISE EXCEPTION 'approval needs a selected activity or WBS split rows' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_pd_guard BEFORE INSERT ON planner_decisions FOR EACH ROW EXECUTE FUNCTION trg_pd_guard();

-- The decision drives the claim state atomically.
CREATE FUNCTION trg_pd_apply() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE execution_events SET status = CASE NEW.action
      WHEN 'APPROVE' THEN 'APPROVED' WHEN 'EDIT' THEN 'APPROVED' WHEN 'REJECT' THEN 'REJECTED' ELSE 'DISPUTED' END
   WHERE project_id = NEW.project_id AND event_id = NEW.event_id;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_pd_apply AFTER INSERT ON planner_decisions FOR EACH ROW EXECUTE FUNCTION trg_pd_apply();

-- ------------------------------------------------------------------ ledgers
CREATE TABLE approved_activity_progress (
  entry_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entry_seq          BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
  project_id         UUID NOT NULL,
  activity_uid       UUID NOT NULL,
  decision_id        UUID NOT NULL,
  as_of_date         DATE NOT NULL,
  actual_start       DATE,
  actual_finish      DATE,
  reported_pct       NUMERIC(6,3) CHECK (reported_pct IS NULL OR reported_pct BETWEEN 0 AND 100),  -- non-quantity activities only
  supersedes_entry_id UUID REFERENCES approved_activity_progress(entry_id),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_aap_decision FOREIGN KEY (project_id, decision_id) REFERENCES planner_decisions (project_id, decision_id),
  CONSTRAINT fk_aap_activity FOREIGN KEY (project_id, activity_uid) REFERENCES activities (project_id, activity_uid),
  CONSTRAINT uq_aap_decision_activity UNIQUE (decision_id, activity_uid),
  CHECK (actual_finish IS NULL OR actual_start IS NOT NULL),
  CHECK (actual_finish IS NULL OR actual_finish >= actual_start)
);
CREATE INDEX idx_aap_activity ON approved_activity_progress (activity_uid, entry_seq DESC);
CREATE UNIQUE INDEX uq_aap_superseded_once ON approved_activity_progress (supersedes_entry_id) WHERE supersedes_entry_id IS NOT NULL;
CREATE TRIGGER trg_aap_append_only BEFORE UPDATE OR DELETE ON approved_activity_progress FOR EACH ROW EXECUTE FUNCTION trg_append_only();

CREATE TABLE approved_resource_progress (
  entry_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entry_seq            BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
  project_id           UUID NOT NULL,
  activity_uid         UUID NOT NULL,
  assignment_uid       UUID NOT NULL,
  decision_id          UUID NOT NULL,
  as_of_date           DATE NOT NULL,
  cumulative_qty       NUMERIC(18,3) NOT NULL CHECK (cumulative_qty >= 0),
  incremental_qty      NUMERIC(18,3) NOT NULL,
  prev_cumulative_qty  NUMERIC(18,3) NOT NULL CHECK (prev_cumulative_qty >= 0),
  baseline_qty_at_entry NUMERIC(18,3) NOT NULL CHECK (baseline_qty_at_entry > 0),
  overrun_pct          NUMERIC(10,3) GENERATED ALWAYS AS (greatest(0, (cumulative_qty / baseline_qty_at_entry - 1) * 100)) STORED,
  over_baseline        BOOLEAN GENERATED ALWAYS AS (cumulative_qty > baseline_qty_at_entry) STORED,
  overrun_ack_by       UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  overrun_ack_note     TEXT,
  supersedes_entry_id  UUID REFERENCES approved_resource_progress(entry_id),
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_arp_decision FOREIGN KEY (project_id, decision_id) REFERENCES planner_decisions (project_id, decision_id),
  CONSTRAINT fk_arp_assignment FOREIGN KEY (project_id, assignment_uid, activity_uid) REFERENCES assignments (project_id, assignment_uid, activity_uid),
  CONSTRAINT uq_arp_decision_assignment UNIQUE (decision_id, assignment_uid),
  CHECK (incremental_qty = cumulative_qty - prev_cumulative_qty)
);
CREATE INDEX idx_arp_assignment ON approved_resource_progress (assignment_uid, entry_seq DESC);
CREATE INDEX idx_arp_activity ON approved_resource_progress (activity_uid);
CREATE UNIQUE INDEX uq_arp_superseded_once ON approved_resource_progress (supersedes_entry_id) WHERE supersedes_entry_id IS NOT NULL;
CREATE TRIGGER trg_arp_append_only BEFORE UPDATE OR DELETE ON approved_resource_progress FOR EACH ROW EXECUTE FUNCTION trg_append_only();

-- shared: the project's ACTIVE version, and whether the decision approves progress
CREATE FUNCTION active_version_of(p_project UUID) RETURNS UUID LANGUAGE sql STABLE AS $$
  SELECT version_id FROM schedule_versions WHERE project_id = p_project AND status = 'ACTIVE' $$;

CREATE FUNCTION trg_aap_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_action text; v_decided timestamptz; v_prev approved_activity_progress%ROWTYPE; v_version UUID;
BEGIN
  SELECT action, decided_at INTO v_action, v_decided FROM planner_decisions
   WHERE project_id = NEW.project_id AND decision_id = NEW.decision_id;
  IF v_action NOT IN ('APPROVE','EDIT') THEN
    RAISE EXCEPTION 'progress can only be recorded from an APPROVE/EDIT decision (decision is %)', v_action USING ERRCODE = '23514';
  END IF;
  IF NEW.as_of_date > v_decided::date THEN
    RAISE EXCEPTION 'as_of_date % is after the decision date %', NEW.as_of_date, v_decided::date USING ERRCODE = '23514';
  END IF;
  v_version := active_version_of(NEW.project_id);
  IF v_version IS NULL OR NOT EXISTS (SELECT 1 FROM baseline_activities WHERE version_id = v_version AND activity_uid = NEW.activity_uid) THEN
    RAISE EXCEPTION 'activity is not part of the project''s active schedule version' USING ERRCODE = '23514';
  END IF;
  SELECT * INTO v_prev FROM approved_activity_progress WHERE activity_uid = NEW.activity_uid ORDER BY entry_seq DESC LIMIT 1;
  IF FOUND THEN
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
CREATE TRIGGER trg_aap_guard BEFORE INSERT ON approved_activity_progress FOR EACH ROW EXECUTE FUNCTION trg_aap_guard();

CREATE FUNCTION trg_arp_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_action text; v_decided timestamptz; v_decider UUID; v_ack boolean; v_version UUID;
  v_prev approved_resource_progress%ROWTYPE; v_base numeric; v_tol numeric;
BEGIN
  SELECT action, decided_at, decided_by, overrun_ack INTO v_action, v_decided, v_decider, v_ack FROM planner_decisions
   WHERE project_id = NEW.project_id AND decision_id = NEW.decision_id;
  IF v_action NOT IN ('APPROVE','EDIT') THEN
    RAISE EXCEPTION 'quantities can only be approved by an APPROVE/EDIT decision (decision is %)', v_action USING ERRCODE = '23514';
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

  -- Over-baseline: any overrun is flagged (generated columns); beyond the project tolerance a Supervisor must acknowledge it.
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
CREATE TRIGGER trg_arp_guard BEFORE INSERT ON approved_resource_progress FOR EACH ROW EXECUTE FUNCTION trg_arp_guard();

-- Activation gate: a version cannot become ACTIVE while approved progress would be orphaned.
CREATE FUNCTION trg_activation_gate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_missing text;
BEGIN
  IF NEW.status = 'ACTIVE' AND OLD.status IS DISTINCT FROM 'ACTIVE' THEN
    SELECT string_agg(DISTINCT a.activity_uid::text, ', ') INTO v_missing
      FROM (SELECT activity_uid FROM approved_activity_progress WHERE project_id = NEW.project_id
            UNION SELECT activity_uid FROM approved_resource_progress WHERE project_id = NEW.project_id) a
     WHERE NOT EXISTS (SELECT 1 FROM baseline_activities b WHERE b.version_id = NEW.version_id AND b.activity_uid = a.activity_uid)
       AND NOT EXISTS (SELECT 1 FROM activity_lineage l WHERE l.version_id = NEW.version_id
                         AND l.from_activity_uid = a.activity_uid AND l.confirmed_at IS NOT NULL);
    IF v_missing IS NOT NULL THEN
      RAISE EXCEPTION 'cannot activate: approved progress exists for activities absent from this version without a confirmed lineage/retirement decision: %', v_missing
        USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM approved_resource_progress r
                WHERE r.project_id = NEW.project_id
                  AND EXISTS (SELECT 1 FROM baseline_activities b WHERE b.version_id = NEW.version_id AND b.activity_uid = r.activity_uid)
                  AND NOT EXISTS (SELECT 1 FROM baseline_resources br WHERE br.version_id = NEW.version_id AND br.assignment_uid = r.assignment_uid)) THEN
      RAISE EXCEPTION 'cannot activate: an assignment with approved quantities is missing from this version' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_versions_activation_gate BEFORE UPDATE ON schedule_versions FOR EACH ROW EXECUTE FUNCTION trg_activation_gate();
