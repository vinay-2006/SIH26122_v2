/**
 * Supervising-agent and audit clients (real V7 endpoints). The agent is advisory and read-only: it can explain and
 * recommend, it can never approve, reject or change anything. Shapes mirror backend/agents/schemas.py and
 * backend/dossier/schemas.py.
 */
import { apiFetch } from './client';

export type FindingSeverity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO';

export interface EvidenceReference {
  entity_type: string;
  entity_id: string;
  source_type: string | null;
  reference_code: string | null;
  details: string | null;
}

export interface AgentFinding {
  finding_id: string;
  category: string;
  severity: FindingSeverity;
  title: string;
  description: string;
  why_it_matters: string;
  evidence: EvidenceReference[];
  affected_entity_type: string | null;
  affected_entity_id: string | null;
  recommended_action: string;
}

export interface SupervisoryBriefing {
  briefing_id: string;
  project_id: string;
  generated_at: string;
  /** DEGRADED = the language model was unavailable and the briefing is the deterministic engine facts only. */
  agent_status: 'HEALTHY' | 'DEGRADED';
  summary: string;
  findings: AgentFinding[];
  recommended_reviews: string[];
  review_queue_summary: Record<string, unknown>;
  audit_verification: Record<string, unknown> | null;
}

export interface AgentAnswer {
  project_id: string;
  query: string;
  generated_at: string;
  agent_status: 'HEALTHY' | 'DEGRADED';
  answer: string;
  findings: AgentFinding[];
  evidence: EvidenceReference[];
  recommendations: string[];
}

export interface AuditVerification {
  status: 'VALID' | 'BROKEN' | 'LEGACY_ONLY' | 'EMPTY' | string;
  records_checked: number;
  legacy_records: number;
  v7_records: number;
  failure_type: string | null;
  broken_at_log_id: number | null;
  reason: string | null;
  first_log_id: number | null;
  last_log_id: number | null;
  verified_at: string;
}

export interface AuditRecord {
  log_id: number;
  entity_type: string;
  entity_id: string;
  action: string;
  actor_id: string | null;
  timestamp: string;
  payload_hash: string | null;
  previous_hash: string | null;
  current_hash: string | null;
}

const p = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}`;

export const agentApi = {
  briefing: (projectId: string) => apiFetch<SupervisoryBriefing>(`${p(projectId)}/agent/briefing`),
  ask: (projectId: string, query: string) =>
    apiFetch<AgentAnswer>(`${p(projectId)}/agent/query`, { method: 'POST', body: JSON.stringify({ query }) }),
};

export const auditTrailApi = {
  verify: (projectId: string) => apiFetch<AuditVerification>(`${p(projectId)}/dossier/audit-verification`),
  recent: () => apiFetch<AuditRecord[]>('/api/v1/audit'),
  dossier: (projectId: string) => apiFetch<Record<string, unknown>>(`${p(projectId)}/dossier`),
};
