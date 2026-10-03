/**
 * The v2 route tree. The guard of every route is DATA (V2_ROUTES) so it can be tested exhaustively; v2Routes() turns it into <Route>s to be placed inside the
 * shared AppShell route. Guards use the same ProtectedRoute as the legacy app and the capabilities the SERVER granted for the selected project.
 * (A guard only decides what the UI shows. The API authorises every request again.)
 */
import React from 'react';
import { Route } from 'react-router-dom';
import ProtectedRoute from '@/auth/ProtectedRoute';
import type { Permission } from '@/api/projects';
import OverviewPage from '@/v2/pages/OverviewPage';
import PortfolioPage from '@/v2/pages/PortfolioPage';
import SchedulePage from '@/v2/pages/SchedulePage';
import SettingsPage from '@/v2/pages/SettingsPage';

export interface RouteRule { path: string; requires: Permission[]; element: React.ReactElement }

/** Project Manager pages only. Every Site Engineer and Supervisor page is the ORIGINAL page (see App.tsx), served by the v2 backend through the legacy API contract. */
export const V2_ROUTES: RouteRule[] = [
  { path: '/portfolio', requires: ['MANAGE_PROJECT'], element: <PortfolioPage /> },
  { path: '/overview', requires: ['MANAGE_PROJECT'], element: <OverviewPage /> },
  { path: '/schedule', requires: ['MANAGE_SCHEDULE'], element: <SchedulePage /> },
  { path: '/settings', requires: ['MANAGE_PROJECT'], element: <SettingsPage /> },
];

export function v2Routes() {
  return (
    <>
      {V2_ROUTES.map((r) => (
        <Route key={r.path} element={<ProtectedRoute requires={r.requires} />}>
          <Route path={r.path} element={r.element} />
        </Route>
      ))}
    </>
  );
}
