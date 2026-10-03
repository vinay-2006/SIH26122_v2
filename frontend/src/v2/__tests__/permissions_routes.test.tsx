import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { Permission } from '@/api/projects';
import { landingForV2, primaryRole, toUiPermissions } from '@/v2/permissions';

// The permission lists the SERVER returns (backend/v2/permissions.py), per role.
const SERVER = {
  PROJECT_MANAGER: ['VIEW_PROJECT', 'VIEW_SCHEDULE', 'MANAGE_PROJECT', 'MANAGE_SETTINGS', 'MANAGE_MEMBERS', 'VIEW_MEMBERS', 'MANAGE_SCHEDULE', 'ACTIVATE_SCHEDULE', 'VIEW_DOCUMENTS', 'VIEW_CLAIM_COUNTS', 'VIEW_ISSUES', 'VIEW_DASHBOARD', 'VIEW_AUDIT', 'VIEW_ROOT_CAUSES'],
  SUPERVISOR: ['VIEW_PROJECT', 'VIEW_SCHEDULE', 'VIEW_MEMBERS', 'UPLOAD_EVIDENCE', 'VIEW_DOCUMENTS', 'REVIEW_CLAIMS', 'VIEW_CLAIM_COUNTS', 'REPORT_ISSUE', 'VIEW_ISSUES', 'RESOLVE_ISSUE', 'VIEW_DASHBOARD', 'VIEW_AUDIT', 'VIEW_ROOT_CAUSES'],
  SITE_ENGINEER: ['VIEW_PROJECT', 'VIEW_SCHEDULE', 'UPLOAD_REPORT', 'UPLOAD_EVIDENCE', 'VIEW_DOCUMENTS', 'SUBMIT_CLAIM', 'VIEW_OWN_CLAIMS', 'REPORT_ISSUE', 'VIEW_ISSUES', 'VIEW_DASHBOARD'],
} as const;
const caps = (r: keyof typeof SERVER) => new Set(toUiPermissions([...SERVER[r]]));
const can = (r: keyof typeof SERVER) => { const c = caps(r); return (p: Permission) => c.has(p); };

describe('server permissions -> UI capabilities', () => {
  it('Project Manager: manages projects and schedules, never reviews or files claims', () => {
    const c = caps('PROJECT_MANAGER');
    for (const p of ['MANAGE_PROJECT', 'MANAGE_SCHEDULE', 'VIEW_AUDIT', 'VIEW_PROJECT'] as Permission[]) expect(c.has(p)).toBe(true);
    for (const p of ['REVIEW_CLAIM', 'APPROVE_ACTUAL', 'CREATE_EXECUTION_EVENT', 'MANAGE_BLOCKERS', 'REPORT_ISSUE'] as Permission[]) expect(c.has(p)).toBe(false);
  });
  it('Supervisor: reviews and resolves, never manages schedules or the project', () => {
    const c = caps('SUPERVISOR');
    for (const p of ['REVIEW_CLAIM', 'APPROVE_ACTUAL', 'MANAGE_BLOCKERS', 'REPORT_ISSUE', 'VIEW_AUDIT'] as Permission[]) expect(c.has(p)).toBe(true);
    for (const p of ['MANAGE_SCHEDULE', 'MANAGE_PROJECT', 'CREATE_EXECUTION_EVENT'] as Permission[]) expect(c.has(p)).toBe(false);
  });
  it('Site Engineer: files claims and reports issues, nothing else', () => {
    const c = caps('SITE_ENGINEER');
    for (const p of ['CREATE_EXECUTION_EVENT', 'REPORT_ISSUE', 'VIEW_SCHEDULE'] as Permission[]) expect(c.has(p)).toBe(true);
    for (const p of ['MANAGE_SCHEDULE', 'MANAGE_PROJECT', 'REVIEW_CLAIM', 'APPROVE_ACTUAL', 'VIEW_AUDIT', 'MANAGE_BLOCKERS'] as Permission[]) expect(c.has(p)).toBe(false);
  });
  it('unknown server permissions grant nothing in the UI', () => {
    expect(toUiPermissions(['SOMETHING_NEW', 'DROP_TABLE'])).toEqual([]);
  });
  it('each role lands on its own page', () => {
    expect(landingForV2(can('PROJECT_MANAGER'))).toBe('/portfolio');
    expect(landingForV2(can('SUPERVISOR'))).toBe('/review');
    expect(landingForV2(can('SITE_ENGINEER'))).toBe('/claims/new');
    expect(landingForV2(() => false)).toBe('/overview');
  });
  it('the display role is the highest membership (informational only)', () => {
    expect(primaryRole(['SITE_ENGINEER', 'SUPERVISOR'])).toBe('SUPERVISOR');
    expect(primaryRole(['SITE_ENGINEER', 'PROJECT_MANAGER'])).toBe('PROJECT_MANAGER');
    expect(primaryRole([])).toBe('SITE_ENGINEER');
  });
});

// ---- the real route table, rendered through the real ProtectedRoute with a fake project context
vi.mock('@/context/ProjectContext', async () => {
  const state: any = { value: null };
  return { useProjectState: () => state.value, __state: state, SELECTED_PROJECT_KEY: 'k', SELECTED_VERSION_KEY: 'v' };
});
vi.mock('@/auth/AuthProvider', () => ({ useAuth: () => ({ user: { id: 'u' }, loading: false }) }));
vi.mock('@/config', () => ({ IS_V2: true, BACKEND_MODE: 'v2', V2_BASE_URL: 'http://x', V2_LOCAL_LOGIN_HINTS: false }));

describe('route guards (the real V2 route table)', async () => {
  const { V2_ROUTES } = await import('@/v2/routes').catch(() => ({ V2_ROUTES: [] as any[] }));
  const ProtectedRoute = (await import('@/auth/ProtectedRoute')).default;
  const ctx: any = await import('@/context/ProjectContext');

  const visit = (role: keyof typeof SERVER, path: string) => {
    ctx.__state.value = { status: 'ready', can: can(role), role };
    return render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          {V2_ROUTES.map((r: any) => <Route key={r.path} element={<ProtectedRoute requires={r.requires} />}><Route path={r.path} element={<div data-testid="page">{r.path}</div>} /></Route>)}
          <Route path="*" element={<div data-testid="landing">landed</div>} />
        </Routes>
      </MemoryRouter>);
  };
  /** the route pattern that rendered for this visit (or null when the guard sent the person away) */
  const reach = (role: keyof typeof SERVER, path: string) => { const { unmount } = visit(role, path); const page = screen.queryByTestId('page'); const r = page ? page.textContent : null; unmount(); return r === null ? null : r.replace(':id', 'abc'); };

  it('has the route table', () => { expect(V2_ROUTES.length).toBeGreaterThanOrEqual(12); });
  it('Project Manager reaches management pages and NO claim page', () => {
    for (const p of ['/portfolio', '/settings', '/schedule', '/overview', '/issues', '/activities', '/audit', '/notifications']) expect(reach('PROJECT_MANAGER', p), p).toBe(p);
    for (const p of ['/review', '/claims/new', '/claims/mine', '/claims/abc']) expect(reach('PROJECT_MANAGER', p), p).not.toBe(p);
  });
  it('Supervisor reaches review and audit, but not schedule, settings, portfolio or the claim form', () => {
    for (const p of ['/review', '/claims/abc', '/audit', '/overview', '/issues']) expect(reach('SUPERVISOR', p), p).toBe(p);
    for (const p of ['/schedule', '/settings', '/portfolio', '/claims/new', '/claims/mine']) expect(reach('SUPERVISOR', p), p).not.toBe(p);
  });
  it('Site Engineer reaches claim pages but not schedule, review, audit, settings or portfolio', () => {
    for (const p of ['/claims/new', '/claims/mine', '/claims/abc', '/overview', '/issues', '/activities']) expect(reach('SITE_ENGINEER', p), p).toBe(p);
    for (const p of ['/schedule', '/review', '/audit', '/settings', '/portfolio']) expect(reach('SITE_ENGINEER', p), p).not.toBe(p);
  });
  it('no route is open to someone with no capabilities', () => {
    ctx.__state.value = { status: 'ready', can: () => false, role: null };
    for (const r of V2_ROUTES) { const { unmount } = render(<MemoryRouter initialEntries={[r.path.replace(':id', 'x')]}><Routes><Route element={<ProtectedRoute requires={r.requires} />}><Route path={r.path} element={<div data-testid="page" />} /></Route><Route path="*" element={<div data-testid="landing" />} /></Routes></MemoryRouter>); expect(screen.queryByTestId('page'), r.path).toBeNull(); unmount(); }
  });
});
