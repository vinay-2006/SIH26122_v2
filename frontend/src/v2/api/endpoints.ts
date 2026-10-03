/** Typed v2 endpoints, grouped by area. Paths are exactly those of docs/V2_API.md (all under /api/v2). */
import { request, upload, qs, type Page, type Query } from './http';
import type * as T from './types';

const P = (id: string) => `/projects/${encodeURIComponent(id)}`;
type Sig = { signal?: AbortSignal };

export const authApi = {
  config: () => request<{ local_login: boolean }>('/auth/config', { anonymous: true }),
  localLogin: (email: string, password: string) =>
    request<{ access_token: string; token_type: string; expires_in: number }>('/auth/local-login', { method: 'POST', json: { email, password }, anonymous: true }),
  me: (o: Sig = {}) => request<T.Me>('/me', o),
};

export const projectsApi = {
  list: (o: Sig = {}) => request<T.MyProject[]>('/projects', o),
  get: (id: string, o: Sig = {}) => request<T.ProjectDetail>(P(id), o),
  create: (body: T.ProjectCreate, idempotencyKey?: string) => request<T.ProjectDetail>('/projects', { method: 'POST', json: body, idempotencyKey }),
  patch: (id: string, body: Partial<T.ProjectCreate>) => request<T.ProjectDetail>(P(id), { method: 'PATCH', json: body }),
  archive: (id: string) => request<unknown>(`${P(id)}/archive`, { method: 'POST' }),
  restore: (id: string) => request<unknown>(`${P(id)}/restore`, { method: 'POST' }),
  settings: (id: string) => request<T.ProjectSettings>(`${P(id)}/settings`),
  patchSettings: (id: string, body: Partial<Omit<T.ProjectSettings, 'extra'>>) => request<T.ProjectSettings>(`${P(id)}/settings`, { method: 'PATCH', json: body }),
  members: (id: string) => request<T.Member[]>(`${P(id)}/members`),
  addMember: (id: string, email: string, role: 'SUPERVISOR' | 'SITE_ENGINEER') => request<T.Member>(`${P(id)}/members`, { method: 'POST', json: { email, role } }),
  patchMember: (id: string, userId: string, body: { role?: 'SUPERVISOR' | 'SITE_ENGINEER'; status?: 'ACTIVE' | 'SUSPENDED' | 'REMOVED' }) =>
    request<unknown>(`${P(id)}/members/${encodeURIComponent(userId)}`, { method: 'PATCH', json: body }),
  removeMember: (id: string, userId: string) => request<void>(`${P(id)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
  invitations: (id: string) => request<T.Invitation[]>(`${P(id)}/invitations`),
  invite: (id: string, email: string, role: 'SUPERVISOR' | 'SITE_ENGINEER') =>
    request<T.Invitation & { token: string; accept_path: string }>(`${P(id)}/invitations`, { method: 'POST', json: { email, role } }),
  revokeInvitation: (id: string, invitationId: string) => request<void>(`${P(id)}/invitations/${encodeURIComponent(invitationId)}`, { method: 'DELETE' }),
  acceptInvitation: (token: string) => request<{ project_id: string; role: T.V2Role }>('/invitations/accept', { method: 'POST', json: { token } }),
};

export interface ImportParams { baseline_name?: string; data_date?: string; planned_start?: string; planned_finish?: string; project_name?: string }
export const scheduleApi = {
  stage: (id: string, file: File, resources: File | null, params: ImportParams, onProgress?: (f: number) => void, signal?: AbortSignal) => {
    const f = new FormData();
    f.append('file', file);
    if (resources) f.append('resources_file', resources);
    Object.entries(params).forEach(([k, v]) => { if (v) f.append(k, v); });
    return upload<T.ScheduleImport>(`${P(id)}/schedule-imports`, f, { onProgress, signal });
  },
  getImport: (id: string, importId: string) => request<T.ScheduleImport>(`${P(id)}/schedule-imports/${encodeURIComponent(importId)}`),
  decisions: (id: string, importId: string, body: Record<string, unknown>) =>
    request<T.ScheduleImport>(`${P(id)}/schedule-imports/${encodeURIComponent(importId)}/decisions`, { method: 'PUT', json: body }),
  build: (id: string, importId: string) => request<T.BuiltVersion>(`${P(id)}/schedule-imports/${encodeURIComponent(importId)}/build`, { method: 'POST' }),
  discardImport: (id: string, importId: string) => request<void>(`${P(id)}/schedule-imports/${encodeURIComponent(importId)}`, { method: 'DELETE' }),
  versions: (id: string, o: Sig = {}) => request<T.VersionRow[]>(`${P(id)}/schedule-versions`, o),
  version: (id: string, versionId: string) => request<T.VersionDetail>(`${P(id)}/schedule-versions/${encodeURIComponent(versionId)}`),
  wbs: (id: string, versionId: string) => request<T.WbsRow[]>(`${P(id)}/schedule-versions/${encodeURIComponent(versionId)}/wbs`),
  activate: (id: string, versionId: string, reason?: string) =>
    request<{ version_id: string; status: string; previous_active_version_id: string | null; rollback: boolean }>(
      `${P(id)}/schedule-versions/${encodeURIComponent(versionId)}/activate`, { method: 'POST', json: reason ? { reason } : {} }),
  discardVersion: (id: string, versionId: string) => request<void>(`${P(id)}/schedule-versions/${encodeURIComponent(versionId)}`, { method: 'DELETE' }),
  compare: (id: string, oldId: string, newId: string) => request<T.VersionCompare>(`${P(id)}/schedule-versions/compare`, { query: { old: oldId, new: newId } }),
};

export const catalogApi = {
  activities: (id: string, q: { q?: string; wbs_prefix?: string; discipline?: string; limit?: number; offset?: number } = {}, o: Sig = {}) =>
    request<Page<T.CatalogActivity> & { version: T.ActiveVersion }>(`${P(id)}/activities`, { query: q as Query, ...o }),
};

export const claimsApi = {
  submit: (id: string, body: T.ClaimIn, idempotencyKey: string) => request<T.ClaimSubmitted>(`${P(id)}/claims`, { method: 'POST', json: body, idempotencyKey }),
  correction: (id: string, rejectedId: string, body: T.ClaimIn, idempotencyKey: string) =>
    request<T.ClaimSubmitted>(`${P(id)}/claims/${encodeURIComponent(rejectedId)}/correction`, { method: 'POST', json: body, idempotencyKey }),
  mine: (id: string, q: { status?: string; limit?: number; offset?: number } = {}, o: Sig = {}) => request<Page<T.ClaimListItem>>(`${P(id)}/my-claims`, { query: q as Query, ...o }),
  queue: (id: string, q: { status?: string[]; limit?: number; offset?: number } = {}, o: Sig = {}) => request<Page<T.ClaimListItem>>(`${P(id)}/review-queue`, { query: q as Query, ...o }),
  get: (id: string, claimId: string, o: Sig = {}) => request<T.ClaimDetail>(`${P(id)}/claims/${encodeURIComponent(claimId)}`, o),
  withdraw: (id: string, claimId: string, reason: string) => request<unknown>(`${P(id)}/claims/${encodeURIComponent(claimId)}/withdraw`, { method: 'POST', json: { reason } }),
  answer: (id: string, claimId: string, answer: string, evidence: string[] = []) =>
    request<unknown>(`${P(id)}/claims/${encodeURIComponent(claimId)}/clarification-answer`, { method: 'POST', json: { answer, evidence_document_ids: evidence } }),
  attach: (id: string, claimId: string, documentId: string) => request<unknown>(`${P(id)}/claims/${encodeURIComponent(claimId)}/evidence`, { method: 'POST', json: { document_id: documentId } }),
  rematch: (id: string, claimId: string, activityUid: string) => request<unknown>(`${P(id)}/claims/${encodeURIComponent(claimId)}/rematch`, { method: 'POST', json: { activity_uid: activityUid } }),
  bind: (id: string, claimId: string, bindings: Record<string, string>) => request<unknown>(`${P(id)}/claims/${encodeURIComponent(claimId)}/bind-quantities`, { method: 'POST', json: { bindings } }),
  preview: (id: string, claimId: string, body: T.DecisionIn) => request<T.DecisionPreview>(`${P(id)}/claims/${encodeURIComponent(claimId)}/decision-preview`, { method: 'POST', json: body }),
  decide: (id: string, claimId: string, body: T.DecisionIn) => request<T.DecisionDone>(`${P(id)}/claims/${encodeURIComponent(claimId)}/decision`, { method: 'POST', json: body }),
  ask: (id: string, claimId: string, question: string) => request<T.DecisionDone>(`${P(id)}/claims/${encodeURIComponent(claimId)}/clarification-request`, { method: 'POST', json: { question } }),
  counts: (id: string, o: Sig = {}) => request<T.ClaimCounts>(`${P(id)}/claim-counts`, o),
};

export const documentsApi = {
  upload: (id: string, kind: string, file: File, onProgress?: (f: number) => void, signal?: AbortSignal) => {
    const f = new FormData();
    f.append('kind', kind);
    f.append('file', file);
    return upload<T.DocumentRow>(`${P(id)}/documents`, f, { onProgress, signal });
  },
  list: (id: string, q: { kind?: string; limit?: number; offset?: number } = {}) => request<Page<T.DocumentRow>>(`${P(id)}/documents`, { query: q as Query }),
  get: (id: string, documentId: string) => request<T.DocumentRow>(`${P(id)}/documents/${encodeURIComponent(documentId)}`),
  content: (id: string, documentId: string) => request<Blob>(`${P(id)}/documents/${encodeURIComponent(documentId)}/content`, { responseType: 'blob' }),
  extract: (id: string, documentId: string, body: { event_date?: string; create_claims?: boolean } = {}) =>
    request<T.ExtractionResult>(`${P(id)}/documents/${encodeURIComponent(documentId)}/extract`, { method: 'POST', json: body }),
};

export const issuesApi = {
  report: (id: string, body: T.IssueIn, idempotencyKey: string) => request<{ issue_id: string }>(`${P(id)}/issues`, { method: 'POST', json: body, idempotencyKey }),
  list: (id: string, q: { status?: 'ACTIVE' | 'RESOLVED'; limit?: number; offset?: number } = {}) => request<Page<T.Issue>>(`${P(id)}/issues`, { query: q as Query }),
  get: (id: string, issueId: string) => request<T.Issue>(`${P(id)}/issues/${encodeURIComponent(issueId)}`),
  resolve: (id: string, issueId: string, body: { notes: string; delay_ended_on?: string; impact_days_actual?: number }) =>
    request<unknown>(`${P(id)}/issues/${encodeURIComponent(issueId)}/resolve`, { method: 'POST', json: body }),
  attach: (id: string, issueId: string, documentId: string) => request<unknown>(`${P(id)}/issues/${encodeURIComponent(issueId)}/evidence`, { method: 'POST', json: { document_id: documentId } }),
  assignRootCause: (id: string, issueId: string, rootCauseId: string) => request<unknown>(`${P(id)}/issues/${encodeURIComponent(issueId)}/root-cause`, { method: 'POST', json: { root_cause_id: rootCauseId } }),
  toMemory: (id: string, issueId: string, body: { lessons_learned: string; corrective_action?: string; outcome?: string; visibility?: 'PROJECT' | 'ORGANISATION' }) =>
    request<unknown>(`${P(id)}/issues/${encodeURIComponent(issueId)}/memory`, { method: 'POST', json: body }),
  rootCauses: (id: string) => request<{ items: T.RootCause[] }>(`${P(id)}/root-causes`),
  createRootCause: (id: string, body: { title: string; category_code: string; summary?: string }) => request<{ root_cause_id: string }>(`${P(id)}/root-causes`, { method: 'POST', json: body }),
  memory: (id: string) => request<{ items: T.MemoryEntry[] }>(`${P(id)}/memory`),
  blockers: (id: string, o: Sig = {}) => request<T.Blockers>(`${P(id)}/blockers`, o),
};

export const dashboardApi = {
  summary: (id: string, q: { as_of?: string; version_id?: string } = {}, o: Sig = {}) => request<T.Summary>(`${P(id)}/dashboard/summary`, { query: q, ...o }),
  wbs: (id: string, q: { as_of?: string; version_id?: string } = {}) => request<{ items: T.WbsProgress[] }>(`${P(id)}/dashboard/wbs`, { query: q }),
  stages: (id: string, q: { as_of?: string; version_id?: string } = {}) => request<{ items: T.WbsProgress[] }>(`${P(id)}/dashboard/stages`, { query: q }),
  disciplines: (id: string, q: { as_of?: string; version_id?: string } = {}) => request<{ items: T.DisciplineProgress[] }>(`${P(id)}/dashboard/disciplines`, { query: q }),
  activities: (id: string, q: { as_of?: string; version_id?: string; discipline?: string; state?: string; wbs_prefix?: string; limit?: number; offset?: number } = {}, o: Sig = {}) =>
    request<Page<T.ActivityProgress>>(`${P(id)}/dashboard/activities`, { query: q as Query, ...o }),
  timeline: (id: string, q: { date_from?: string; date_to?: string; step_days?: number; version_id?: string } = {}) => request<T.Timeline>(`${P(id)}/dashboard/timeline`, { query: q as Query }),
  compare: (id: string, oldId: string, newId: string) => request<any>(`${P(id)}/dashboard/compare`, { query: { old: oldId, new: newId } }),
  activityTimeline: (id: string, activityUid: string) => request<T.ActivityTimeline>(`${P(id)}/activities/${encodeURIComponent(activityUid)}/timeline`),
  notifications: (id: string, q: { unread_only?: boolean; limit?: number; offset?: number } = {}, o: Sig = {}) => request<Page<T.Notification>>(`${P(id)}/notifications`, { query: q as Query, ...o }),
  markRead: (id: string, notificationId: string) => request<unknown>(`${P(id)}/notifications/${encodeURIComponent(notificationId)}/read`, { method: 'POST' }),
  audit: (id: string, q: { entity_type?: string; entity_id?: string; limit?: number } = {}) => request<{ items: T.AuditEntry[] }>(`${P(id)}/audit`, { query: q as Query }),
  verifyAudit: (id: string) => request<T.AuditVerify>(`${P(id)}/audit/verify`),
};

export { qs };
