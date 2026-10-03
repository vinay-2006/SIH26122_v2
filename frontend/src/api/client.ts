/**
 * API client core: one fetch wrapper for the whole app.
 *  - sends the bearer token AND the explicit project/schedule context (lib/apiContext) on every request
 *  - turns the backend's standardized error contract ({detail: {error_code, message}}) into ApiError
 *    with a user-safe message (never a raw JSON blob or stack trace)
 *  - centralizes session-expiry handling
 */
import { apiContext, requestHeaders } from '@/lib/apiContext';
import { clearAuthToken, getAuthToken } from '@/lib/authToken';
import { IS_V2, V2_BASE_URL } from '@/config';

/** legacy builds talk to the legacy backend; v2 builds serve the same /api/v1 contract from the v2 server (backend/v2/compat) */
export const BASE_URL: string = IS_V2 ? V2_BASE_URL : (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000');

export interface ApiFetchOptions extends RequestInit {
  responseType?: 'json' | 'blob' | 'text';
  /** Skip the project/schedule headers (user-level calls such as /auth/me and the project list). */
  noContext?: boolean;
}

/** Thrown on a non-2xx response. `.message` is always safe to show a user; `.code` is the backend's
 * stable error_code when it sent one; `.raw` is the untouched body for logging. */
export class ApiError extends Error {
  status: number;
  raw: string;
  code?: string;
  constructor(status: number, message: string, raw: string, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.raw = raw;
    this.code = code;
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

/** Meaningful messages for the backend's stable error codes. */
export const ERROR_CODE_MESSAGES: Record<string, string> = {
  AUTHENTICATION_REQUIRED: 'Please sign in to continue.',
  INVALID_TOKEN: 'Your session is invalid or has expired. Please sign in again.',
  PROJECT_ACCESS_DENIED: "You don't have access to this project.",
  INVALID_PROJECT_CONTEXT: 'Select a project to continue.',
  INVALID_SCHEDULE_CONTEXT: 'Select a schedule version to continue.',
  SCHEDULE_ACCESS_DENIED: 'That schedule version does not belong to the selected project.',
  PERMISSION_DENIED: "Your role in this project doesn't allow that action.",
  ROLE_REQUIRED: "Your role in this project doesn't allow that action.",
  RESOURCE_NOT_FOUND: 'The requested item could not be found.',
  REOPEN_NOT_ALLOWED: 'This activity is completed. Request a governed reopen before changing it.',
  QUALITY_HOLD: 'This activity is on a quality hold until its quality gate is passed or waived.',
  NO_ELIGIBLE_CANDIDATE: 'No eligible unfinished activity matches this report.',
  IMPACT_GRAPH_CYCLE: 'The dependency network contains a cycle, so impact cannot be calculated.',
  VALIDATION_FAILED: 'The submitted data failed validation.',
  APPROVAL_REQUIRED: 'This action requires human approval.',
};

interface ParsedError {
  message: string;
  code?: string;
}

function parseError(status: number, bodyText: string): ParsedError {
  const fallback = FRIENDLY_STATUS_MESSAGES[status] || `Request failed (${status}). Please try again.`;
  try {
    const parsed = JSON.parse(bodyText);
    const detail = parsed?.detail ?? parsed?.error?.message ?? parsed?.message;
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const code = typeof detail.error_code === 'string' ? detail.error_code : undefined;
      const message =
        (code && ERROR_CODE_MESSAGES[code]) ||
        (typeof detail.message === 'string' && detail.message.trim() ? detail.message : undefined);
      return { message: message || fallback, code };
    }
    if (typeof detail === 'string' && detail.trim() && !detail.trim().startsWith('Traceback')) {
      // the v2 error envelope carries a machine code beside the message ({"error": {"code", "message"}}); keep it so screens can react to it
      return { message: detail, code: typeof parsed?.error?.code === 'string' ? parsed.error.code : undefined };
    }
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0];
      const field = Array.isArray(first?.loc) ? first.loc[first.loc.length - 1] : undefined;
      if (typeof first?.msg === 'string') return { message: field ? `${field}: ${first.msg}` : first.msg };
    }
  } catch {
    // not JSON / unexpected shape: fall through to the generic message
  }
  return { message: fallback };
}

export async function apiFetch<T>(path: string, options?: ApiFetchOptions): Promise<T> {
  const { responseType, noContext, ...init } = options ?? {};
  const headers: Record<string, string> = {
    ...(responseType !== 'blob' && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
    ...(noContext ? {} : apiContext.headers()),
    ...((init.headers as Record<string, string>) ?? {}),
  };
  const token = getAuthToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, { ...init, headers });
  } catch (err: any) {
    if (err instanceof TypeError) {
      throw new ApiError(0, `Unable to reach the SETUAI backend at ${BASE_URL}. Check that it is running.`, err.message || 'Network error');
    }
    throw err;
  }

  if (!res.ok) {
    const errorText = await res.text().catch(() => '');
    console.error(`[api] ${init.method || 'GET'} ${path} -> ${res.status}`, errorText);
    if (res.status === 401) {
      clearAuthToken();
      window.dispatchEvent(new Event('auth:unauthorized'));
    }
    const { message, code } = parseError(res.status, errorText);
    throw new ApiError(res.status, message, errorText, code);
  }

  if (responseType === 'blob') return (await res.blob()) as unknown as T;
  if (responseType === 'text') return (await res.text()) as unknown as T;
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

export { requestHeaders };
