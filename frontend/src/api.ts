/**
 * SETUAI V7 — typed API clients and contracts.
 *
 * Every request goes through api/client.ts, which sends the bearer token and the explicit
 * project / schedule-version context (lib/apiContext). There is no mock mode: the UI only ever shows
 * what the backend returns.
 */
import { apiFetch, ApiError, BASE_URL } from './api/client';
import { apiContext, requestHeaders } from '@/lib/apiContext';
import { getAuthToken } from '@/lib/authToken';

export { ApiError, apiFetch, BASE_URL };

// ─── Canonical PRD Enums & Types ──────────────────────────────────────────────

export type UserRole = 'SITE_ENGINEER' | 'SUPERVISOR';

export type InputChannel =
  | 'FILE_UPLOAD'
  | 'SCANNED_OCR'
  | 'TYPED_TEXT'
  | 'VOICE'
  | 'SCHEDULE_EXPORT';

export type EventType =
  | 'ACTUAL_START'
  | 'ACTUAL_FINISH'
  | 'PROGRESS_UPDATE'
  | 'DELAY'
  | 'BLOCKER';

export type ClaimMode = 'CUMULATIVE_PCT' | 'INCREMENTAL_QUANTITY';

export type DecisionAction = 'APPROVE' | 'EDIT' | 'REJECT' | 'HOLD';

export type ClaimStatus =
  | 'EXTRACTED'
  | 'MATCHED'
  | 'UNMATCHED'
  | 'VALIDATED'
  | 'REVIEW_REQUIRED'
  | 'APPROVED'
  | 'EDITED'
  | 'REJECTED'
  | 'HOLD';

export type Discipline =
  | 'CIVIL'
  | 'PIPING'
  | 'STATIC_ROTATING_EQUIPMENT'
  | 'ELECTRICAL'
  | 'INSTRUMENTATION'
  | 'HSE';

export const DISCIPLINES: Discipline[] = [
  'CIVIL',
  'PIPING',
  'STATIC_ROTATING_EQUIPMENT',
  'ELECTRICAL',
  'INSTRUMENTATION',
  'HSE',
];

export type ExecutionState =
  | 'NOT_STARTED'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'ON_HOLD'
  | 'REOPEN_REQUESTED'
  | 'REOPENED';

export interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
}

export type ClarificationStatus = 'NONE' | 'PENDING' | 'ANSWERED' | 'RESOLVED';

export interface ExecutionEvent {
  event_id: string;
  document_id: string | null;
  schedule_id: string;
  event_date: string; // YYYY-MM-DD
  raw_claim_text: string;
  input_channel: InputChannel;
  language_detected: string | null;
  reported_activity_id: string | null;
  matched_activity_id: string | null;
  discipline: Discipline | null;
  action: string | null;
  event_type: EventType | null;
  claim_mode: ClaimMode;
  asset_tag: string | null;
  location: string | null;
  claimed_quantity: number | null;
  claimed_uom: string | null;
  claimed_pct: number | null;
  delay_reason: string | null;
  supervisor_id: string | null;
  photo_path: string | null;
  status: ClaimStatus;
  created_at: string;
  clarification_status?: ClarificationStatus | null;
  clarification_question?: string | null;
  clarification_answer?: string | null;
  priority_score?: number | null;
  priority_reasons?: string[] | null;
  is_escalated?: boolean | null;
  priority_rank?: number | null;
  field_provenance?: Record<string, FieldProvenance | FieldProvenanceSource> | null;
  is_completed_activity_target?: boolean;
}

export interface SourceReference {
  reference_id: string;
  event_id: string;
  file_name: string | null;
  sheet_name: string | null;
  row_cell_ref: string | null;
  message_id: string | null;
  raw_snippet: string;
}

export interface CandidateMatch {
  candidate_id: string;
  event_id: string;
  schedule_id: string;
  project_id?: string;
  activity_id: string;
  stage_id?: string;
  rank_order: 1 | 2 | 3;
  match_tier: 'EXACT' | 'SEMANTIC' | 'FUZZY' | 'DISCIPLINE_LOCATION' | 'COMPLETED_PROTECTED' | 'STAGE_COMPLETED' | string | null;
  composite_confidence: number;
  semantic_score: number | null;
  fuzzy_score: number | null;
  location_score: number | null;
  discipline_score: number | null;
  supporting_signals: string | null;
  disqualifying_signals: string | null;
  execution_state?: ExecutionState;
  eligibility_explanation?: string;
  eligibility_reasons?: string[];
  stage_name?: string;
  wbs_code?: string;
  is_eligible?: boolean;
  is_completed_protected?: boolean;
  is_stage_completed?: boolean;
}

export interface ReopenRequest {
  request_id?: string;
  reopen_id?: string;
  activity_id: string;
  activity_name?: string;
  schedule_id: string;
  project_id: string;
  event_id?: string | null;
  requested_by?: string;
  requested_by_role?: UserRole;
  requested_by_name?: string;
  requested_at?: string;
  created_at?: string;
  reason: string;
  justification?: string;
  status: 'PENDING' | 'APPROVED' | 'REJECTED';
  original_actual_finish?: string | null;
  original_actual_pct?: number | null;
  evidence_ref?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  review_comments?: string | null;
  supervisor_notes?: string | null;
  locked_actuals_summary?: {
    actual_start: string | null;
    actual_finish: string | null;
    actual_pct: number;
    actual_qty?: number | null;
  };
}

export interface ConflictRecord {
  conflict_id: string;
  schedule_id: string;
  activity_id: string;
  reporting_period: string;
  event_id_a: string;
  event_id_b: string;
  value_a: number;
  value_b: number;
  variance_pct: number;
  status: 'OPEN' | 'RESOLVED';
}

export interface ValidationIssue {
  issue_id: string;
  event_id: string;
  rule_code: string | null;
  severity: 'WARNING' | 'ERROR' | null;
  description: string;
}

export interface PlannerDecision {
  decision_id: string;
  event_id: string;
  selected_activity_id: string;
  action: DecisionAction;
  approved_pct: number | null;
  approved_qty: number | null;
  planner_id: string;
  justification: string;
  decided_at: string;
}

export interface Contractor {
  id: string;
  projectId: string;
  name: string;
  code: string;
  description?: string;
  status: 'ACTIVE' | 'INACTIVE' | 'ON_HOLD';
  contactName?: string;
  contactRole?: string;
  contactEmail?: string;
  contactPhone?: string;
  active: boolean;
}

export interface WorkPackage {
  id: string;
  projectId: string;
  scheduleId: string;
  contractorId: string;
  name: string;
  code: string;
  description?: string;
  discipline: Discipline | string;
  stageId?: string;
  wbsId?: string;
  status: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED' | 'ON_HOLD';
  plannedStart?: string;
  plannedFinish?: string;
  activityIds: string[];
}

export type QualityGateType = 'HOLD_POINT' | 'WITNESS_POINT' | 'REVIEW_POINT' | 'ITP_CHECK';
export type QualityGateStatus = 'PENDING' | 'READY' | 'COMPLETED' | 'BLOCKED' | 'WAIVED';

export interface QualityGate {
  id: string;
  projectId: string;
  scheduleId: string;
  stageId?: string;
  wbsId?: string;
  activityId: string;
  gateType: QualityGateType;
  name: string;
  description: string;
  status: QualityGateStatus;
  required: boolean;
  sequence: number;
  ownerRole?: UserRole | 'QA_QC_INSPECTOR' | string;
  dueDate?: string;
  completedAt?: string | null;
  completedBy?: string | null;
  evidenceRequired: boolean;
  evidenceIds?: string[];
  waiverJustification?: string | null;
  waivedBy?: string | null;
  waivedAt?: string | null;
}

export interface QualityGateSummary {
  totalGates: number;
  completedGates: number;
  pendingRequiredHoldPoint: boolean;
  blockedRequiredHoldPoint: boolean;
  hasBlockingHoldPoint: boolean;
  blockingHoldPointName?: string;
  gates: QualityGate[];
}

export interface ScheduleActivity {
  activity_id: string;
  schedule_id: string;
  activity_name: string;
  wbs_code: string | null;
  discipline: Discipline;
  location: string;
  asset_tag: string | null;
  planned_start: string;
  planned_finish: string;
  planned_quantity: number | null;
  uom: string | null;
  baseline_pct_complete: number;
  total_float?: number | null;
  is_critical?: boolean | null;
  execution_state?: ExecutionState;
  actual_start?: string | null;
  actual_finish?: string | null;
  actual_pct_complete?: number | null;
  stage_id?: string | null;
  stage_name?: string | null;
  is_stage_completed?: boolean;
  weight?: number;
  contractor_id?: string | null;
  contractor_name?: string | null;
  work_package_id?: string | null;
  work_package_code?: string | null;
  work_package_name?: string | null;
}

/** Server-computed impact (backend ImpactService, V7 Phase 10): deterministic propagation over the stored network. */
export type ImpactSeverity = 'NONE' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export interface ActivityImpactItem {
  activity_id: string;
  activity_name: string;
  stage_id: string | null;
  stage_name: string | null;
  is_critical: boolean;
  canonical_state: string;
  workflow_condition: string;
  baseline_start: string | null;
  baseline_finish: string | null;
  shifted_start: string | null;
  shifted_finish: string | null;
  gross_delay_days: number;
  total_float: number | null;
  float_status: string;
  absorbed_delay_days: number | null;
  residual_delay_days: number | null;
  controlling_predecessor: string | null;
  controlling_relationship: string | null;
  lag_days: number;
  propagation_depth: number;
  causal_path: string[];
  classification: string;
  explanation: string;
}

export interface StageImpactItem {
  stage_id: string;
  stage_name: string;
  affected_activities_count: number;
  max_stage_delay_days: number;
  stage_progression_impact: string;
}

export interface ImpactScenarioResult {
  scenario_id: string | null;
  project_id: string;
  schedule_id: string;
  name: string;
  affected_activities: ActivityImpactItem[];
  affected_stages: StageImpactItem[];
  float_analysis: {
    total_activities_evaluated: number;
    activities_with_known_float: number;
    activities_with_unknown_float: number;
    total_float_absorbed_days: number;
    critical_path_delays_count: number;
  };
  schedule_impact_days: number;
  project_completion_impact_days: number;
  severity: ImpactSeverity;
  has_cycle: boolean;
  cycle_path: string[] | null;
  calculated_at: string;
  algorithm_version: string;
}

export interface ScheduleDependency {
  dependency_id: string;
  schedule_id: string;
  predecessor_activity_id: string;
  successor_activity_id: string;
  relationship_type: 'FS' | 'SS' | 'FF' | 'SF';
}

export interface ImpactConstraintItem {
  predecessor_activity_id: string;
  successor_activity_id: string;
  relationship_type: string;
  lag_days: number;
  constraint_dimension: string;
  required_successor_start: string | null;
  required_successor_finish: string | null;
  baseline_successor_start: string | null;
  gross_delay_days: number;
  is_controlling: boolean;
  uncertainty: boolean;
  uncertainty_reason: string | null;
}

export interface ImpactEvaluationItem {
  successor_activity_id: string;
  activity_name: string;
  dependency_type: string;
  original_earliest_start: string;
  shifted_earliest_start: string;
  original_planned_finish: string;
  shifted_earliest_finish: string;
  propagation_depth: number;
  target_path: string[];
  execution_state: string;
  gross_delay_days: number;
  total_float: number | null;
  float_status: 'KNOWN' | 'UNKNOWN';
  absorbed_delay_days: number | null;
  net_delay_days: number | null;
  controlling_predecessor: string | null;
  controlling_relationship: string | null;
  uncertainty: boolean;
  classification: string;
  constraints_evaluated?: ImpactConstraintItem[];
}

export interface ImpactPreviewResult {
  activity_id: string;
  activity_name?: string;
  planned_start?: string;
  planned_finish?: string;
  shifted_finish?: string;
  schedule_id?: string;
  delay_days: number;
  propagation_depth_limit?: number;
  disclaimer: string;
  impacts: ImpactEvaluationItem[];
  successors: {
    successor_activity_id: string;
    activity_name: string;
    relationship_type: string;
    original_start: string;
    original_finish: string;
    shifted_start: string;
    shifted_finish: string;
    lag_days: number;
    depth?: number;
    net_delay_days?: number | null;
    execution_state?: string;
  }[];
}

// ─── Feature 30A: WBS Activity Explorer (read-only) ──────────────────────────

export interface WBSGroupActivity {
  activity_id: string;
  planned_quantity: number | null;
}

export interface WBSGroup {
  wbs_code: string;
  activities: WBSGroupActivity[];
}

export interface WBSTreeResponse {
  schedule_id: string;
  wbs_groups: WBSGroup[];
}

// ─── Feature 30: WBS Granularity Bridge (Split Editor) ──────────────────────

export type SplitBasis = 'EQUAL' | 'WBS_WEIGHTED' | 'MANUAL';

export interface WBSSplitItem {
  split_id?: string;
  event_id: string;
  activity_id: string;
  split_basis: SplitBasis;
  split_pct: number;
  allocated_quantity?: number | null;
  uom?: string | null;
  rationale?: string | null;
  created_at?: string;
}

export interface WBSSplitAllocation {
  activity_id: string;
  split_basis?: SplitBasis;
  split_pct?: number;
  allocated_pct?: number | null;
  allocated_quantity?: number | null;
  uom?: string | null;
  rationale?: string | null;
}

export interface WBSSplitRequest {
  event_id: string;
  schedule_id: string;
  allocations: WBSSplitAllocation[];
  justification?: string;
}

export interface WBSSplitResponse {
  event_id: string;
  status: string;
  created_decisions?: PlannerDecision[];
  splits?: WBSSplitItem[];
  message?: string;
}

// ─── Feature 33: Fine-Grained Field Provenance ───────────────────────────────

export type FieldProvenanceSource =
  | 'AI_EXTRACTED'
  | 'SCHEDULE_AUTO_FILLED'
  | 'ENGINEER_ENTERED'
  | 'SUPERVISOR_EDITED';

export interface FieldProvenance {
  field_name: string;
  source: FieldProvenanceSource;
  source_detail?: string | null;
  confidence?: number | null;
  timestamp?: string | null;
  actor?: string | null;
}

// ─── Feature 34: Ask Why (Graph Traversal & Explanation) ─────────────────────

export interface AskWhyEntity {
  name: string;
  type: string;
  role: string;
}

export interface AskWhyRequest {
  event_id?: string;
  activity_id?: string;
  depth?: number;
  question?: string;
}

export interface AskWhyResponse {
  activity_id?: string;
  event_id?: string;
  explanation: string;
  traversal_depth?: number | null;
  reasoning_steps?: string[] | null;
  entities_involved?: AskWhyEntity[] | null;
  evidence_references?: string[] | null;
}

// ─── Feature 35: AI Execution Summary ────────────────────────────────────────

export interface ExecutionSummaryFilter {
  language?: string; // display language (en/hi/te); canonical stored summary stays English
  start?: string;
  end?: string;
  start_date?: string; // backwards compatibility alias
  end_date?: string;   // backwards compatibility alias
  discipline?: Discipline | string;
  schedule_id?: string;
}

export interface ExecutionSummaryMetrics {
  total_claims_processed?: number;
  approval_rate_pct?: number;
  open_conflicts_count?: number;
  high_priority_escalations?: number;
  top_delay_drivers?: { reason: string; count: number }[];
  disciplines_active?: string[];
}

export interface ExecutionSummaryAggregate {
  period: { type: string; start: string; end: string };
  discipline: string;
  claims: {
    total_claims: number;
    by_status: Record<string, number>;
    by_event_type: Record<string, number>;
  };
  approved_progress: {
    total_approved: number;
    activities_with_actuals: number;
    avg_approved_pct: number;
  };
  conflicts: {
    total_conflicts: number;
    by_status: Record<string, number>;
  };
  validation_issues: {
    total_issues: number;
    by_severity: Record<string, number>;
  };
  delays: {
    total_delay_events: number;
    reasons: Record<string, number>;
  };
  activities: {
    total: number;
    completed: number;
    in_progress: number;
    not_started: number;
  };
  forecast: {
    status: string;
    historical_ratio: number | null;
    note?: string;
  };
}

export interface ExecutionSummaryResponse {
  period: { type: string; start: string; end: string };
  discipline: string;
  aggregate: ExecutionSummaryAggregate;
  canonical_summary: string;
  summary: string;
  language: string;
  cached: boolean;
  generated_by: 'llm' | 'deterministic_fallback';
}

export interface ExecutionReportResponse {
  reporting_period: {
    start_date: string;
    end_date: string;
  };
  discipline?: Discipline | string | null;
  schedule_id?: string | null;
  summary_text: string;
  metrics?: ExecutionSummaryMetrics | null;
  key_highlights?: string[] | null;
  generated_at: string;
}

export interface AuditLogEntry {
  log_id: number;
  entity_type: string;
  entity_id: string;
  action: string;
  actor_id: string;
  before_state: string | null;
  after_state: string | null;
  payload_hash: string;
  previous_hash: string;
  current_hash: string;
  timestamp: string;
}

export interface DisciplineForecastItem {
  activity_id: string;
  discipline: string;
  planned_duration: number | null;
  historical_ratio: number | null;
  forecast_duration: number | null;
  slippage_days: number;
}

export interface DisciplineForecastData {
  discipline: string;
  historical_ratio: number | null;
  activities: DisciplineForecastItem[];
  total_activities: number;
}



// ─── API Methods ─────────────────────────────────────────────────────────────

export const authApi = {
  getMe: async (hintEmail?: string): Promise<UserProfile> => {
    const data: any = await apiFetch('/api/v1/auth/me');
    return { ...data, email: data.email || '' };
  },
};

// ─── Backend → UI shape normalisation ────────────────────────────────────────
// The backend stores priority_reasons as newline-separated "[Tag] explanation" lines
// and scores are unbounded points (routine ≈ 5, critical-path sequence error ≥ 200).
// The UI works with a list of reasons, a rank and an escalation flag.
const ESCALATION_SCORE_THRESHOLD = 100; // >= one critical-severity issue (base 100) or worse

function normalizeEvent(raw: any, rank?: number): ExecutionEvent {
  if (!raw || typeof raw !== 'object') return raw;
  const reasons =
    typeof raw.priority_reasons === 'string'
      ? raw.priority_reasons
          .split('\n')
          .map((l: string) => l.replace(/^\[[^\]]+\]\s*/, '').trim())
          .filter(Boolean)
      : raw.priority_reasons ?? null;
  const score = raw.priority_score == null ? null : Number(raw.priority_score);
  return {
    ...raw,
    priority_score: score,
    priority_reasons: reasons,
    is_escalated: raw.is_escalated ?? (score != null ? score >= ESCALATION_SCORE_THRESHOLD : null),
    priority_rank: rank ?? raw.priority_rank ?? null,
  } as ExecutionEvent;
}

export const claimsApi = {
  submitText: async (
    text: string,
    evidenceFile?: File | null
  ): Promise<{ event: ExecutionEvent }> => {
    if (evidenceFile) {
      const res = await claimsApi.submitFile(evidenceFile, {
        purpose: 'EVIDENCE_PHOTO',
        rawClaimText: text,
      });
      return { event: res.events[0] };
    }
    const data = await apiFetch('/api/v1/claims/text', {
      method: 'POST',
      body: JSON.stringify({ raw_claim_text: text, input_channel: 'TYPED_TEXT' }),
    });
    return { event: data as any };
  },

  // Submit a schedule-export file (P6 / MSP .xer, .xml, .csv, .xlsx) as field execution progress claims
  submitScheduleExport: async (file: File): Promise<{ events: ExecutionEvent[] }> => {
    const formData = new FormData();
    formData.append('file', file);
    const data = await apiFetch<ExecutionEvent[]>('/api/v1/claims/schedule-export', {
      method: 'POST',
      body: formData,
    });
    return { events: data };
  },

  submitFile: async (
    file: File,
    options?: { purpose?: 'EVIDENCE_PHOTO' | 'SCANNED_DIARY'; rawClaimText?: string }
  ): Promise<{ events: ExecutionEvent[] }> => {
    const form = new FormData();
    form.append('file', file);
    if (options?.purpose) form.append('purpose', options.purpose);
    if (options?.rawClaimText) form.append('raw_claim_text', options.rawClaimText);
    const token = getAuthToken();
    const headers: Record<string, string> = { ...apiContext.headers() };   // explicit project / schedule context, like every other request
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const res = await fetch(`${BASE_URL}/api/v1/claims/file`, {
      method: 'POST',
      headers,
      body: form,
    });
    if (!res.ok) throw new Error(await res.text().catch(() => 'File submit failed'));
    const data = await res.json();
    return { events: data as ExecutionEvent[] };
  },

  match: async (eventId: string): Promise<{ status: ClaimStatus; matches: CandidateMatch[] }> => {
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/match`, { method: 'POST' });
    return { status: data.status, matches: data.candidates || [] };
  },

  check: async (eventId: string): Promise<{ status: ClaimStatus; issues: ValidationIssue[] }> => {
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/check`, { method: 'POST' });
    return { status: data.status, issues: data.validation_issues || [] };
  },

  clarify: async (eventId: string, answer: string): Promise<{ event: ExecutionEvent }> => {
    const data = await apiFetch(`/api/v1/claims/${eventId}/clarify`, {
      method: 'POST',
      body: JSON.stringify({ answer }),
    });
    return { event: data as any };
  },

  getCandidates: async (eventId: string): Promise<CandidateMatch[]> => {
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/candidates`);
    return data.candidates || [];
  },

  getConflicts: async (eventId: string): Promise<ConflictRecord[]> => {
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/conflicts`);
    return data.conflicts || [];
  },

  getValidation: async (eventId: string): Promise<ValidationIssue[]> => {
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/validation`);
    return data.validation_issues || [];
  },

  getPhotoBlobUrl: async (eventId: string): Promise<string> => {
    const blob = await apiFetch<Blob>(`/api/v1/claims/${eventId}/photo`, { responseType: 'blob' });
    return URL.createObjectURL(blob);
  },

  askWhy: async (request: AskWhyRequest): Promise<AskWhyResponse> => {
    const activityId = request.activity_id;
    if (!activityId) throw new Error('askWhy requires an activity_id');
    const depth = request.depth ?? 2;

    const qs = new URLSearchParams({ depth: String(depth) });
    if (request.event_id) qs.set('event_id', request.event_id);
    const data = await apiFetch(`/api/v1/graph/explain/${encodeURIComponent(activityId)}?${qs.toString()}`);
    return data as AskWhyResponse;
  },

  getReviewQueue: async (sort: string = 'priority'): Promise<ExecutionEvent[]> => {
    const data: any = await apiFetch(`/api/v1/review-queue?sort=${sort}`);
    const items: any[] = Array.isArray(data)
      ? data
      : data.items || data.review_queue || data.queue || data.claims || [];
    return items.map((it, i) => normalizeEvent(it, i + 1));
  },

  getEvent: async (eventId: string): Promise<ExecutionEvent> => {
    return normalizeEvent(await apiFetch(`/api/v1/claims/${eventId}`));
  },
};

export const digestApi = {
  getByDate: async (dateStr: string): Promise<ExecutionEvent[]> => {
    const rows: any[] = await apiFetch(`/api/v1/digest?date=${dateStr}`);
    return rows.map((r) => normalizeEvent(r));
  },

  getAll: async (): Promise<ExecutionEvent[]> => {
    const rows: any[] = await apiFetch('/api/v1/digest');
    return rows.map((r) => normalizeEvent(r));
  },

  bulkApprove: async (eventIds: string[]): Promise<{ approved: string[]; failed: string[] }> => {
    const data: any = await apiFetch('/api/v1/digest/bulk-approve', {
      method: 'POST',
      body: JSON.stringify({ event_ids: eventIds }),
    });
    return { approved: data.approved || [], failed: (data.failed || []).map((f: any) => typeof f === 'string' ? f : f.event_id) };
  },
};

export const decisionsApi = {
  submit: async (payload: {
    event_id: string;
    selected_activity_id: string;
    action: DecisionAction;
    approved_pct?: number | null;
    approved_qty?: number | null;
    justification: string;
  }): Promise<PlannerDecision> => {
    const data: any = await apiFetch('/api/v1/decisions', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return {
      decision_id: data.decision_id,
      event_id: data.event_id,
      selected_activity_id: data.selected_activity_id,
      action: data.action as DecisionAction,
      approved_pct: payload.approved_pct ?? null,
      approved_qty: payload.approved_qty ?? null,
      planner_id: '',
      justification: payload.justification,
      decided_at: new Date().toISOString(),
    };
  },

  getRecent: async (): Promise<PlannerDecision[]> => {
    return apiFetch('/api/v1/decisions?limit=10');
  },
};

// ── Quality gates (V7: /projects/{pid}/schedules/{sid}/quality-gates, pass/fail/waive) ──────────────
interface BackendGate {
  quality_gate_id: string;
  project_id: string;
  schedule_id: string | null;
  stage_id: string | null;
  activity_id: string | null;
  gate_name: string;
  gate_type: string;
  checkpoint_category: 'HOLD' | 'WITNESS' | 'REVIEW' | 'QUALITY_CHECK';
  required: boolean;
  status: 'NOT_REQUIRED' | 'PENDING' | 'SUBMITTED' | 'PASSED' | 'FAILED' | 'WAIVED';
  due_date: string | null;
  passed_at: string | null;
  passed_by: string | null;
  waived_at: string | null;
  waived_by: string | null;
  waiver_reason: string | null;
  remarks: string | null;
}

const GATE_TYPE: Record<string, QualityGateType> = {
  HOLD: 'HOLD_POINT', WITNESS: 'WITNESS_POINT', REVIEW: 'REVIEW_POINT', QUALITY_CHECK: 'ITP_CHECK',
};
const GATE_STATUS: Record<string, QualityGateStatus> = {
  PASSED: 'COMPLETED', FAILED: 'BLOCKED', WAIVED: 'WAIVED', SUBMITTED: 'READY', PENDING: 'PENDING', NOT_REQUIRED: 'PENDING',
};

function toGate(g: BackendGate, index: number): QualityGate {
  return {
    id: g.quality_gate_id,
    projectId: g.project_id,
    scheduleId: g.schedule_id ?? '',
    stageId: g.stage_id ?? undefined,
    activityId: g.activity_id ?? '',
    gateType: GATE_TYPE[g.checkpoint_category] ?? 'ITP_CHECK',
    name: g.gate_name,
    description: [g.gate_type.replace(/_/g, ' '), g.remarks].filter(Boolean).join(' — '),
    status: GATE_STATUS[g.status] ?? 'PENDING',
    required: g.required,
    sequence: index + 1,
    dueDate: g.due_date ?? undefined,
    completedAt: g.passed_at,
    completedBy: g.passed_by,
    evidenceRequired: g.checkpoint_category === 'HOLD',
    waiverJustification: g.waiver_reason,
    waivedBy: g.waived_by,
    waivedAt: g.waived_at,
  };
}

const projectBase = () => {
  const pid = apiContext.getProjectId();
  if (!pid) throw new ApiError(400, 'Select a project to continue.', '', 'INVALID_PROJECT_CONTEXT');
  return `/api/v1/projects/${encodeURIComponent(pid)}`;
};

export const qualityGatesApi = {
  /** Gates of the selected schedule version (or one activity). */
  getGates: async (params?: { scheduleId?: string; activityId?: string }): Promise<QualityGate[]> => {
    const sid = params?.scheduleId ?? (await getActiveScheduleId());
    const rows = await apiFetch<BackendGate[]>(`${projectBase()}/schedules/${encodeURIComponent(sid)}/quality-gates`);
    const filtered = params?.activityId ? rows.filter((g) => g.activity_id === params.activityId) : rows;
    return filtered.map(toGate);
  },

  /** Inspector/supervisor releases the gate (the server requires the role permission and records who and when). */
  completeGate: async (gateId: string, remarks?: string): Promise<QualityGate> => {
    const g = await apiFetch<BackendGate>(`${projectBase()}/quality-gates/${encodeURIComponent(gateId)}/pass`, {
      method: 'POST', body: JSON.stringify({ remarks }),
    });
    return toGate(g, 0);
  },

  /** Inspection record for a gate (a required HOLD point is only released with a PASS record). */
  recordEvidence: async (
    gateId: string,
    evidence: { evidence_type: string; result: 'PASS' | 'FAIL' | 'PENDING_REVIEW'; inspector_name?: string; notes?: string },
  ): Promise<void> => {
    await apiFetch(`${projectBase()}/quality-gates/${encodeURIComponent(gateId)}/evidence`, {
      method: 'POST',
      body: JSON.stringify({
        evidence_type: evidence.evidence_type,
        result: evidence.result,
        inspector_name: evidence.inspector_name,
        inspection_date: new Date().toISOString().slice(0, 10),
        metadata: evidence.notes ? { notes: evidence.notes } : {},
      }),
    });
  },

  failGate: async (gateId: string, remarks?: string): Promise<QualityGate> => {
    const g = await apiFetch<BackendGate>(`${projectBase()}/quality-gates/${encodeURIComponent(gateId)}/fail`, {
      method: 'POST', body: JSON.stringify({ remarks }),
    });
    return toGate(g, 0);
  },

  waiveGate: async (gateId: string, justification: string): Promise<QualityGate> => {
    const g = await apiFetch<BackendGate>(`${projectBase()}/quality-gates/${encodeURIComponent(gateId)}/waive`, {
      method: 'POST', body: JSON.stringify({ waiver_reason: justification }),
    });
    return toGate(g, 0);
  },
};

export const dashboardApi = {
  getSummary: async (): Promise<{
    total_claims: number;
    pending_review: number;
    actuals: number;
    conflicts: number;
    discipline_breakdown: { discipline: string; name: string; count: number; value: number }[];
    claims_trend_pct: number | null;
  }> => {
    const data: any = await apiFetch('/api/v1/dashboard/summary');
    return { ...data, claims_trend_pct: data.claims_trend_pct ?? null };
  },

  getDelayReasons: async (): Promise<{ reason: string; count: number }[]> => {
    const data: any = await apiFetch('/api/v1/dashboard/delay-reasons');
    return (data.delay_reasons || []).map((d: any) => ({ reason: d.delay_reason, count: d.count }));
  },

  getInstitutionalMemory: async (): Promise<{ topic: string; resolution: string; count: number }[]> => {
    const data: any = await apiFetch('/api/v1/dashboard/institutional-memory');
    return (data.activities || []).map((a: any) => ({ topic: a.activity_id + ' (' + a.discipline + ')', resolution: a.variance_days !== null ? (a.variance_days > 0 ? a.variance_days + ' days delayed' : Math.abs(a.variance_days) + ' days ahead') : 'No actuals yet', count: a.planned_duration || 0 }));
  },

  getForecast: async (discipline: string = 'CIVIL'): Promise<DisciplineForecastData> => {
    const data: any = await apiFetch(`/api/v1/dashboard/forecast?discipline=${encodeURIComponent(discipline)}`);
    const activities = (data.activities || (data.activity_id ? [data] : [])).map((a: any) => ({
      activity_id: a.activity_id || 'Unknown',
      discipline: a.discipline || discipline,
      planned_duration: a.planned_duration,
      historical_ratio: a.historical_ratio,
      forecast_duration: a.forecast_duration,
      slippage_days: (a.forecast_duration != null && a.planned_duration != null) ? a.forecast_duration - a.planned_duration : 0,
    }));
    return {
      discipline: data.discipline || discipline,
      historical_ratio: data.historical_ratio ?? null,
      total_activities: data.total_activities || activities.length,
      activities,
    };
  },

  getSilentActivities: async (): Promise<ScheduleActivity[]> => {
    const data: any = await apiFetch('/api/v1/alerts/silent-activities');
    return (data.silent_activities || []).map((a: any) => ({ ...a, asset_tag: a.asset_tag || null, uom: a.uom || null, baseline_pct_complete: a.baseline_pct_complete || 0 }));
  },

  getExportCsvUrl: (): string => {
    return `${BASE_URL}/api/v1/export/csv`;
  },

  exportCsv: async (): Promise<Blob> => {
    return apiFetch<Blob>('/api/v1/export/csv', { responseType: 'blob' });
  },
};

export interface ActivityTimelineItem {
  type: 'execution_event' | 'planner_decision' | 'approved_actual';
  timestamp: string | null;
  event_id?: string;
  schedule_id?: string | null;
  event_date?: string | null;
  raw_claim_text?: string;
  claim_mode?: string;
  claimed_pct?: number | null;
  claimed_quantity?: number | null;
  delay_reason?: string | null;
  status?: string | null;
  photo_path?: string | null;
  document_id?: string | null;
  source_references?: {
    reference_id: string;
    file_name: string | null;
    sheet_name: string | null;
    row_cell_ref: string | null;
    message_id: string | null;
    raw_snippet: string;
  }[];
  decision_id?: string;
  selected_activity_id?: string;
  action?: string;
  approved_pct?: number | null;
  approved_qty?: number | null;
  planner_id?: string | null;
  justification?: string;
  actual_id?: string;
  activity_id?: string;
  actual_start?: string | null;
  actual_finish?: string | null;
  actual_pct_complete?: number | null;
  actual_quantity?: number | null;
}

export interface ActivitySummary {
  activity_id: string;
  activity_name: string;
  schedule_id: string;
  discipline: Discipline;
  location: string;
  asset_tag: string | null;
  wbs_code: string | null;
  planned_start: string | null;
  planned_finish: string | null;
  planned_quantity: number | null;
  uom: string | null;
  baseline_pct_complete: number;
  actual_start: string | null;
  actual_finish: string | null;
  actual_pct_complete: number | null;
  execution_state: ExecutionState;
  is_critical: boolean | null;
  total_float: number | null;
  has_changes: boolean;
  event_count: number;
  last_changed_at: string | null;
}

export interface ActivityMetrics {
  total: number;
  in_progress: number;
  completed: number;
  not_started: number;
  critical: number;
  changed: number;
}

export interface ActivityListResponse {
  items: ActivitySummary[];
  total: number;
  page: number;
  page_size: number;
  schedule_id: string;
  metrics: ActivityMetrics;
}

export interface ActivityFilterParams {
  schedule_id?: string;
  search?: string;
  discipline?: string;
  location?: string;
  wbs_code?: string;
  execution_state?: string;
  is_critical?: string;
  float_range?: string;
  has_changes?: boolean;
  change_recency?: string;
  page?: number;
  page_size?: number;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
}

export const activitiesApi = {
  getActivities: async (params?: ActivityFilterParams): Promise<ActivityListResponse> => {

    const query = new URLSearchParams();
    if (params) {
      if (params.schedule_id) query.set('schedule_id', params.schedule_id);
      if (params.search) query.set('search', params.search);
      if (params.discipline) query.set('discipline', params.discipline);
      if (params.location) query.set('location', params.location);
      if (params.wbs_code) query.set('wbs_code', params.wbs_code);
      if (params.execution_state) query.set('execution_state', params.execution_state);
      if (params.is_critical) query.set('is_critical', params.is_critical);
      if (params.float_range) query.set('float_range', params.float_range);
      if (params.has_changes !== undefined) query.set('has_changes', String(params.has_changes));
      if (params.change_recency) query.set('change_recency', params.change_recency);
      if (params.page) query.set('page', String(params.page));
      if (params.page_size) query.set('page_size', String(params.page_size));
      if (params.sort_by) query.set('sort_by', params.sort_by);
      if (params.sort_order) query.set('sort_order', params.sort_order);
    }
    const qs = query.toString();
    return apiFetch<ActivityListResponse>(`/api/v1/activities${qs ? `?${qs}` : ''}`);
  },

  getHistory: async (activityId: string, scheduleIdArg?: string): Promise<{
    activity: ScheduleActivity | null;
    timeline: ActivityTimelineItem[];
    history: {
      timestamp: string;
      raw_claim_text: string;
      input_channel: InputChannel;
      claimed_pct: number | null;
      claimed_qty: number | null;
      status: ClaimStatus;
      supervisor_action: string | null;
      actor: string;
    }[];
  }> => {
    const historyQs = scheduleIdArg ? `?schedule_id=${encodeURIComponent(scheduleIdArg)}` : '';
    const data: any = await apiFetch(`/api/v1/activities/${encodeURIComponent(activityId)}/history${historyQs}`);
    const timeline: ActivityTimelineItem[] = data.timeline || [];
    const eventItems = timeline.filter((t: any) => t.type === 'execution_event');
    const decisionItem = timeline.find((t: any) => t.type === 'planner_decision');

    const scheduleId: string | undefined = data.schedule_id;
    let activity: ScheduleActivity | null = scheduleId
      ? {
          activity_id: data.activity_id,
          schedule_id: scheduleId,
          activity_name: data.activity_name || data.activity_id,
          wbs_code: data.wbs_code ?? null,
          discipline: (data.discipline || 'CIVIL') as Discipline,
          location: data.location || '',
          asset_tag: null,
          planned_start: data.planned_start || '',
          planned_finish: data.planned_finish || '',
          planned_quantity: null,
          uom: null,
          baseline_pct_complete: 0,
        }
      : null;
    if (scheduleId) {
      try {
        const enriched = await apiFetch<ScheduleActivity>(`/api/v1/schedules/${scheduleId}/activities/${activityId}`);
        activity = enriched;
      } catch {
        // keep metadata
      }
    }

    return {
      activity,
      timeline,
      history: eventItems.map((ev: any) => ({
        timestamp: ev.timestamp || ev.event_date || '',
        raw_claim_text: ev.raw_claim_text || '',
        input_channel: 'TYPED_TEXT' as InputChannel,
        claimed_pct: ev.claimed_pct,
        claimed_qty: ev.claimed_quantity,
        status: ev.status || 'EXTRACTED',
        supervisor_action: decisionItem?.action || null,
        actor: decisionItem?.planner_id || 'System',
      })),
    };
  },

  updateState: async (activityId: string, newState: ExecutionState): Promise<ScheduleActivity | null> => {
    return apiFetch<ScheduleActivity>(`/api/v1/activities/${encodeURIComponent(activityId)}/state`, {
      method: 'PATCH',
      body: JSON.stringify({ execution_state: newState }),
    });
  },
};

// ── Reopen (V7: activity-scoped lifecycle; the activity id identifies the request within a schedule) ──
export const REOPEN_REASON_OPTIONS: { value: string; label: string }[] = [
  { value: 'INCORRECT_COMPLETION', label: 'Incorrect / premature completion' },
  { value: 'CONTRADICTORY_FIELD_REPORT', label: 'Contradictory field report' },
  { value: 'QUALITY_FAILURE', label: 'Quality failure / rework' },
  { value: 'QUANTITY_CORRECTION', label: 'Quantity correction' },
  { value: 'DATE_CORRECTION', label: 'Date correction' },
  { value: 'SUPERVISOR_CORRECTION', label: 'Supervisor correction' },
  { value: 'OTHER', label: 'Other' },
];

interface BackendReopen {
  activity_id: string;
  schedule_id: string;
  project_id: string;
  reopen_status: 'NONE' | 'REQUESTED' | 'APPROVED' | 'REJECTED';
  reason: string | null;
  justification: string | null;
  requested_by: string | null;
  requested_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_notes: string | null;
  rework_instructions: string | null;
  original_actual_id: string | null;
}

function toReopen(r: BackendReopen, name?: string): ReopenRequest {
  return {
    request_id: r.activity_id,
    reopen_id: r.activity_id,
    activity_id: r.activity_id,
    activity_name: name,
    schedule_id: r.schedule_id,
    project_id: r.project_id,
    requested_by: r.requested_by ?? undefined,
    requested_at: r.requested_at ?? undefined,
    created_at: r.requested_at ?? undefined,
    reason: r.reason ?? '',
    justification: r.justification ?? undefined,
    status: r.reopen_status === 'REQUESTED' ? 'PENDING' : (r.reopen_status === 'NONE' ? 'PENDING' : r.reopen_status),
    reviewed_by: r.decided_by,
    reviewed_at: r.decided_at,
    review_comments: r.decision_notes,
    supervisor_notes: r.decision_notes,
  };
}

export const reopenApi = {
  getRequests: async (params?: { activity_id?: string; status?: string }): Promise<ReopenRequest[]> => {
    const sid = await getActiveScheduleId();
    const backendStatus = params?.status === 'PENDING' ? 'REQUESTED' : params?.status;
    const qs = backendStatus ? `?status=${encodeURIComponent(backendStatus)}` : '';
    const rows = await apiFetch<BackendReopen[]>(`${projectBase()}/schedules/${encodeURIComponent(sid)}/reopen-requests${qs}`);
    const list = rows.map((r) => toReopen(r));
    return params?.activity_id ? list.filter((r) => r.activity_id === params.activity_id) : list;
  },

  createRequest: async (payload: { activity_id: string; reason: string; justification: string; event_id?: string }): Promise<ReopenRequest> => {
    const sid = await getActiveScheduleId();
    const r = await apiFetch<BackendReopen>(
      `${projectBase()}/schedules/${encodeURIComponent(sid)}/activities/${encodeURIComponent(payload.activity_id)}/reopen`,
      {
        method: 'POST',
        body: JSON.stringify({
          reason: payload.reason,
          justification: payload.justification,
          evidence_event_ids: payload.event_id ? [payload.event_id] : [],
        }),
      },
    );
    return toReopen(r);
  },

  /** `reopen_id` is the activity id (one open reopen lifecycle per activity per schedule version). */
  reviewRequest: async (payload: { reopen_id: string; decision: 'APPROVED' | 'REJECTED'; supervisor_notes?: string; rework_instructions?: string }): Promise<ReopenRequest> => {
    const sid = await getActiveScheduleId();
    const r = await apiFetch<BackendReopen>(
      `${projectBase()}/schedules/${encodeURIComponent(sid)}/activities/${encodeURIComponent(payload.reopen_id)}/reopen/decide`,
      {
        method: 'POST',
        body: JSON.stringify({ decision: payload.decision, notes: payload.supervisor_notes, rework_instructions: payload.rework_instructions }),
      },
    );
    return toReopen(r);
  },
};

// The EXPLICITLY selected schedule version (ProjectProvider -> lib/apiContext). There is no "active" or
// "latest" schedule lookup: a screen that needs a schedule uses the one the user selected.
export function getActiveScheduleId(): Promise<string> {
  const id = apiContext.getScheduleId();
  if (!id) return Promise.reject(new ApiError(400, 'Select a schedule version to continue.', '', 'INVALID_SCHEDULE_CONTEXT'));
  return Promise.resolve(id);
}

export const graphApi = {
  getActivityGraph: async (activityId: string, depth = 1, scheduleId?: string): Promise<{ nodes: any[]; edges: any[] }> => {
    const sid = scheduleId ?? (await getActiveScheduleId().catch(() => undefined));
    const query = sid ? `?depth=${depth}&schedule_id=${sid}` : `?depth=${depth}`;
    return apiFetch<{ nodes: any[]; edges: any[] }>(`/api/v1/graph/activity/${activityId}${query}`);
  },
};

export const schedulesApi = {
  getActivities: async (scheduleId?: string): Promise<ScheduleActivity[]> => {
    const sid = scheduleId ?? (await getActiveScheduleId());
    return apiFetch(`/api/v1/schedules/${sid}/activities`);
  },

  getImpactPreview: async (activityId: string, delayDays: number, scheduleId?: string): Promise<ImpactPreviewResult> => {
    const queryParams = new URLSearchParams({ delay_days: String(delayDays) });
    const impactScheduleId = scheduleId ?? (await getActiveScheduleId().catch(() => undefined));
    if (impactScheduleId) queryParams.set('schedule_id', impactScheduleId);
    const data: any = await apiFetch(`/api/v1/schedule/${encodeURIComponent(activityId)}/impact-preview?${queryParams.toString()}`);
    const impacts: ImpactEvaluationItem[] = (data.impacts || []).map((imp: any) => ({
      successor_activity_id: imp.successor_activity_id,
      activity_name: imp.activity_name || imp.successor_activity_id,
      dependency_type: imp.dependency_type || 'FS',
      original_earliest_start: imp.original_earliest_start || '',
      shifted_earliest_start: imp.shifted_earliest_start || '',
      original_planned_finish: imp.original_planned_finish || '',
      shifted_earliest_finish: imp.shifted_earliest_finish || '',
      propagation_depth: imp.propagation_depth || 1,
      target_path: imp.target_path || [data.activity_id, imp.successor_activity_id],
      execution_state: imp.execution_state || 'NOT_STARTED',
      gross_delay_days: imp.gross_delay_days || 0,
      total_float: imp.total_float ?? null,
      float_status: imp.float_status || 'UNKNOWN',
      absorbed_delay_days: imp.absorbed_delay_days ?? null,
      net_delay_days: imp.net_delay_days ?? null,
      controlling_predecessor: imp.controlling_predecessor ?? null,
      controlling_relationship: imp.controlling_relationship ?? null,
      uncertainty: Boolean(imp.uncertainty),
      classification: imp.classification || 'NO_IMPACT',
      constraints_evaluated: imp.constraints_evaluated || [],
    }));

    return {
      activity_id: data.activity_id,
      activity_name: data.activity_name || data.activity_id,
      planned_start: data.planned_start || '',
      planned_finish: data.planned_finish || '',
      shifted_finish: data.shifted_finish || '',
      schedule_id: data.schedule_id,
      delay_days: data.delay_days,
      propagation_depth_limit: data.propagation_depth_limit || 5,
      disclaimer: 'Preview only · Deterministic A1 CPM Evaluation · Multi-hop bounded propagation',
      impacts,
      successors: impacts.map((imp) => ({
        successor_activity_id: imp.successor_activity_id,
        activity_name: imp.activity_name,
        relationship_type: imp.dependency_type,
        original_start: imp.original_earliest_start,
        original_finish: imp.original_planned_finish,
        shifted_start: imp.shifted_earliest_start,
        shifted_finish: imp.shifted_earliest_finish,
        lag_days: 0,
        depth: imp.propagation_depth,
        net_delay_days: imp.net_delay_days,
        execution_state: imp.execution_state,
      })),
    };
  },

  getWbsTree: async (scheduleIdArg?: string): Promise<WBSTreeResponse> => {
    const scheduleId = scheduleIdArg ?? (await getActiveScheduleId());
    return apiFetch(`/api/v1/schedules/${scheduleId}/wbs-tree`);
  },
};

export const wbsApi = {
  getSplits: async (eventId: string): Promise<WBSSplitItem[]> => {
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/splits`);
    return Array.isArray(data) ? data : data.splits || [];
  },

  updateSplits: async (eventId: string, splits: Partial<WBSSplitItem>[]): Promise<WBSSplitResponse> => {
    return apiFetch(`/api/v1/claims/${eventId}/splits`, {
      method: 'PATCH',
      body: JSON.stringify({
        splits: splits.map((s) => ({ activity_id: s.activity_id, split_pct: s.split_pct })),
      }),
    });
  },

  splitClaim: async (request: WBSSplitRequest): Promise<WBSSplitResponse> => {
    return wbsApi.updateSplits(request.event_id, request.allocations as any);
  },

  getTree: async (scheduleId?: string): Promise<WBSTreeResponse> => {
    return schedulesApi.getWbsTree(scheduleId);
  },
};

export const auditApi = {
  getLogs: async (): Promise<AuditLogEntry[]> => {
    return apiFetch('/api/v1/audit');
  },
};

export const reportsApi = {
  getExecutionSummary: async (filter?: ExecutionSummaryFilter): Promise<ExecutionReportResponse> => {
    const startDate = filter?.start || filter?.start_date;
    const endDate = filter?.end || filter?.end_date;

    const params = new URLSearchParams();
    if (startDate) params.append('start', startDate);
    if (endDate) params.append('end', endDate);
    if (filter?.discipline) params.append('discipline', filter.discipline);
    if (filter?.language) params.append('language', filter.language);

    const qs = params.toString();
    return apiFetch(`/api/v1/reports/execution-summary${qs ? `?${qs}` : ''}`);
  },
};

export interface InvestigationContext {
  root_activity_id: string;
  depth: number;
  context: {
    activity: Record<string, any>;
    execution_events: any[];
    validations: any[];
    conflicts: any[];
    impacts: any[];
    evidence: any[];
    decisions: any[];
    approved_actuals: any[];
    dependencies: any[];
  };
  summary: {
    conflict_status: string;
    validation_status: string;
    impact_status: string;
    evidence_status: string;
    approved_actual_status: string;
    dependencies_count: number;
    execution_events_count: number;
    decisions_count: number;
  };
  graph: {
    nodes: any[];
    edges: any[];
  };
}

export const investigationApi = {
  getInvestigation: async (activityId: string, depth = 1): Promise<InvestigationContext> => {
    return apiFetch<InvestigationContext>(`/api/v1/investigation/activity/${activityId}?depth=${depth}`);
  },
};

// Runtime translation of generated text for display (canonical text is never changed).
// Falls back to the original strings on any failure.
export const translateApi = {
  translate: async (texts: string[], targetLanguage: string): Promise<string[]> => {
    const lang = (targetLanguage || 'en').slice(0, 2);
    if (lang === 'en' || texts.length === 0) return texts;
    try {
      const res: any = await apiFetch('/api/v1/reports/translate', {
        method: 'POST',
        body: JSON.stringify({ texts, target_language: lang }),
      });
      return Array.isArray(res?.texts) && res.texts.length === texts.length ? res.texts : texts;
    } catch {
      return texts;
    }
  },
};

export const executionSummaryApi = {
  getSummary: async (params?: {
    period?: 'last_7_days' | 'this_month' | 'custom';
    start_date?: string;
    end_date?: string;
    discipline?: string;
    language?: 'en' | 'hi' | 'te';
  }): Promise<ExecutionSummaryResponse> => {
    const query = new URLSearchParams();
    if (params?.period) query.set('period', params.period);
    if (params?.start_date) query.set('start_date', params.start_date);
    if (params?.end_date) query.set('end_date', params.end_date);
    if (params?.discipline) query.set('discipline', params.discipline);
    if (params?.language) query.set('language', params.language);

    return apiFetch<ExecutionSummaryResponse>(`/api/v1/execution-summary?${query.toString()}`);
  },
};

/** v2 only: the Supervisor's Time Agent drafts a claim and hands it to a Site Engineer (a Supervisor never files an execution claim). */
export interface ClaimHandoff { handoff_id: string; status: 'OPEN' | 'FILED' | 'DISMISSED'; draft: Record<string, any>; note: string | null; created_at: string; filed_event_id: string | null; }
export const timeAgentApi = {
  handOff: (draft: Record<string, any>, note?: string): Promise<ClaimHandoff> =>
    apiFetch('/api/v1/time-agent/handoffs', { method: 'POST', body: JSON.stringify({ draft, note }) }),
  list: (): Promise<ClaimHandoff[]> => apiFetch('/api/v1/time-agent/handoffs'),
  filed: (id: string, eventId: string): Promise<ClaimHandoff> =>
    apiFetch(`/api/v1/time-agent/handoffs/${id}/filed`, { method: 'POST', body: JSON.stringify({ event_id: eventId }) }),
  dismiss: (id: string): Promise<ClaimHandoff> => apiFetch(`/api/v1/time-agent/handoffs/${id}/dismiss`, { method: 'POST' }),
};

export const mockP6Api = {
  getReceived: async (): Promise<{ count: number; payloads: any[] }> => {
    return apiFetch('/api/v1/mock-p6/received');
  },

  updateActivity: async (
    activityId: string,
    payload: { Id: string; StartDate?: string; FinishDate?: string; PercentComplete?: number }
  ): Promise<{ status: string; activity_id: string; p6_id: string; message: string }> => {
    return apiFetch(`/api/v1/mock-p6/activities/${encodeURIComponent(activityId)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
};

export type ImpactLevel = ImpactSeverity;

/** One row of the server impact watch list, in the shape the review/intake/dashboard screens render. */
export interface CompoundImpact {
  activityId: string;
  activityName: string;
  projectId: string;
  scheduleId: string;
  stageId: string | null;
  impactLevel: ImpactLevel;
  workflowCondition: string;
  sensitivityDays: number;
  directSuccessorCount: number;
  totalDownstreamCount: number;
  criticalDownstreamCount: number;
  impactedStageIds: string[];
  impactedStageNames: string[];
  primaryReason: string;
  riskFactors: string[];
  isBlockedOrAtRisk: boolean;
}

interface WatchlistRow {
  activity_id: string;
  activity_name: string;
  stage_id: string | null;
  workflow_condition: string;
  severity: ImpactSeverity;
  sensitivity_days: number;
  direct_successor_count: number;
  total_downstream_count: number;
  critical_downstream_count: number;
  stages: { stage_id: string; stage_name: string; count: number }[];
  project_completion_impact_days: number;
  primary_reason: string;
}

export const impactApi = {
  /**
   * Activities in a problem state (blocked / quality hold / reopen / rework) ranked by how far a slip would
   * propagate. Server-computed by the impact engine; the browser derives nothing.
   */
  getScheduleImpacts: async (projectId: string, scheduleId: string): Promise<CompoundImpact[]> => {
    const rows = await apiFetch<WatchlistRow[]>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/schedules/${encodeURIComponent(scheduleId)}/impact/watchlist`,
    );
    return rows.map((r) => ({
      activityId: r.activity_id,
      activityName: r.activity_name,
      projectId,
      scheduleId,
      stageId: r.stage_id,
      impactLevel: r.severity,
      workflowCondition: r.workflow_condition,
      sensitivityDays: r.sensitivity_days,
      directSuccessorCount: r.direct_successor_count,
      totalDownstreamCount: r.total_downstream_count,
      criticalDownstreamCount: r.critical_downstream_count,
      impactedStageIds: r.stages.map((x) => x.stage_id),
      impactedStageNames: r.stages.map((x) => x.stage_name),
      primaryReason: r.primary_reason,
      riskFactors: [
        r.primary_reason,
        `A ${r.sensitivity_days}-day slip reaches ${r.total_downstream_count} downstream activit${r.total_downstream_count === 1 ? 'y' : 'ies'}` +
          (r.critical_downstream_count ? ` (${r.critical_downstream_count} critical)` : ''),
      ],
      isBlockedOrAtRisk: true,
    }));
  },

  /** What-if: delay one activity by N days and propagate through the stored network (nothing is written). */
  preview: (projectId: string, scheduleId: string, activityId: string, delayDays: number) =>
    apiFetch<ImpactScenarioResult>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/schedules/${encodeURIComponent(scheduleId)}/activities/${encodeURIComponent(activityId)}/impact-preview?delay_days=${delayDays}`,
    ),

  getDependencies: async (scheduleId: string): Promise<ScheduleDependency[]> =>
    apiFetch<ScheduleDependency[]>(`/api/v1/schedules/${encodeURIComponent(scheduleId)}/dependencies`),
};

