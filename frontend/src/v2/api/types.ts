/** Shapes of the v2 API responses used by the UI. Mirrors docs/V2_API.md and the services in backend/v2. Numbers may arrive as numbers or strings. */
export type Num = number | string;
export type V2Role = 'PROJECT_MANAGER' | 'SUPERVISOR' | 'SITE_ENGINEER';
export type Lifecycle = 'UPCOMING' | 'ONGOING' | 'COMPLETED';

export interface MyProject {
  project_id: string; project_code: string; project_name: string; lifecycle_status: Lifecycle; record_status: 'ACTIVE' | 'ARCHIVED';
  location: string | null; my_role: V2Role; active_version_id: string | null;
}
export interface Me { id: string; email: string; full_name: string; capabilities: string[]; projects: MyProject[] }

export interface ActiveVersion { version_id: string; version_no: number; baseline_name: string | null; data_date: string | null }
export interface ProjectDetail {
  project_id: string; project_code: string; project_name: string; description: string | null; client_name: string | null; project_type: string | null; location: string | null;
  latitude: number | null; longitude: number | null; planned_start: string | null; planned_finish: string | null; contract_finish: string | null;
  lifecycle_status: Lifecycle; record_status: 'ACTIVE' | 'ARCHIVED'; active_version: ActiveVersion | null; my_role: V2Role; my_permissions: string[];
}
export interface ProjectCreate {
  project_code: string; project_name: string; description?: string; client_name?: string; project_type?: string; location?: string;
  planned_start?: string; planned_finish?: string; contract_finish?: string; lifecycle_status?: Lifecycle;
}
export interface ProjectSettings { over_baseline_tolerance_pct: Num; completion_threshold_pct: Num; working_days_per_week: number; require_photo_evidence: boolean; extra: Record<string, unknown> | null }
export interface Member { user_id: string; full_name: string; email: string; role: V2Role; status: 'ACTIVE' | 'SUSPENDED' | 'REMOVED'; created_at: string }
export interface Invitation { invitation_id: string; email: string; role: V2Role; expires_at: string; state: 'PENDING' | 'ACCEPTED' | 'REVOKED' | 'EXPIRED'; created_at: string }

// ---- schedule
export interface ImportIssue { code: string; message: string; ref?: string | null; severity?: string }
export interface ImportReport {
  valid: boolean; ready_to_build: boolean; errors: ImportIssue[]; warnings: ImportIssue[];
  stats?: Record<string, number>; mapping?: { unmapped_disciplines: string[]; unmapped_units: string[] };
  [k: string]: unknown;
}
export interface ReconItem { new: string; outcome: string; uid?: string | null; old_uid?: string | null; [k: string]: unknown }
export interface Reconciliation {
  items: ReconItem[]; blockers: { code: string; ref: string; message?: string }[];
  split_proposals: { from_external_id: string; from_uid?: string; [k: string]: unknown }[];
  merge_proposals: { to: string; from_uids: string[]; [k: string]: unknown }[];
  changes?: unknown[]; [k: string]: unknown;
}
export interface ScheduleImport {
  import_id: string; status: 'PARSED' | 'BUILT' | 'DISCARDED'; format: string; file_name: string | null; header: Record<string, string | null>;
  report: ImportReport; wbs: { code: string; name: string; parent: string | null; type: string | null }[];
  base_version_id: string | null; reconciliation: Reconciliation | null; decisions: Record<string, any>;
  built_version: { version_id: string; version_no: number; status: string } | null;
}
export interface VersionRow {
  version_id: string; version_no: number; kind: 'BASELINE' | 'REVISION'; status: 'DRAFT' | 'VALIDATED' | 'ACTIVE' | 'SUPERSEDED'; baseline_name: string | null; label: string | null;
  data_date: string | null; planned_start_date: string | null; planned_finish_date: string | null; locked_at: string | null; activated_at: string | null;
  parent_version_id: string | null; created_at: string; activities: number;
}
export interface VersionDetail extends VersionRow { wbs_nodes: number; assignments: number; dependencies: number; lineage: Record<string, number> }
export interface BuiltVersion { version_id: string; version_no: number; kind: string; status: string; activities: number; assignments: number; dependencies: number }
export interface WbsRow { wbs_id: string; wbs_uid: string; wbs_code: string; wbs_name: string; node_type: string; level: number; parent_wbs_id: string | null }
export interface VersionCompare {
  old_version_id: string; new_version_id: string; summary: Record<string, number>;
  activities: { change_kind: string; external_activity_id?: string; [k: string]: unknown }[]; assignments: Record<string, unknown>[];
}

// ---- activities / progress
export interface MeasuredAssignment { assignment_uid: string; resource_code: string; resource_name: string; unit_of_measure: string; baseline_qty: Num; approved_cumulative_qty: Num | null }
export interface CatalogActivity {
  activity_uid: string; external_activity_id: string; activity_name: string; wbs_path: string; discipline_code: string; activity_type: string; baseline_start: string; baseline_finish: string;
  baseline_duration: Num; physical_pct: Num; execution_state: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED'; actual_start: string | null; actual_finish: string | null; any_overrun: boolean;
  progress_basis: string; measured_assignments: MeasuredAssignment[]; claim_types: string[];
}
export interface ActivityProgress {
  activity_uid: string; external_activity_id: string; activity_name: string; wbs_path: string; discipline_code: string; activity_type: string; baseline_start: string; baseline_finish: string;
  physical_pct: Num; planned_pct: Num; execution_state: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED'; actual_start: string | null; actual_finish: string | null; any_overrun: boolean;
  max_overrun_pct: Num | null; weight: Num; weight_basis: string; measured_assignments: number; start_variance_days: number | null; finish_variance_days: number | null;
}
export interface Summary {
  project: { project_id: string; name: string; lifecycle_status: Lifecycle };
  version: ActiveVersion; as_of: string; data_date: string | null; physical_pct: Num; planned_pct: Num; spi_approx: Num | null;
  activities: { total: number; completed: number; in_progress: number; not_started: number }; any_overrun: boolean;
  weight_basis: string; weight_basis_explanation: string; planned_method_note: string; spi_note: string;
  issues: { active: number; blocking: number; resolved: number };
  claims: { scope: 'aggregate' | 'own'; pending_total: number; [status: string]: Num | string };
  source: string;
}
export interface RollupRow { physical_pct: Num; planned_pct: Num; activities: number; [k: string]: unknown }
export interface WbsProgress extends RollupRow { wbs_id: string; wbs_code: string; wbs_name: string; node_type: string; level: number }
export interface DisciplineProgress extends RollupRow { discipline_code: string }
export interface TimelinePoint { as_of: string; physical_pct: Num; planned_pct: Num; spi_approx: Num | null }
export interface Timeline { version_id: string; data_date: string | null; points: TimelinePoint[]; planned_method_note: string; spi_note: string }
export interface ActivityTimeline {
  activity_uid: string; versions: Record<string, any>[]; quantity_entries: Record<string, any>[]; activity_entries: Record<string, any>[]; issues: Record<string, any>[];
  lineage: Record<string, any>[]; claims: Record<string, any>[]; decisions: Record<string, any>[]; claims_note?: string;
}

// ---- claims
export interface QuantityIn { qty: number | string; uom: string; basis: 'CUMULATIVE' | 'INCREMENTAL'; resource_hint?: string | null }
export interface ClaimIn {
  event_date: string; raw_text: string; activity_uid?: string | null; reported_activity_ref?: string | null; quantities?: QuantityIn[]; claimed_pct?: number | string | null;
  claimed_start?: string | null; claimed_finish?: string | null; location?: string | null; evidence_document_ids?: string[]; input_channel?: 'TYPED';
}
export interface ClaimSubmitted { claim_id: string; status: string; activity_uid: string | null; quantities: Record<string, any>[]; validations: { rule: string; severity: string; message: string }[]; priority_score: Num }
export type ClaimStatus = 'REPORTED' | 'EXTRACTED' | 'MATCHED' | 'VALIDATED' | 'DISPUTED' | 'APPROVED' | 'REJECTED' | 'WITHDRAWN';
export interface ClaimListItem {
  event_id: string; event_date: string; status: ClaimStatus; matched_activity_uid: string | null; external_activity_id?: string | null; claimed_pct: Num | null;
  clarification_status: 'ASKED' | 'ANSWERED' | null; clarification_question?: string | null; priority_score?: Num; priority_reasons?: string | null; errors?: number; created_at: string;
  withdrawn_at?: string | null; resubmits_event_id?: string | null; filed_by?: string;
}
export interface ClaimQuantity { claim_quantity_id: string; assignment_uid: string | null; resource_code: string | null; reported_qty: Num; reported_uom: string; qty_basis: string; normalized_qty: Num | null; normalized_uom: string | null; reported_resource?: string | null }
export interface ClaimDetail extends ClaimListItem {
  raw_claim_text: string; claimed_start: string | null; claimed_finish: string | null; location: string | null; input_channel: string; document_id: string | null; clarification_answer: string | null;
  withdrawn_reason: string | null; field_provenance: Record<string, any> | null; quantities: ClaimQuantity[];
  evidence: { document_id: string; kind: string; file_name: string; sha256: string }[];
  validations: { rule_code: string; severity: string; description: string }[];
  candidates: { activity_uid: string; rank_order: number; composite_confidence: Num; match_tier: string }[];
  decisions: Decision[];
}
export interface Decision {
  decision_id: string; action: 'APPROVE' | 'EDIT' | 'REJECT' | 'HOLD'; method: string; justification: string | null; decided_at: string; approved_pct: Num | null;
  overrun_ack: boolean; overrun_ack_note: string | null; applied: AppliedRow[]; result: Partial<DecisionResult>;
}
export interface AppliedRow { assignment_uid: string; resource: string; unit: string; baseline_qty: Num; head_cumulative: Num; approved_cumulative: Num; incremental: Num; source: 'CLAIM' | 'MANUAL' | 'PCT' | string; claim_quantity_id: string | null; overrun_pct: Num; beyond_tolerance: boolean }
export interface DecisionIn {
  action: 'APPROVE' | 'EDIT' | 'REJECT' | 'HOLD'; justification?: string; method?: 'QUANTITIES_AS_CLAIMED' | 'MANUAL_QUANTITIES' | 'APPLY_PCT_TO_ASSIGNMENTS' | 'PCT_ONLY_ACTIVITY';
  activity_uid?: string; approved_quantities?: Record<string, { cumulative?: number | string; increment?: number | string }>; apply_pct?: number | string;
  actual_start?: string; actual_finish?: string; overrun_ack_note?: string; short_close_note?: string; clarification_question?: string;
}
export interface DecisionResult { activity_pct_before: Num; activity_pct_after: Num; start_inferred: boolean; short_close_acknowledged: boolean; overruns: any[]; tolerance_pct: Num; completion_threshold_pct: Num; [k: string]: any }
export interface DecisionPreview { ok: boolean; action?: string; method?: string; applied?: AppliedRow[]; result?: DecisionResult; requires_overrun_acknowledgement?: boolean; error?: { code: string; message: string; details?: any } }
export interface DecisionDone { decision_id: string; claim_id: string; action: string; status: string; method: string; applied: AppliedRow[]; result: Partial<DecisionResult> }
export interface ClaimCounts { REPORTED: number; EXTRACTED: number; MATCHED: number; VALIDATED: number; DISPUTED: number; APPROVED: number; REJECTED: number; WITHDRAWN: number; pending_total: number }

// ---- documents
export interface DocumentRow {
  document_id: string; kind: string; file_name: string; mime_type: string | null; size_bytes: number | null; sha256: string; uploaded_by: string; uploaded_at: string;
  extraction_status: 'PENDING' | 'EXTRACTED' | 'NO_CLAIMS' | 'FAILED' | null; extraction_method: string | null; extraction_error: string | null; claims_extracted: number;
  page_count: number | null; captured_at?: string | null; extractable?: boolean;
}
export interface ExtractionResult {
  document_id: string; status: string; method: string; page_count: number | null; preview_only: boolean;
  claims: { source_ref: string; activity_uid: string; claim_id: string | null; outcome: 'CREATED' | 'EXISTING' | 'ELIGIBLE' }[];
  skipped: { source_ref: string; activity_ref: string; reasons: string[] }[]; counts: { created: number; existing: number; skipped: number }; note: string;
}

// ---- issues
export type Severity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export interface IssueIn {
  title: string; category_code: string; activity_uid?: string | null; stage_wbs_uid?: string | null; description?: string; severity?: Severity; blocks_work?: boolean;
  delay_started_on?: string | null; impact_days_estimated?: number | null; expected_duration_days?: number | null; evidence_document_ids?: string[];
}
export interface Issue {
  issue_id: string; title: string; category_code: string; description: string | null; severity: Severity; status: 'ACTIVE' | 'RESOLVED'; blocks_work: boolean; activity_uid: string | null;
  stage_wbs_uid: string | null; reported_by: string; reported_date: string; delay_started_on: string | null; delay_ended_on: string | null; impact_days_estimated: Num | null; impact_days_actual: Num | null;
  expected_duration_days: Num | null; root_cause_id: string | null; resolution_notes?: string | null; resolved_by?: string | null; resolved_at?: string | null; external_activity_id?: string | null;
  evidence?: { document_id: string; file_name?: string; kind?: string }[];
}
export interface RootCause { root_cause_id: string; title: string; category_code: string; summary: string | null; status: string; issues: number }
export interface MemoryEntry { memory_id: string; title: string; lessons_learned: string; corrective_action: string | null; outcome: string | null; category_code: string; delay_days: Num | null; visibility: string; recorded_at: string }
export interface Blockers { blocked_activities: string[]; blocked_stages: string[] }

// ---- notifications / audit
export interface Notification { notification_id: string; notification_type: string; title: string; body: string | null; claim_id: string | null; decision_id: string | null; issue_id: string | null; created_at: string; read_at: string | null }
export interface AuditEntry { log_id: number | string; occurred_at: string; actor_id: string | null; role: string | null; action: string; entity_type: string; entity_id: string | null; before_state: any; after_state: any }
export interface AuditVerify { valid: boolean; entries: number; broken_at: unknown }
