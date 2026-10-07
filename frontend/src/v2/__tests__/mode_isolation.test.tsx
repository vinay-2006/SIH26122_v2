import { describe, expect, it, vi } from 'vitest';

describe('backend mode selection', () => {
  it('defaults to the legacy demo backend; v2 must be chosen explicitly at build / start', async () => {
    vi.resetModules();
    const c = await import('@/config');
    expect(c.BACKEND_MODE).toBe('legacy'); expect(c.IS_V2).toBe(false);
    expect(c.V2_BASE_URL).toBe('http://127.0.0.1:8020');                        // only read in v2 mode; trailing slashes are trimmed
  });

  it('legacy landing logic is untouched by the v2 additions', async () => {
    vi.resetModules();
    const { landingFor } = await import('@/auth/ProtectedRoute');
    const has = (...p: string[]) => (x: string) => p.includes(x);
    expect(landingFor(has('REVIEW_CLAIM') as any)).toBe('/dashboard');
    expect(landingFor(has('CREATE_EXECUTION_EVENT') as any)).toBe('/intake');
    expect(landingFor(has('VIEW_AUDIT') as any)).toBe('/audit');
    expect(landingFor(has('VIEW_SCHEDULE') as any)).toBe('/wbs');
  });

  it('v2 and legacy sessions use different storage keys, so a token of one never reaches the other', async () => {
    const { V2_TOKEN_KEY, V2_PROJECT_KEY } = await import('@/v2/session');
    expect(V2_TOKEN_KEY).not.toBe('supabase_access_token'); expect(V2_TOKEN_KEY).not.toBe('auth_token');
    expect(V2_PROJECT_KEY).not.toBe('setu_selected_project_id_v7');
    localStorage.setItem('supabase_access_token', 'legacy-token');
    const { v2Session } = await import('@/v2/session');
    expect(v2Session.getToken()).toBeNull();
  });
});
