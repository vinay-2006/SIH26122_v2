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
import ActivitiesPage from '@/v2/pages/ActivitiesPage';
import SchedulePage from '@/v2/pages/SchedulePage';
import SettingsPage from '@/v2/pages/SettingsPage';
import ClaimFormPage from '@/v2/pages/ClaimFormPage';
import MyClaimsPage from '@/v2/pages/MyClaimsPage';
import ReviewQueuePage from '@/v2/pages/ReviewQueuePage';
import ClaimDetailPage from '@/v2/pages/ClaimDetailPage';
import IssuesPage from '@/v2/pages/IssuesPage';
import NotificationsPage from '@/v2/pages/NotificationsPage';
import AuditPage from '@/v2/pages/AuditPage';

export interface RouteRule { path: string; requires: Permission[]; element: React.ReactElement }

export const V2_ROUTES: RouteRule[] = [
  // Project Managers: portfolio, project settings and members, schedule management
  { path: '/portfolio', requires: ['MANAGE_PROJECT'], element: <PortfolioPage /> },
  { path: '/settings', requires: ['MANAGE_PROJECT'], element: <SettingsPage /> },
  { path: '/schedule', requires: ['MANAGE_SCHEDULE'], element: <SchedulePage /> },
  // every project member
  { path: '/overview', requires: ['VIEW_PROJECT'], element: <OverviewPage /> },
  { path: '/issues', requires: ['VIEW_PROJECT'], element: <IssuesPage /> },
  { path: '/notifications', requires: ['VIEW_PROJECT'], element: <NotificationsPage /> },
  { path: '/activities', requires: ['VIEW_SCHEDULE'], element: <ActivitiesPage /> },
  // Site Engineers
  { path: '/claims/new', requires: ['CREATE_EXECUTION_EVENT'], element: <ClaimFormPage /> },
  { path: '/claims/mine', requires: ['CREATE_EXECUTION_EVENT'], element: <MyClaimsPage /> },
  // a claim: its engineer or a Supervisor (the API refuses everyone else; Project Managers never reach it)
  { path: '/claims/:id', requires: ['CREATE_EXECUTION_EVENT', 'REVIEW_CLAIM'], element: <ClaimDetailPage /> },
  // Supervisors
  { path: '/review', requires: ['REVIEW_CLAIM'], element: <ReviewQueuePage /> },
  { path: '/audit', requires: ['VIEW_AUDIT'], element: <AuditPage /> },
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
