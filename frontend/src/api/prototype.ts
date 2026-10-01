/**
 * Prototype clients: project dashboard, issues & delays (root cause), notifications / my claims, multi-file intake and
 * institutional memory. Real endpoints only; shapes mirror backend/schemas/issue.py, backend/services/*_service.py and
 * backend/routers/{project_dashboard,issues,notifications,batches,memory}.py.
 */
import { apiFetch, BASE_URL } from './client';
import { requestHeaders } from '@/lib/apiContext';

const proj = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}`;
const sched = (projectId: string, scheduleId: string) => `${proj(projectId)}/schedules/${encodeURIComponent(scheduleId)}`;

// ───────────────────────────────────────────────────────────────────────────── dashboard (database-derived progress)
export type ProjectLifecycle = 'UPCOMING' | 'ONGOING' | 'COMPLETED';

export interface ProgressRollup {
  actual_pct: number;
  planned_pct: number;
  variance_pct: number;
  activity_count: number;
  completed_count: number;
  in_progress_count: number;
  not_started_count: number;
  blocked_count: number;
}

export interface StageRollup extends ProgressRollup {
  stage_id: string;
  stage_code: string | null;
  stage_name: string;
  sequence_order: number;
  weight_pct: number | null;
  status: string;
  planned_start: string | null;
  planned_finish: string | null;
  open_issue_count: number;
  issue_count: number;
}

export interface DisciplineRollup extends ProgressRollup {
  discipline: string;
  discipline_name: string;
}

export interface ProjectDashboard {
  project_id: string;
  schedule_id: string;
  project_code: string;
  project_name: string;
  lifecycle_status: ProjectLifecycle;
  location: string | null;
  project_type: string | null;
  planned_start: string | null;
  planned_finish: string | null;
  as_of_date: string;
  overall: ProgressRollup;
  stages: StageRollup[];
  disciplines: DisciplineRollup[];
  issues: { open: number; total: number; by_category: { category_code: string; n: number; open: number }[] };
  pending_review_count: number;
}

export const projectDashboardApi = {
  get: (projectId: string, scheduleId: string) => apiFetch<ProjectDashboard>(`${sched(projectId, scheduleId)}/dashboard`),
};

// ─────────────────────────────────────────────────────────────────────────────────────────── issues & root causes
export type IssueCategoryCode =
  | 'LABOUR_SHORTAGE'
  | 'EQUIPMENT_SHORTAGE'
  | 'MATERIAL_SHORTAGE'
  | 'MATERIAL_DELIVERY_DELAY'
  | 'CONTRACTOR_ISSUE'
  | 'WEATHER'
  | 'SITE_ACCESS'
  | 'SAFETY'
  | 'TECHNICAL'
  | 'DESIGN_DOCUMENTATION'
  | 'PERMIT_APPROVAL'
  | 'OTHER';

export type IssueSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export interface IssueCategory {
  code: IssueCategoryCode;
  name: string;
}

export interface IssueEvidenceItem {
  document_id: string;
  file_name: string | null;
}

export interface Issue {
  issue_id: string;
  project_id: string;
  schedule_id: string;
  activity_id: string | null;
  activity_name: string | null;
  discipline: string | null;
  stage_id: string | null;
  stage_name: string | null;
  category_code: IssueCategoryCode;
  category_name: string;
  title: string;
  description: string;
  severity: IssueSeverity;
  reported_date: string;
  expected_duration_days: number | null;
  blocks_work: boolean;
  status: 'ACTIVE' | 'RESOLVED';
  source_event_id: string | null;
  reported_by: string;
  reported_by_name: string | null;
  created_at: string;
  resolved_by: string | null;
  resolved_by_name: string | null;
  resolved_at: string | null;
  resolution_notes: string | null;
  root_cause_id: string | null;
  root_cause_title: string | null;
  evidence: IssueEvidenceItem[];
  memory_incident_id: string | null;
}

export interface IssueCreateInput {
  activity_id?: string | null;
  stage_id?: string | null;
  category_code: IssueCategoryCode;
  title: string;
  description: string;
  severity: IssueSeverity;
  reported_date?: string | null;
  expected_duration_days?: number | null;
  blocks_work: boolean;
}

export interface IssueResolveInput {
  resolution_notes: string;
  cause?: string | null;
  outcome?: string | null;
  lessons_learned?: string | null;
  add_to_memory: boolean;
  share_with_organisation: boolean;
}

export interface RootCause {
  root_cause_id: string;
  project_id: string;
  category_code: string;
  category_name: string;
  title: string;
  summary: string | null;
  status: 'IDENTIFIED' | 'ADDRESSED';
  identified_at: string;
  issue_count: number;
  open_issue_count: number;
  activity_ids: string[];
  stage_names: string[];
  expected_delay_days: number;
}

export interface CategoryPattern {
  category_code: string;
  category_name: string;
  issue_count: number;
  open_count: number;
  activity_count: number;
  stage_count: number;
  expected_delay_days: number;
  linked_to_root_cause: number;
  activity_ids: string[];
  stage_names: string[];
  is_repeated_pattern: boolean;
}

export interface RootCauseAnalysis {
  project_id: string;
  schedule_id: string;
  total_issues: number;
  open_issues: number;
  categories: CategoryPattern[];
  root_causes: RootCause[];
  delayed_stages: { stage_id: string; stage_name: string; issue_count: number; open_count: number; open_expected_delay_days: number }[];
}

export const issuesApi = {
  categories: (projectId: string) => apiFetch<IssueCategory[]>(`${proj(projectId)}/issue-categories`),
  list: (projectId: string, scheduleId: string, opts?: { status?: 'ACTIVE' | 'RESOLVED' | 'ALL'; mine?: boolean }) => {
    const qs = new URLSearchParams({ status: opts?.status ?? 'ALL' });
    if (opts?.mine) qs.set('mine', 'true');
    return apiFetch<Issue[]>(`${sched(projectId, scheduleId)}/issues?${qs.toString()}`);
  },
  report: (projectId: string, scheduleId: string, input: IssueCreateInput) =>
    apiFetch<Issue>(`${sched(projectId, scheduleId)}/issues`, { method: 'POST', body: JSON.stringify(input) }),
  resolve: (projectId: string, scheduleId: string, issueId: string, input: IssueResolveInput) =>
    apiFetch<Issue>(`${sched(projectId, scheduleId)}/issues/${encodeURIComponent(issueId)}/resolve`, {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  addEvidence: (projectId: string, scheduleId: string, issueId: string, file: File, notes?: string) => {
    const form = new FormData();
    form.append('file', file);
    if (notes) form.append('notes', notes);
    return apiFetch<Issue>(`${sched(projectId, scheduleId)}/issues/${encodeURIComponent(issueId)}/evidence`, {
      method: 'POST',
      body: form,
    });
  },
  analysis: (projectId: string, scheduleId: string) => apiFetch<RootCauseAnalysis>(`${sched(projectId, scheduleId)}/root-cause-analysis`),
  createRootCause: (
    projectId: string,
    scheduleId: string,
    input: { category_code: IssueCategoryCode; title: string; summary?: string | null; issue_ids: string[] },
  ) => apiFetch<RootCause>(`${sched(projectId, scheduleId)}/root-causes`, { method: 'POST', body: JSON.stringify(input) }),
  linkToRootCause: (projectId: string, scheduleId: string, rootCauseId: string, issueIds: string[]) =>
    apiFetch<RootCause>(`${sched(projectId, scheduleId)}/root-causes/${encodeURIComponent(rootCauseId)}/issues`, {
      method: 'POST',
      body: JSON.stringify({ issue_ids: issueIds }),
    }),
};

// ──────────────────────────────────────────────────────────────────────────────────────── institutional memory
export interface MemoryRecordDto {
  memory_id: string;
  project_id: string;
  stage_id: string | null;
  activity_id: string | null;
  title: string;
  summary: string | null;
  content: string;
  incident_type: string | null;
  status: string | null;
  created_at: string | null;
  metadata: {
    category_code?: string | null;
    root_cause?: string | null;
    resolution?: string | null;
    outcome?: string | null;
    lessons_learned?: string | null;
    delay_days?: number | null;
    project_name?: string | null;
    scope?: 'PROJECT' | 'ORGANISATION';
    shared_from_other_project?: boolean;
    source?: string | null;
    discipline?: string | null;
  };
}

export interface MemoryResultDto {
  record: MemoryRecordDto;
  score: number;
  match_reasons: string[];
}

export interface MemoryResponse {
  query: string;
  results: MemoryResultDto[];
  total_candidates: number;
  retrieval_mode: string;
}

export const memoryApi = {
  /** Historical incidents relevant to an issue: same category, own project + lessons other projects chose to share. */
  forIssue: (
    projectId: string,
    input: { category_code: IssueCategoryCode; query: string; activity_id?: string | null; stage_id?: string | null; top_k?: number },
  ) => apiFetch<MemoryResponse>(`${proj(projectId)}/memory/for-issue`, { method: 'POST', body: JSON.stringify(input) }),
  browse: (projectId: string, opts?: { q?: string; category?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (opts?.q) qs.set('q', opts.q);
    if (opts?.category) qs.set('category', opts.category);
    qs.set('limit', String(opts?.limit ?? 50));
    return apiFetch<MemoryResponse>(`${proj(projectId)}/memory?${qs.toString()}`);
  },
  record: (
    projectId: string,
    input: {
      category_code: IssueCategoryCode;
      title: string;
      narrative: string;
      root_cause?: string | null;
      resolution?: string | null;
      outcome?: string | null;
      lessons_learned?: string | null;
      share_with_organisation?: boolean;
    },
  ) => apiFetch<{ memory_id: string }>(`${proj(projectId)}/memory`, { method: 'POST', body: JSON.stringify(input) }),
};

// ────────────────────────────────────────────────────────────────────────── supervisor decisions → site engineer
export type DecisionAction = 'APPROVE' | 'EDIT' | 'REJECT' | 'HOLD';

export interface NotificationItem {
  notification_id: string;
  notification_type: 'CLAIM_DECISION' | 'ISSUE_UPDATE';
  title: string;
  body: string | null;
  created_at: string;
  read_at: string | null;
  event_id: string | null;
  decision_id: string | null;
  issue_id: string | null;
  decision_action: DecisionAction | null;
  decision_comment: string | null;
  decided_at: string | null;
  approved_pct: number | null;
  decided_by_name: string | null;
  claim_status: string | null;
  raw_claim_text: string | null;
  claimed_pct: number | null;
  schedule_id: string | null;
  activity_id: string | null;
  activity_name: string | null;
  issue_title: string | null;
  issue_status: string | null;
}

export interface MyClaim {
  event_id: string;
  schedule_id: string;
  event_date: string;
  created_at: string;
  raw_claim_text: string;
  status: string;
  claimed_pct: number | null;
  activity_id: string | null;
  activity_name: string | null;
  discipline: string | null;
  decision_id: string | null;
  decision_action: DecisionAction | null;
  decision_comment: string | null;
  decided_at: string | null;
  approved_pct: number | null;
  decided_by_name: string | null;
}

export const updatesApi = {
  notifications: (projectId: string, opts?: { unreadOnly?: boolean; limit?: number }) => {
    const qs = new URLSearchParams({ limit: String(opts?.limit ?? 100) });
    if (opts?.unreadOnly) qs.set('unread_only', 'true');
    return apiFetch<{ unread_count: number; items: NotificationItem[] }>(`${proj(projectId)}/notifications?${qs.toString()}`);
  },
  markRead: (projectId: string, notificationId: string) =>
    apiFetch<{ notification_id: string; read_at: string }>(`${proj(projectId)}/notifications/${encodeURIComponent(notificationId)}/read`, { method: 'POST' }),
  markAllRead: (projectId: string) => apiFetch<{ marked_read: number }>(`${proj(projectId)}/notifications/read-all`, { method: 'POST' }),
  myClaims: (projectId: string, limit = 150) => apiFetch<MyClaim[]>(`${proj(projectId)}/my-claims?limit=${limit}`),
};

// ───────────────────────────────────────────────────────────────────────────────────────── multi-file intake
export interface BatchCandidate {
  activity_id: string;
  activity_name: string | null;
  rank: number;
  tier: string | null;
  confidence: number;
  supporting: string | null;
  disqualifying: string | null;
}

export interface BatchClaimSource {
  document_id: string;
  file_name: string | null;
  snippet: string | null;
}

export interface BatchClaim {
  event_id: string;
  document_id: string;
  event_date: string;
  raw_claim_text: string;
  status: string;
  claimed_pct: number | null;
  claimed_quantity: number | null;
  claimed_uom: string | null;
  claim_mode: string;
  event_type: string | null;
  discipline: string | null;
  matched_activity_id: string | null;
  reported_activity_id: string | null;
  activity_name: string | null;
  stage_name: string | null;
  clarification_status: string | null;
  clarification_question: string | null;
  candidates: BatchCandidate[];
  match_confidence: number | null;
  match_tier: string | null;
  sources: BatchClaimSource[];
  file_names: string[];
  reported_by_multiple_files: boolean;
  created_in_this_batch: boolean;
  error: string | null;
}

export interface BatchFile {
  document_id: string;
  file_name: string;
  document_type: string | null;
  extraction_status: 'EXTRACTED' | 'EMPTY' | 'FAILED' | null;
  extraction_method: 'STRUCTURED' | 'LLM' | 'RULES_FALLBACK' | null;
  error: string | null;
  claims_extracted: number;
  claim_ids: string[];
  merged_into_claim_ids: string[];
}

export interface BatchReport {
  batch_id: string;
  project_id: string;
  schedule_id: string;
  status: 'PROCESSING' | 'COMPLETED' | 'PARTIAL' | 'FAILED';
  created_at: string;
  completed_at: string | null;
  notes: string | null;
  file_count: number;
  claim_count: number;
  merged_count: number;
  matched_count: number;
  unmatched_count: number;
  needs_clarification_count: number;
  files: BatchFile[];
  claims: BatchClaim[];
  activities: { activity_id: string; activity_name: string | null; stage_name: string | null; claim_ids: string[]; file_names: string[] }[];
}

export interface BatchListItem {
  batch_id: string;
  status: string;
  file_count: number;
  claim_count: number;
  merged_count: number;
  created_at: string;
  completed_at: string | null;
  uploaded_by_name: string | null;
}

export const batchApi = {
  /** Upload several reports / files / photos in one operation (multipart; the context headers are added here). */
  upload: async (files: File[], scheduleId: string, notes?: string): Promise<BatchReport> => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f, f.name));
    form.append('schedule_id', scheduleId);
    if (notes) form.append('notes', notes);
    return apiFetch<BatchReport>('/api/v1/claims/batch', { method: 'POST', body: form });
  },
  list: (projectId: string, mine = true) => apiFetch<BatchListItem[]>(`${proj(projectId)}/upload-batches?mine=${mine}`),
  get: (projectId: string, batchId: string) => apiFetch<BatchReport>(`${proj(projectId)}/upload-batches/${encodeURIComponent(batchId)}`),
};

// kept so a page that needs the raw base URL / headers (downloads) does not reach into the client internals
export const apiBase = { url: BASE_URL, headers: requestHeaders };
