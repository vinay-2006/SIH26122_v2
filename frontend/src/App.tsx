import { PAGE_AUDIT, PAGE_INTELLIGENCE, PAGE_WBS } from './layout/pageAccess';
import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, Outlet } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from './auth/AuthProvider';
import { ThemeProvider } from './theme/ThemeProvider';
import { ProjectProvider, useProjectState } from './context/ProjectContext';
import ProtectedRoute, { landingFor } from './auth/ProtectedRoute';
import AppShell from './layout/AppShell';
import { IS_V2 } from './config';
import { AuthProviderV2 } from './v2/AuthProviderV2';
import { ProjectProviderV2 } from './v2/ProjectProviderV2';
import { v2Routes } from './v2/routes';
import { ScheduleRequired } from './v2/ScheduleRequired';

// Pages
import LoginScreen from './pages/LoginScreen';
import ClaimIntake from './pages/ClaimIntake';
import DailyDigest from './pages/DailyDigest';
import ReviewWorkspace from './pages/ReviewWorkspace';
import Dashboard from './pages/Dashboard';
import ActivityHistory from './pages/ActivityHistory';
import ImpactPreview from './pages/ImpactPreview';
import WBSExplorerPage from './pages/WBSExplorerPage';
import AIExecutionSummary from './pages/AIExecutionSummary';
import TimeAgent from './pages/TimeAgent';
import ProjectIntelligence from './pages/ProjectIntelligence';
import AuditTrail from './pages/AuditTrail';
import IssuesDelays from './pages/IssuesDelays';
import MyUpdates from './pages/MyUpdates';
import RootCauseMemory from './pages/RootCauseMemory';

const queryClient = new QueryClient();

// One build talks to exactly one backend (see config.ts): the providers are chosen once, at start-up.
const AuthProv = IS_V2 ? AuthProviderV2 : AuthProvider;
const ProjectProv = IS_V2 ? ProjectProviderV2 : ProjectProvider;

function PermissionRedirect() {
  const { can, status } = useProjectState();
  if (status !== 'ready') return null; // ProjectGate shows the loading / selection state
  return <Navigate to={landingFor(can)} replace />;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthProv>
          <ProjectProv>
            <Router>
              <Routes>
                <Route path="/login" element={<LoginScreen />} />

                <Route element={<AppShell />}>
                  {/* Landing route: decided by the caller's PROJECT permissions */}
                  <Route element={<ProtectedRoute />}>
                    <Route path="/" element={<PermissionRedirect />} />
                  </Route>

                  {IS_V2 && v2Routes()}
                  <Route element={IS_V2 ? <ScheduleRequired /> : <Outlet />}>
                  {/* Reviewers (REVIEW_CLAIM: supervisor / planner / project manager / owner) */}
                  <Route element={<ProtectedRoute requires={['REVIEW_CLAIM']} />}>
                    <Route path="/time-agent" element={<TimeAgent />} />
                    <Route path="/digest" element={<DailyDigest />} />
                    <Route path="/review" element={<ReviewWorkspace />} />
                    <Route path="/dashboard" element={<Dashboard />} />
                    <Route path="/history" element={<ActivityHistory />} />
                    <Route path="/summary" element={<AIExecutionSummary />} />
                    <Route path="/reports/execution-summary" element={<AIExecutionSummary />} />
                  </Route>

                  {/* Impact preview reads only the schedule: reviewers and (v2) the Project Manager */}
                  <Route element={<ProtectedRoute requires={['REVIEW_CLAIM', 'VIEW_MONITORING']} />}>
                    <Route path="/impact" element={<ImpactPreview />} />
                  </Route>

                  <Route element={<ProtectedRoute requires={PAGE_WBS} />}>
                    <Route path="/wbs" element={<WBSExplorerPage />} />
                  </Route>

                  {/* Supervising agent: read-only, every project member */}
                  <Route element={<ProtectedRoute requires={PAGE_INTELLIGENCE} />}>
                    <Route path="/intelligence" element={<ProjectIntelligence />} />
                  </Route>

                  {/* Tamper-evident audit trail */}
                  <Route element={<ProtectedRoute requires={PAGE_AUDIT} />}>
                    <Route path="/audit" element={<AuditTrail />} />
                  </Route>

                  {/* Field reporting (CREATE_EXECUTION_EVENT: site engineer / project manager / owner) */}
                  <Route element={<ProtectedRoute requires={['CREATE_EXECUTION_EVENT']} />}>
                    <Route path="/intake" element={<ClaimIntake />} />
                    {/* what happened to the claims I submitted: supervisor decisions, persisted by the backend */}
                    <Route path="/updates" element={<MyUpdates />} />
                  </Route>

                  {/* Issues & delays: reported from the field (REPORT_ISSUE), resolved by reviewers (MANAGE_BLOCKERS) */}
                  <Route element={<ProtectedRoute requires={['REPORT_ISSUE', 'VIEW_MONITORING']} />}>
                    <Route path="/issues" element={<IssuesDelays />} />
                  </Route>

                  {/* Root-cause analysis + institutional memory (reviewers) */}
                  <Route element={<ProtectedRoute requires={['MANAGE_BLOCKERS', 'VIEW_MONITORING']} />}>
                    <Route path="/root-cause" element={<RootCauseMemory />} />
                  </Route>
                  </Route>
                </Route>

                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Router>
          </ProjectProv>
        </AuthProv>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

export default App;