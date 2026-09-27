/**
 * Setu AI (SIH26122) — Typed API Client & PRD Contracts
 *
 * Fully compliant with PRD v5 specification.
 * VITE_USE_MOCKS=true → returns realistic mock data (explicit demo mode)
 * Any other value or omission → calls real FastAPI endpoints at VITE_API_BASE_URL
 */

// Never silently substitute fabricated records for a real backend response.
// Mock mode must be explicitly opted into by a demo build.
const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === 'true';
export const IS_MOCK_MODE = USE_MOCKS;
const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

interface ApiFetchOptions extends RequestInit {
  responseType?: 'json' | 'blob' | 'text';
}

/** Thrown by apiFetch on a non-2xx response. `.message` is always safe to show
 * a user (never raw JSON/stack traces); `.status` and `.raw` are for callers
 * that want to branch on the status code or log the untouched backend body. */
export class ApiError extends Error {
  status: number;
  raw: string;
  constructor(status: number, message: string, raw: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.raw = raw;
  }
}

const FRIENDLY_STATUS_MESSAGES: Record<number, string> = {
  400: 'The request was invalid.',
  401: 'Your session has expired. Please sign in again.',
  403: "You don't have permission to do that.",
  404: 'The requested item could not be found.',
  409: 'This conflicts with existing data.',
  422: 'Some of the submitted information was invalid.',
  500: 'Something went wrong on the server. Please try again.',
  502: 'The service is temporarily unavailable. Please try again shortly.',
  503: 'The service is temporarily unavailable. Please try again shortly.',
};

function friendlyErrorMessage(status: number, bodyText: string): string {
  // FastAPI's standard error shape is {"detail": "..."} or {"detail": [...]} for
  // validation errors -- surface that human-readable detail when present, since
  // it's already meant to be read (e.g. "Schedule 'x' not found"), but never a
  // raw stack trace or an unparsed JSON blob.
  try {
    const parsed = JSON.parse(bodyText);
    const detail = parsed?.detail ?? parsed?.error?.message ?? parsed?.message;
    if (typeof detail === 'string' && detail.trim() && !detail.trim().startsWith('Traceback')) {
      return detail;
    }
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0];
      const field = Array.isArray(first?.loc) ? first.loc[first.loc.length - 1] : undefined;
      if (typeof first?.msg === 'string') {
        return field ? `${field}: ${first.msg}` : first.msg;
      }
    }
  } catch {
    // Not JSON (or not the expected shape) -- fall through to the generic message.
  }
  return FRIENDLY_STATUS_MESSAGES[status] || `Request failed (${status}). Please try again.`;
}

async function apiFetch<T>(path: string, options?: ApiFetchOptions): Promise<T> {
  const token = localStorage.getItem('supabase_access_token') || localStorage.getItem('auth_token');
  const headers: Record<string, string> = {
    ...(options?.responseType !== 'blob' ? { 'Content-Type': 'application/json' } : {}),
    ...(options?.headers as Record<string, string>),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      headers,
    });
  } catch (err: any) {
    if (err instanceof TypeError && (err.message?.includes('fetch') || err.message?.includes('NetworkError') || err.message?.includes('Failed'))) {
      throw new ApiError(
        0,
        `Unable to reach backend at ${BASE_URL}. Ensure the backend is running or enable mock mode (VITE_USE_MOCKS=true).`,
        err.message || 'Network error'
      );
    }
    throw err;
  }

  if (!res.ok) {
    const errorText = await res.text().catch(() => '');
    // Technical detail stays in the console for debugging; never in the thrown
    // message a UI component might render directly (ISS-10).
    console.error(`[api] ${options?.method || 'GET'} ${path} -> ${res.status}`, errorText);

    if (res.status === 401) {
      // Centralized session-expiry handling (ISS-22): clear the dead token so
      // no further request is sent with it, and let AuthProvider react (it
      // listens for this event) to drop `user` and bounce to /login via the
      // existing ProtectedRoute redirect -- never a raw error left on screen.
      localStorage.removeItem('supabase_access_token');
      localStorage.removeItem('auth_token');
      window.dispatchEvent(new Event('auth:unauthorized'));
    }

    throw new ApiError(res.status, friendlyErrorMessage(res.status, errorText), errorText);
  }

  if (options?.responseType === 'blob') {
    return (await res.blob()) as unknown as T;
  }
  if (options?.responseType === 'text') {
    return (await res.text()) as unknown as T;
  }
  return res.json();
}

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

import type {
  ActivityProgressSummary,
  WBSProgressSummary,
  StageProgressSummary,
  ProjectProgressSummary,
  WorkPackageProgressSummary,
  ContractorProgressSummary,
} from '@/lib/progressEngine';

export type {
  ActivityProgressSummary,
  WBSProgressSummary,
  StageProgressSummary,
  ProjectProgressSummary,
  WorkPackageProgressSummary,
  ContractorProgressSummary,
};

import type {
  ImpactLevel,
  DownstreamImpactedActivity,
  CompoundImpact,
} from './lib/impactEngine';

export type {
  ImpactLevel,
  DownstreamImpactedActivity,
  CompoundImpact,
};

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

// ─── Feature 31: Evidence Fusion & Knowledge Graph ──────────────────────────

export type EvidenceRelation = 'CORROBORATES' | 'CONTRADICTS' | 'SUPPORTING' | 'NEUTRAL';

export interface EvidenceDocument {
  evidence_id: string;
  event_id: string;
  document_type: string; // 'DAILY_REPORT' | 'INSPECTION_PHOTO' | 'SURVEY_LOG' | 'CAD_DWG' | string
  relation?: EvidenceRelation | string | null; // CORROBORATES vs CONTRADICTS
  file_name: string;
  page_or_cell_ref?: string | null;
  snippet_text?: string | null;
  ocr_confidence?: number | null;
  gps_lat?: number | null;
  gps_lon?: number | null;
  timestamp?: string | null;
  source_url?: string | null;
}

export interface KnowledgeGraphNode {
  id: string;
  label: string;
  type: 'CLAIM' | 'ACTIVITY' | 'WBS' | 'DOCUMENT' | 'LOCATION' | 'DISCIPLINE' | string;
  properties?: Record<string, any>;
}

export interface KnowledgeGraphEdge {
  source: string;
  target: string;
  relationship: string; // 'MATCHED_TO' | 'PART_OF_WBS' | 'EVIDENCED_BY' | 'LOCATED_AT' | string
  confidence?: number | null;
}

export interface KnowledgeGraphData {
  event_id: string;
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
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

// ─── Realistic Demo Dataset (Dynamically Scoped to Active Project) ────────────

import {
  getActiveProjectId,
  getActiveScheduleVersionId,
  getDatasetForCurrentProject,
  getActivitiesForCurrentSchedule,
  getAllEventsForCurrentProject,
  MOCK_DYNAMIC_EVENTS,
  extractMockClaimFields,
  TODAY,
  runEligibilityFirstMatching,
  getMockReopenRequests,
  createMockReopenRequest,
  reviewMockReopenRequest,
  updateActivityExecutionState,
  recordApprovedActualProgress,
  getMockProjectProgress,
  getMockStageProgress,
  getMockWBSProgress,
  getMockActivityProgress,
  getMockContractors,
  getMockWorkPackages,
  getMockContractorProgress,
  getMockWorkPackageProgress,
  getMockQualityGates,
  completeMockQualityGate,
  waiveMockQualityGate,
  getActivityQualitySummary,
  getMockCompoundImpacts,
  getMockActivityImpact,
  getMockScheduleDependencies,
} from './mockData';

const MOCK_AUDIT_LOGS: AuditLogEntry[] = [
  {
    log_id: 102,
    entity_type: 'execution_event',
    entity_id: 'evt-104',
    action: 'DECISION_APPROVE',
    actor_id: 'usr-supervisor-01',
    before_state: '{"status":"VALIDATED","claimed_pct":100}',
    after_state: '{"status":"APPROVED","approved_pct":100}',
    payload_hash: 'e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7',
    previous_hash: '9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b',
    current_hash: 'f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8',
    timestamp: new Date(Date.now() - 7200000).toISOString(),
  },
  {
    log_id: 101,
    entity_type: 'execution_event',
    entity_id: 'evt-106',
    action: 'DECISION_APPROVE',
    actor_id: 'usr-supervisor-01',
    before_state: '{"status":"VALIDATED","claimed_pct":100}',
    after_state: '{"status":"APPROVED","approved_pct":100}',
    payload_hash: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
    previous_hash: '0000000000000000000000000000000000000000000000000000000000000000',
    current_hash: '9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b',
    timestamp: new Date(Date.now() - 5400000).toISOString(),
  },
];

// ─── API Methods ─────────────────────────────────────────────────────────────

export const authApi = {
  getMe: async (hintEmail?: string): Promise<UserProfile> => {
    if (USE_MOCKS) {
      await sleep(200);
      const rawUser = localStorage.getItem('user');
      if (rawUser) {
        try {
          const parsed = JSON.parse(rawUser);
          if (parsed && parsed.role && parsed.id) return parsed;
        } catch {
          // ignore corrupted JSON
        }
      }
      const devEmail = hintEmail || localStorage.getItem('setu_dev_email_v1') || '';
      const isEngineer =
        devEmail.toLowerCase().includes('engineer') ||
        devEmail.toLowerCase().includes('site');

      if (isEngineer) {
        return {
          id: '811a1e0f-976d-42ea-a37f-1096186daf36',
          email: devEmail || 'site.engineer@sih26122.internal',
          full_name: 'Site Engineer',
          role: 'SITE_ENGINEER',
        };
      }
      return {
        id: '4b8e6901-de81-490c-8bec-9761f62bee70',
        email: devEmail || 'supervisor@sih26122.internal',
        full_name: 'Supervisor',
        role: 'SUPERVISOR',
      };
    }
    const data: any = await apiFetch('/api/v1/auth/me');
    return { ...data, email: data.email || '' };
  },
};

// ─── Backend → UI shape normalisation ────────────────────────────────────────
// The backend stores priority_reasons as newline-separated "[Tag] explanation" lines
// and scores are unbounded points (routine ≈ 5, critical-path sequence error ≥ 200).
// The UI works with a list of reasons, a rank and an escalation flag.
const ESCALATION_SCORE_THRESHOLD = 100; // >= one critical-severity issue (base 100) or worse

// GET /claims/{id}/evidence returns evidence *links* (link_id, relation_type, rationale,
// opposite_* context describing the other claim/document), not documents. Map them onto
// EvidenceDocument so the panel always has the fields it renders. Rows that are already
// document-shaped (mock data) pass through unchanged.
function normalizeEvidence(raw: any): EvidenceDocument {
  const ctx = raw?.opposite_context || {};
  const docType = raw?.document_type || raw?.opposite_document_type || raw?.opposite_channel || 'LINKED_CLAIM';
  return {
    evidence_id: raw?.evidence_id || raw?.link_id || `${raw?.event_id_a ?? ''}:${raw?.event_id_b ?? ''}`,
    event_id: raw?.event_id || raw?.event_id_a || '',
    document_type: docType,
    relation: raw?.relation ?? raw?.relation_type ?? null,
    file_name: raw?.file_name || ctx.file_name || raw?.opposite_document_type || 'Linked claim',
    page_or_cell_ref: raw?.page_or_cell_ref ?? (ctx.event_date ? `Claim dated ${ctx.event_date}` : null),
    snippet_text: raw?.snippet_text ?? raw?.rationale ?? ctx.raw_claim_text ?? null,
    ocr_confidence: raw?.ocr_confidence ?? null, // link `confidence` is not OCR confidence
    gps_lat: raw?.gps_lat ?? null,
    gps_lon: raw?.gps_lon ?? null,
    timestamp: raw?.timestamp ?? raw?.created_at ?? null,
    source_url: raw?.source_url ?? null,
  };
}

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
    if (USE_MOCKS) {
      await sleep(1000);
      const extracted = extractMockClaimFields(text);
      const activeSched = getActiveScheduleVersionId();
      const allActs = getActivitiesForCurrentSchedule(activeSched);
      const matchedTarget = allActs.find(
        (a) =>
          text.toLowerCase().includes(a.activity_id.toLowerCase()) ||
          (a.asset_tag && text.toLowerCase().includes(a.asset_tag.toLowerCase())) ||
          (a.activity_name && text.toLowerCase().includes(a.activity_name.toLowerCase().slice(0, 15)))
      );

      const reportedActId = matchedTarget?.activity_id || extracted.activity_id || null;
      const discipline = matchedTarget?.discipline || extracted.discipline || 'CIVIL';
      const location = matchedTarget?.location || extracted.location || null;
      const assetTag = matchedTarget?.asset_tag || extracted.asset_tag || null;
      const isCompleted = matchedTarget?.execution_state === 'COMPLETED';

      const ev: ExecutionEvent = {
        event_id: `evt-${Date.now()}`,
        document_id: evidenceFile ? `doc-${Date.now()}` : null,
        schedule_id: activeSched,
        event_date: TODAY,
        raw_claim_text: text,
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: reportedActId,
        matched_activity_id: matchedTarget?.activity_id || null,
        discipline,
        action: extracted.event_type,
        event_type: extracted.event_type,
        claim_mode: extracted.claimed_quantity ? 'INCREMENTAL_QUANTITY' : 'CUMULATIVE_PCT',
        asset_tag: assetTag,
        location,
        claimed_quantity: extracted.claimed_quantity,
        claimed_uom: extracted.claimed_uom,
        claimed_pct: extracted.claimed_pct,
        delay_reason: null,
        supervisor_id: null,
        photo_path: evidenceFile ? `/uploads/${evidenceFile.name}` : null,
        status: isCompleted ? 'REVIEW_REQUIRED' : 'EXTRACTED',
        created_at: new Date().toISOString(),
        clarification_status: extracted.clarification_status,
        clarification_question: extracted.clarification_question,
        is_completed_activity_target: isCompleted,
        field_provenance: {
          activity_id: {
            field_name: 'activity_id',
            source: 'AI_EXTRACTED',
            source_detail: reportedActId || 'Pending Matching',
          },
          discipline: {
            field_name: 'discipline',
            source: matchedTarget ? 'SCHEDULE_AUTO_FILLED' : 'AI_EXTRACTED',
            source_detail: discipline,
          },
          location: {
            field_name: 'location',
            source: 'SCHEDULE_AUTO_FILLED',
            source_detail: location || 'Project Site',
          },
          asset_tag: {
            field_name: 'asset_tag',
            source: 'SCHEDULE_AUTO_FILLED',
            source_detail: assetTag || 'Master Asset',
          },
        },
      };
      MOCK_DYNAMIC_EVENTS.set(ev.event_id, ev);
      return { event: ev };
    }
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
    if (USE_MOCKS) {
      await sleep(1500);
      const activeSched = getActiveScheduleVersionId();
      const ev: ExecutionEvent = {
        event_id: `evt-${Date.now()}`,
        document_id: `doc-${Date.now()}`,
        schedule_id: activeSched,
        event_date: TODAY,
        raw_claim_text: `P6/MSP Schedule Progress Export: ${file.name} (Actuals Batch)`,
        input_channel: 'SCHEDULE_EXPORT',
        language_detected: 'en',
        reported_activity_id: 'ACT-201',
        matched_activity_id: 'ACT-201',
        discipline: 'CIVIL',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: 'COL-C4',
        location: 'Block-4 North',
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 85,
        delay_reason: null,
        supervisor_id: null,
        photo_path: null,
        status: 'VALIDATED',
        created_at: new Date().toISOString(),
      };
      MOCK_DYNAMIC_EVENTS.set(ev.event_id, ev);
      return { events: [ev] };
    }
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
    if (USE_MOCKS) {
      await sleep(1500);
      const activeSched = getActiveScheduleVersionId();
      const ev: ExecutionEvent = {
        event_id: `evt-${Date.now()}`,
        document_id: `doc-${Date.now()}`,
        schedule_id: activeSched,
        event_date: TODAY,
        raw_claim_text: options?.rawClaimText || `Ingested update from file: ${file.name}`,
        input_channel: 'FILE_UPLOAD',
        language_detected: 'en',
        reported_activity_id: 'ACT-201',
        matched_activity_id: null,
        discipline: 'CIVIL',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: null,
        location: null,
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 60,
        delay_reason: null,
        supervisor_id: null,
        photo_path: null,
        status: 'EXTRACTED',
        created_at: new Date().toISOString(),
        clarification_status: 'NONE',
        clarification_question: null,
      };
      MOCK_DYNAMIC_EVENTS.set(ev.event_id, ev);
      return { events: [ev] };
    }
    const form = new FormData();
    form.append('file', file);
    if (options?.purpose) form.append('purpose', options.purpose);
    if (options?.rawClaimText) form.append('raw_claim_text', options.rawClaimText);
    const token = localStorage.getItem('supabase_access_token') || localStorage.getItem('auth_token');
    const headers: Record<string, string> = {};
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
    if (USE_MOCKS) {
      await sleep(600);
      const allEvents = getAllEventsForCurrentProject();
      const ev = MOCK_DYNAMIC_EVENTS.get(eventId) || allEvents.find((e) => e.event_id === eventId);
      const res = runEligibilityFirstMatching(ev);
      if (ev) {
        ev.status = res.status;
        MOCK_DYNAMIC_EVENTS.set(eventId, ev);
      }
      return res;
    }
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/match`, { method: 'POST' });
    return { status: data.status, matches: data.candidates || [] };
  },

  check: async (eventId: string): Promise<{ status: ClaimStatus; issues: ValidationIssue[] }> => {
    if (USE_MOCKS) {
      await sleep(800);
      const dataset = getDatasetForCurrentProject();
      return { status: 'REVIEW_REQUIRED', issues: dataset.validationIssues };
    }
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/check`, { method: 'POST' });
    return { status: data.status, issues: data.validation_issues || [] };
  },

  clarify: async (eventId: string, answer: string): Promise<{ event: ExecutionEvent }> => {
    if (USE_MOCKS) {
      await sleep(500);
      const allEvents = getAllEventsForCurrentProject();
      const ev = MOCK_DYNAMIC_EVENTS.get(eventId) || allEvents.find((e) => e.event_id === eventId) || {
        event_id: eventId,
        document_id: null,
        schedule_id: getActiveScheduleVersionId(),
        event_date: TODAY,
        raw_claim_text: 'Clarified claim',
        input_channel: 'TYPED_TEXT' as const,
        language_detected: 'en',
        reported_activity_id: 'ACT-201',
        matched_activity_id: null,
        discipline: 'CIVIL' as const,
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE' as const,
        claim_mode: 'CUMULATIVE_PCT' as const,
        asset_tag: null,
        location: null,
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 100,
        delay_reason: null,
        supervisor_id: null,
        photo_path: null,
        status: 'EXTRACTED' as const,
        created_at: new Date().toISOString(),
      };
      ev.clarification_status = 'ANSWERED';
      ev.clarification_answer = answer;
      ev.discipline = ev.discipline || 'CIVIL';
      ev.claimed_pct = ev.claimed_pct || 100;
      ev.status = 'EXTRACTED';
      MOCK_DYNAMIC_EVENTS.set(eventId, ev);
      return { event: { ...ev } };
    }
    const data = await apiFetch(`/api/v1/claims/${eventId}/clarify`, {
      method: 'POST',
      body: JSON.stringify({ answer }),
    });
    return { event: data as any };
  },

  getCandidates: async (eventId: string): Promise<CandidateMatch[]> => {
    if (USE_MOCKS) {
      await sleep(200);
      const allEvents = getAllEventsForCurrentProject();
      const ev = MOCK_DYNAMIC_EVENTS.get(eventId) || allEvents.find((e) => e.event_id === eventId);
      const res = runEligibilityFirstMatching(ev);
      return res.matches;
    }
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/candidates`);
    return data.candidates || [];
  },

  getConflicts: async (eventId: string): Promise<ConflictRecord[]> => {
    if (USE_MOCKS) {
      await sleep(300);
      const dataset = getDatasetForCurrentProject();
      return [
        {
          conflict_id: `cnf-${getActiveProjectId()}-01`,
          schedule_id: getActiveScheduleVersionId(),
          activity_id: dataset.candidates[0]?.activity_id || 'ACT-202',
          reporting_period: TODAY,
          event_id_a: eventId,
          event_id_b: 'evt-previous-09',
          value_a: 75,
          value_b: 60,
          variance_pct: 25,
          status: 'OPEN',
        },
      ];
    }
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/conflicts`);
    return data.conflicts || [];
  },

  getValidation: async (eventId: string): Promise<ValidationIssue[]> => {
    if (USE_MOCKS) {
      await sleep(300);
      const dataset = getDatasetForCurrentProject();
      return dataset.validationIssues;
    }
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/validation`);
    return data.validation_issues || [];
  },

  getEvidence: async (eventId: string): Promise<EvidenceDocument[]> => {
    if (USE_MOCKS) {
      await sleep(300);
      const pId = getActiveProjectId();
      const docName = pId === 'PRJ-RAJ-02' ? 'Rajasthan_GasPlant_Shift_Report.pdf' : pId === 'PRJ-KG-03' ? 'Offshore_Barge_QC_Log.pdf' : 'Assam_Civil_Shift_Report.pdf';
      return [
        {
          evidence_id: 'ev-01',
          event_id: eventId,
          document_type: 'DAILY_REPORT',
          relation: 'CORROBORATES',
          file_name: docName,
          page_or_cell_ref: 'Page 3, Line 14',
          snippet_text: `Verified physical execution and test progress against project schedule ${getActiveScheduleVersionId()}. QC signoff attached.`,
          ocr_confidence: 0.95,
          gps_lat: 28.6139,
          gps_lon: 77.209,
          timestamp: new Date(Date.now() - 3600000).toISOString(),
        },
        {
          evidence_id: 'ev-02',
          event_id: eventId,
          document_type: 'INSPECTION_PHOTO',
          relation: 'CORROBORATES',
          file_name: 'Site_Inspection_Evidence.jpg',
          page_or_cell_ref: 'Attachment 1',
          snippet_text: 'Site photo verifying construction progress, material staging and inspection tags.',
          ocr_confidence: 0.98,
          gps_lat: 28.6141,
          gps_lon: 77.2093,
          timestamp: new Date(Date.now() - 1800000).toISOString(),
        },
      ];
    }
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/evidence`);
    const rows: any[] = Array.isArray(data) ? data : data?.evidence || data?.evidence_links || [];
    return rows.map(normalizeEvidence);
  },

  getPhotoBlobUrl: async (eventId: string): Promise<string> => {
    if (USE_MOCKS) {
      await sleep(200);
      return 'https://images.unsplash.com/photo-1541888946425-d81bb19240f5?w=800&q=80';
    }
    const blob = await apiFetch<Blob>(`/api/v1/claims/${eventId}/photo`, { responseType: 'blob' });
    return URL.createObjectURL(blob);
  },

  getKnowledgeGraph: async (eventId: string): Promise<KnowledgeGraphData> => {
    if (USE_MOCKS) {
      await sleep(400);
      const acts = getActivitiesForCurrentSchedule();
      const firstAct = acts[0];
      return {
        event_id: eventId,
        nodes: [
          { id: 'node-claim', label: `Claim ${eventId}`, type: 'CLAIM' },
          { id: 'node-act', label: `${firstAct?.activity_id || 'ACT-01'} ${firstAct?.activity_name || ''}`, type: 'ACTIVITY' },
          { id: 'node-wbs', label: `${firstAct?.wbs_code || 'WBS-1.0'} Work Package`, type: 'WBS' },
          { id: 'node-doc', label: `${firstAct?.discipline || 'Civil'}_Shift_Report.pdf`, type: 'DOCUMENT' },
          { id: 'node-loc', label: firstAct?.location || 'Project Site', type: 'LOCATION' },
        ],
        edges: [
          { source: 'node-claim', target: 'node-act', relationship: 'MATCHED_TO', confidence: 0.88 },
          { source: 'node-act', target: 'node-wbs', relationship: 'PART_OF_WBS' },
          { source: 'node-claim', target: 'node-doc', relationship: 'EVIDENCED_BY', confidence: 0.94 },
          { source: 'node-claim', target: 'node-loc', relationship: 'LOCATED_AT' },
        ],
      };
    }
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/knowledge-graph`);
    return data;
  },

  askWhy: async (request: AskWhyRequest): Promise<AskWhyResponse> => {
    const activities = getActivitiesForCurrentSchedule();
    const activity = activities.find((a) => a.activity_id === request.activity_id) || activities[0];
    const activityId = activity?.activity_id || request.activity_id || 'ACT-201';
    const depth = request.depth ?? 2;

    if (USE_MOCKS) {
      await sleep(600);
      return {
        activity_id: activityId,
        event_id: request.event_id,
        explanation:
          `Matched to activity ${activityId} (${activity?.activity_name || 'Activity'}) with 88% confidence based on spatial alignment in ${activity?.location || 'Sector'} and prerequisite dependencies verified in ${getActiveScheduleVersionId()}.`,
        traversal_depth: depth,
        reasoning_steps: [
          `Extracted entity "${activity?.asset_tag || activityId}" and location "${activity?.location || 'Site'}" from source update.`,
          `Traversed WBS hierarchy: Schedule -> ${activity?.wbs_code || 'WBS-1.0'} -> ${activity?.activity_name || activityId}.`,
          `Verified predecessor prerequisite milestones cleared QC inspection.`,
        ],
        entities_involved: [
          { name: activityId, type: 'ACTIVITY', role: 'Matched Schedule Package' },
          { name: activity?.location || 'Site', type: 'LOCATION', role: 'Spatial Constraint' },
          { name: activity?.asset_tag || 'TAG-01', type: 'PREDECESSOR', role: 'Verified Dependency' },
        ],
        evidence_references: [
          `${activity?.discipline || 'Discipline'}_Shift_Report_2026.pdf (Page 3)`,
          `IMG_${activityId}_QC.jpg (Attachment 1)`,
        ],
      };
    }
    const qs = new URLSearchParams({ depth: String(depth) });
    if (request.event_id) qs.set('event_id', request.event_id);
    const data = await apiFetch(`/api/v1/graph/explain/${encodeURIComponent(activityId)}?${qs.toString()}`);
    return data as AskWhyResponse;
  },

  getReviewQueue: async (sort: string = 'priority'): Promise<ExecutionEvent[]> => {
    if (USE_MOCKS) {
      await sleep(400);
      const allEvents = getAllEventsForCurrentProject();
      return allEvents.filter(
        (c) =>
          c.status === 'REVIEW_REQUIRED' ||
          c.status === 'VALIDATED' ||
          c.status === 'HOLD' ||
          c.status === 'UNMATCHED' ||
          c.status === 'EXTRACTED'
      );
    }
    const data: any = await apiFetch(`/api/v1/review-queue?sort=${sort}`);
    const items: any[] = Array.isArray(data)
      ? data
      : data.items || data.review_queue || data.queue || data.claims || [];
    return items.map((it, i) => normalizeEvent(it, i + 1));
  },

  getEvent: async (eventId: string): Promise<ExecutionEvent> => {
    if (USE_MOCKS) {
      await sleep(300);
      const allEvents = getAllEventsForCurrentProject();
      return allEvents.find((e) => e.event_id === eventId) || allEvents[0];
    }
    return normalizeEvent(await apiFetch(`/api/v1/claims/${eventId}`));
  },
};

export const digestApi = {
  getByDate: async (dateStr: string): Promise<ExecutionEvent[]> => {
    if (USE_MOCKS) {
      await sleep(500);
      return getAllEventsForCurrentProject();
    }
    const rows: any[] = await apiFetch(`/api/v1/digest?date=${dateStr}`);
    return rows.map((r) => normalizeEvent(r));
  },

  getAll: async (): Promise<ExecutionEvent[]> => {
    if (USE_MOCKS) {
      await sleep(500);
      return getAllEventsForCurrentProject();
    }
    const rows: any[] = await apiFetch('/api/v1/digest');
    return rows.map((r) => normalizeEvent(r));
  },

  bulkApprove: async (eventIds: string[]): Promise<{ approved: string[]; failed: string[] }> => {
    if (USE_MOCKS) {
      await sleep(900);
      return { approved: eventIds, failed: [] };
    }
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
    if (USE_MOCKS) {
      await sleep(800);
      recordApprovedActualProgress(payload.selected_activity_id, payload.approved_pct, payload.approved_qty, payload.action);
      return {
        decision_id: `dec-${Date.now()}`,
        event_id: payload.event_id,
        selected_activity_id: payload.selected_activity_id,
        action: payload.action,
        approved_pct: payload.approved_pct ?? null,
        approved_qty: payload.approved_qty ?? null,
        planner_id: 'usr-supervisor-01',
        justification: payload.justification,
        decided_at: new Date().toISOString(),
      };
    }
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
    if (USE_MOCKS) {
      await sleep(300);
      return getDatasetForCurrentProject().decisions;
    }
    return apiFetch('/api/v1/decisions?limit=10');
  },
};

export const progressApi = {
  getProjectProgress: async (projectId?: string, scheduleId?: string): Promise<ProjectProgressSummary> => {
    if (USE_MOCKS) {
      await sleep(150);
      const pId = projectId || getActiveProjectId();
      const sId = scheduleId || getActiveScheduleVersionId();
      return getMockProjectProgress(pId, sId);
    }
    const qs = new URLSearchParams();
    if (projectId) qs.set('project_id', projectId);
    if (scheduleId) qs.set('schedule_id', scheduleId);
    return apiFetch<ProjectProgressSummary>(`/api/v1/progress/project?${qs.toString()}`);
  },

  getStageProgress: async (stageId?: string, projectId?: string, scheduleId?: string): Promise<StageProgressSummary[]> => {
    if (USE_MOCKS) {
      await sleep(150);
      const pId = projectId || getActiveProjectId();
      const sId = scheduleId || getActiveScheduleVersionId();
      return getMockStageProgress(pId, sId, stageId);
    }
    const qs = new URLSearchParams();
    if (stageId) qs.set('stage_id', stageId);
    if (projectId) qs.set('project_id', projectId);
    if (scheduleId) qs.set('schedule_id', scheduleId);
    return apiFetch<StageProgressSummary[]>(`/api/v1/progress/stages?${qs.toString()}`);
  },

  getWbsProgress: async (scheduleId?: string, projectId?: string): Promise<WBSProgressSummary[]> => {
    if (USE_MOCKS) {
      await sleep(150);
      const pId = projectId || getActiveProjectId();
      const sId = scheduleId || getActiveScheduleVersionId();
      return getMockWBSProgress(pId, sId);
    }
    const qs = new URLSearchParams();
    if (scheduleId) qs.set('schedule_id', scheduleId);
    if (projectId) qs.set('project_id', projectId);
    return apiFetch<WBSProgressSummary[]>(`/api/v1/progress/wbs?${qs.toString()}`);
  },

  getActivityProgress: async (activityId: string, scheduleId?: string, projectId?: string): Promise<ActivityProgressSummary | null> => {
    if (USE_MOCKS) {
      await sleep(100);
      const pId = projectId || getActiveProjectId();
      const sId = scheduleId || getActiveScheduleVersionId();
      return getMockActivityProgress(activityId, pId, sId);
    }
    const qs = new URLSearchParams();
    if (scheduleId) qs.set('schedule_id', scheduleId);
    if (projectId) qs.set('project_id', projectId);
    return apiFetch<ActivityProgressSummary>(`/api/v1/progress/activities/${encodeURIComponent(activityId)}?${qs.toString()}`);
  },
};

export const contractorsApi = {
  getContractors: async (projectId?: string): Promise<Contractor[]> => {
    if (USE_MOCKS) {
      await sleep(150);
      const pId = projectId || getActiveProjectId();
      return getMockContractors(pId);
    }
    const qs = new URLSearchParams();
    if (projectId) qs.set('project_id', projectId);
    return apiFetch<Contractor[]>(`/api/v1/contractors?${qs.toString()}`);
  },

  getWorkPackages: async (projectId?: string, scheduleId?: string): Promise<WorkPackage[]> => {
    if (USE_MOCKS) {
      await sleep(150);
      const pId = projectId || getActiveProjectId();
      const sId = scheduleId || getActiveScheduleVersionId();
      return getMockWorkPackages(pId, sId);
    }
    const qs = new URLSearchParams();
    if (projectId) qs.set('project_id', projectId);
    if (scheduleId) qs.set('schedule_id', scheduleId);
    return apiFetch<WorkPackage[]>(`/api/v1/work-packages?${qs.toString()}`);
  },

  getContractorProgress: async (projectId?: string, scheduleId?: string): Promise<ContractorProgressSummary[]> => {
    if (USE_MOCKS) {
      await sleep(150);
      const pId = projectId || getActiveProjectId();
      const sId = scheduleId || getActiveScheduleVersionId();
      return getMockContractorProgress(pId, sId);
    }
    const qs = new URLSearchParams();
    if (projectId) qs.set('project_id', projectId);
    if (scheduleId) qs.set('schedule_id', scheduleId);
    return apiFetch<ContractorProgressSummary[]>(`/api/v1/progress/contractors?${qs.toString()}`);
  },

  getWorkPackageProgress: async (contractorId?: string, projectId?: string, scheduleId?: string): Promise<WorkPackageProgressSummary[]> => {
    if (USE_MOCKS) {
      await sleep(150);
      const pId = projectId || getActiveProjectId();
      const sId = scheduleId || getActiveScheduleVersionId();
      return getMockWorkPackageProgress(pId, sId, contractorId);
    }
    const qs = new URLSearchParams();
    if (contractorId) qs.set('contractor_id', contractorId);
    if (projectId) qs.set('project_id', projectId);
    if (scheduleId) qs.set('schedule_id', scheduleId);
    return apiFetch<WorkPackageProgressSummary[]>(`/api/v1/progress/work-packages?${qs.toString()}`);
  },
};

export const qualityGatesApi = {
  getGates: async (params?: { projectId?: string; scheduleId?: string; activityId?: string }): Promise<QualityGate[]> => {
    if (USE_MOCKS) {
      await sleep(100);
      const pId = params?.projectId || getActiveProjectId();
      const sId = params?.scheduleId || getActiveScheduleVersionId();
      return getMockQualityGates(pId, sId, params?.activityId);
    }
    const qs = new URLSearchParams();
    if (params?.projectId) qs.set('project_id', params.projectId);
    if (params?.scheduleId) qs.set('schedule_id', params.scheduleId);
    if (params?.activityId) qs.set('activity_id', params.activityId);
    return apiFetch<QualityGate[]>(`/api/v1/quality-gates?${qs.toString()}`);
  },

  getQualitySummary: async (activityId: string, projectId?: string, scheduleId?: string) => {
    if (USE_MOCKS) {
      await sleep(80);
      const pId = projectId || getActiveProjectId();
      const sId = scheduleId || getActiveScheduleVersionId();
      return getActivityQualitySummary(activityId, pId, sId);
    }
    const qs = new URLSearchParams();
    if (projectId) qs.set('project_id', projectId);
    if (scheduleId) qs.set('schedule_id', scheduleId);
    return apiFetch<{
      totalGates: number;
      completedGates: number;
      pendingRequiredHoldPoint: boolean;
      blockedRequiredHoldPoint: boolean;
      hasBlockingHoldPoint: boolean;
      blockingHoldPointName?: string;
      gates: QualityGate[];
    }>(`/api/v1/quality-gates/summary/${encodeURIComponent(activityId)}?${qs.toString()}`);
  },

  completeGate: async (gateId: string, completedBy = 'usr-supervisor-01', evidenceId?: string): Promise<QualityGate> => {
    if (USE_MOCKS) {
      await sleep(250);
      const updated = completeMockQualityGate(gateId, completedBy, evidenceId);
      MOCK_AUDIT_LOGS.unshift({
        log_id: Date.now(),
        entity_type: 'quality_gate',
        entity_id: gateId,
        action: 'QUALITY_GATE_COMPLETED',
        actor_id: completedBy,
        before_state: JSON.stringify({ gate_id: gateId, status: 'PENDING' }),
        after_state: JSON.stringify({ gate_id: gateId, status: 'COMPLETED', completed_at: updated.completedAt }),
        payload_hash: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
        previous_hash: 'e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7',
        current_hash: 'f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8',
        timestamp: new Date().toISOString(),
      });
      return updated;
    }
    return apiFetch<QualityGate>(`/api/v1/quality-gates/${encodeURIComponent(gateId)}/complete`, {
      method: 'POST',
      body: JSON.stringify({ completed_by: completedBy, evidence_id: evidenceId }),
    });
  },

  waiveGate: async (gateId: string, justification: string, waivedBy = 'usr-supervisor-01'): Promise<QualityGate> => {
    if (USE_MOCKS) {
      await sleep(250);
      const updated = waiveMockQualityGate(gateId, justification, waivedBy);
      MOCK_AUDIT_LOGS.unshift({
        log_id: Date.now(),
        entity_type: 'quality_gate',
        entity_id: gateId,
        action: 'QUALITY_GATE_WAIVED',
        actor_id: waivedBy,
        before_state: JSON.stringify({ gate_id: gateId, status: 'PENDING' }),
        after_state: JSON.stringify({ gate_id: gateId, status: 'WAIVED', justification, waived_at: updated.waivedAt }),
        payload_hash: 'f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8',
        previous_hash: 'e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7',
        current_hash: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
        timestamp: new Date().toISOString(),
      });
      return updated;
    }
    return apiFetch<QualityGate>(`/api/v1/quality-gates/${encodeURIComponent(gateId)}/waive`, {
      method: 'POST',
      body: JSON.stringify({ justification, waived_by: waivedBy }),
    });
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
    if (USE_MOCKS) {
      await sleep(300);
      return getDatasetForCurrentProject().dashboard;
    }
    const data: any = await apiFetch('/api/v1/dashboard/summary');
    return { ...data, claims_trend_pct: data.claims_trend_pct ?? null };
  },

  getDelayReasons: async (): Promise<{ reason: string; count: number }[]> => {
    if (USE_MOCKS) {
      await sleep(400);
      return getDatasetForCurrentProject().delayReasons;
    }
    const data: any = await apiFetch('/api/v1/dashboard/delay-reasons');
    return (data.delay_reasons || []).map((d: any) => ({ reason: d.delay_reason, count: d.count }));
  },

  getInstitutionalMemory: async (): Promise<{ topic: string; resolution: string; count: number }[]> => {
    if (USE_MOCKS) {
      await sleep(400);
      return getDatasetForCurrentProject().institutionalMemory;
    }
    const data: any = await apiFetch('/api/v1/dashboard/institutional-memory');
    return (data.activities || []).map((a: any) => ({ topic: a.activity_id + ' (' + a.discipline + ')', resolution: a.variance_days !== null ? (a.variance_days > 0 ? a.variance_days + ' days delayed' : Math.abs(a.variance_days) + ' days ahead') : 'No actuals yet', count: a.planned_duration || 0 }));
  },

  getForecast: async (discipline: string = 'CIVIL'): Promise<DisciplineForecastData> => {
    if (USE_MOCKS) {
      await sleep(400);
      const fc = getDatasetForCurrentProject().forecast;
      return { ...fc, discipline };
    }
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
    if (USE_MOCKS) {
      await sleep(400);
      const acts = getActivitiesForCurrentSchedule();
      return acts.filter((a) => !a.is_critical || a.baseline_pct_complete < 50).slice(0, 3);
    }
    const data: any = await apiFetch('/api/v1/alerts/silent-activities');
    return (data.silent_activities || []).map((a: any) => ({ ...a, asset_tag: a.asset_tag || null, uom: a.uom || null, baseline_pct_complete: a.baseline_pct_complete || 0 }));
  },

  getExportCsvUrl: (): string => {
    return `${BASE_URL}/api/v1/export/csv`;
  },

  exportCsv: async (): Promise<Blob> => {
    if (USE_MOCKS) {
      await sleep(400);
      const acts = getActivitiesForCurrentSchedule();
      const rows = ['activity_id,activity_name,discipline,wbs_code,planned_start,planned_finish,baseline_pct_complete'];
      for (const a of acts) {
        rows.push(`${a.activity_id},"${a.activity_name}",${a.discipline},${a.wbs_code || ''},${a.planned_start},${a.planned_finish},${a.baseline_pct_complete}`);
      }
      return new Blob([rows.join('\n')], { type: 'text/csv; charset=utf-8' });
    }
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
    if (USE_MOCKS) {
      await sleep(300);
      const acts = getActivitiesForCurrentSchedule(params?.schedule_id);
      let filtered = acts.map((a): ActivitySummary => {
        const isCrit = a.is_critical ?? null;
        const totalFloat = a.total_float ?? null;
        const actualStart = a.actual_start || (a.baseline_pct_complete > 0 ? a.planned_start : null);
        const actualPct = a.actual_pct_complete !== undefined ? a.actual_pct_complete : (a.baseline_pct_complete > 0 ? a.baseline_pct_complete : null);
        let execState: ExecutionState = a.execution_state || 'NOT_STARTED';
        if (!a.execution_state) {
          if (actualPct !== null && actualPct >= 100) execState = 'COMPLETED';
          else if (actualStart) execState = 'IN_PROGRESS';
        }

        const hasChg = a.baseline_pct_complete > 0 || execState !== 'NOT_STARTED';
        return {
          activity_id: a.activity_id,
          activity_name: a.activity_name,
          schedule_id: a.schedule_id,
          discipline: a.discipline,
          location: a.location,
          asset_tag: a.asset_tag,
          wbs_code: a.wbs_code,
          planned_start: a.planned_start,
          planned_finish: a.planned_finish,
          planned_quantity: a.planned_quantity,
          uom: a.uom,
          baseline_pct_complete: a.baseline_pct_complete,
          actual_start: actualStart,
          actual_finish: a.actual_finish || (execState === 'COMPLETED' ? a.planned_finish : null),
          actual_pct_complete: actualPct,
          execution_state: execState,
          is_critical: isCrit,
          total_float: totalFloat,
          has_changes: hasChg,
          event_count: hasChg ? 2 : 0,
          last_changed_at: hasChg ? new Date().toISOString() : null,
        };
      });

      if (params?.search) {
        const s = params.search.toLowerCase();
        filtered = filtered.filter(
          (a) =>
            a.activity_id.toLowerCase().includes(s) ||
            a.activity_name.toLowerCase().includes(s) ||
            (a.asset_tag && a.asset_tag.toLowerCase().includes(s))
        );
      }
      if (params?.discipline && params.discipline !== 'ALL') {
        filtered = filtered.filter((a) => a.discipline.toUpperCase() === params.discipline!.toUpperCase());
      }
      if (params?.location && params.location !== 'ALL') {
        filtered = filtered.filter((a) => a.location.toLowerCase() === params.location!.toLowerCase());
      }
      if (params?.wbs_code && params.wbs_code !== 'ALL') {
        filtered = filtered.filter((a) => a.wbs_code && a.wbs_code.startsWith(params.wbs_code!));
      }
      if (params?.execution_state && params.execution_state !== 'ALL') {
        filtered = filtered.filter((a) => a.execution_state === params.execution_state);
      }
      if (params?.is_critical && params.is_critical !== 'ALL') {
        if (params.is_critical === 'CRITICAL') filtered = filtered.filter((a) => a.is_critical === true);
        else if (params.is_critical === 'NON_CRITICAL') filtered = filtered.filter((a) => a.is_critical === false);
        else if (params.is_critical === 'UNKNOWN') filtered = filtered.filter((a) => a.is_critical === null);
      }
      if (params?.float_range && params.float_range !== 'ALL') {
        if (params.float_range === 'ZERO') filtered = filtered.filter((a) => a.total_float === 0);
        else if (params.float_range === '1_TO_5') filtered = filtered.filter((a) => a.total_float !== null && a.total_float > 0 && a.total_float <= 5);
        else if (params.float_range === 'GT_5') filtered = filtered.filter((a) => a.total_float !== null && a.total_float > 5);
        else if (params.float_range === 'UNKNOWN') filtered = filtered.filter((a) => a.total_float === null);
      }
      if (params?.has_changes !== undefined) {
        filtered = filtered.filter((a) => a.has_changes === params.has_changes);
      }

      const total = filtered.length;
      const in_progress = filtered.filter((a) => a.execution_state === 'IN_PROGRESS').length;
      const completed = filtered.filter((a) => a.execution_state === 'COMPLETED').length;
      const not_started = filtered.filter((a) => a.execution_state === 'NOT_STARTED').length;
      const critical = filtered.filter((a) => a.is_critical === true).length;
      const changed = filtered.filter((a) => a.has_changes).length;

      const page = params?.page || 1;
      const pageSize = params?.page_size || 25;
      const paginated = filtered.slice((page - 1) * pageSize, page * pageSize);

      return {
        items: paginated,
        total,
        page,
        page_size: pageSize,
        schedule_id: params?.schedule_id || getActiveScheduleVersionId(),
        metrics: {
          total,
          in_progress,
          completed,
          not_started,
          critical,
          changed,
        },
      };
    }

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
    if (USE_MOCKS) {
      await sleep(500);
      const acts = getActivitiesForCurrentSchedule(scheduleIdArg);
      const activity = acts.find((a) => a.activity_id === activityId) || acts[0];
      const allEvents = getAllEventsForCurrentProject();
      const matchedEv = allEvents.find((e) => e.matched_activity_id === activityId || e.reported_activity_id === activityId) || allEvents[0];
      const schedId = scheduleIdArg || getActiveScheduleVersionId();

      const isCompletedOrReopened =
        activity?.execution_state === 'COMPLETED' ||
        activity?.execution_state === 'REOPENED' ||
        activity?.execution_state === 'REOPEN_REQUESTED' ||
        activity?.activity_id === 'ACT-ASSAM-401';

      const mockTimeline: ActivityTimelineItem[] = isCompletedOrReopened
        ? [
            {
              type: 'approved_actual',
              actual_id: `actl-hist-${activityId}`,
              decision_id: `dec-close-${activityId}`,
              event_id: `evt-comp-${activityId}`,
              schedule_id: schedId,
              activity_id: activityId,
              actual_start: activity?.actual_start || '2026-09-01',
              actual_finish: activity?.actual_finish || '2026-09-08',
              actual_pct_complete: 100,
              actual_quantity: activity?.planned_quantity || null,
              timestamp: '2026-09-08T18:00:00.000Z',
            },
            {
              type: 'planner_decision',
              decision_id: `dec-close-${activityId}`,
              event_id: `evt-comp-${activityId}`,
              selected_activity_id: activityId,
              action: 'APPROVE',
              approved_pct: 100,
              approved_qty: activity?.planned_quantity || null,
              planner_id: 'usr-supervisor-01',
              justification: 'Completed SCADA RTU & Solar Power Skid Installation signed off and QA/QC inspection certificate verified. Authoritative Actual Finish locked.',
              timestamp: '2026-09-08T17:30:00.000Z',
            },
            {
              type: 'execution_event',
              event_id: `evt-comp-${activityId}`,
              schedule_id: schedId,
              event_date: '2026-09-08',
              raw_claim_text: `Installation of ${activity?.activity_name || activityId} completed to 100% on site. Solar power skid energized.`,
              claim_mode: 'CUMULATIVE_PCT',
              claimed_pct: 100,
              claimed_quantity: activity?.planned_quantity || null,
              delay_reason: null,
              status: 'APPROVED',
              timestamp: '2026-09-08T16:00:00.000Z',
              source_references: [
                {
                  reference_id: 'REF-COMP-01',
                  file_name: `${activity?.discipline || 'Electrical'}_Commissioning_DPR.pdf`,
                  sheet_name: 'Completion Sign-off',
                  row_cell_ref: 'Row 42',
                  message_id: null,
                  raw_snippet: `Final handover signoff for ${activity?.activity_name || activityId}. 100% complete.`,
                },
              ],
            },
            ...(activity?.execution_state === 'REOPEN_REQUESTED' || activity?.execution_state === 'REOPENED'
              ? [
                  {
                    type: 'execution_event' as const,
                    event_id: `evt-reopen-${activityId}`,
                    schedule_id: schedId,
                    event_date: TODAY,
                    raw_claim_text: `Activity Reopen Request: Additional instrumentation integration required for ${activity?.activity_name || activityId}.`,
                    claim_mode: 'CUMULATIVE_PCT',
                    claimed_pct: activity?.actual_pct_complete ?? 100,
                    claimed_quantity: null,
                    delay_reason: null,
                    status: 'REOPEN_REQUESTED',
                    timestamp: new Date(Date.now() - 3600000).toISOString(),
                  },
                ]
              : []),
            ...(activity?.execution_state === 'REOPENED'
              ? [
                  {
                    type: 'planner_decision' as const,
                    decision_id: `dec-reopen-${activityId}`,
                    event_id: `evt-reopen-${activityId}`,
                    selected_activity_id: activityId,
                    action: 'APPROVE',
                    approved_pct: activity?.actual_pct_complete ?? 100,
                    approved_qty: null,
                    planner_id: 'usr-supervisor-01',
                    justification: 'Supervisor approved Reopen Request. Previous Actual Finish (2026-09-08) & 100% completion remain traceable in history. Activity unlocked for new progress claims.',
                    timestamp: new Date(Date.now() - 1800000).toISOString(),
                  },
                ]
              : []),
          ]
        : [
            {
              type: 'execution_event',
              event_id: matchedEv?.event_id || 'evt-101',
              schedule_id: schedId,
              event_date: matchedEv?.event_date || TODAY,
              raw_claim_text: matchedEv?.raw_claim_text || 'Daily execution progress claim logged for work package.',
              claim_mode: matchedEv?.claim_mode || 'CUMULATIVE_PCT',
              claimed_pct: matchedEv?.claimed_pct ?? 75,
              claimed_quantity: matchedEv?.claimed_quantity ?? null,
              delay_reason: matchedEv?.delay_reason ?? null,
              status: matchedEv?.status || 'REVIEW_REQUIRED',
              timestamp: matchedEv?.created_at || new Date().toISOString(),
              source_references: [
                {
                  reference_id: 'REF-001',
                  file_name: `${activity?.discipline || 'Civil'}_Shift_Report.pdf`,
                  sheet_name: 'Field Execution',
                  row_cell_ref: 'Row 14',
                  message_id: null,
                  raw_snippet: `Work on ${activity?.activity_name || activityId} progressed to ${activity?.baseline_pct_complete || 50}%.`,
                },
              ],
            },
            {
              type: 'planner_decision',
              decision_id: 'dec-102',
              event_id: matchedEv?.event_id || 'evt-101',
              selected_activity_id: activityId,
              action: 'APPROVE',
              approved_pct: activity?.baseline_pct_complete || 75,
              approved_qty: null,
              planner_id: 'usr-supervisor-01',
              justification: 'Verified against QA/QC physical inspection sign-off certificate.',
              timestamp: new Date(Date.now() - 3600000).toISOString(),
            },
            {
              type: 'approved_actual',
              actual_id: 'actl-102',
              decision_id: 'dec-102',
              event_id: matchedEv?.event_id || 'evt-101',
              schedule_id: schedId,
              activity_id: activityId,
              actual_start: activity?.planned_start || '2026-09-01',
              actual_finish: activity?.baseline_pct_complete >= 100 ? activity?.planned_finish : null,
              actual_pct_complete: activity?.baseline_pct_complete || 75,
              actual_quantity: null,
              timestamp: new Date(Date.now() - 3590000).toISOString(),
            },
          ];

      return {
        activity: activity || null,
        timeline: mockTimeline,
        history: [
          {
            timestamp: '2026-09-08T16:00:00.000Z',
            raw_claim_text: isCompletedOrReopened
              ? `Handover sign-off for ${activity?.activity_name || activityId} (100% Complete)`
              : matchedEv?.raw_claim_text || `Field report for ${activity?.activity_name || activityId}`,
            input_channel: 'TYPED_TEXT' as InputChannel,
            claimed_pct: 100,
            claimed_qty: null,
            status: 'APPROVED' as ClaimStatus,
            supervisor_action: 'APPROVE',
            actor: 'Site Engineer (Commissioning Shift Log)',
          },
        ],
      };
    }
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
    if (USE_MOCKS) {
      await sleep(200);
      return updateActivityExecutionState(activityId, newState);
    }
    return apiFetch<ScheduleActivity>(`/api/v1/activities/${encodeURIComponent(activityId)}/state`, {
      method: 'PATCH',
      body: JSON.stringify({ execution_state: newState }),
    });
  },
};

export const reopenApi = {
  getRequests: async (params?: { activity_id?: string; status?: string }): Promise<ReopenRequest[]> => {
    if (USE_MOCKS) {
      await sleep(200);
      let list = getMockReopenRequests();
      if (params?.activity_id) {
        list = list.filter((r) => r.activity_id === params.activity_id);
      }
      if (params?.status) {
        list = list.filter((r) => r.status === params.status);
      }
      return list;
    }
    const query = new URLSearchParams();
    if (params?.activity_id) query.set('activity_id', params.activity_id);
    if (params?.status) query.set('status', params.status);
    return apiFetch<ReopenRequest[]>(`/api/v1/reopen-requests?${query.toString()}`);
  },

  createRequest: async (payload: {
    activity_id: string;
    reason: string;
    justification: string;
    requested_by_role: UserRole;
    requested_by_name: string;
    event_id?: string;
  }): Promise<ReopenRequest> => {
    if (USE_MOCKS) {
      await sleep(400);
      return createMockReopenRequest(payload);
    }
    return apiFetch<ReopenRequest>('/api/v1/reopen-requests', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  reviewRequest: async (payload: {
    reopen_id: string;
    decision: 'APPROVED' | 'REJECTED';
    supervisor_notes?: string;
    reviewer_name?: string;
  }): Promise<ReopenRequest> => {
    if (USE_MOCKS) {
      await sleep(400);
      const res = reviewMockReopenRequest(payload);
      MOCK_AUDIT_LOGS.unshift({
        log_id: Date.now(),
        entity_type: 'execution_event',
        entity_id: res.event_id || res.reopen_id || 'reopen-log',
        action: payload.decision === 'APPROVED' ? 'DECISION_APPROVE' : 'DECISION_REJECT',
        actor_id: payload.reviewer_name || 'usr-supervisor-01',
        before_state: JSON.stringify({
          activity_id: res.activity_id,
          execution_state: 'COMPLETED',
          actual_start: res.locked_actuals_summary?.actual_start || '2026-09-01',
          actual_finish: res.locked_actuals_summary?.actual_finish || '2026-09-08',
          actual_pct_complete: res.original_actual_pct || 100,
          status: 'REOPEN_REQUESTED',
        }),
        after_state: JSON.stringify({
          activity_id: res.activity_id,
          execution_state: payload.decision === 'APPROVED' ? 'REOPENED' : 'COMPLETED',
          previous_actual_start: res.locked_actuals_summary?.actual_start || '2026-09-01',
          previous_actual_finish: res.locked_actuals_summary?.actual_finish || '2026-09-08',
          decision: payload.decision,
          supervisor_notes: payload.supervisor_notes || null,
          status: payload.decision === 'APPROVED' ? 'REOPEN_APPROVED' : 'REOPEN_REJECTED',
        }),
        payload_hash: 'f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8',
        previous_hash: 'e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7',
        current_hash: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
        timestamp: new Date().toISOString(),
      });
      return res;
    }
    return apiFetch<ReopenRequest>(`/api/v1/reopen-requests/${payload.reopen_id}/review`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
};

// The schedule new claims are matched against (most recently created). Cached for the
// session; real mode only -- mock mode keeps its active mock schedule id.
let activeScheduleIdPromise: Promise<string> | null = null;
export function getActiveScheduleId(): Promise<string> {
  if (USE_MOCKS) return Promise.resolve(getActiveScheduleVersionId());
  if (!activeScheduleIdPromise) {
    activeScheduleIdPromise = apiFetch<{ schedule_id: string }>('/api/v1/schedules/active')
      .then((s) => s.schedule_id)
      .catch((err) => {
        activeScheduleIdPromise = null; // retry next call
        throw err;
      });
  }
  return activeScheduleIdPromise;
}

export const graphApi = {
  getActivityGraph: async (activityId: string, depth = 1, scheduleId?: string): Promise<{ nodes: any[]; edges: any[] }> => {
    if (USE_MOCKS) {
      await sleep(300);
      const activities = getActivitiesForCurrentSchedule(scheduleId);
      const current = activities.find((a) => a.activity_id === activityId) || activities[0];
      const nodes = current ? [{ id: current.activity_id, label: current.activity_name, type: 'activity' }] : [];
      return { nodes, edges: [] };
    }
    const sid = scheduleId ?? (await getActiveScheduleId().catch(() => undefined));
    const query = sid ? `?depth=${depth}&schedule_id=${sid}` : `?depth=${depth}`;
    return apiFetch<{ nodes: any[]; edges: any[] }>(`/api/v1/graph/activity/${activityId}${query}`);
  },
};

export const schedulesApi = {
  getActivities: async (scheduleId?: string): Promise<ScheduleActivity[]> => {
    if (USE_MOCKS) {
      await sleep(300);
      return getActivitiesForCurrentSchedule(scheduleId);
    }
    const sid = scheduleId ?? (await getActiveScheduleId());
    return apiFetch(`/api/v1/schedules/${sid}/activities`);
  },

  getImpactPreview: async (activityId: string, delayDays: number, scheduleId?: string): Promise<ImpactPreviewResult> => {
    if (USE_MOCKS) {
      await sleep(500);
      const activities = getActivitiesForCurrentSchedule(scheduleId);
      const activity = activities.find((a) => a.activity_id === activityId) || activities[0];
      const successors = activities.filter((a) => a.activity_id !== activity?.activity_id).slice(0, 2);
      const mockImpacts: ImpactEvaluationItem[] = successors.map((succ, idx) => ({
        successor_activity_id: succ.activity_id,
        activity_name: succ.activity_name,
        dependency_type: 'FS',
        original_earliest_start: succ.planned_start,
        shifted_earliest_start: succ.planned_start,
        original_planned_finish: succ.planned_finish,
        shifted_earliest_finish: succ.planned_finish,
        propagation_depth: idx + 1,
        target_path: [activity?.activity_id || 'ACT-101', succ.activity_id],
        execution_state: 'IN_PROGRESS' as ExecutionState,
        gross_delay_days: delayDays,
        total_float: idx === 0 ? 2 : 0,
        float_status: 'KNOWN',
        absorbed_delay_days: idx === 0 ? Math.min(2, delayDays) : 0,
        net_delay_days: idx === 0 ? Math.max(0, delayDays - 2) : delayDays,
        controlling_predecessor: activity?.activity_id || 'ACT-101',
        controlling_relationship: 'FS',
        uncertainty: false,
        classification: delayDays > 2 ? 'CRITICAL_PATH_SLIP' : idx === 0 ? 'ABSORBED_BY_FLOAT' : 'NO_IMPACT',
      }));

      return {
        activity_id: activity.activity_id,
        activity_name: activity.activity_name,
        planned_start: activity.planned_start,
        planned_finish: activity.planned_finish,
        shifted_finish: activity.planned_finish,
        delay_days: delayDays,
        propagation_depth_limit: 5,
        disclaimer: 'Preview only · Deterministic A1 CPM Evaluation · Full Multi-Hop Propagation',
        impacts: mockImpacts,
        successors: mockImpacts.map((imp) => ({
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
    }
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
    if (USE_MOCKS) {
      await sleep(400);
      const activities = getActivitiesForCurrentSchedule(scheduleId);
      const groupMap = new Map<string, WBSGroupActivity[]>();
      for (const a of activities) {
        if (!a.wbs_code) continue;
        const list = groupMap.get(a.wbs_code) || [];
        list.push({ activity_id: a.activity_id, planned_quantity: a.planned_quantity });
        groupMap.set(a.wbs_code, list);
      }
      const wbs_groups: WBSGroup[] = Array.from(groupMap.entries()).map(
        ([wbs_code, acts]) => ({ wbs_code, activities: acts })
      );
      return { schedule_id: scheduleId, wbs_groups };
    }
    return apiFetch(`/api/v1/schedules/${scheduleId}/wbs-tree`);
  },
};

export const MOCK_SPLITS_STORE = new Map<string, WBSSplitItem[]>();

export const wbsApi = {
  getSplits: async (eventId: string): Promise<WBSSplitItem[]> => {
    if (USE_MOCKS) {
      await sleep(300);
      if (MOCK_SPLITS_STORE.has(eventId)) {
        return MOCK_SPLITS_STORE.get(eventId)!;
      }
      // Only the explicit demo unmatched/decomposed claim in initial dataset has default mock splits
      if (eventId === 'evt-assam-104') {
        const activities = getActivitiesForCurrentSchedule();
        const a1 = activities[0]?.activity_id || 'ACT-ASSAM-101';
        const a2 = activities[1]?.activity_id || 'ACT-ASSAM-201';
        return [
          {
            split_id: `spl-${eventId}-1`,
            event_id: eventId,
            activity_id: a1,
            split_basis: 'WBS_WEIGHTED',
            split_pct: 0.6,
            allocated_quantity: 45,
            uom: 'cu.m',
            rationale: 'Primary work package',
            created_at: new Date().toISOString(),
          },
          {
            split_id: `spl-${eventId}-2`,
            event_id: eventId,
            activity_id: a2,
            split_basis: 'MANUAL',
            split_pct: 0.4,
            allocated_quantity: 30,
            uom: 'cu.m',
            rationale: 'Secondary tie-in / handover scope',
            created_at: new Date().toISOString(),
          },
        ];
      }
      return [];
    }
    const data: any = await apiFetch(`/api/v1/claims/${eventId}/splits`);
    return Array.isArray(data) ? data : data.splits || [];
  },

  updateSplits: async (eventId: string, splits: Partial<WBSSplitItem>[]): Promise<WBSSplitResponse> => {
    if (USE_MOCKS) {
      await sleep(600);
      const items: WBSSplitItem[] = splits.map((s, i) => ({
        split_id: s.split_id || `spl-${eventId}-${i + 1}`,
        event_id: eventId,
        activity_id: s.activity_id || 'ACT-ASSAM-101',
        split_basis: s.split_basis || 'WBS_WEIGHTED',
        split_pct: s.split_pct || 0.5,
        allocated_quantity: s.allocated_quantity || null,
        uom: s.uom || null,
        rationale: s.rationale || 'Supervisor manual WBS decomposition',
        created_at: new Date().toISOString(),
      }));
      MOCK_SPLITS_STORE.set(eventId, items);
      return {
        event_id: eventId,
        status: 'SPLIT_APPROVED',
        message: `Successfully updated ${splits.length} WBS split allocations.`,
      };
    }
    return apiFetch(`/api/v1/claims/${eventId}/splits`, {
      method: 'PATCH',
      body: JSON.stringify({
        splits: splits.map((s) => ({ activity_id: s.activity_id, split_pct: s.split_pct })),
      }),
    });
  },

  splitClaim: async (request: WBSSplitRequest): Promise<WBSSplitResponse> => {
    if (USE_MOCKS) {
      await sleep(600);
      return {
        event_id: request.event_id,
        status: 'SPLIT_APPROVED',
        message: `Successfully allocated claim across ${request.allocations.length} WBS activities.`,
      };
    }
    return wbsApi.updateSplits(request.event_id, request.allocations as any);
  },

  getTree: async (scheduleId?: string): Promise<WBSTreeResponse> => {
    return schedulesApi.getWbsTree(scheduleId);
  },
};

export const auditApi = {
  getLogs: async (): Promise<AuditLogEntry[]> => {
    if (USE_MOCKS) {
      await sleep(400);
      return MOCK_AUDIT_LOGS;
    }
    return apiFetch('/api/v1/audit');
  },
};

export const reportsApi = {
  getExecutionSummary: async (filter?: ExecutionSummaryFilter): Promise<ExecutionReportResponse> => {
    const startDate = filter?.start || filter?.start_date;
    const endDate = filter?.end || filter?.end_date;

    if (USE_MOCKS) {
      await sleep(700);
      const ds = getDatasetForCurrentProject();
      const todayStr = new Date().toISOString().split('T')[0];
      const pastWeekStr = new Date(Date.now() - 7 * 86400000).toISOString().split('T')[0];
      return {
        reporting_period: {
          start_date: startDate || pastWeekStr,
          end_date: endDate || todayStr,
        },
        discipline: filter?.discipline || null,
        schedule_id: filter?.schedule_id || getActiveScheduleVersionId(),
        summary_text: ds.summaryReport.text,
        metrics: {
          total_claims_processed: ds.dashboard.total_claims,
          approval_rate_pct: 92.4,
          open_conflicts_count: ds.dashboard.conflicts,
          high_priority_escalations: 1,
          top_delay_drivers: ds.delayReasons,
          disciplines_active: ['CIVIL', 'PIPING', 'ELECTRICAL', 'HSE'],
        },
        key_highlights: ds.summaryReport.highlights,
        generated_at: new Date().toISOString(),
      };
    }
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
    if (USE_MOCKS) {
      await sleep(300);
      const activities = getActivitiesForCurrentSchedule();
      const activity = activities.find((a) => a.activity_id === activityId) || activities[0];
      const events = getAllEventsForCurrentProject();
      return {
        root_activity_id: activityId,
        depth,
        context: {
          activity: activity || { activity_id: activityId, activity_name: 'Activity', discipline: 'CIVIL' },
          execution_events: events.slice(0, 2),
          validations: [],
          conflicts: [],
          impacts: [],
          evidence: [{ reference_id: 'REF-01', file_name: 'inspection_log.pdf', raw_snippet: 'Visual inspection accepted' }],
          decisions: [],
          approved_actuals: [],
          dependencies: [],
        },
        summary: {
          conflict_status: 'not_present',
          validation_status: 'not_present',
          impact_status: 'not_present',
          evidence_status: 'present',
          approved_actual_status: 'not_present',
          dependencies_count: 0,
          execution_events_count: events.length > 0 ? 1 : 0,
          decisions_count: 0,
        },
        graph: { nodes: [], edges: [] },
      };
    }
    return apiFetch<InvestigationContext>(`/api/v1/investigation/activity/${activityId}?depth=${depth}`);
  },
};

// Runtime translation of generated text for display (canonical text is never changed).
// Falls back to the original strings on any failure or in mock mode.
export const translateApi = {
  translate: async (texts: string[], targetLanguage: string): Promise<string[]> => {
    const lang = (targetLanguage || 'en').slice(0, 2);
    if (USE_MOCKS || lang === 'en' || texts.length === 0) return texts;
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

    if (USE_MOCKS) {
      await sleep(350);
      const ds = getDatasetForCurrentProject();
      const isHindi = params?.language === 'hi';
      const isTelugu = params?.language === 'te';
      const lang = params?.language || 'en';

      const canonical = ds.summaryReport.text;
      let summaryText = canonical;
      if (isHindi) {
        summaryText = `[परियोजना सारांश] ${canonical}`;
      } else if (isTelugu) {
        summaryText = `[ప్రాజెక్ట్ సారాంశం] ${canonical}`;
      }

      return {
        period: {
          type: params?.period || 'last_7_days',
          start: params?.start_date || '2026-09-11',
          end: params?.end_date || '2026-09-18',
        },
        discipline: params?.discipline || 'ALL',
        aggregate: {
          period: { type: params?.period || 'last_7_days', start: '2026-09-11', end: '2026-09-18' },
          discipline: params?.discipline || 'ALL',
          claims: { total_claims: ds.dashboard.total_claims, by_status: { APPROVED: 18, REVIEW_REQUIRED: 4, EXTRACTED: 2 }, by_event_type: { PROGRESS_UPDATE: 20, DELAY: 4 } },
          approved_progress: { total_approved: ds.dashboard.actuals, activities_with_actuals: 14, avg_approved_pct: 68.4 },
          conflicts: { total_conflicts: ds.dashboard.conflicts, by_status: { OPEN: ds.dashboard.conflicts } },
          validation_issues: { total_issues: ds.validationIssues.length, by_severity: { WARNING: 2, ERROR: 1 } },
          delays: { total_delay_events: ds.delayReasons.length, reasons: { WEATHER: 2, MATERIAL: 2 } },
          activities: { total: Object.values(ds.activities).flat().length, completed: 8, in_progress: 16, not_started: 8 },
          forecast: { status: 'available', historical_ratio: 1.12 },
        },
        canonical_summary: canonical,
        summary: summaryText,
        language: lang,
        cached: false,
        generated_by: 'llm',
      };
    }
    return apiFetch<ExecutionSummaryResponse>(`/api/v1/execution-summary?${query.toString()}`);
  },
};

export const mockP6Api = {
  getReceived: async (): Promise<{ count: number; payloads: any[] }> => {
    if (USE_MOCKS) {
      await sleep(300);
      const activities = getActivitiesForCurrentSchedule().slice(0, 3);
      return {
        count: activities.length,
        payloads: activities.map((a) => ({
          Id: a.activity_id,
          StartDate: a.planned_start,
          FinishDate: a.planned_finish,
          PercentComplete: a.baseline_pct_complete || 0,
        })),
      };
    }
    return apiFetch('/api/v1/mock-p6/received');
  },

  updateActivity: async (
    activityId: string,
    payload: { Id: string; StartDate?: string; FinishDate?: string; PercentComplete?: number }
  ): Promise<{ status: string; activity_id: string; p6_id: string; message: string }> => {
    if (USE_MOCKS) {
      await sleep(500);
      return {
        status: 'success',
        activity_id: activityId,
        p6_id: payload.Id,
        message: `Activity ${activityId} actuals updated in mock P6 EPPM`,
      };
    }
    return apiFetch(`/api/v1/mock-p6/activities/${encodeURIComponent(activityId)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
};

export const impactApi = {
  getScheduleImpacts: async (projectId: string, scheduleId: string): Promise<CompoundImpact[]> => {
    if (USE_MOCKS) {
      await sleep(150);
      return getMockCompoundImpacts(projectId, scheduleId);
    }
    return apiFetch<CompoundImpact[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/schedules/${encodeURIComponent(scheduleId)}/compound-impacts`);
  },

  getActivityImpact: async (projectId: string, scheduleId: string, activityId: string): Promise<CompoundImpact | null> => {
    if (USE_MOCKS) {
      await sleep(100);
      return getMockActivityImpact(projectId, scheduleId, activityId);
    }
    return apiFetch<CompoundImpact | null>(`/api/v1/projects/${encodeURIComponent(projectId)}/schedules/${encodeURIComponent(scheduleId)}/activities/${encodeURIComponent(activityId)}/compound-impact`);
  },

  getDependencies: async (scheduleId: string): Promise<ScheduleDependency[]> => {
    if (USE_MOCKS) {
      await sleep(100);
      return getMockScheduleDependencies(scheduleId);
    }
    return apiFetch<ScheduleDependency[]>(`/api/v1/schedules/${encodeURIComponent(scheduleId)}/dependencies`);
  },
};

