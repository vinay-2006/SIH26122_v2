-- 0018 Governed reopen of a completed activity (decision D7), as an APPEND-ONLY correction.
--
-- Nothing is deleted or edited. A reopen is a request (Site Engineer or Supervisor, with a reason) that a Supervisor approves or rejects (with notes). Approval opens REWORK on the
-- activity: its approved progress stays exactly as it was, and the next claim a Supervisor approves for it is a FRESH review whose ledger entries SUPERSEDE the latest ones (the
-- compensating entries the ledger design already provides for: the old entries remain, the new ones become the head). Doing so closes the reopen. Without an APPROVED reopen the
-- ledger refuses any superseding entry (trigger below), so a completed activity can only be changed through this path.

CREATE TABLE activity_reopens (
  reopen_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id          UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  activity_uid        UUID NOT NULL,
  status              TEXT NOT NULL DEFAULT 'REQUESTED' CHECK (status IN ('REQUESTED','APPROVED','REJECTED','CLOSED')),
  reason_code         TEXT NOT NULL CHECK (reason_code IN ('INCORRECT_COMPLETION','CONTRADICTORY_FIELD_REPORT','QUALITY_FAILURE','QUANTITY_CORRECTION','DATE_CORRECTION','SUPERVISOR_CORRECTION','OTHER')),
  justification       TEXT NOT NULL CHECK (length(btrim(justification)) >= 3),
  evidence_event_ids  UUID[] NOT NULL DEFAULT '{}',
  completed_snapshot  JSONB NOT NULL DEFAULT '{}',                  -- the approved progress at the time of the request (for the record)
  requested_by        UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  requested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_by          UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  decided_at          TIMESTAMPTZ,
  decision_notes      TEXT,
  rework_instructions TEXT,
  closed_at           TIMESTAMPTZ,
  closing_decision_id UUID,
  CONSTRAINT fk_reopen_activity FOREIGN KEY (project_id, activity_uid) REFERENCES activities (project_id, activity_uid),
  CONSTRAINT reopen_decided_chk CHECK ((status IN ('APPROVED','REJECTED','CLOSED')) = (decided_by IS NOT NULL AND decided_at IS NOT NULL AND length(btrim(coalesce(decision_notes, ''))) >= 3)),
  CONSTRAINT reopen_closed_chk CHECK ((status = 'CLOSED') = (closed_at IS NOT NULL AND closing_decision_id IS NOT NULL))
);
-- at most ONE open reopen (waiting for a decision, or in rework) per activity
CREATE UNIQUE INDEX uq_reopen_open ON activity_reopens (project_id, activity_uid) WHERE status IN ('REQUESTED','APPROVED');
CREATE INDEX idx_reopen_activity ON activity_reopens (project_id, activity_uid, requested_at DESC);
CREATE TRIGGER trg_reopen_nodelete BEFORE DELETE ON activity_reopens FOR EACH ROW EXECUTE FUNCTION trg_append_only();

CREATE FUNCTION trg_reopen_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_actor UUID := app_actor(); v_role text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    v_role := project_role_of(NEW.project_id, NEW.requested_by);
    IF v_role IS NULL OR v_role NOT IN ('SITE_ENGINEER','SUPERVISOR') THEN
      RAISE EXCEPTION 'a reopen can be requested by a SITE_ENGINEER or SUPERVISOR of the project (project managers never)' USING ERRCODE = '42501';
    END IF;
    IF NEW.status <> 'REQUESTED' THEN RAISE EXCEPTION 'a reopen starts REQUESTED' USING ERRCODE = '23514'; END IF;
    RETURN NEW;
  END IF;
  IF NEW.project_id <> OLD.project_id OR NEW.activity_uid <> OLD.activity_uid OR NEW.requested_by <> OLD.requested_by OR NEW.requested_at <> OLD.requested_at
     OR NEW.reason_code <> OLD.reason_code OR NEW.justification <> OLD.justification OR NEW.completed_snapshot <> OLD.completed_snapshot THEN
    RAISE EXCEPTION 'what was asked for in a reopen request is immutable' USING ERRCODE = '23514';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF NOT ((OLD.status = 'REQUESTED' AND NEW.status IN ('APPROVED','REJECTED')) OR (OLD.status = 'APPROVED' AND NEW.status = 'CLOSED')) THEN
      RAISE EXCEPTION 'illegal reopen transition % -> %', OLD.status, NEW.status USING ERRCODE = '23514';
    END IF;
    IF NOT app_is_system() AND project_role_of(NEW.project_id, v_actor) IS DISTINCT FROM 'SUPERVISOR' THEN
      RAISE EXCEPTION 'only a SUPERVISOR decides or closes a reopen' USING ERRCODE = '42501';
    END IF;
  ELSIF OLD.status IN ('REJECTED','CLOSED') OR to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
    RAISE EXCEPTION 'a decided reopen is never edited' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_reopen_guard BEFORE INSERT OR UPDATE ON activity_reopens FOR EACH ROW EXECUTE FUNCTION trg_reopen_guard();

-- ---- the ledger only accepts a superseding (compensating) entry for an activity whose reopen is APPROVED
CREATE FUNCTION trg_ledger_supersede_needs_reopen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF app_is_system() THEN RETURN NEW; END IF;          -- migrations / seeds / data repair only; the API never runs in system mode
  IF NEW.supersedes_entry_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM activity_reopens r WHERE r.project_id = NEW.project_id AND r.activity_uid = NEW.activity_uid AND r.status = 'APPROVED') THEN
    RAISE EXCEPTION 'REOPEN_REQUIRED: approved progress can only be superseded while a governed reopen of the activity is APPROVED' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_aap_supersede_needs_reopen BEFORE INSERT ON approved_activity_progress FOR EACH ROW EXECUTE FUNCTION trg_ledger_supersede_needs_reopen();
CREATE TRIGGER trg_arp_supersede_needs_reopen BEFORE INSERT ON approved_resource_progress FOR EACH ROW EXECUTE FUNCTION trg_ledger_supersede_needs_reopen();

ALTER TABLE activity_reopens ENABLE ROW LEVEL SECURITY;
CREATE POLICY reopens_select ON activity_reopens FOR SELECT TO authenticated USING (is_project_member(project_id));
GRANT SELECT ON activity_reopens TO authenticated;
