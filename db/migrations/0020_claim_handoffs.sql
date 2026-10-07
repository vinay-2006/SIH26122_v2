-- 0020 Time Agent hand-off: a Supervisor never files an execution claim (only a Site Engineer does), so the Time Agent's drafted claim is HANDED OFF to the project's Site
-- Engineers as a pre-filled draft. The draft is not a claim: it has no status in the claim workflow, no ledger effect and no review. The engineer reads it on the Intake page,
-- files it through the ordinary intake (extraction, matching, validation) or dismisses it. Additive; nothing existing is changed.

CREATE TABLE claim_handoffs (
  handoff_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  created_by      UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  to_user_id      UUID REFERENCES profiles(id) ON DELETE RESTRICT,           -- NULL: any Site Engineer of the project
  draft           JSONB NOT NULL,
  note            TEXT,
  status          TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','FILED','DISMISSED')),
  filed_event_id  UUID,
  closed_by       UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  closed_at       TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_handoff_event FOREIGN KEY (project_id, filed_event_id) REFERENCES execution_events (project_id, event_id),
  CONSTRAINT handoff_text_chk CHECK (length(btrim(coalesce(draft ->> 'rawText', ''))) >= 3),
  CONSTRAINT handoff_closed_chk CHECK ((status = 'OPEN') = (closed_by IS NULL AND closed_at IS NULL)),
  CONSTRAINT handoff_filed_chk CHECK ((status = 'FILED') = (filed_event_id IS NOT NULL))
);
CREATE INDEX idx_handoff_open ON claim_handoffs (project_id, status, created_at DESC);
CREATE TRIGGER trg_handoff_nodelete BEFORE DELETE ON claim_handoffs FOR EACH ROW EXECUTE FUNCTION trg_append_only();

CREATE FUNCTION trg_handoff_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_role text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF project_role_of(NEW.project_id, NEW.created_by) IS DISTINCT FROM 'SUPERVISOR' THEN
      RAISE EXCEPTION 'a claim draft is handed off by a SUPERVISOR of the project' USING ERRCODE = '42501';
    END IF;
    IF NEW.to_user_id IS NOT NULL AND project_role_of(NEW.project_id, NEW.to_user_id) IS DISTINCT FROM 'SITE_ENGINEER' THEN
      RAISE EXCEPTION 'a claim draft is handed off to a SITE_ENGINEER of the project' USING ERRCODE = '42501';
    END IF;
    IF NEW.status <> 'OPEN' THEN RAISE EXCEPTION 'a hand-off starts OPEN' USING ERRCODE = '23514'; END IF;
    RETURN NEW;
  END IF;
  IF NEW.project_id <> OLD.project_id OR NEW.created_by <> OLD.created_by OR NEW.to_user_id IS DISTINCT FROM OLD.to_user_id OR NEW.draft <> OLD.draft
     OR NEW.created_at <> OLD.created_at OR NEW.note IS DISTINCT FROM OLD.note THEN
    RAISE EXCEPTION 'a hand-off draft is immutable' USING ERRCODE = '23514';
  END IF;
  IF OLD.status <> 'OPEN' THEN RAISE EXCEPTION 'a closed hand-off is never edited' USING ERRCODE = '23514'; END IF;
  IF NOT app_is_system() THEN
    v_role := project_role_of(NEW.project_id, NEW.closed_by);
    IF NEW.status = 'FILED' AND v_role IS DISTINCT FROM 'SITE_ENGINEER' THEN
      RAISE EXCEPTION 'only a SITE_ENGINEER files a hand-off' USING ERRCODE = '42501';
    END IF;
    IF NEW.status = 'DISMISSED' AND v_role NOT IN ('SITE_ENGINEER','SUPERVISOR') THEN
      RAISE EXCEPTION 'a hand-off is dismissed by a SITE_ENGINEER or SUPERVISOR' USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_handoff_guard BEFORE INSERT OR UPDATE ON claim_handoffs FOR EACH ROW EXECUTE FUNCTION trg_handoff_guard();

ALTER TABLE claim_handoffs ENABLE ROW LEVEL SECURITY;
CREATE POLICY handoffs_select ON claim_handoffs FOR SELECT TO authenticated USING (
  created_by = auth.uid() OR (project_role_of(project_id, auth.uid()) = 'SITE_ENGINEER' AND (to_user_id IS NULL OR to_user_id = auth.uid())));
GRANT SELECT ON claim_handoffs TO authenticated;
