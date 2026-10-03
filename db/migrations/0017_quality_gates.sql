-- 0017 Quality gates, inspection & test plans (ITP) and inspection evidence (decision D8).
--
-- Inspection status is its own thing: it never changes progress. A REQUIRED ("mandatory") gate must be PASSED or WAIVED before a Supervisor can approve progress on its
-- activity; a gate that is not required is informational and never blocks. Existing projects have no gates, so nothing is imposed on them.
-- Gates hang on the STABLE activity identity (activity_uid) or a stage (wbs_uid), so they survive schedule revisions exactly like progress does.
-- Who does what (the database enforces it independently of the API): Supervisor configures gates and records pass / fail / waive; a Site Engineer may submit inspection
-- evidence for a gate; a Project Manager only reads gate status. Nothing here is deletable (append-only history is the audit log + the evidence table).

CREATE TABLE inspection_test_plans (
  itp_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id       UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  title            TEXT NOT NULL CHECK (length(btrim(title)) >= 1),
  description      TEXT,
  discipline_code  TEXT REFERENCES disciplines(code),
  responsible_party TEXT,
  status           TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','ACTIVE','ARCHIVED')),
  created_by       UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_itp_project UNIQUE (project_id, itp_id)
);
CREATE TRIGGER trg_itp_touch BEFORE UPDATE ON inspection_test_plans FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE quality_gates (
  quality_gate_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id          UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  itp_id              UUID,
  activity_uid        UUID,
  stage_wbs_uid       UUID,
  gate_name           TEXT NOT NULL CHECK (length(btrim(gate_name)) >= 1),
  gate_type           TEXT NOT NULL CHECK (gate_type IN ('INSPECTION','TEST','POUR_CARD','WELD_INSPECTION','NDT','MATERIAL_CERTIFICATE','NCR_CLEARANCE','CLIENT_APPROVAL',
                                                          'PRE_COMMENCEMENT','INTERMEDIATE_HOLD','CLEARANCE','FINAL_TAKEOVER','SAFETY_AUDIT')),
  checkpoint_category TEXT NOT NULL DEFAULT 'QUALITY_CHECK' CHECK (checkpoint_category IN ('HOLD','WITNESS','REVIEW','QUALITY_CHECK')),
  required            BOOLEAN NOT NULL DEFAULT TRUE,                 -- TRUE = mandatory: blocks approval until PASSED / WAIVED; FALSE = informational
  status              TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SUBMITTED','PASSED','FAILED','WAIVED')),
  due_date            DATE,
  remarks             TEXT,
  passed_at           TIMESTAMPTZ,
  passed_by           UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  failed_at           TIMESTAMPTZ,
  failed_by           UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  waived_at           TIMESTAMPTZ,
  waived_by           UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  waiver_reason       TEXT,
  created_by          UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_qgate_project UNIQUE (project_id, quality_gate_id),
  CONSTRAINT fk_qgate_itp FOREIGN KEY (project_id, itp_id) REFERENCES inspection_test_plans (project_id, itp_id),
  CONSTRAINT fk_qgate_activity FOREIGN KEY (project_id, activity_uid) REFERENCES activities (project_id, activity_uid),
  CONSTRAINT qgate_target_chk CHECK (activity_uid IS NOT NULL OR stage_wbs_uid IS NOT NULL),
  CONSTRAINT qgate_waiver_chk CHECK ((status = 'WAIVED') = (waived_at IS NOT NULL AND waived_by IS NOT NULL AND length(btrim(coalesce(waiver_reason, ''))) >= 3)),
  CONSTRAINT qgate_passed_chk CHECK ((status = 'PASSED') = (passed_at IS NOT NULL AND passed_by IS NOT NULL))
);
CREATE INDEX idx_qgates_activity ON quality_gates (project_id, activity_uid);
CREATE TRIGGER trg_qgate_touch BEFORE UPDATE ON quality_gates FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE quality_evidence (
  quality_evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id          UUID NOT NULL,
  quality_gate_id     UUID NOT NULL,
  document_id         UUID,
  evidence_type       TEXT NOT NULL CHECK (evidence_type IN ('TEST_REPORT','PHOTO','THIRD_PARTY_CERT','NCR_CLEARANCE','INSPECTION_NOTE','POUR_CARD','WELD_INSPECTION',
                                                             'NDT_RESULT','MATERIAL_CERTIFICATE','CLIENT_APPROVAL','OTHER')),
  result              TEXT NOT NULL DEFAULT 'PASS' CHECK (result IN ('PASS','FAIL','PENDING_REVIEW')),
  inspector_name      TEXT,
  inspection_date     DATE,
  evidence_hash       TEXT,
  metadata            JSONB NOT NULL DEFAULT '{}',
  submitted_by        UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_qev_gate FOREIGN KEY (project_id, quality_gate_id) REFERENCES quality_gates (project_id, quality_gate_id),
  CONSTRAINT fk_qev_doc FOREIGN KEY (project_id, document_id) REFERENCES source_documents (project_id, document_id)
);
CREATE INDEX idx_qev_gate ON quality_evidence (quality_gate_id);
CREATE TRIGGER trg_qev_append_only BEFORE UPDATE OR DELETE ON quality_evidence FOR EACH ROW EXECUTE FUNCTION trg_append_only();
CREATE TRIGGER trg_qgate_nodelete BEFORE DELETE ON quality_gates FOR EACH ROW EXECUTE FUNCTION trg_append_only();
CREATE TRIGGER trg_itp_nodelete BEFORE DELETE ON inspection_test_plans FOR EACH ROW EXECUTE FUNCTION trg_append_only();

-- ---- who may do what, and which transitions exist
CREATE FUNCTION trg_qgate_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_actor UUID := app_actor(); v_role text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF project_role_of(NEW.project_id, NEW.created_by) IS DISTINCT FROM 'SUPERVISOR' THEN
      RAISE EXCEPTION 'quality gates are configured by a SUPERVISOR of the project' USING ERRCODE = '42501';
    END IF;
    IF NEW.status <> 'PENDING' THEN RAISE EXCEPTION 'a quality gate starts PENDING' USING ERRCODE = '23514'; END IF;
    RETURN NEW;
  END IF;
  IF NEW.project_id <> OLD.project_id OR NEW.activity_uid IS DISTINCT FROM OLD.activity_uid OR NEW.stage_wbs_uid IS DISTINCT FROM OLD.stage_wbs_uid
     OR NEW.created_by <> OLD.created_by OR NEW.required <> OLD.required OR NEW.gate_type <> OLD.gate_type OR NEW.checkpoint_category <> OLD.checkpoint_category THEN
    RAISE EXCEPTION 'what a quality gate is (target, type, category, mandatory flag) is immutable: add a new gate instead' USING ERRCODE = '23514';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF NOT ((OLD.status = 'PENDING'   AND NEW.status IN ('SUBMITTED','PASSED','FAILED','WAIVED')) OR
            (OLD.status = 'SUBMITTED' AND NEW.status IN ('PASSED','FAILED','WAIVED')) OR
            (OLD.status = 'FAILED'    AND NEW.status IN ('SUBMITTED','PASSED','WAIVED'))) THEN
      RAISE EXCEPTION 'illegal quality gate transition % -> %', OLD.status, NEW.status USING ERRCODE = '23514';
    END IF;
    IF NOT app_is_system() THEN
      v_role := project_role_of(NEW.project_id, v_actor);
      IF NEW.status = 'SUBMITTED' THEN
        IF v_role IS NULL OR v_role NOT IN ('SUPERVISOR','SITE_ENGINEER') THEN RAISE EXCEPTION 'only a SUPERVISOR or SITE_ENGINEER submits inspection evidence' USING ERRCODE = '42501'; END IF;
      ELSIF v_role IS DISTINCT FROM 'SUPERVISOR' THEN
        RAISE EXCEPTION 'only a SUPERVISOR passes, fails or waives a quality gate' USING ERRCODE = '42501';
      END IF;
    END IF;
  END IF;
  IF NOT app_is_system() AND project_role_of(NEW.project_id, v_actor) = 'SITE_ENGINEER'
     AND (to_jsonb(NEW) - 'status' - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'updated_at') THEN
    RAISE EXCEPTION 'a SITE_ENGINEER can only submit evidence (move a gate to SUBMITTED); nothing else on a gate' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_qgate_guard BEFORE INSERT OR UPDATE ON quality_gates FOR EACH ROW EXECUTE FUNCTION trg_qgate_guard();

CREATE FUNCTION trg_itp_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF project_role_of(NEW.project_id, NEW.created_by) IS DISTINCT FROM 'SUPERVISOR' AND NOT app_is_system() THEN
    RAISE EXCEPTION 'inspection and test plans are managed by a SUPERVISOR of the project' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_itp_guard BEFORE INSERT ON inspection_test_plans FOR EACH ROW EXECUTE FUNCTION trg_itp_guard();

CREATE FUNCTION trg_qev_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF project_role_of(NEW.project_id, NEW.submitted_by) IS NULL OR project_role_of(NEW.project_id, NEW.submitted_by) NOT IN ('SUPERVISOR','SITE_ENGINEER') THEN
    RAISE EXCEPTION 'inspection evidence is submitted by a SUPERVISOR or SITE_ENGINEER of the project (project managers never)' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_qev_guard BEFORE INSERT ON quality_evidence FOR EACH ROW EXECUTE FUNCTION trg_qev_guard();

-- ---- the approval gate: a Supervisor cannot approve progress on an activity while a REQUIRED gate of it is not PASSED / WAIVED
CREATE FUNCTION quality_unsatisfied_gates(p_project UUID, p_activity UUID) RETURNS TABLE (quality_gate_id UUID, gate_name TEXT, status TEXT)
LANGUAGE sql STABLE AS $$
  SELECT g.quality_gate_id, g.gate_name, g.status FROM quality_gates g
   WHERE g.project_id = p_project AND g.activity_uid = p_activity AND g.required AND g.status NOT IN ('PASSED','WAIVED') $$;

CREATE FUNCTION trg_pd_quality_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_n int; v_names text;
BEGIN
  IF NEW.action IN ('APPROVE','EDIT') AND NEW.selected_activity_uid IS NOT NULL THEN
    SELECT count(*), string_agg(gate_name, ', ') INTO v_n, v_names FROM quality_unsatisfied_gates(NEW.project_id, NEW.selected_activity_uid);
    IF v_n > 0 THEN
      RAISE EXCEPTION 'QUALITY_HOLD: % mandatory quality gate(s) are not satisfied (%)', v_n, v_names USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_pd_quality_guard BEFORE INSERT ON planner_decisions FOR EACH ROW EXECUTE FUNCTION trg_pd_quality_guard();

-- ---- row security: members read, the backend writes (same posture as every other project table)
ALTER TABLE inspection_test_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE quality_gates ENABLE ROW LEVEL SECURITY;
ALTER TABLE quality_evidence ENABLE ROW LEVEL SECURITY;
CREATE POLICY itp_select ON inspection_test_plans FOR SELECT TO authenticated USING (is_project_member(project_id));
CREATE POLICY qgates_select ON quality_gates FOR SELECT TO authenticated USING (is_project_member(project_id));
CREATE POLICY qev_select ON quality_evidence FOR SELECT TO authenticated USING (project_role_of(project_id, auth.uid()) = 'SUPERVISOR' OR submitted_by = auth.uid());
GRANT SELECT ON inspection_test_plans, quality_gates, quality_evidence TO authenticated;
