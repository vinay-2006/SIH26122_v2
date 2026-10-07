-- 0003 documents: upload batches and source documents (reports, evidence, schedule files).

CREATE TABLE upload_batches (
  batch_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id   UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  uploaded_by  UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  status       TEXT NOT NULL DEFAULT 'PROCESSING' CHECK (status IN ('PROCESSING','COMPLETED','PARTIAL','FAILED')),
  file_count   INTEGER NOT NULL DEFAULT 0 CHECK (file_count >= 0),
  claim_count  INTEGER NOT NULL DEFAULT 0 CHECK (claim_count >= 0),
  merged_count INTEGER NOT NULL DEFAULT 0 CHECK (merged_count >= 0),
  notes        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  UNIQUE (project_id, batch_id)
);
CREATE INDEX idx_upload_batches_project ON upload_batches (project_id, created_at DESC);

CREATE TABLE source_documents (
  document_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  batch_id          UUID,
  kind              TEXT NOT NULL CHECK (kind IN
                      ('DAILY_REPORT','SITE_REPORT','PHOTO','EVIDENCE','ISSUE_REPORT','SCHEDULE_FILE')),
  file_name         TEXT NOT NULL CHECK (length(btrim(file_name)) > 0),
  mime_type         TEXT,
  storage_path      TEXT,
  sha256            TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes        BIGINT CHECK (size_bytes IS NULL OR size_bytes >= 0),
  uploaded_by       UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  extraction_status TEXT CHECK (extraction_status IN ('PENDING','EXTRACTED','NO_CLAIMS','FAILED')),
  extraction_method TEXT,
  extraction_error  TEXT,
  claims_extracted  INTEGER NOT NULL DEFAULT 0 CHECK (claims_extracted >= 0),
  is_synthetic      BOOLEAN NOT NULL DEFAULT FALSE,   -- demo library documents are flagged, never passed off as genuine
  CONSTRAINT fk_documents_batch FOREIGN KEY (project_id, batch_id) REFERENCES upload_batches (project_id, batch_id),
  CONSTRAINT uq_documents_content UNIQUE (project_id, kind, sha256),
  CONSTRAINT uq_documents_project_doc UNIQUE (project_id, document_id)
);
CREATE INDEX idx_documents_batch ON source_documents (batch_id);

-- Who may upload what: schedule files ONLY by an active PROJECT_MANAGER; reports/evidence by SITE_ENGINEER
-- (SUPERVISOR may attach issue evidence). Checked on the row's uploader, independent of the connection used.
CREATE FUNCTION trg_documents_uploader_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_role text := project_role_of(NEW.project_id, NEW.uploaded_by);
BEGIN
  IF NEW.kind = 'SCHEDULE_FILE' THEN
    IF v_role IS DISTINCT FROM 'PROJECT_MANAGER' THEN
      RAISE EXCEPTION 'schedule files can only be uploaded by a PROJECT_MANAGER (uploader role: %)', coalesce(v_role,'none')
        USING ERRCODE = '42501';
    END IF;
  ELSIF NEW.kind IN ('ISSUE_REPORT','EVIDENCE') THEN
    IF v_role IS NULL OR v_role NOT IN ('SITE_ENGINEER','SUPERVISOR') THEN
      RAISE EXCEPTION '% documents can only be uploaded by a SITE_ENGINEER or SUPERVISOR (uploader role: %)', NEW.kind, coalesce(v_role,'none')
        USING ERRCODE = '42501';
    END IF;
  ELSE
    IF v_role IS DISTINCT FROM 'SITE_ENGINEER' THEN
      RAISE EXCEPTION '% documents can only be uploaded by a SITE_ENGINEER (uploader role: %)', NEW.kind, coalesce(v_role,'none')
        USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_documents_uploader BEFORE INSERT ON source_documents FOR EACH ROW EXECUTE FUNCTION trg_documents_uploader_guard();
