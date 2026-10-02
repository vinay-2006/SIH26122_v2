-- 0006 issues & delays, root causes, institutional memory, notifications, audit log.

CREATE TABLE root_causes (
  root_cause_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  category_code TEXT NOT NULL REFERENCES issue_categories(code),
  title         TEXT NOT NULL CHECK (length(btrim(title)) >= 3),
  summary       TEXT,
  status        TEXT NOT NULL DEFAULT 'IDENTIFIED' CHECK (status IN ('IDENTIFIED','ADDRESSED')),
  identified_by UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  identified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_root_cause_project UNIQUE (project_id, root_cause_id)
);
CREATE INDEX idx_root_causes_project ON root_causes (project_id, category_code);

-- An issue/delay targets an activity (stable uid) or a stage (stable wbs_uid). BLOCKED is derived from ACTIVE blocking issues.
CREATE TABLE issues (
  issue_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  activity_uid    UUID,
  stage_wbs_uid   UUID,
  category_code   TEXT NOT NULL REFERENCES issue_categories(code),
  title           TEXT NOT NULL CHECK (length(btrim(title)) >= 3),
  description     TEXT,
  severity        TEXT NOT NULL DEFAULT 'MEDIUM' CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
  reported_date   DATE NOT NULL,
  expected_duration_days NUMERIC(8,2) CHECK (expected_duration_days IS NULL OR expected_duration_days >= 0),
  blocks_work     BOOLEAN NOT NULL DEFAULT TRUE,
  status          TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','RESOLVED')),
  root_cause_id   UUID,
  source_event_id UUID,
  reported_by     UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_by     UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  resolved_at     TIMESTAMPTZ,
  resolution_notes TEXT,
  CONSTRAINT fk_issue_activity FOREIGN KEY (project_id, activity_uid) REFERENCES activities (project_id, activity_uid),
  CONSTRAINT fk_issue_root_cause FOREIGN KEY (project_id, root_cause_id) REFERENCES root_causes (project_id, root_cause_id),
  CONSTRAINT fk_issue_event FOREIGN KEY (project_id, source_event_id) REFERENCES execution_events (project_id, event_id),
  CONSTRAINT uq_issue_project UNIQUE (project_id, issue_id),
  CONSTRAINT issue_target_chk CHECK (activity_uid IS NOT NULL OR stage_wbs_uid IS NOT NULL),
  CONSTRAINT issue_lifecycle_chk CHECK (
    (status = 'ACTIVE' AND resolved_by IS NULL AND resolved_at IS NULL)
    OR (status = 'RESOLVED' AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL AND resolved_at >= created_at))
);
CREATE INDEX idx_issues_active ON issues (project_id, status, activity_uid);
CREATE INDEX idx_issues_root_cause ON issues (root_cause_id);

CREATE FUNCTION trg_issues_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF project_role_of(NEW.project_id, NEW.reported_by) IS NULL
       OR project_role_of(NEW.project_id, NEW.reported_by) NOT IN ('SITE_ENGINEER','SUPERVISOR') THEN
      RAISE EXCEPTION 'issues can be reported only by a SITE_ENGINEER or SUPERVISOR of the project' USING ERRCODE = '42501';
    END IF;
  ELSE
    IF NEW.project_id <> OLD.project_id OR NEW.reported_by <> OLD.reported_by OR NEW.created_at <> OLD.created_at THEN
      RAISE EXCEPTION 'issue provenance is immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status = 'RESOLVED' AND NEW.status <> 'RESOLVED' THEN
      RAISE EXCEPTION 'a resolved issue cannot be reopened (raise a new issue)' USING ERRCODE = '23514';
    END IF;
    IF (NEW.status = 'RESOLVED' AND OLD.status <> 'RESOLVED' AND project_role_of(NEW.project_id, NEW.resolved_by) IS DISTINCT FROM 'SUPERVISOR')
       OR (NEW.root_cause_id IS DISTINCT FROM OLD.root_cause_id AND app_actor() IS NOT NULL
           AND project_role_of(NEW.project_id, app_actor()) IS DISTINCT FROM 'SUPERVISOR' AND NOT app_is_system()) THEN
      RAISE EXCEPTION 'only a SUPERVISOR can resolve issues and group them under root causes' USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_issues_guard BEFORE INSERT OR UPDATE ON issues FOR EACH ROW EXECUTE FUNCTION trg_issues_guard();
CREATE TRIGGER trg_issues_nodelete BEFORE DELETE ON issues FOR EACH ROW EXECUTE FUNCTION trg_append_only();

CREATE FUNCTION trg_root_causes_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF project_role_of(NEW.project_id, NEW.identified_by) IS DISTINCT FROM 'SUPERVISOR' THEN
    RAISE EXCEPTION 'root causes are managed by a SUPERVISOR of the project' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_root_causes_guard BEFORE INSERT ON root_causes FOR EACH ROW EXECUTE FUNCTION trg_root_causes_guard();

CREATE TABLE issue_evidence (
  issue_id    UUID NOT NULL,
  project_id  UUID NOT NULL,
  document_id UUID NOT NULL,
  notes       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (issue_id, document_id),
  CONSTRAINT fk_ie_issue FOREIGN KEY (project_id, issue_id) REFERENCES issues (project_id, issue_id) ON DELETE CASCADE,
  CONSTRAINT fk_ie_document FOREIGN KEY (project_id, document_id) REFERENCES source_documents (project_id, document_id)
);

-- Institutional memory: lessons, optionally promoted from a resolved issue; may be shared organisation-wide.
CREATE TABLE institutional_memory (
  memory_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id       UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  issue_id         UUID,
  activity_uid     UUID,
  discipline_code  TEXT REFERENCES disciplines(code),
  category_code    TEXT REFERENCES issue_categories(code),
  title            TEXT NOT NULL CHECK (length(btrim(title)) >= 3),
  narrative        TEXT,
  root_cause       TEXT,
  corrective_action TEXT,
  lessons_learned  TEXT NOT NULL CHECK (length(btrim(lessons_learned)) >= 3),
  outcome          TEXT,
  delay_days       NUMERIC(8,2) CHECK (delay_days IS NULL OR delay_days >= 0),
  visibility       TEXT NOT NULL DEFAULT 'PROJECT' CHECK (visibility IN ('PROJECT','ORGANISATION')),
  source           TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL','ISSUE_RESOLUTION','HISTORICAL')),
  recorded_by      UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_mem_issue FOREIGN KEY (project_id, issue_id) REFERENCES issues (project_id, issue_id),
  CONSTRAINT fk_mem_activity FOREIGN KEY (project_id, activity_uid) REFERENCES activities (project_id, activity_uid)
);
CREATE INDEX idx_memory_project ON institutional_memory (project_id, recorded_at DESC);
CREATE INDEX idx_memory_org ON institutional_memory (visibility, category_code) WHERE visibility = 'ORGANISATION';
CREATE FUNCTION trg_memory_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF project_role_of(NEW.project_id, NEW.recorded_by) IS DISTINCT FROM 'SUPERVISOR' THEN
    RAISE EXCEPTION 'institutional memory is managed by a SUPERVISOR of the project' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_memory_guard BEFORE INSERT ON institutional_memory FOR EACH ROW EXECUTE FUNCTION trg_memory_guard();

CREATE TABLE notifications (
  notification_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  recipient_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  notification_type TEXT NOT NULL CHECK (notification_type IN ('CLAIM_DECISION','ISSUE_UPDATE')),
  decision_id       UUID,
  event_id          UUID,
  issue_id          UUID,
  title             TEXT NOT NULL,
  body              TEXT,
  created_by        UUID REFERENCES profiles(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  read_at           TIMESTAMPTZ,
  CONSTRAINT fk_n_decision FOREIGN KEY (project_id, decision_id) REFERENCES planner_decisions (project_id, decision_id),
  CONSTRAINT fk_n_event FOREIGN KEY (project_id, event_id) REFERENCES execution_events (project_id, event_id),
  CONSTRAINT fk_n_issue FOREIGN KEY (project_id, issue_id) REFERENCES issues (project_id, issue_id),
  CONSTRAINT notifications_subject_chk CHECK (
    (notification_type = 'CLAIM_DECISION' AND decision_id IS NOT NULL AND event_id IS NOT NULL)
    OR (notification_type = 'ISSUE_UPDATE' AND issue_id IS NOT NULL))
);
CREATE INDEX idx_notifications_recipient ON notifications (recipient_id, read_at, created_at DESC);
CREATE UNIQUE INDEX uq_notifications_decision_recipient ON notifications (decision_id, recipient_id) WHERE decision_id IS NOT NULL;

-- Tamper-evident audit chain (hashes computed by the application over the stored text, so states stay TEXT).
CREATE TABLE audit_logs (
  log_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id      UUID REFERENCES projects(project_id) ON DELETE RESTRICT,   -- NULL only for platform-level actions
  actor_id        UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  role            TEXT,
  action          TEXT NOT NULL,
  entity_type     TEXT NOT NULL,
  entity_id       TEXT NOT NULL,
  schedule_version_id UUID REFERENCES schedule_versions(version_id) ON DELETE RESTRICT,
  before_state    TEXT,
  after_state     TEXT,
  entity_context  JSONB NOT NULL DEFAULT '{}',
  payload_hash    TEXT NOT NULL,
  previous_hash   TEXT NOT NULL,
  current_hash    TEXT NOT NULL UNIQUE,
  occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_project ON audit_logs (project_id, log_id DESC);
CREATE INDEX idx_audit_entity ON audit_logs (entity_type, entity_id);
CREATE TRIGGER trg_audit_append_only BEFORE UPDATE OR DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION trg_append_only();
