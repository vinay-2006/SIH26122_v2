/**
 * The explicit V7 request context: Authenticated user -> current PROJECT -> current SCHEDULE VERSION.
 *
 * This module is the single source of truth that the API client reads on every request, so no call site
 * can forget the context (the backend rejects project-owned requests without it). It is written only by
 * ProjectProvider when the user selects a project / schedule version.
 */
import { getAuthToken } from '@/lib/authToken';

export const SELECTED_PROJECT_KEY = 'setu_selected_project_id_v7';
export const SELECTED_VERSION_KEY = 'setu_selected_sched_version_v7';

type Listener = () => void;

let projectId: string | null = null;
let scheduleId: string | null = null;
const listeners = new Set<Listener>();

export const apiContext = {
  getProjectId: () => projectId,
  getScheduleId: () => scheduleId,
  set(nextProjectId: string | null, nextScheduleId: string | null) {
    if (nextProjectId === projectId && nextScheduleId === scheduleId) return;
    projectId = nextProjectId;
    scheduleId = nextScheduleId;
    listeners.forEach((l) => l());
  },
  clear() {
    apiContext.set(null, null);
  },
  subscribe(listener: Listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  /** Headers every project/schedule-scoped request must carry. Empty until a project is selected. */
  headers(): Record<string, string> {
    const h: Record<string, string> = {};
    if (projectId) h['X-Project-ID'] = projectId;
    if (scheduleId) h['X-Schedule-ID'] = scheduleId;
    return h;
  },
};

/** Auth + context headers for the few call sites that use fetch directly (multipart uploads, blobs). */
export function requestHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...apiContext.headers(), ...(extra ?? {}) };
  const token = getAuthToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}
