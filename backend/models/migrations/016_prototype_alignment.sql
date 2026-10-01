-- Migration 016: prototype alignment
-- Description:
--   One coherent Project -> Stage -> Activity -> (Claims | Issues | Evidence) model for the SIH prototype.
--   Evolves existing tables instead of layering new ones:
--     * disciplines              NEW reference table; schedule_activities.discipline becomes a real FK
--     * projects.lifecycle_status NEW (UPCOMING / ONGOING / COMPLETED). `status` stays ACTIVE/ARCHIVED (record admin state)
--     * schedule_activities.description NEW (matching signal). activity_id IS the P6 activity code; no duplicate column.
--     * issues                   execution_blockers GENERALISED in place (category, severity, dates, root cause, evidence).
--                                A blocker is an issue with blocks_work = TRUE; BLOCKED is still derived, never stored.
--     * issue_categories, root_causes, issue_evidence   NEW
--     * upload_batches           NEW: batch -> files (source_documents) -> claims (execution_events) -> matches
--     * execution_events.claim_fingerprint  duplicate-claim guard across files of a batch
--     * notifications            NEW: supervisor decision -> site engineer
--     * institutional_incidents  extended into a read/write memory (category, outcome, visibility, issue link)
--   Actual progress is NOT stored on activities: approved_actuals stays the single authoritative source.
--   Activity status is likewise derived (StageService), not stored.
-- Idempotent. RLS follows the V7 model (project members only); notifications are recipient-only.

-- ---------------------------------------------------------------------------------------------------------------
-- 1. Disciplines (reference) + FK from activities
-- ---------------------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS disciplines (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 100
);
INSERT INTO disciplines (code, name, sort_order) VALUES
    ('CIVIL', 'Civil', 10),
    ('STRUCTURAL', 'Structural', 20),
    ('PIPING', 'Piping', 30),
    ('STATIC_ROTATING_EQUIPMENT', 'Static & Rotating Equipment', 40),
    ('ELECTRICAL', 'Electrical', 50),
    ('INSTRUMENTATION', 'Instrumentation', 60),
    ('PROCESS', 'Process & Commissioning', 70),
    ('DRILLING', 'Drilling & Well Engineering', 80),
    ('LOGISTICS', 'Marine & Materials Logistics', 90),
    ('HSE', 'Health, Safety & Environment', 100),
    ('OTHER', 'Other / Unclassified', 999)
ON CONFLICT (code) DO NOTHING;

-- Free-text discipline spellings are normalised on write (the same alias table the Python intake normaliser uses), so
-- 'Piping' / 'piping' / 'Piping Works' / 'Mechanical' can never exist as separate values. A label with no mapping is kept
-- (schedule import must never fail over a label) in the explicit OTHER bucket, with the original text in discipline_source.
ALTER TABLE schedule_activities ADD COLUMN IF NOT EXISTS discipline_source TEXT;
CREATE TABLE IF NOT EXISTS discipline_aliases (
    alias  TEXT PRIMARY KEY,                       -- lower-case, single-spaced
    code   TEXT NOT NULL REFERENCES disciplines(code) ON UPDATE CASCADE
);
INSERT INTO discipline_aliases (alias, code) VALUES
    ('civil works', 'CIVIL'), ('civil and structural', 'CIVIL'),
    ('structural works', 'STRUCTURAL'), ('structure', 'STRUCTURAL'), ('structures', 'STRUCTURAL'), ('steel structure', 'STRUCTURAL'),
    ('piping works', 'PIPING'), ('pipe', 'PIPING'), ('pipeline', 'PIPING'),
    ('static/rotating equipment', 'STATIC_ROTATING_EQUIPMENT'), ('static rotating equipment', 'STATIC_ROTATING_EQUIPMENT'),
    ('static and rotating equipment', 'STATIC_ROTATING_EQUIPMENT'), ('mechanical', 'STATIC_ROTATING_EQUIPMENT'),
    ('mechanical works', 'STATIC_ROTATING_EQUIPMENT'), ('equipment', 'STATIC_ROTATING_EQUIPMENT'),
    ('electrical works', 'ELECTRICAL'), ('power', 'ELECTRICAL'),
    ('instrumentation works', 'INSTRUMENTATION'), ('instrument', 'INSTRUMENTATION'), ('control systems', 'INSTRUMENTATION'),
    ('process', 'PROCESS'), ('commissioning', 'PROCESS'), ('process and commissioning', 'PROCESS'),
    ('drilling', 'DRILLING'), ('well engineering', 'DRILLING'), ('drilling and well engineering', 'DRILLING'),
    ('logistics', 'LOGISTICS'), ('marine logistics', 'LOGISTICS'), ('materials logistics', 'LOGISTICS'), ('procurement', 'LOGISTICS'),
    ('hse', 'HSE'), ('safety', 'HSE'), ('health, safety and environment', 'HSE'), ('health safety environment', 'HSE'),
    ('health safety and environment', 'HSE'), ('environment', 'HSE')
ON CONFLICT (alias) DO NOTHING;

CREATE OR REPLACE FUNCTION normalize_discipline(raw TEXT) RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (SELECT d.code FROM disciplines d WHERE d.code = upper(regexp_replace(btrim(raw), '[\s/&-]+', '_', 'g'))),
        (SELECT a.code FROM discipline_aliases a WHERE a.alias = lower(regexp_replace(btrim(raw), '\s+', ' ', 'g')))
    )
$$;

CREATE OR REPLACE FUNCTION trg_normalize_activity_discipline() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE c TEXT;
BEGIN
    IF NEW.discipline IS NULL THEN
        RETURN NEW;
    END IF;
    c := COALESCE(normalize_discipline(NEW.discipline), 'OTHER');
    IF c <> NEW.discipline THEN
        NEW.discipline_source := NEW.discipline;       -- keep what the source file said
        NEW.discipline := c;
    ELSIF TG_OP = 'UPDATE' AND NEW.discipline IS DISTINCT FROM OLD.discipline THEN
        NEW.discipline_source := NULL;                 -- re-labelled with a canonical code: nothing left to explain
    END IF;
    RETURN NEW;
END $$;

-- normalise what already exists (unmapped labels move to OTHER, original text kept), then enforce going forward
UPDATE schedule_activities
   SET discipline_source = COALESCE(discipline_source, discipline),
       discipline = COALESCE(normalize_discipline(discipline), 'OTHER')
 WHERE discipline IS DISTINCT FROM COALESCE(normalize_discipline(discipline), 'OTHER');
DROP TRIGGER IF EXISTS trg_activities_discipline ON schedule_activities;
CREATE TRIGGER trg_activities_discipline BEFORE INSERT OR UPDATE OF discipline ON schedule_activities
    FOR EACH ROW EXECUTE FUNCTION trg_normalize_activity_discipline();
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_activities_discipline') THEN
        ALTER TABLE schedule_activities ADD CONSTRAINT fk_activities_discipline
            FOREIGN KEY (discipline) REFERENCES disciplines(code) ON UPDATE CASCADE;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_activities_proj_discipline ON schedule_activities (project_id, schedule_id, discipline);

ALTER TABLE discipline_aliases ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "v7_discipline_aliases_select" ON discipline_aliases;
CREATE POLICY "v7_discipline_aliases_select" ON discipline_aliases FOR SELECT TO authenticated USING (auth.uid() IS NOT NULL);

ALTER TABLE disciplines ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "v7_disciplines_select" ON disciplines;
CREATE POLICY "v7_disciplines_select" ON disciplines FOR SELECT TO authenticated USING (auth.uid() IS NOT NULL);

-- ---------------------------------------------------------------------------------------------------------------
-- 2. Project lifecycle + activity description
-- ---------------------------------------------------------------------------------------------------------------
ALTER TABLE projects ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'ONGOING';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_projects_lifecycle_status') THEN
        ALTER TABLE projects ADD CONSTRAINT chk_projects_lifecycle_status
            CHECK (lifecycle_status IN ('UPCOMING', 'ONGOING', 'COMPLETED'));
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_projects_lifecycle ON projects (lifecycle_status);

ALTER TABLE schedule_activities ADD COLUMN IF NOT EXISTS description TEXT;

-- ---------------------------------------------------------------------------------------------------------------
-- 3. Issue categories (reference)
-- ---------------------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS issue_categories (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 100
);
INSERT INTO issue_categories (code, name, sort_order) VALUES
    ('LABOUR_SHORTAGE', 'Shortage of workers', 10),
    ('EQUIPMENT_SHORTAGE', 'Lack of equipment', 20),
    ('MATERIAL_SHORTAGE', 'Lack of materials', 30),
    ('MATERIAL_DELIVERY_DELAY', 'Material delivery delay', 40),
    ('CONTRACTOR_ISSUE', 'Contractor issue', 50),
    ('WEATHER', 'Weather disruption', 60),
    ('SITE_ACCESS', 'Site access problem', 70),
    ('SAFETY', 'Safety issue', 80),
    ('TECHNICAL', 'Technical issue', 90),
    ('DESIGN_DOCUMENTATION', 'Design / documentation issue', 100),
    ('PERMIT_APPROVAL', 'Permit / approval delay', 110),
    ('OTHER', 'Other', 999)
ON CONFLICT (code) DO NOTHING;
ALTER TABLE issue_categories ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "v7_issue_categories_select" ON issue_categories;
CREATE POLICY "v7_issue_categories_select" ON issue_categories FOR SELECT TO authenticated USING (auth.uid() IS NOT NULL);

-- ---------------------------------------------------------------------------------------------------------------
-- 4. root_causes (a root cause groups many issues; the repeated-pattern anchor for root-cause analysis)
-- ---------------------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS root_causes (
    root_cause_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    category_code   TEXT NOT NULL REFERENCES issue_categories(code),
    title           TEXT NOT NULL CHECK (length(btrim(title)) >= 3),
    summary         TEXT,
    status          TEXT NOT NULL DEFAULT 'IDENTIFIED' CHECK (status IN ('IDENTIFIED', 'ADDRESSED')),
    identified_by   UUID REFERENCES profiles(id) ON DELETE SET NULL,
    identified_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_root_causes_project ON root_causes (project_id, category_code);

-- ---------------------------------------------------------------------------------------------------------------
-- 5. issues = execution_blockers generalised in place (no second concept)
-- ---------------------------------------------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.execution_blockers') IS NOT NULL AND to_regclass('public.issues') IS NULL THEN
        ALTER TABLE execution_blockers RENAME TO issues;
        ALTER TABLE issues RENAME COLUMN blocker_id TO issue_id;
        ALTER TABLE issues RENAME COLUMN created_by TO reported_by;
        ALTER TABLE issues RENAME COLUMN reason TO description;
        ALTER TABLE issues RENAME CONSTRAINT execution_blockers_target_chk TO issues_target_chk;
        ALTER TABLE issues RENAME CONSTRAINT execution_blockers_activity_fk TO issues_activity_fk;
        ALTER TABLE issues RENAME CONSTRAINT execution_blockers_lifecycle_chk TO issues_lifecycle_chk;
    END IF;
END $$;

-- legacy object names (idempotent; each is a no-op once renamed)
ALTER INDEX IF EXISTS execution_blockers_pkey RENAME TO issues_pkey;
ALTER INDEX IF EXISTS idx_blockers_active_activity RENAME TO idx_issues_active_activity;
ALTER INDEX IF EXISTS idx_blockers_active_stage RENAME TO idx_issues_active_stage;
ALTER INDEX IF EXISTS idx_blockers_source_event RENAME TO idx_issues_source_event;
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint WHERE conrelid = 'public.issues'::regclass AND conname LIKE 'execution_blockers\_%' ESCAPE '\'
    LOOP
        EXECUTE format('ALTER TABLE issues RENAME CONSTRAINT %I TO %I', r.conname, replace(r.conname, 'execution_blockers_', 'issues_'));
    END LOOP;
END $$;

ALTER TABLE issues ADD COLUMN IF NOT EXISTS category_code TEXT REFERENCES issue_categories(code);
ALTER TABLE issues ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS severity TEXT NOT NULL DEFAULT 'MEDIUM';
ALTER TABLE issues ADD COLUMN IF NOT EXISTS reported_date DATE NOT NULL DEFAULT CURRENT_DATE;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS expected_duration_days REAL;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS blocks_work BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS root_cause_id UUID REFERENCES root_causes(root_cause_id) ON DELETE SET NULL;

-- carry the legacy blocker_type over to the category, then retire the old column
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'issues' AND column_name = 'blocker_type') THEN
        UPDATE issues SET category_code = CASE blocker_type
            WHEN 'MATERIAL' THEN 'MATERIAL_SHORTAGE'
            WHEN 'EQUIPMENT' THEN 'EQUIPMENT_SHORTAGE'
            WHEN 'LABOUR' THEN 'LABOUR_SHORTAGE'
            WHEN 'ACCESS' THEN 'SITE_ACCESS'
            WHEN 'PERMIT' THEN 'PERMIT_APPROVAL'
            WHEN 'DESIGN' THEN 'DESIGN_DOCUMENTATION'
            WHEN 'WEATHER' THEN 'WEATHER'
            ELSE 'OTHER' END
        WHERE category_code IS NULL;
        ALTER TABLE issues DROP COLUMN blocker_type;
    END IF;
END $$;
UPDATE issues SET category_code = 'OTHER' WHERE category_code IS NULL;
UPDATE issues SET title = left(description, 80) WHERE title IS NULL;
ALTER TABLE issues ALTER COLUMN category_code SET NOT NULL;
ALTER TABLE issues ALTER COLUMN title SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_issues_severity') THEN
        ALTER TABLE issues ADD CONSTRAINT chk_issues_severity CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_issues_expected_duration') THEN
        ALTER TABLE issues ADD CONSTRAINT chk_issues_expected_duration CHECK (expected_duration_days IS NULL OR expected_duration_days >= 0);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_issues_project_category ON issues (project_id, category_code);
CREATE INDEX IF NOT EXISTS idx_issues_root_cause ON issues (root_cause_id);
CREATE INDEX IF NOT EXISTS idx_issues_reported_by ON issues (reported_by);

-- policies follow the rename; recreate under the new names and add the ones a site engineer needs
DROP POLICY IF EXISTS "v7_blockers_select" ON issues;
DROP POLICY IF EXISTS "v7_blockers_insert" ON issues;
DROP POLICY IF EXISTS "v7_blockers_update" ON issues;
DROP POLICY IF EXISTS "v7_issues_select" ON issues;
CREATE POLICY "v7_issues_select" ON issues FOR SELECT TO authenticated
    USING (project_id IS NOT NULL AND is_project_member(project_id));
DROP POLICY IF EXISTS "v7_issues_insert" ON issues;
CREATE POLICY "v7_issues_insert" ON issues FOR INSERT TO authenticated
    WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));
DROP POLICY IF EXISTS "v7_issues_update" ON issues;
CREATE POLICY "v7_issues_update" ON issues FOR UPDATE TO authenticated
    USING (project_id IS NOT NULL AND is_project_member(project_id))
    WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));
-- no DELETE policy: an issue is history, it is resolved, never removed.

ALTER TABLE root_causes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "v7_root_causes_select" ON root_causes;
CREATE POLICY "v7_root_causes_select" ON root_causes FOR SELECT TO authenticated USING (is_project_member(project_id));
DROP POLICY IF EXISTS "v7_root_causes_insert" ON root_causes;
CREATE POLICY "v7_root_causes_insert" ON root_causes FOR INSERT TO authenticated WITH CHECK (is_project_member(project_id));
DROP POLICY IF EXISTS "v7_root_causes_update" ON root_causes;
CREATE POLICY "v7_root_causes_update" ON root_causes FOR UPDATE TO authenticated
    USING (is_project_member(project_id)) WITH CHECK (is_project_member(project_id));

-- ---------------------------------------------------------------------------------------------------------------
-- 6. Upload batches; source_documents become the batch's files
-- ---------------------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS upload_batches (
    batch_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    schedule_id     TEXT NOT NULL REFERENCES schedules(schedule_id) ON DELETE RESTRICT,
    uploaded_by     UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    status          TEXT NOT NULL DEFAULT 'PROCESSING' CHECK (status IN ('PROCESSING', 'COMPLETED', 'PARTIAL', 'FAILED')),
    file_count      INTEGER NOT NULL DEFAULT 0,
    claim_count     INTEGER NOT NULL DEFAULT 0,
    merged_count    INTEGER NOT NULL DEFAULT 0,   -- extracted claims folded into an existing identical claim
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_upload_batches_project ON upload_batches (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_upload_batches_uploader ON upload_batches (uploaded_by);

ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(project_id) ON DELETE RESTRICT;
ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS batch_id UUID REFERENCES upload_batches(batch_id) ON DELETE SET NULL;
ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS extraction_status TEXT;
ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS extraction_method TEXT;
ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS extraction_error TEXT;
ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS claims_extracted INTEGER NOT NULL DEFAULT 0;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_source_documents_extraction_status') THEN
        ALTER TABLE source_documents ADD CONSTRAINT chk_source_documents_extraction_status
            CHECK (extraction_status IS NULL OR extraction_status IN ('EXTRACTED', 'EMPTY', 'FAILED'));
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_source_documents_batch ON source_documents (batch_id);
CREATE INDEX IF NOT EXISTS idx_source_documents_project ON source_documents (project_id);

-- a corroborating file (same claim seen in a second report) is linked as a reference, not as a second claim
ALTER TABLE source_references ADD COLUMN IF NOT EXISTS document_id TEXT REFERENCES source_documents(document_id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_source_references_event ON source_references (event_id);

ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS claim_fingerprint TEXT;
-- one live claim per (project, schedule, fingerprint); a REJECTED claim may be re-submitted
CREATE UNIQUE INDEX IF NOT EXISTS uq_events_claim_fingerprint ON execution_events (project_id, schedule_id, claim_fingerprint)
    WHERE claim_fingerprint IS NOT NULL AND status <> 'REJECTED';

ALTER TABLE upload_batches ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "v7_upload_batches_select" ON upload_batches;
CREATE POLICY "v7_upload_batches_select" ON upload_batches FOR SELECT TO authenticated USING (is_project_member(project_id));
DROP POLICY IF EXISTS "v7_upload_batches_insert" ON upload_batches;
CREATE POLICY "v7_upload_batches_insert" ON upload_batches FOR INSERT TO authenticated WITH CHECK (is_project_member(project_id));
DROP POLICY IF EXISTS "v7_upload_batches_update" ON upload_batches;
CREATE POLICY "v7_upload_batches_update" ON upload_batches FOR UPDATE TO authenticated
    USING (is_project_member(project_id)) WITH CHECK (is_project_member(project_id));

-- documents that carry their own project (batch files, issue evidence) are readable by that project's members, and a
-- member may add one to THEIR project (issue evidence is attached as the caller; there is no UPDATE/DELETE policy)
DROP POLICY IF EXISTS "v7_source_documents_project_select" ON source_documents;
CREATE POLICY "v7_source_documents_project_select" ON source_documents FOR SELECT TO authenticated
    USING (project_id IS NOT NULL AND is_project_member(project_id));
DROP POLICY IF EXISTS "v7_source_documents_project_insert" ON source_documents;
CREATE POLICY "v7_source_documents_project_insert" ON source_documents FOR INSERT TO authenticated
    WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));

-- ---------------------------------------------------------------------------------------------------------------
-- 7. Issue evidence
-- ---------------------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS issue_evidence (
    issue_evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id          UUID NOT NULL REFERENCES issues(issue_id) ON DELETE CASCADE,
    document_id       TEXT NOT NULL REFERENCES source_documents(document_id) ON DELETE RESTRICT,
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_issue_evidence UNIQUE (issue_id, document_id)
);
CREATE INDEX IF NOT EXISTS idx_issue_evidence_issue ON issue_evidence (issue_id);
ALTER TABLE issue_evidence ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "v7_issue_evidence_select" ON issue_evidence;
CREATE POLICY "v7_issue_evidence_select" ON issue_evidence FOR SELECT TO authenticated
    USING (EXISTS (SELECT 1 FROM issues i WHERE i.issue_id = issue_evidence.issue_id AND is_project_member(i.project_id)));
DROP POLICY IF EXISTS "v7_issue_evidence_insert" ON issue_evidence;
CREATE POLICY "v7_issue_evidence_insert" ON issue_evidence FOR INSERT TO authenticated
    WITH CHECK (EXISTS (SELECT 1 FROM issues i WHERE i.issue_id = issue_evidence.issue_id AND is_project_member(i.project_id)));

-- ---------------------------------------------------------------------------------------------------------------
-- 8. Notifications: supervisor decision -> site engineer
-- ---------------------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
    notification_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id        UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    recipient_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    notification_type TEXT NOT NULL CHECK (notification_type IN ('CLAIM_DECISION', 'ISSUE_UPDATE')),
    decision_id       TEXT REFERENCES planner_decisions(decision_id) ON DELETE CASCADE,
    event_id          TEXT REFERENCES execution_events(event_id) ON DELETE CASCADE,
    issue_id          UUID REFERENCES issues(issue_id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    body              TEXT,
    created_by        UUID REFERENCES profiles(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at           TIMESTAMPTZ,
    CONSTRAINT notifications_subject_chk CHECK (
        (notification_type = 'CLAIM_DECISION' AND decision_id IS NOT NULL AND event_id IS NOT NULL)
        OR (notification_type = 'ISSUE_UPDATE' AND issue_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications (recipient_id, read_at, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_project ON notifications (project_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_decision_recipient ON notifications (decision_id, recipient_id) WHERE decision_id IS NOT NULL;

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "v7_notifications_select" ON notifications;
CREATE POLICY "v7_notifications_select" ON notifications FOR SELECT TO authenticated
    USING (recipient_id = auth.uid() AND is_project_member(project_id));
DROP POLICY IF EXISTS "v7_notifications_insert" ON notifications;
CREATE POLICY "v7_notifications_insert" ON notifications FOR INSERT TO authenticated
    WITH CHECK (is_project_member(project_id));
DROP POLICY IF EXISTS "v7_notifications_update" ON notifications;
CREATE POLICY "v7_notifications_update" ON notifications FOR UPDATE TO authenticated
    USING (recipient_id = auth.uid()) WITH CHECK (recipient_id = auth.uid());

-- ---------------------------------------------------------------------------------------------------------------
-- 9. Institutional memory: extend institutional_incidents (existing root_cause / corrective_action / lessons_learned
--    already carry cause, resolution and lesson; add the missing outcome, category, link to the issue, scope)
-- ---------------------------------------------------------------------------------------------------------------
ALTER TABLE institutional_incidents ADD COLUMN IF NOT EXISTS schedule_id TEXT REFERENCES schedules(schedule_id) ON DELETE SET NULL;
ALTER TABLE institutional_incidents ADD COLUMN IF NOT EXISTS category_code TEXT REFERENCES issue_categories(code);
ALTER TABLE institutional_incidents ADD COLUMN IF NOT EXISTS issue_id UUID REFERENCES issues(issue_id) ON DELETE SET NULL;
ALTER TABLE institutional_incidents ADD COLUMN IF NOT EXISTS outcome TEXT;
ALTER TABLE institutional_incidents ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'PROJECT';
ALTER TABLE institutional_incidents ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'MANUAL';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_incidents_visibility') THEN
        ALTER TABLE institutional_incidents ADD CONSTRAINT chk_incidents_visibility CHECK (visibility IN ('PROJECT', 'ORGANISATION'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_incidents_source') THEN
        ALTER TABLE institutional_incidents ADD CONSTRAINT chk_incidents_source CHECK (source IN ('MANUAL', 'ISSUE_RESOLUTION', 'HISTORICAL'));
    END IF;
    -- an activity reference must resolve to a real activity of that schedule version (MATCH SIMPLE: skipped when schedule_id is NULL)
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_incidents_activity') THEN
        ALTER TABLE institutional_incidents ADD CONSTRAINT fk_incidents_activity
            FOREIGN KEY (schedule_id, activity_id) REFERENCES schedule_activities(schedule_id, activity_id) ON DELETE RESTRICT;
    END IF;
END $$;
-- one memory record per resolved issue (promotion is idempotent)
CREATE UNIQUE INDEX IF NOT EXISTS uq_incidents_issue ON institutional_incidents (issue_id) WHERE issue_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_incidents_category ON institutional_incidents (category_code);
CREATE INDEX IF NOT EXISTS idx_incidents_visibility ON institutional_incidents (visibility);

-- lessons a project explicitly shares are readable by members of any project (cross-project learning);
-- everything else stays strictly project-scoped (the existing v7_incidents_select policy is untouched).
DROP POLICY IF EXISTS "v7_incidents_org_select" ON institutional_incidents;
CREATE POLICY "v7_incidents_org_select" ON institutional_incidents FOR SELECT TO authenticated
    USING (visibility = 'ORGANISATION'
           AND EXISTS (SELECT 1 FROM project_memberships pm
                        WHERE pm.user_id = auth.uid() AND pm.active = TRUE AND pm.status = 'ACTIVE'));

-- the memory write path (resolve-to-memory, manual lesson) runs as the caller: project members may add records to THEIR project.
-- (Before this migration memory had no write path; there is deliberately still no UPDATE/DELETE policy: records are history.)
DROP POLICY IF EXISTS "v7_incidents_insert" ON institutional_incidents;
CREATE POLICY "v7_incidents_insert" ON institutional_incidents FOR INSERT TO authenticated
    WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));
