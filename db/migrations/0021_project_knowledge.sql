-- 0021 Project knowledge: the authored context of a project (scope, contract context, site, stakeholders, milestones, constraints, risks, safety and quality
-- requirements, procurement, reporting rules, glossary). It is the source of truth for what a project IS; everything operational (progress, delays, claims, issues) stays
-- live in its own tables. Project Intelligence and the Time Agent read both and say which they used.
--
-- Additive. Each entry says where it comes from (provenance): FROM_RECORDS (generated from the project's own records), AUTHORED (written by a Project Manager),
-- ILLUSTRATIVE (a plausible example, not a contractual fact) or NOT_SPECIFIED (an explicit statement that the information is not available). Entries are never deleted:
-- an entry is RETIRED, and every change is audited by the application. Only a Project Manager of the project writes; every member reads (nothing here is claim content).

CREATE TABLE project_knowledge (
  knowledge_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  section       TEXT NOT NULL CHECK (section IN ('OVERVIEW','SCOPE','CONTRACT','SITE','STAKEHOLDERS','MILESTONES','CONSTRAINTS','RISKS','SAFETY_QUALITY','PROCUREMENT','REPORTING','GLOSSARY')),
  title         TEXT NOT NULL CHECK (length(btrim(title)) BETWEEN 3 AND 160),
  body          TEXT NOT NULL CHECK (length(btrim(body)) BETWEEN 3 AND 8000),
  provenance    TEXT NOT NULL CHECK (provenance IN ('FROM_RECORDS','AUTHORED','ILLUSTRATIVE','NOT_SPECIFIED')),
  tags          TEXT[] NOT NULL DEFAULT '{}',
  sort_order    INTEGER NOT NULL DEFAULT 100 CHECK (sort_order BETWEEN 0 AND 100000),
  status        TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','RETIRED')),
  version       INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_by    UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by    UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_project_knowledge_title ON project_knowledge (project_id, section, lower(title)) WHERE status = 'ACTIVE';
CREATE INDEX idx_project_knowledge_project ON project_knowledge (project_id, status, section, sort_order);
CREATE TRIGGER trg_project_knowledge_nodelete BEFORE DELETE ON project_knowledge FOR EACH ROW EXECUTE FUNCTION trg_append_only();

CREATE FUNCTION trg_project_knowledge_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_actor UUID := app_actor();
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF NEW.project_id <> OLD.project_id OR NEW.created_by <> OLD.created_by OR NEW.created_at <> OLD.created_at OR NEW.knowledge_id <> OLD.knowledge_id THEN
      RAISE EXCEPTION 'project knowledge identity is immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.version <> OLD.version + 1 THEN RAISE EXCEPTION 'project knowledge version must advance by one' USING ERRCODE = '23514'; END IF;
    IF OLD.status = 'RETIRED' THEN RAISE EXCEPTION 'a retired knowledge entry is not edited' USING ERRCODE = '23514'; END IF;
  END IF;
  IF app_is_system() THEN RETURN NEW; END IF;           -- migrations / generated-content loaders only; the API never runs in system mode
  IF project_role_of(NEW.project_id, v_actor) IS DISTINCT FROM 'PROJECT_MANAGER' THEN
    RAISE EXCEPTION 'project knowledge is written by a PROJECT_MANAGER of the project' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.created_by IS DISTINCT FROM v_actor THEN RAISE EXCEPTION 'created_by must be the acting user' USING ERRCODE = '42501'; END IF;
  IF NEW.updated_by IS DISTINCT FROM v_actor THEN RAISE EXCEPTION 'updated_by must be the acting user' USING ERRCODE = '42501'; END IF;
  IF NEW.provenance = 'FROM_RECORDS' THEN RAISE EXCEPTION 'FROM_RECORDS entries are generated from the project records, not typed in' USING ERRCODE = '42501'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_project_knowledge_guard BEFORE INSERT OR UPDATE ON project_knowledge FOR EACH ROW EXECUTE FUNCTION trg_project_knowledge_guard();
CREATE TRIGGER trg_project_knowledge_touch BEFORE UPDATE ON project_knowledge FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

ALTER TABLE project_knowledge ENABLE ROW LEVEL SECURITY;
CREATE POLICY project_knowledge_select ON project_knowledge FOR SELECT TO authenticated USING (is_project_member(project_id));
GRANT SELECT ON project_knowledge TO authenticated;
