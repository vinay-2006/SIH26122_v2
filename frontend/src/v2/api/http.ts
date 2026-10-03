/**
 * The one HTTP client for the v2 API. Handles: base URL, bearer token, JSON and multipart bodies, the v2 error envelope
 * ({error: {code, message, details}}), session expiry, network failures, cancellation (AbortSignal), query strings, upload progress and
 * Idempotency-Key headers. No page calls fetch directly.
 */
import { V2_BASE_URL } from '@/config';
import { v2Session } from '@/v2/session';

export class V2Error extends Error {
  status: number;
  code: string;
  details: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = 'V2Error';
    this.status = status;
    this.code = code;
    this.details = details ?? null;
  }
  get isAuth() { return this.status === 401; }
  get isForbidden() { return this.status === 403; }
  get isValidation() { return this.status === 422; }
  get isNetwork() { return this.status === 0; }
}

/** Plain-language text for stable error codes. Anything not listed shows the server's own message, which is already user-facing. */
const CODE_MESSAGES: Record<string, string> = {
  UNAUTHENTICATED: 'Please sign in to continue.',
  TOKEN_REVOKED: 'Your session was ended. Please sign in again.',
  NO_PROFILE: 'This account has no profile yet. Ask an administrator.',
  ACCOUNT_DISABLED: 'This account has been deactivated.',
  NOT_A_MEMBER: 'You are not a member of this project.',
  PERMISSION_DENIED: "Your role in this project doesn't allow that.",
  MEMBERSHIP_CHANGED: 'Your access to this project changed. Reload and sign in again.',
  CLAIM_CONTENT_FORBIDDEN: 'Project managers see claim counts only, not claim contents.',
  PROJECT_ARCHIVED: 'This project is archived and read-only.',
  INVALID_CREDENTIALS: 'Incorrect email or password.',
  TOO_MANY_ATTEMPTS: 'Too many failed attempts. Wait a few minutes and try again.',
  LOCAL_LOGIN_DISABLED: 'Local sign-in is not enabled on this server.',
  DUPLICATE_CLAIM: 'An identical claim has already been filed.',
  DUPLICATE_UPLOAD: 'This exact file was already uploaded to the project.',
  CLAIM_ALREADY_FINAL: 'This claim has already been decided and can no longer be changed.',
  CLAIM_NOT_DECIDABLE: 'This claim can no longer be decided.',
  IDEMPOTENCY_IN_PROGRESS: 'The first attempt is still being processed. Try again in a moment.',
  IDEMPOTENCY_KEY_REUSED: 'This submission was already used for different content. Reload and try again.',
  NO_ACTIVE_SCHEDULE: 'This project has no active schedule yet.',
  SCHEDULE_FILE_NOT_ALLOWED: 'Schedule files are imported by a Project Manager, not uploaded as reports.',
  SCANNED_NOT_SUPPORTED: 'Scanned documents and photographs cannot be read yet; only text, CSV, XLSX and text-layer PDF.',
  INTERNAL_ERROR: 'Something went wrong on the server. Please try again.',
};

function describe(status: number, body: any): { code: string; message: string; details: unknown } {
  const err = body?.error;
  if (err && typeof err === 'object') {
    const code = typeof err.code === 'string' ? err.code : `HTTP_${status}`;
    let message: string = CODE_MESSAGES[code] ?? (typeof err.message === 'string' && err.message ? err.message : `Request failed (${status}).`);
    if (code === 'VALIDATION_ERROR' && Array.isArray(err.details) && err.details.length) {
      const d = err.details[0];
      message = d?.field ? `${d.field}: ${d.problem}` : String(d?.problem ?? message);
    }
    return { code, message, details: err.details ?? null };
  }
  const fallback: Record<number, string> = { 401: CODE_MESSAGES.UNAUTHENTICATED, 403: "You don't have permission to do that.", 404: 'Not found.', 413: 'The file is too large.', 429: CODE_MESSAGES.TOO_MANY_ATTEMPTS, 502: 'The service is temporarily unavailable.', 503: 'The service is temporarily unavailable.' };
  return { code: `HTTP_${status}`, message: fallback[status] ?? `Request failed (${status}).`, details: null };
}

export type Query = Record<string, string | number | boolean | null | undefined | Array<string | number>>;

export function qs(query?: Query): string {
  if (!query) return '';
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.append(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  query?: Query;
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  idempotencyKey?: string;
  /** do not attach the bearer token (sign-in calls) */
  anonymous?: boolean;
  responseType?: 'json' | 'blob';
}

function onUnauthorized() {
  v2Session.clear();
  window.dispatchEvent(new Event('auth:unauthorized'));
}

export async function request<T>(path: string, o: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (o.json !== undefined) headers['Content-Type'] = 'application/json';
  if (o.idempotencyKey) headers['Idempotency-Key'] = o.idempotencyKey;
  const token = o.anonymous ? null : v2Session.getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${V2_BASE_URL}/api/v2${path}${qs(o.query)}`, {
      method: o.method ?? 'GET', headers, signal: o.signal,
      body: o.form ?? (o.json !== undefined ? JSON.stringify(o.json) : undefined),
    });
  } catch (e: any) {
    if (e?.name === 'AbortError') throw e;
    throw new V2Error(0, 'NETWORK_ERROR', `Unable to reach the SetuAI v2 server at ${V2_BASE_URL}. Check that it is running.`);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    let body: any = null;
    try { body = JSON.parse(text); } catch { /* not JSON */ }
    const { code, message, details } = describe(res.status, body);
    if (res.status === 401 && !o.anonymous) onUnauthorized();
    throw new V2Error(res.status, code, message, details);
  }
  if (o.responseType === 'blob') return (await res.blob()) as unknown as T;
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

/** multipart upload with progress (fetch cannot report upload progress) */
export function upload<T>(path: string, form: FormData, opts: { onProgress?: (fraction: number) => void; signal?: AbortSignal } = {}): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${V2_BASE_URL}/api/v2${path}`);
    const token = v2Session.getToken();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (ev) => { if (ev.lengthComputable && opts.onProgress) opts.onProgress(ev.loaded / ev.total); };
    xhr.onerror = () => reject(new V2Error(0, 'NETWORK_ERROR', `Unable to reach the SetuAI v2 server at ${V2_BASE_URL}. Check that it is running.`));
    xhr.onabort = () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    xhr.onload = () => {
      let body: any = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as T);
      const { code, message, details } = describe(xhr.status, body);
      if (xhr.status === 401) onUnauthorized();
      reject(new V2Error(xhr.status, code, message, details));
    };
    if (opts.signal) {
      if (opts.signal.aborted) return reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
      opts.signal.addEventListener('abort', () => xhr.abort(), { once: true });
    }
    xhr.send(form);
  });
}

/** A fresh idempotency key. Keep ONE per form submission and reuse it for retries of that submission; make a new one after success. */
export function newIdempotencyKey(prefix = 'ui'): string {
  const rnd = (globalThis.crypto && 'randomUUID' in globalThis.crypto) ? globalThis.crypto.randomUUID() : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${rnd}`;
}

export interface Page<T> { items: T[]; limit: number; offset: number; next_offset: number | null }
