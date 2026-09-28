-- Migration 000: V6 Baseline Core Tables
-- Description: Establishes the foundational V6 schema tables required for SETUAI.
-- Safe & idempotent (CREATE TABLE IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS profiles (
  id UUID PRIMARY KEY,
  full_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('SITE_ENGINEER', 'SUPERVISOR')),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS schedules (
  schedule_id TEXT PRIMARY KEY,
  project_name TEXT NOT NULL,
  data_date DATE,
  source_format TEXT, -- P6_CSV, MSP_XML, EXCEL
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS schedule_activities (
  activity_id TEXT NOT NULL,
  schedule_id TEXT NOT NULL,
  activity_name TEXT NOT NULL,
  wbs_code TEXT,
  discipline TEXT NOT NULL,
  location TEXT NOT NULL,
  asset_tag TEXT,
  planned_start DATE NOT NULL,
  planned_finish DATE NOT NULL,
  planned_quantity REAL,
  uom TEXT,
  baseline_pct_complete REAL DEFAULT 0.0,
  total_float REAL,
  is_critical BOOLEAN,
  PRIMARY KEY (schedule_id, activity_id)
);

CREATE TABLE IF NOT EXISTS schedule_dependencies (
  dependency_id TEXT PRIMARY KEY,
  schedule_id TEXT NOT NULL,
  predecessor_activity_id TEXT NOT NULL,
  successor_activity_id TEXT NOT NULL,
  relationship_type TEXT DEFAULT 'FS',
  lag_days REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS source_documents (
  document_id TEXT PRIMARY KEY,
  file_name TEXT NOT NULL,
  document_type TEXT,
  uploader_id UUID REFERENCES profiles(id),
  file_hash TEXT NOT NULL,
  uploaded_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution_events (
  event_id TEXT PRIMARY KEY,
  document_id TEXT,
  schedule_id TEXT NOT NULL,
  event_date DATE NOT NULL,
  raw_claim_text TEXT NOT NULL,
  input_channel TEXT NOT NULL,
  language_detected TEXT,
  reported_activity_id TEXT,
  matched_activity_id TEXT,
  discipline TEXT,
  action TEXT,
  event_type TEXT,
  claim_mode TEXT DEFAULT 'CUMULATIVE_PCT',
  asset_tag TEXT,
  location TEXT,
  claimed_quantity REAL,
  claimed_uom TEXT,
  claimed_pct REAL,
  delay_reason TEXT,
  supervisor_id UUID REFERENCES profiles(id),
  photo_path TEXT,
  status TEXT DEFAULT 'EXTRACTED',
  created_at TIMESTAMPTZ DEFAULT now(),
  clarification_status TEXT DEFAULT 'NONE',
  clarification_question TEXT,
  clarification_answer TEXT,
  field_provenance JSONB DEFAULT '{}',
  priority_score REAL DEFAULT 0.0,
  priority_reasons TEXT
);

CREATE TABLE IF NOT EXISTS source_references (
  reference_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  file_name TEXT,
  sheet_name TEXT,
  row_cell_ref TEXT,
  message_id TEXT,
  raw_snippet TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_matches (
  candidate_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  schedule_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  rank_order INTEGER CHECK (rank_order BETWEEN 1 AND 3),
  match_tier TEXT,
  composite_confidence REAL CHECK (composite_confidence BETWEEN 0.0 AND 1.0),
  semantic_score REAL,
  fuzzy_score REAL,
  location_score REAL,
  discipline_score REAL,
  supporting_signals TEXT,
  disqualifying_signals TEXT,
  UNIQUE (event_id, rank_order)
);

CREATE TABLE IF NOT EXISTS conflict_records (
  conflict_id TEXT PRIMARY KEY,
  schedule_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  reporting_period DATE NOT NULL,
  event_id_a TEXT NOT NULL,
  event_id_b TEXT NOT NULL,
  value_a REAL NOT NULL,
  value_b REAL NOT NULL,
  variance_pct REAL NOT NULL,
  status TEXT DEFAULT 'OPEN'
);

CREATE TABLE IF NOT EXISTS validation_issues (
  issue_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  rule_code TEXT,
  severity TEXT,
  description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS planner_decisions (
  decision_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  selected_activity_id TEXT NOT NULL,
  action TEXT,
  approved_pct REAL,
  approved_qty REAL,
  planner_id UUID NOT NULL REFERENCES profiles(id),
  justification TEXT NOT NULL,
  decided_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approved_actuals (
  actual_id TEXT PRIMARY KEY,
  decision_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  schedule_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  actual_start DATE,
  actual_finish DATE,
  actual_pct_complete REAL CHECK (actual_pct_complete BETWEEN 0.0 AND 100.0),
  actual_quantity REAL,
  exported_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (schedule_id, activity_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
  log_id SERIAL PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  action TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  before_state TEXT,
  after_state TEXT,
  payload_hash TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  current_hash TEXT NOT NULL,
  timestamp TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS claim_activity_splits (
  split_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  split_basis TEXT CHECK (split_basis IN ('EQUAL', 'WBS_WEIGHTED', 'MANUAL')),
  split_pct REAL NOT NULL,
  schedule_id TEXT,
  wbs_code TEXT,
  planned_quantity REAL,
  allocated_quantity REAL,
  uom TEXT,
  rationale TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (event_id, activity_id)
);

CREATE TABLE IF NOT EXISTS claim_wbs_splits (
  split_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  split_basis TEXT CHECK (split_basis IN ('EQUAL', 'WBS_WEIGHTED', 'MANUAL')),
  split_pct REAL NOT NULL,
  schedule_id TEXT,
  wbs_code TEXT,
  planned_quantity REAL,
  allocated_quantity REAL,
  uom TEXT,
  rationale TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (event_id, activity_id)
);

CREATE TABLE IF NOT EXISTS evidence_links (
  link_id TEXT PRIMARY KEY,
  event_id_a TEXT NOT NULL,
  event_id_b TEXT NOT NULL,
  relation_type TEXT CHECK (relation_type IN ('CORROBORATES', 'CONTRADICTS')),
  confidence REAL,
  rationale TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution_summaries (
  summary_id TEXT PRIMARY KEY,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  discipline TEXT,
  summary_text TEXT NOT NULL,
  generated_at TIMESTAMPTZ DEFAULT now(),
  aggregate_hash TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_summaries_period
  ON execution_summaries (period_start, period_end, COALESCE(discipline, 'ALL'));
