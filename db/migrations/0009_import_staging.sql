-- 0009 import staging. An import is first STAGED (parsed + validated, nothing in the schedule tables), reviewed/mapped/reconciled by the
-- PM, then BUILT atomically into a schedule version. Also lets a PM discard a VALIDATED-but-unlocked version.

ALTER TABLE schedule_imports ADD COLUMN file_name      TEXT;
ALTER TABLE schedule_imports ADD COLUMN baseline_name  TEXT;
ALTER TABLE schedule_imports ADD COLUMN parsed_payload JSONB;                       -- normalized parse result (staging)
ALTER TABLE schedule_imports ADD COLUMN decisions      JSONB NOT NULL DEFAULT '{}'; -- PM mapping / reconciliation decisions
ALTER TABLE schedule_imports ADD COLUMN built_at       TIMESTAMPTZ;
ALTER TABLE schedule_imports DROP CONSTRAINT schedule_imports_status_check;
ALTER TABLE schedule_imports ADD CONSTRAINT schedule_imports_status_check CHECK (status IN ('PARSED','BUILT','DISCARDED'));
ALTER TABLE schedule_imports ADD CONSTRAINT uq_import_document UNIQUE (project_id, source_document_id);

-- Assignments are identities owned by their activity: they go away only together with a discarded draft's own activities.
ALTER TABLE assignments DROP CONSTRAINT fk_assignment_activity;
ALTER TABLE assignments ADD CONSTRAINT fk_assignment_activity FOREIGN KEY (project_id, activity_uid)
  REFERENCES activities (project_id, activity_uid) ON DELETE CASCADE;

CREATE OR REPLACE FUNCTION trg_versions_lifecycle_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status NOT IN ('DRAFT','VALIDATED') OR OLD.locked_at IS NOT NULL THEN
      RAISE EXCEPTION 'schedule version % is % and cannot be deleted (history is preserved)', OLD.version_id, OLD.status
        USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'DRAFT' OR NEW.locked_at IS NOT NULL THEN
      RAISE EXCEPTION 'a schedule version must be created as an unlocked DRAFT' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
       (OLD.status = 'DRAFT'      AND NEW.status = 'VALIDATED') OR
       (OLD.status = 'VALIDATED'  AND NEW.status IN ('ACTIVE')) OR
       (OLD.status = 'ACTIVE'     AND NEW.status = 'SUPERSEDED') OR
       (OLD.status = 'SUPERSEDED' AND NEW.status = 'ACTIVE')) THEN
    RAISE EXCEPTION 'illegal schedule version transition % -> %', OLD.status, NEW.status USING ERRCODE = '23514';
  END IF;
  IF OLD.locked_at IS NOT NULL AND
     (to_jsonb(NEW) - 'status' - 'activated_at' - 'activated_by' - 'updated_at')
     IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'activated_at' - 'activated_by' - 'updated_at') THEN
    RAISE EXCEPTION 'schedule version % is locked: baseline fields are immutable', OLD.version_id USING ERRCODE = '23514';
  END IF;
  IF NEW.status = 'ACTIVE' AND NEW.locked_at IS NULL THEN
    RAISE EXCEPTION 'a version must be locked when it is activated' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
