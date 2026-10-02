-- 0004 schedule model. Logical PROJECT_MASTER / SCHEDULE_WBS / BASELINE_ACTIVITIES / BASELINE_RESOURCES, versioned.
--   * PROJECT_MASTER  = projects (identity) + schedule_versions (baseline_name, data_date, planned dates, lock)  [view in 0007]
--   * activity identity is activities.activity_uid (stable); external P6/MSP ids are per-version attributes.
--   * assignment identity is assignments.assignment_uid (stable) so quantity history survives revisions.
-- Rows of a locked/non-DRAFT version are immutable (trigger). All writes need a PROJECT_MANAGER actor (tripwire).

CREATE TABLE schedule_imports (
  import_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id         UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  source_document_id UUID NOT NULL,
  format             TEXT NOT NULL CHECK (format IN ('XER','CSV','MSPDI','MPP')),
  status             TEXT NOT NULL DEFAULT 'PARSED' CHECK (status IN ('PARSED','INVALID','DISCARDED')),
  validation_report  JSONB NOT NULL DEFAULT '{}',
  error_count        INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
  warning_count      INTEGER NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
  uploaded_by        UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_import_document FOREIGN KEY (project_id, source_document_id) REFERENCES source_documents (project_id, document_id),
  CONSTRAINT uq_import_project UNIQUE (project_id, import_id)
);
CREATE TRIGGER trg_imports_pm BEFORE INSERT OR UPDATE OR DELETE ON schedule_imports FOR EACH ROW EXECUTE FUNCTION trg_require_pm();

CREATE TABLE schedule_versions (
  version_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id          UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  version_no          INTEGER NOT NULL CHECK (version_no >= 1),
  kind                TEXT NOT NULL CHECK (kind IN ('BASELINE','REVISION')),
  status              TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','VALIDATED','ACTIVE','SUPERSEDED')),
  baseline_name       TEXT NOT NULL CHECK (length(btrim(baseline_name)) >= 2),
  label               TEXT,
  data_date           DATE NOT NULL,
  planned_start_date  DATE NOT NULL,
  planned_finish_date DATE NOT NULL,
  import_id           UUID,
  parent_version_id   UUID,
  locked_at           TIMESTAMPTZ,
  locked_by           UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  activated_at        TIMESTAMPTZ,
  activated_by        UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  created_by          UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_version_no UNIQUE (project_id, version_no),
  CONSTRAINT uq_version_project UNIQUE (project_id, version_id),
  CONSTRAINT fk_version_import FOREIGN KEY (project_id, import_id) REFERENCES schedule_imports (project_id, import_id),
  CONSTRAINT fk_version_parent FOREIGN KEY (project_id, parent_version_id) REFERENCES schedule_versions (project_id, version_id),
  CHECK (planned_finish_date >= planned_start_date),
  CHECK ((kind = 'BASELINE') = (parent_version_id IS NULL)),
  CHECK (status NOT IN ('ACTIVE','SUPERSEDED') OR locked_at IS NOT NULL),
  CHECK ((locked_at IS NULL) = (locked_by IS NULL))
);
CREATE UNIQUE INDEX uq_one_active_version ON schedule_versions (project_id) WHERE status = 'ACTIVE';
CREATE UNIQUE INDEX uq_one_baseline ON schedule_versions (project_id) WHERE kind = 'BASELINE';
CREATE INDEX idx_versions_project_status ON schedule_versions (project_id, status);
CREATE TRIGGER trg_versions_touch BEFORE UPDATE ON schedule_versions FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_versions_pm BEFORE INSERT OR UPDATE OR DELETE ON schedule_versions FOR EACH ROW EXECUTE FUNCTION trg_require_pm();

CREATE FUNCTION trg_versions_lifecycle_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'DRAFT' OR OLD.locked_at IS NOT NULL THEN
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
  -- UPDATE: legal status transitions only
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
       (OLD.status = 'DRAFT'      AND NEW.status = 'VALIDATED') OR
       (OLD.status = 'VALIDATED'  AND NEW.status IN ('ACTIVE')) OR
       (OLD.status = 'ACTIVE'     AND NEW.status = 'SUPERSEDED') OR
       (OLD.status = 'SUPERSEDED' AND NEW.status = 'ACTIVE')) THEN
    RAISE EXCEPTION 'illegal schedule version transition % -> %', OLD.status, NEW.status USING ERRCODE = '23514';
  END IF;
  -- Once locked, only status / activation bookkeeping may change.
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
CREATE TRIGGER trg_versions_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON schedule_versions
  FOR EACH ROW EXECUTE FUNCTION trg_versions_lifecycle_guard();

-- Child rows may only change while their version is an unlocked DRAFT (version already deleted => cascade, allow).
CREATE FUNCTION trg_version_mutable() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_version UUID; v_status text; v_locked timestamptz;
BEGIN
  v_version := CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
  SELECT status, locked_at INTO v_status, v_locked FROM schedule_versions WHERE version_id = v_version;
  IF FOUND AND (v_status <> 'DRAFT' OR v_locked IS NOT NULL) THEN
    RAISE EXCEPTION '% % rejected: schedule version is % (locked=%): rows are immutable',
      TG_TABLE_NAME, TG_OP, v_status, v_locked IS NOT NULL USING ERRCODE = '23514';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

-- ------------------------------------------------------------------ stable identities
CREATE TABLE activities (
  activity_uid          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id            UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  first_version_id      UUID NOT NULL REFERENCES schedule_versions(version_id) ON DELETE CASCADE,
  retired_in_version_id UUID REFERENCES schedule_versions(version_id) ON DELETE SET NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_activity_project UNIQUE (project_id, activity_uid)
);
CREATE TRIGGER trg_activities_pm BEFORE INSERT OR UPDATE OR DELETE ON activities FOR EACH ROW EXECUTE FUNCTION trg_require_pm();

CREATE TABLE assignments (
  assignment_uid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id     UUID NOT NULL,
  activity_uid   UUID NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_assignment_activity FOREIGN KEY (project_id, activity_uid) REFERENCES activities (project_id, activity_uid),
  CONSTRAINT uq_assignment_identity UNIQUE (project_id, assignment_uid, activity_uid)
);
CREATE TRIGGER trg_assignments_pm BEFORE INSERT OR UPDATE OR DELETE ON assignments FOR EACH ROW EXECUTE FUNCTION trg_require_pm();

-- ------------------------------------------------------------------ SCHEDULE_WBS
CREATE TABLE schedule_wbs (
  wbs_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID NOT NULL,
  version_id    UUID NOT NULL,
  wbs_uid       UUID NOT NULL DEFAULT gen_random_uuid(),
  parent_wbs_id UUID,
  wbs_code      TEXT NOT NULL CHECK (length(btrim(wbs_code)) > 0 AND wbs_code !~ '/'),
  wbs_name      TEXT NOT NULL CHECK (length(btrim(wbs_name)) > 0),
  node_type     TEXT NOT NULL DEFAULT 'AREA' CHECK (node_type IN ('PROJECT','STAGE','AREA','SUB_ASSET','PACKAGE')),
  sequence      INTEGER NOT NULL DEFAULT 1,
  level         SMALLINT NOT NULL DEFAULT 0,
  wbs_path      TEXT NOT NULL DEFAULT '',          -- '/ROOT/STAGE/AREA/' maintained by trigger; prefix match = descendants
  CONSTRAINT fk_wbs_version FOREIGN KEY (project_id, version_id) REFERENCES schedule_versions (project_id, version_id) ON DELETE CASCADE,
  CONSTRAINT uq_wbs_version_id UNIQUE (version_id, wbs_id),
  CONSTRAINT uq_wbs_type UNIQUE (wbs_id, node_type),
  CONSTRAINT uq_wbs_code UNIQUE (version_id, wbs_code),
  CONSTRAINT uq_wbs_uid UNIQUE (version_id, wbs_uid),
  CONSTRAINT uq_wbs_path UNIQUE (version_id, wbs_path),
  CONSTRAINT fk_wbs_parent FOREIGN KEY (version_id, parent_wbs_id) REFERENCES schedule_wbs (version_id, wbs_id) ON DELETE CASCADE,
  CHECK ((parent_wbs_id IS NULL) = (node_type = 'PROJECT')),
  CHECK (parent_wbs_id IS NULL OR parent_wbs_id <> wbs_id)
);
CREATE UNIQUE INDEX uq_wbs_single_root ON schedule_wbs (version_id) WHERE parent_wbs_id IS NULL;
CREATE INDEX idx_wbs_parent ON schedule_wbs (version_id, parent_wbs_id);
CREATE INDEX idx_wbs_path ON schedule_wbs (version_id, wbs_path text_pattern_ops);

CREATE FUNCTION trg_wbs_path() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_ppath text; v_plevel smallint;
BEGIN
  IF TG_OP = 'UPDATE' AND (NEW.wbs_code <> OLD.wbs_code OR NEW.parent_wbs_id IS DISTINCT FROM OLD.parent_wbs_id
                           OR NEW.version_id <> OLD.version_id) THEN
    RAISE EXCEPTION 'WBS code/parent/version are immutable; delete and re-insert the node' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.parent_wbs_id IS NULL THEN
      NEW.wbs_path := '/' || NEW.wbs_code || '/'; NEW.level := 0;
    ELSE
      SELECT wbs_path, level INTO v_ppath, v_plevel FROM schedule_wbs
       WHERE wbs_id = NEW.parent_wbs_id AND version_id = NEW.version_id;
      IF NOT FOUND THEN RAISE EXCEPTION 'parent WBS node not found in this version' USING ERRCODE = '23503'; END IF;
      NEW.wbs_path := v_ppath || NEW.wbs_code || '/'; NEW.level := v_plevel + 1;
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_wbs_path BEFORE INSERT OR UPDATE ON schedule_wbs FOR EACH ROW EXECUTE FUNCTION trg_wbs_path();
CREATE TRIGGER trg_wbs_mutable BEFORE INSERT OR UPDATE OR DELETE ON schedule_wbs FOR EACH ROW EXECUTE FUNCTION trg_version_mutable();
CREATE TRIGGER trg_wbs_pm BEFORE INSERT OR UPDATE OR DELETE ON schedule_wbs FOR EACH ROW EXECUTE FUNCTION trg_require_pm();

-- Stage-only attributes live on STAGE nodes (no separate, competing stages table).
CREATE TABLE wbs_stage_rules (
  wbs_id                    UUID PRIMARY KEY,
  node_type                 TEXT NOT NULL DEFAULT 'STAGE' CHECK (node_type = 'STAGE'),
  project_id                UUID NOT NULL,
  version_id                UUID NOT NULL,
  weight_pct                NUMERIC(6,3) CHECK (weight_pct IS NULL OR weight_pct BETWEEN 0 AND 100),
  gating_predecessor_wbs_id UUID,
  completion_rule           JSONB NOT NULL DEFAULT '{}',
  CONSTRAINT fk_stage_rule_node FOREIGN KEY (wbs_id, node_type) REFERENCES schedule_wbs (wbs_id, node_type) ON DELETE CASCADE,
  CONSTRAINT fk_stage_rule_version FOREIGN KEY (project_id, version_id) REFERENCES schedule_versions (project_id, version_id) ON DELETE CASCADE,
  CONSTRAINT fk_stage_rule_gate FOREIGN KEY (version_id, gating_predecessor_wbs_id) REFERENCES schedule_wbs (version_id, wbs_id) ON DELETE SET NULL (gating_predecessor_wbs_id),
  CHECK (gating_predecessor_wbs_id IS NULL OR gating_predecessor_wbs_id <> wbs_id)
);
CREATE TRIGGER trg_stage_rules_mutable BEFORE INSERT OR UPDATE OR DELETE ON wbs_stage_rules FOR EACH ROW EXECUTE FUNCTION trg_version_mutable();
CREATE TRIGGER trg_stage_rules_pm BEFORE INSERT OR UPDATE OR DELETE ON wbs_stage_rules FOR EACH ROW EXECUTE FUNCTION trg_require_pm();

-- ------------------------------------------------------------------ BASELINE_ACTIVITIES
CREATE TABLE baseline_activities (
  activity_row_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id           UUID NOT NULL,
  version_id           UUID NOT NULL,
  activity_uid         UUID NOT NULL,
  external_activity_id TEXT NOT NULL CHECK (length(btrim(external_activity_id)) > 0),
  wbs_id               UUID NOT NULL,
  activity_name        TEXT NOT NULL CHECK (length(btrim(activity_name)) > 0),
  description          TEXT,
  discipline_code      TEXT NOT NULL REFERENCES disciplines(code),
  discipline_source    TEXT,                               -- original label when it was mapped through an alias / OTHER
  activity_type        TEXT NOT NULL DEFAULT 'TASK' CHECK (activity_type IN ('TASK','MILESTONE','LOE')),
  location             TEXT,
  baseline_duration    NUMERIC(10,2) NOT NULL CHECK (baseline_duration >= 0),   -- working days (project calendar)
  baseline_start       DATE NOT NULL,
  baseline_finish      DATE NOT NULL,
  total_float          NUMERIC(10,2),                                           -- working days; NULL = not computed
  is_critical          BOOLEAN GENERATED ALWAYS AS (total_float IS NOT NULL AND total_float <= 0) STORED,
  sequence             INTEGER NOT NULL DEFAULT 1,
  CONSTRAINT fk_ba_version FOREIGN KEY (project_id, version_id) REFERENCES schedule_versions (project_id, version_id) ON DELETE CASCADE,
  CONSTRAINT fk_ba_identity FOREIGN KEY (project_id, activity_uid) REFERENCES activities (project_id, activity_uid),
  CONSTRAINT fk_ba_wbs FOREIGN KEY (version_id, wbs_id) REFERENCES schedule_wbs (version_id, wbs_id),
  CONSTRAINT uq_ba_external UNIQUE (version_id, external_activity_id),       -- external ids are unique per VERSION only
  CONSTRAINT uq_ba_uid UNIQUE (version_id, activity_uid),
  CONSTRAINT uq_ba_row_identity UNIQUE (activity_row_id, version_id, project_id, activity_uid),
  CHECK (baseline_finish >= baseline_start),
  CHECK (activity_type <> 'MILESTONE' OR baseline_duration = 0)
);
CREATE INDEX idx_ba_wbs ON baseline_activities (version_id, wbs_id);
CREATE INDEX idx_ba_discipline ON baseline_activities (version_id, discipline_code);
CREATE INDEX idx_ba_uid ON baseline_activities (activity_uid);
CREATE TRIGGER trg_ba_mutable BEFORE INSERT OR UPDATE OR DELETE ON baseline_activities FOR EACH ROW EXECUTE FUNCTION trg_version_mutable();
CREATE TRIGGER trg_ba_pm BEFORE INSERT OR UPDATE OR DELETE ON baseline_activities FOR EACH ROW EXECUTE FUNCTION trg_require_pm();

CREATE TABLE schedule_dependencies (
  dependency_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        UUID NOT NULL,
  version_id        UUID NOT NULL,
  predecessor_uid   UUID NOT NULL,
  successor_uid     UUID NOT NULL,
  relationship_type TEXT NOT NULL DEFAULT 'FS' CHECK (relationship_type IN ('FS','SS','FF','SF')),
  lag_days          NUMERIC(10,2) NOT NULL DEFAULT 0,
  CONSTRAINT fk_dep_version FOREIGN KEY (project_id, version_id) REFERENCES schedule_versions (project_id, version_id) ON DELETE CASCADE,
  CONSTRAINT fk_dep_pred FOREIGN KEY (version_id, predecessor_uid) REFERENCES baseline_activities (version_id, activity_uid) ON DELETE CASCADE,
  CONSTRAINT fk_dep_succ FOREIGN KEY (version_id, successor_uid) REFERENCES baseline_activities (version_id, activity_uid) ON DELETE CASCADE,
  CONSTRAINT uq_dep UNIQUE (version_id, predecessor_uid, successor_uid, relationship_type),
  CHECK (predecessor_uid <> successor_uid)
);
CREATE INDEX idx_dep_succ ON schedule_dependencies (version_id, successor_uid);
CREATE TRIGGER trg_dep_mutable BEFORE INSERT OR UPDATE OR DELETE ON schedule_dependencies FOR EACH ROW EXECUTE FUNCTION trg_version_mutable();
CREATE TRIGGER trg_dep_pm BEFORE INSERT OR UPDATE OR DELETE ON schedule_dependencies FOR EACH ROW EXECUTE FUNCTION trg_require_pm();

-- ------------------------------------------------------------------ BASELINE_RESOURCES
CREATE TABLE project_resources (
  resource_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id     UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  resource_code  TEXT NOT NULL CHECK (resource_code ~ '^[A-Z0-9_]+$'),
  resource_name  TEXT NOT NULL,
  resource_class TEXT NOT NULL CHECK (resource_class IN ('MATERIAL','LABOR','EQUIPMENT','OTHER')),
  default_uom    TEXT NOT NULL REFERENCES units_of_measure(code),
  CONSTRAINT uq_resource_code UNIQUE (project_id, resource_code),
  CONSTRAINT uq_resource_project UNIQUE (project_id, resource_id)
);
CREATE TRIGGER trg_resources_pm BEFORE INSERT OR UPDATE OR DELETE ON project_resources FOR EACH ROW EXECUTE FUNCTION trg_require_pm();

CREATE TABLE baseline_resources (
  assignment_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  assignment_uid   UUID NOT NULL,
  project_id       UUID NOT NULL,
  version_id       UUID NOT NULL,
  activity_row_id  UUID NOT NULL,
  activity_uid     UUID NOT NULL,
  resource_id      UUID NOT NULL,
  baseline_qty     NUMERIC(18,3) NOT NULL CHECK (baseline_qty > 0),
  unit_of_measure  TEXT NOT NULL REFERENCES units_of_measure(code),
  measures_progress BOOLEAN NOT NULL DEFAULT FALSE,     -- only physical-output assignments drive physical progress
  progress_weight  NUMERIC(8,4) NOT NULL DEFAULT 1 CHECK (progress_weight > 0),
  CONSTRAINT fk_br_activity FOREIGN KEY (activity_row_id, version_id, project_id, activity_uid)
    REFERENCES baseline_activities (activity_row_id, version_id, project_id, activity_uid) ON DELETE CASCADE,
  CONSTRAINT fk_br_identity FOREIGN KEY (project_id, assignment_uid, activity_uid)
    REFERENCES assignments (project_id, assignment_uid, activity_uid),
  CONSTRAINT fk_br_resource FOREIGN KEY (project_id, resource_id) REFERENCES project_resources (project_id, resource_id),
  CONSTRAINT uq_br_activity_resource UNIQUE (activity_row_id, resource_id),
  CONSTRAINT uq_br_version_assignment UNIQUE (version_id, assignment_uid)
);
CREATE INDEX idx_br_activity ON baseline_resources (version_id, activity_uid);
CREATE INDEX idx_br_assignment ON baseline_resources (assignment_uid);

-- Effort resources (labour/equipment hours) are consumption denominators, not physical output; a unit must also fit its resource.
CREATE FUNCTION trg_assignment_semantics() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_class text; v_dim_res text; v_dim_new text;
BEGIN
  SELECT r.resource_class, u.dimension INTO v_class, v_dim_res
    FROM project_resources r JOIN units_of_measure u ON u.code = r.default_uom
   WHERE r.project_id = NEW.project_id AND r.resource_id = NEW.resource_id;
  SELECT dimension INTO v_dim_new FROM units_of_measure WHERE code = NEW.unit_of_measure;
  IF v_dim_res IS DISTINCT FROM v_dim_new THEN
    RAISE EXCEPTION 'unit % (%) is incompatible with the resource unit dimension %', NEW.unit_of_measure, v_dim_new, v_dim_res
      USING ERRCODE = '23514';
  END IF;
  IF NEW.measures_progress AND v_class IN ('LABOR','EQUIPMENT') THEN
    RAISE EXCEPTION 'a % resource cannot measure physical progress (it is a consumption quantity)', v_class USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_br_semantics BEFORE INSERT OR UPDATE ON baseline_resources FOR EACH ROW EXECUTE FUNCTION trg_assignment_semantics();
CREATE TRIGGER trg_br_mutable BEFORE INSERT OR UPDATE OR DELETE ON baseline_resources FOR EACH ROW EXECUTE FUNCTION trg_version_mutable();
CREATE TRIGGER trg_br_pm BEFORE INSERT OR UPDATE OR DELETE ON baseline_resources FOR EACH ROW EXECUTE FUNCTION trg_require_pm();

-- ------------------------------------------------------------------ revision lineage (reconciliation outcome)
CREATE TABLE activity_lineage (
  lineage_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        UUID NOT NULL,
  version_id        UUID NOT NULL,                 -- the NEW version the decision belongs to
  from_activity_uid UUID NOT NULL,
  to_activity_uid   UUID,                          -- NULL = retired (progress kept as history)
  relation          TEXT NOT NULL CHECK (relation IN ('SAME','RENAMED','SPLIT','MERGED','REPLACED','RETIRED')),
  fraction          NUMERIC(7,6) CHECK (fraction IS NULL OR (fraction > 0 AND fraction <= 1)),
  confirmed_by      UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  confirmed_at      TIMESTAMPTZ,
  CONSTRAINT fk_lineage_version FOREIGN KEY (project_id, version_id) REFERENCES schedule_versions (project_id, version_id) ON DELETE CASCADE,
  CONSTRAINT fk_lineage_from FOREIGN KEY (project_id, from_activity_uid) REFERENCES activities (project_id, activity_uid),
  CONSTRAINT fk_lineage_to FOREIGN KEY (project_id, to_activity_uid) REFERENCES activities (project_id, activity_uid),
  CHECK ((relation = 'RETIRED') = (to_activity_uid IS NULL)),
  CHECK ((confirmed_by IS NULL) = (confirmed_at IS NULL))
);
CREATE UNIQUE INDEX uq_lineage ON activity_lineage (version_id, from_activity_uid, coalesce(to_activity_uid, '00000000-0000-0000-0000-000000000000'::uuid));
CREATE TRIGGER trg_lineage_mutable BEFORE INSERT OR UPDATE OR DELETE ON activity_lineage FOR EACH ROW EXECUTE FUNCTION trg_version_mutable();
CREATE TRIGGER trg_lineage_pm BEFORE INSERT OR UPDATE OR DELETE ON activity_lineage FOR EACH ROW EXECUTE FUNCTION trg_require_pm();
