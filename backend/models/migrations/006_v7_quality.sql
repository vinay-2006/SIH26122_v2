-- Migration 006: V7 Quality Foundation
-- Description: Establishes quality gates and quality evidence tied to activities, stages, and projects.

CREATE TABLE IF NOT EXISTS quality_gates (
  quality_gate_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  stage_id UUID REFERENCES stages(stage_id) ON DELETE SET NULL,
  schedule_id TEXT,
  activity_id TEXT,
  gate_type TEXT NOT NULL CHECK (gate_type IN ('PRE_COMMENCEMENT', 'INTERMEDIATE_HOLD', 'CLEARANCE', 'FINAL_TAKEOVER', 'SAFETY_AUDIT')),
  gate_name TEXT NOT NULL,
  required BOOLEAN DEFAULT TRUE,
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PASSED', 'FAILED', 'WAIVED')),
  due_date DATE,
  passed_at TIMESTAMPTZ,
  passed_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
  remarks TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quality_gates_project_id ON quality_gates(project_id);
CREATE INDEX IF NOT EXISTS idx_quality_gates_stage_id ON quality_gates(stage_id);
CREATE INDEX IF NOT EXISTS idx_quality_gates_activity ON quality_gates(schedule_id, activity_id);
CREATE INDEX IF NOT EXISTS idx_quality_gates_status ON quality_gates(status);

CREATE TABLE IF NOT EXISTS quality_evidence (
  quality_evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  quality_gate_id UUID NOT NULL REFERENCES quality_gates(quality_gate_id) ON DELETE CASCADE,
  source_document_id TEXT REFERENCES source_documents(document_id) ON DELETE SET NULL,
  evidence_type TEXT CHECK (evidence_type IN ('TEST_REPORT', 'PHOTO', 'THIRD_PARTY_CERT', 'NCR_CLEARANCE', 'INSPECTION_NOTE')),
  result TEXT CHECK (result IN ('PASS', 'FAIL', 'PENDING_REVIEW')),
  inspector_name TEXT,
  inspection_date DATE,
  evidence_hash TEXT,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quality_evidence_gate_id ON quality_evidence(quality_gate_id);
CREATE INDEX IF NOT EXISTS idx_quality_evidence_doc_id ON quality_evidence(source_document_id);
