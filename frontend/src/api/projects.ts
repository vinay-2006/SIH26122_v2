/**
 * Project / schedule-version / stage / progress clients (real V7 endpoints, no mock data).
 * Shapes mirror backend/schemas/{project,schedule_version,stage,progress}.py.
 */
import { apiFetch } from './client';

export type ProjectRole =
  | 'OWNER'
  | 'PROJECT_MANAGER'
  | 'PLANNER'
  | 'SUPERVISOR'
  | 'SITE_ENGINEER'
  | 'QUALITY_INSPECTOR'
  | 'AUDITOR'
  | 'VIEWER'
  | 'CONTRACTOR_REP';

export type Permission =
  | 'VIEW_PROJECT'
  | 'VIEW_SCHEDULE'
  | 'MANAGE_SCHEDULE'
  | 'CREATE_EXECUTION_EVENT'
  | 'VIEW_EXECUTION_EVENTS'
  | 'REVIEW_CLAIM'
  | 'APPROVE_ACTUAL'
  | 'REQUEST_REOPEN'
  | 'APPROVE_REOPEN'
  | 'MANAGE_QUALITY'
  | 'VIEW_QUALITY'
  | 'APPROVE_QUALITY'
  | 'WAIVE_QUALITY'
  | 'VIEW_AUDIT'
  | 'MANAGE_BLOCKERS'
  | 'REPORT_ISSUE'
  /** v2 only: create / edit projects, settings and members (Project Manager) */
  | 'MANAGE_PROJECT';

export interface ProjectListItem {
  project_id: string;
  project_code: string;
  project_name: string;
  status: string;
  assigned_role: ProjectRole;
}

export interface ProjectDetail {
  project_id: string;
  project_code: string;
  project_name: string;
  description: string | null;
  client_name: string | null;
  project_type: string | null;
  location: string | null;
  planned_start: string | null;
  planned_finish: string | null;
  contract_finish: string | null;
  status: string;
}

export interface ProjectMe {
  project_id: string;
  project_name: string | null;
  user_id: string;
  full_name: string | null;
  role: ProjectRole;
  permissions: Permission[];
}

export interface ScheduleVersionDto {
  schedule_id: string;
  project_id: string;
  project_name: string;
  version_code: string | null;
  version_metadata: Record<string, unknown>;
  active: boolean;
  supersedes_schedule_id: string | null;
  data_date: string | null;
  source_format: string | null;
  activity_count: number;
  dependency_count: number;
  created_at: string | null;
}

export interface StageDto {
  stage_id: string;
  project_id: string;
  schedule_id: string;
  parent_stage_id: string | null;
  stage_code: string | null;
  stage_name: string;
  sequence_order: number;
  weight_pct: number | null;
  status: string;
  planned_start: string | null;
  planned_finish: string | null;
  gating_predecessor_stage_id: string | null;
}

export interface ActivityProgressItem {
  activity_id: string;
  activity_name: string;
  stage_id: string | null;
  canonical_state: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED';
  workflow_condition: 'NONE' | 'REOPEN_REQUESTED' | 'REWORK_IN_PROGRESS' | 'QUALITY_HOLD' | 'BLOCKED';
  is_reopened: boolean;
  progress_pct: number;
  actual_pct_complete: number | null;
  actual_quantity: number | null;
  planned_quantity: number | null;
  weight_factor: number;
  weighted_contribution: number;
}

export interface StageProgressBreakdown {
  stage_id: string;
  stage_name: string;
  sequence_order: number;
  weight_pct: number | null;
  progress_pct: number;
  weighted_contribution: number;
  activity_count: number;
  completed_count: number;
  activities: ActivityProgressItem[];
}

export interface ProgressBreakdown {
  project_id: string;
  project_name: string;
  schedule_id: string;
  overall_progress_pct: number;
  calculation_basis: string;
  stages: StageProgressBreakdown[];
  unassigned_activities: ActivityProgressItem[];
}

const p = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}`;

export const projectsApi = {
  /** The caller's own memberships. User-level: no project context is sent. */
  list: () => apiFetch<ProjectListItem[]>('/api/v1/projects', { noContext: true }),
  get: (projectId: string) => apiFetch<ProjectDetail>(p(projectId)),
  me: (projectId: string) => apiFetch<ProjectMe>(`${p(projectId)}/me`),
};

export const scheduleVersionsApi = {
  list: (projectId: string) => apiFetch<ScheduleVersionDto[]>(`${p(projectId)}/schedules`),
  activate: (projectId: string, scheduleId: string) =>
    apiFetch<{ schedule_id: string; active: boolean; message: string }>(
      `${p(projectId)}/schedules/${encodeURIComponent(scheduleId)}/activate`,
      { method: 'POST' },
    ),
};

export interface ScheduleImportInput {
  version_code: string;
  data_date?: string | null;
  supersedes_schedule_id?: string | null;
  activate_immediately?: boolean;
}

export const scheduleImportApi = {
  /** Native CSV schedule (activities + dependencies), validated and stored transactionally. */
  importCsv: (projectId: string, input: ScheduleImportInput & { csv_content: string }) =>
    apiFetch<ScheduleVersionDto>(`${p(projectId)}/schedules`, {
      method: 'POST',
      body: JSON.stringify({ source_format: 'csv', ...input }),
    }),
  /** Primavera P6 .xer export (text). */
  importXer: (projectId: string, input: ScheduleImportInput & { xer_content: string }) =>
    apiFetch<ScheduleVersionDto>(`${p(projectId)}/schedules/xer`, {
      method: 'POST',
      body: JSON.stringify(input),
    }),
};

export const stagesApi = {
  list: (projectId: string, scheduleId: string) =>
    apiFetch<StageDto[]>(`${p(projectId)}/stages?schedule_id=${encodeURIComponent(scheduleId)}`),
};

export const progressApi = {
  breakdown: (projectId: string, scheduleId: string) =>
    apiFetch<ProgressBreakdown>(
      `${p(projectId)}/schedules/${encodeURIComponent(scheduleId)}/progress/breakdown`,
    ),
};
