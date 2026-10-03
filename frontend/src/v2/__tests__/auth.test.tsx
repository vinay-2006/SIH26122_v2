import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { useAuth } from '@/auth/AuthProvider';
import { AuthProviderV2 } from '@/v2/AuthProviderV2';
import { V2Error } from '@/v2/api/http';
import { V2_TOKEN_KEY } from '@/v2/session';

const api = vi.hoisted(() => ({ me: vi.fn(), localLogin: vi.fn(), config: vi.fn() }));
vi.mock('@/v2/api/endpoints', () => ({ authApi: api }));

let ctx: ReturnType<typeof useAuth>;
function Probe() { ctx = useAuth(); return <div data-testid="state">{ctx.loading ? 'loading' : ctx.user ? `${ctx.user.role}:${ctx.user.email}` : 'anonymous'}</div>; }
const mount = () => render(<AuthProviderV2><Probe /></AuthProviderV2>);
const me = (roles: string[], caps: string[] = []) => ({ id: 'u1', email: 'x@seed.setuai.local', full_name: 'X Person', capabilities: caps, projects: roles.map((r, i) => ({ project_id: `p${i}`, my_role: r })) });

beforeEach(() => { vi.clearAllMocks(); });

describe('v2 authentication', () => {
  it('starts anonymous without a token and never calls the API', async () => {
    mount(); await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('anonymous'));
    expect(api.me).not.toHaveBeenCalled();
  });

  it.each([['PROJECT_MANAGER', ['PROJECT_MANAGER']], ['SUPERVISOR', ['SITE_ENGINEER', 'SUPERVISOR']], ['SITE_ENGINEER', ['SITE_ENGINEER']]])('restores a %s session from the SERVER’s answer', async (expected, roles) => {
    localStorage.setItem(V2_TOKEN_KEY, 'good');
    api.me.mockResolvedValue(me(roles, expected === 'PROJECT_MANAGER' ? ['CREATE_PROJECT'] : []));
    mount(); await waitFor(() => expect(screen.getByTestId('state').textContent).toBe(`${expected}:x@seed.setuai.local`));
    expect(ctx.user?.capabilities).toEqual(expected === 'PROJECT_MANAGER' ? ['CREATE_PROJECT'] : []);
  });

  it('ignores a role written into local storage: the server decides', async () => {
    localStorage.setItem(V2_TOKEN_KEY, 'good');
    localStorage.setItem('user', JSON.stringify({ id: 'u1', role: 'PROJECT_MANAGER', capabilities: ['CREATE_PROJECT', 'PLATFORM_ADMIN'] }));
    localStorage.setItem('role', 'PROJECT_MANAGER');
    api.me.mockResolvedValue(me(['SITE_ENGINEER']));
    mount(); await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('SITE_ENGINEER:x@seed.setuai.local'));
    expect(ctx.user?.capabilities).toEqual([]);
  });

  it('drops an invalid / expired / revoked token and falls back to the login page state', async () => {
    localStorage.setItem(V2_TOKEN_KEY, 'dead');
    api.me.mockRejectedValue(new V2Error(401, 'UNAUTHENTICATED', 'Please sign in to continue.'));
    mount(); await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('anonymous'));
    expect(localStorage.getItem(V2_TOKEN_KEY)).toBeNull();
  });

  it('signs in through the server’s local sign-in and stores only the token', async () => {
    mount(); await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('anonymous'));
    api.localLogin.mockResolvedValue({ access_token: 'new-token', token_type: 'bearer', expires_in: 1 });
    api.me.mockResolvedValue(me(['SUPERVISOR']));
    await act(async () => { await ctx.login(' Lakshmi.Iyer@Seed.SetuAI.local ', 'pw'); });
    expect(api.localLogin).toHaveBeenCalledWith('lakshmi.iyer@seed.setuai.local', 'pw');
    expect(localStorage.getItem(V2_TOKEN_KEY)).toBe('new-token');
    expect(Object.keys(localStorage).filter((k) => /role|user/i.test(k))).toEqual([]);          // no role or user object is persisted
    expect(screen.getByTestId('state').textContent).toBe('SUPERVISOR:x@seed.setuai.local');
  });

  it('a failed sign-in keeps the person anonymous and shows the reason', async () => {
    mount(); await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('anonymous'));
    api.localLogin.mockRejectedValue(new V2Error(401, 'INVALID_CREDENTIALS', 'Incorrect email or password.'));
    await act(async () => { await expect(ctx.login('a@b.c', 'bad')).rejects.toThrow('Incorrect email or password.'); });
    expect(ctx.error).toBe('Incorrect email or password.'); expect(ctx.user).toBeNull(); expect(localStorage.getItem(V2_TOKEN_KEY)).toBeNull();
  });

  it('a 401 anywhere (session expiry) ends the session', async () => {
    localStorage.setItem(V2_TOKEN_KEY, 'good'); api.me.mockResolvedValue(me(['SITE_ENGINEER']));
    mount(); await waitFor(() => expect(screen.getByTestId('state').textContent).toMatch(/SITE_ENGINEER/));
    act(() => { window.dispatchEvent(new Event('auth:unauthorized')); });
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('anonymous'));
    expect(localStorage.getItem(V2_TOKEN_KEY)).toBeNull();
  });

  it('sign-out clears the session', async () => {
    localStorage.setItem(V2_TOKEN_KEY, 'good'); api.me.mockResolvedValue(me(['PROJECT_MANAGER']));
    mount(); await waitFor(() => expect(screen.getByTestId('state').textContent).toMatch(/PROJECT_MANAGER/));
    await act(async () => { await ctx.logout(); });
    expect(screen.getByTestId('state').textContent).toBe('anonymous'); expect(localStorage.getItem(V2_TOKEN_KEY)).toBeNull();
  });
});
