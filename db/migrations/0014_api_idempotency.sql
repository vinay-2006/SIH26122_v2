-- 0014 API idempotency. A client that retries a create request (claim, correction, issue) with the same Idempotency-Key gets the original
-- response instead of a second record. Purely additive: one new backend-only table (RLS on, no client privileges, like every other table).
-- Keys are scoped to the acting user and the operation, expire after 24 hours, and are bound to a hash of the request body, so reusing
-- a key for a different request is refused instead of silently replaying the wrong answer.
CREATE TABLE api_idempotency (
  idempotency_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  user_id         UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  scope           TEXT NOT NULL CHECK (length(scope) BETWEEN 3 AND 80),
  idem_key        TEXT NOT NULL CHECK (idem_key ~ '^[A-Za-z0-9_.:-]{8,128}$'),
  request_hash    TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  state           TEXT NOT NULL DEFAULT 'IN_PROGRESS' CHECK (state IN ('IN_PROGRESS','DONE')),
  response_status INTEGER,
  response_body   JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at    TIMESTAMPTZ,
  CONSTRAINT uq_idempotency_key UNIQUE (user_id, scope, idem_key),
  CONSTRAINT chk_idempotency_done CHECK ((state = 'DONE') = (response_status IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE INDEX idx_idempotency_age ON api_idempotency (created_at);
ALTER TABLE api_idempotency ENABLE ROW LEVEL SECURITY;

-- Stored evidence is immutable: only the extraction outcome (and the one-time assignment of the storage key) may change after upload.
CREATE FUNCTION trg_documents_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.project_id IS DISTINCT FROM OLD.project_id OR NEW.kind IS DISTINCT FROM OLD.kind OR NEW.file_name IS DISTINCT FROM OLD.file_name
     OR NEW.mime_type IS DISTINCT FROM OLD.mime_type OR NEW.sha256 IS DISTINCT FROM OLD.sha256 OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
     OR NEW.uploaded_by IS DISTINCT FROM OLD.uploaded_by OR NEW.uploaded_at IS DISTINCT FROM OLD.uploaded_at
     OR NEW.storage_backend IS DISTINCT FROM OLD.storage_backend
     OR (OLD.storage_path IS NOT NULL AND NEW.storage_path IS DISTINCT FROM OLD.storage_path) THEN
    RAISE EXCEPTION 'an uploaded document is immutable (only its extraction outcome may be recorded)' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_documents_immutable BEFORE UPDATE ON source_documents FOR EACH ROW EXECUTE FUNCTION trg_documents_immutable();
