import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { V2Error, newIdempotencyKey, qs, request, upload } from '@/v2/api/http';
import { V2_TOKEN_KEY } from '@/v2/session';

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => { vi.unstubAllGlobals(); });

describe('v2 http client', () => {
  it('sends the bearer token and JSON, and builds the URL from the configured base', async () => {
    localStorage.setItem(V2_TOKEN_KEY, 'tok-123');
    fetchMock.mockResolvedValue(json(200, { ok: true }));
    await request<any>('/projects', { method: 'POST', json: { a: 1 }, query: { x: 'y', skip: undefined, list: [1, 2] } });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://127.0.0.1:8020/api/v2/projects?x=y&list=1&list=2');
    expect(init.headers.Authorization).toBe('Bearer tok-123');
    expect(init.headers['Content-Type']).toBe('application/json');
    expect(init.body).toBe('{"a":1}');
  });

  it('does not send a token for anonymous calls and never invents one', async () => {
    fetchMock.mockResolvedValue(json(200, {}));
    await request<any>('/auth/config', { anonymous: true });
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
  });

  it('sends an Idempotency-Key only when asked', async () => {
    fetchMock.mockImplementation(async () => json(201, {}));
    await request<any>('/x', { method: 'POST', json: {}, idempotencyKey: 'claim-abc' });
    await request<any>('/x', { method: 'POST', json: {} });
    expect(fetchMock.mock.calls[0][1].headers['Idempotency-Key']).toBe('claim-abc');
    expect(fetchMock.mock.calls[1][1].headers['Idempotency-Key']).toBeUndefined();
  });

  it('turns the error envelope into a V2Error with the stable code and a readable message', async () => {
    fetchMock.mockResolvedValue(json(403, { error: { code: 'CLAIM_CONTENT_FORBIDDEN', message: 'server text', details: null } }));
    const e = await request<any>('/x').catch((x: any) => x);
    expect(e).toBeInstanceOf(V2Error);
    expect([e.status, e.code, e.isForbidden]).toEqual([403, 'CLAIM_CONTENT_FORBIDDEN', true]);
    expect(e.message).toMatch(/aggregate|counts only/i);
  });

  it('shows the first field problem for validation errors', async () => {
    fetchMock.mockResolvedValue(json(422, { error: { code: 'VALIDATION_ERROR', message: 'The request is not valid', details: [{ field: 'event_date', problem: 'Input should be a valid date', type: 'date_parsing' }] } }));
    const e = await request<any>('/x', { method: 'POST', json: {} }).catch((x: any) => x);
    expect(e.isValidation).toBe(true);
    expect(e.message).toBe('event_date: Input should be a valid date');
  });

  it('on 401 clears the session and announces it (so the app returns to sign-in)', async () => {
    localStorage.setItem(V2_TOKEN_KEY, 'old');
    const heard = vi.fn(); window.addEventListener('auth:unauthorized', heard);
    fetchMock.mockResolvedValue(json(401, { error: { code: 'UNAUTHENTICATED', message: 'Missing bearer token', details: null } }));
    await expect(request('/me')).rejects.toMatchObject({ status: 401, isAuth: true });
    expect(localStorage.getItem(V2_TOKEN_KEY)).toBeNull();
    expect(heard).toHaveBeenCalled();
    window.removeEventListener('auth:unauthorized', heard);
  });

  it('a failed SIGN-IN (anonymous 401) does not fire the session-expired event', async () => {
    const heard = vi.fn(); window.addEventListener('auth:unauthorized', heard);
    fetchMock.mockResolvedValue(json(401, { error: { code: 'INVALID_CREDENTIALS', message: 'Incorrect email or password', details: null } }));
    const e = await request<any>('/auth/local-login', { method: 'POST', json: {}, anonymous: true }).catch((x: any) => x);
    expect(e.code).toBe('INVALID_CREDENTIALS');
    expect(heard).not.toHaveBeenCalled();
    window.removeEventListener('auth:unauthorized', heard);
  });

  it('reports network failures plainly, never a raw TypeError', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    const e = await request<any>('/me').catch((x: any) => x);
    expect([e.status, e.code, e.isNetwork]).toEqual([0, 'NETWORK_ERROR', true]);
    expect(e.message).toMatch(/Unable to reach the SetuAI v2 server/);
  });

  it('lets an aborted request surface as an AbortError, not as an application error', async () => {
    fetchMock.mockRejectedValue(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    await expect(request('/me')).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('never leaks a server stack trace or an unknown body into the message', async () => {
    fetchMock.mockResolvedValue(new Response('Traceback (most recent call last): secret path /Users/x', { status: 500 }));
    const e = await request<any>('/x').catch((x: any) => x);
    expect(e.message).not.toMatch(/Traceback|\/Users/);
    expect(e.code).toBe('HTTP_500');
  });

  it('serialises queries without empty values', () => {
    expect(qs({ a: 1, b: '', c: null, d: undefined, e: false, f: ['x', 'y'] })).toBe('?a=1&e=false&f=x&f=y');
    expect(qs()).toBe('');
  });

  it('makes distinct idempotency keys of an allowed shape', () => {
    const a = newIdempotencyKey('claim'), b = newIdempotencyKey('claim');
    expect(a).not.toBe(b);
    expect(a).toMatch(/^[A-Za-z0-9_.:-]{8,128}$/);
  });
});

describe('multipart upload', () => {
  class FakeXhr {
    static last: FakeXhr; headers: Record<string, string> = {}; upload: any = {}; status = 0; responseText = ''; onload: any; onerror: any; onabort: any; method = ''; url = ''; sent: any;
    constructor() { FakeXhr.last = this; }
    open(m: string, u: string) { this.method = m; this.url = u; }
    setRequestHeader(k: string, v: string) { this.headers[k] = v; }
    send(b: any) { this.sent = b; }
    abort() { this.onabort?.(); }
  }
  beforeEach(() => vi.stubGlobal('XMLHttpRequest', FakeXhr as any));

  it('posts FormData with the token (no JSON content type) and reports progress', async () => {
    localStorage.setItem(V2_TOKEN_KEY, 'tok');
    const form = new FormData(); form.append('kind', 'EVIDENCE');
    const progress: number[] = [];
    const p = upload('/projects/p/documents', form, { onProgress: (f) => progress.push(f) });
    const x = FakeXhr.last;
    expect([x.method, x.url]).toEqual(['POST', 'http://127.0.0.1:8020/api/v2/projects/p/documents']);
    expect(x.headers.Authorization).toBe('Bearer tok'); expect(x.headers['Content-Type']).toBeUndefined();
    x.upload.onprogress({ lengthComputable: true, loaded: 5, total: 10 });
    x.status = 201; x.responseText = '{"document_id":"d1"}'; x.onload();
    await expect(p).resolves.toEqual({ document_id: 'd1' });
    expect(progress).toEqual([0.5]);
  });

  it('maps an upload rejection to the error envelope', async () => {
    const p = upload('/x', new FormData());
    const x = FakeXhr.last; x.status = 415; x.responseText = JSON.stringify({ error: { code: 'UNSUPPORTED_FILE_TYPE', message: 'Allowed files: PDF, PNG, JPEG, CSV, TXT, XLSX', details: null } }); x.onload();
    await expect(p).rejects.toMatchObject({ status: 415, code: 'UNSUPPORTED_FILE_TYPE' });
  });

  it('can be cancelled', async () => {
    const ctl = new AbortController();
    const p = upload('/x', new FormData(), { signal: ctl.signal });
    ctl.abort();
    await expect(p).rejects.toMatchObject({ name: 'AbortError' });
  });
});
