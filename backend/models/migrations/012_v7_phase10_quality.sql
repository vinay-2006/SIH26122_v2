-- Migration 012: V7 Phase 10 Quality, ITP, and Hold Points Engine
-- Description: Establishes Inspection & Test Plan (ITP) domain, extends quality_gates for hold points and attribution, and sets up RLS policies.

-- 1. Create ITPs table
CREATE TABLE IF NOT EXISTS itps (
  itp_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  schedule_id TEXT,
  stage_id UUID REFERENCES stages(stage_id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  description TEXT,
  discipline TEXT,
  responsible_party TEXT,
  contractor_id UUID REFERENCES contractors(contractor_id) ON DELETE SET NULL,
  work_package_id UUID REFERENCES work_packages(work_package_id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_itps_project_id ON itps(project_id);
CREATE INDEX IF NOT EXISTS idx_itps_contractor_id ON itps(contractor_id);
CREATE INDEX IF NOT EXISTS idx_itps_work_package_id ON itps(work_package_id);

-- 2. Extend quality_gates table
ALTER TABLE quality_gates ADD COLUMN IF NOT EXISTS itp_id UUID REFERENCES itps(itp_id) ON DELETE SET NULL;
ALTER TABLE quality_gates ADD COLUMN IF NOT EXISTS checkpoint_category TEXT DEFAULT 'QUALITY_CHECK';
ALTER TABLE quality_gates ADD COLUMN IF NOT EXISTS contractor_id UUID REFERENCES contractors(contractor_id) ON DELETE SET NULL;
ALTER TABLE quality_gates ADD COLUMN IF NOT EXISTS work_package_id UUID REFERENCES work_packages(work_package_id) ON DELETE SET NULL;
ALTER TABLE quality_gates ADD COLUMN IF NOT EXISTS waived_at TIMESTAMPTZ;
ALTER TABLE quality_gates ADD COLUMN IF NOT EXISTS waived_by UUID REFERENCES profiles(id) ON DELETE SET NULL;
ALTER TABLE quality_gates ADD COLUMN IF NOT EXISTS waiver_reason TEXT;

-- Drop old check constraints if present so we can expand allowed values cleanly
ALTER TABLE quality_gates DROP CONSTRAINT IF EXISTS quality_gates_gate_type_check;
ALTER TABLE quality_gates DROP CONSTRAINT IF EXISTS quality_gates_status_check;
ALTER TABLE quality_gates DROP CONSTRAINT IF EXISTS quality_gates_checkpoint_category_check;

ALTER TABLE quality_gates ADD CONSTRAINT quality_gates_gate_type_check 
  CHECK (gate_type IN ('INSPECTION', 'TEST', 'POUR_CARD', 'WELD_INSPECTION', 'NDT', 'MATERIAL_CERTIFICATE', 'NCR_CLEARANCE', 'CLIENT_APPROVAL', 'PRE_COMMENCEMENT', 'INTERMEDIATE_HOLD', 'CLEARANCE', 'FINAL_TAKEOVER', 'SAFETY_AUDIT'));

ALTER TABLE quality_gates ADD CONSTRAINT quality_gates_status_check 
  CHECK (status IN ('NOT_REQUIRED', 'PENDING', 'SUBMITTED', 'PASSED', 'FAILED', 'WAIVED'));

ALTER TABLE quality_gates ADD CONSTRAINT quality_gates_checkpoint_category_check 
  CHECK (checkpoint_category IN ('HOLD', 'WITNESS', 'REVIEW', 'QUALITY_CHECK'));

CREATE INDEX IF NOT EXISTS idx_quality_gates_itp_id ON quality_gates(itp_id);
CREATE INDEX IF NOT EXISTS idx_quality_gates_contractor_id ON quality_gates(contractor_id);
CREATE INDEX IF NOT EXISTS idx_quality_gates_work_package_id ON quality_gates(work_package_id);
CREATE INDEX IF NOT EXISTS idx_quality_gates_category ON quality_gates(checkpoint_category);

-- 3. Extend quality_evidence table if needed
ALTER TABLE quality_evidence DROP CONSTRAINT IF EXISTS quality_evidence_evidence_type_check;
ALTER TABLE quality_evidence ADD CONSTRAINT quality_evidence_evidence_type_check 
  CHECK (evidence_type IN ('TEST_REPORT', 'PHOTO', 'THIRD_PARTY_CERT', 'NCR_CLEARANCE', 'INSPECTION_NOTE', 'POUR_CARD', 'WELD_INSPECTION', 'NDT_RESULT', 'MATERIAL_CERTIFICATE', 'CLIENT_APPROVAL', 'OTHER'));

-- 4. Enable RLS on ITPS and update policies for Quality domain
ALTER TABLE itps ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    -- itps SELECT
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_itps_select') THEN
        CREATE POLICY "v7_itps_select" ON itps FOR SELECT TO authenticated
        USING (is_project_member(project_id));
    END IF;

    -- itps INSERT
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_itps_insert') THEN
        CREATE POLICY "v7_itps_insert" ON itps FOR INSERT TO authenticated
        WITH CHECK (is_project_member(project_id));
    END IF;

    -- itps UPDATE
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_itps_update') THEN
        CREATE POLICY "v7_itps_update" ON itps FOR UPDATE TO authenticated
        USING (is_project_member(project_id))
        WITH CHECK (is_project_member(project_id));
    END IF;

    -- quality_gates INSERT & UPDATE
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_quality_gates_insert') THEN
        CREATE POLICY "v7_quality_gates_insert" ON quality_gates FOR INSERT TO authenticated
        WITH CHECK (is_project_member(project_id));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_quality_gates_update') THEN
        CREATE POLICY "v7_quality_gates_update" ON quality_gates FOR UPDATE TO authenticated
        USING (is_project_member(project_id))
        WITH CHECK (is_project_member(project_id));
    END IF;

    -- quality_evidence INSERT & UPDATE
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_quality_evidence_insert') THEN
        CREATE POLICY "v7_quality_evidence_insert" ON quality_evidence FOR INSERT TO authenticated
        WITH CHECK (EXISTS (SELECT 1 FROM quality_gates qg WHERE qg.quality_gate_id = quality_evidence.quality_gate_id AND is_project_member(qg.project_id)));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'v7_quality_evidence_update') THEN
        CREATE POLICY "v7_quality_evidence_update" ON quality_evidence FOR UPDATE TO authenticated
        USING (EXISTS (SELECT 1 FROM quality_gates qg WHERE qg.quality_gate_id = quality_evidence.quality_gate_id AND is_project_member(qg.project_id)))
        WITH CHECK (EXISTS (SELECT 1 FROM quality_gates qg WHERE qg.quality_gate_id = quality_evidence.quality_gate_id AND is_project_member(qg.project_id)));
    END IF;
END $$;
