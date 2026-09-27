import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from './auth/AuthProvider';
import { ThemeProvider } from './theme/ThemeProvider';
import { ProjectProvider } from './context/ProjectContext';
import ProtectedRoute from './auth/ProtectedRoute';
import AppShell from './layout/AppShell';

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

const queryClient = new QueryClient();

function RoleRedirect() {
  const { user } = useAuth();
  if (user?.role === 'SITE_ENGINEER') {
    return <Navigate to="/intake" replace />;
  }
  return <Navigate to="/digest" replace />;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthProvider>
          <ProjectProvider>
            <Router>
              <Routes>
                <Route path="/login" element={<LoginScreen />} />

                <Route element={<AppShell />}>
                  {/* Landing route redirected based on role */}
                  <Route element={<ProtectedRoute />}>
                    <Route path="/" element={<RoleRedirect />} />
                  </Route>

                  {/* Supervisor-only routes */}
                  <Route element={<ProtectedRoute allowedRoles={['SUPERVISOR']} />}>
                    <Route path="/time-agent" element={<TimeAgent />} />
                    <Route path="/digest" element={<DailyDigest />} />
                    <Route path="/review" element={<ReviewWorkspace />} />
                    <Route path="/dashboard" element={<Dashboard />} />
                    <Route path="/history" element={<ActivityHistory />} />
                    <Route path="/impact" element={<ImpactPreview />} />
                    <Route path="/wbs" element={<WBSExplorerPage />} />
                    <Route path="/summary" element={<AIExecutionSummary />} />
                    <Route path="/reports/execution-summary" element={<AIExecutionSummary />} />
                  </Route>

                  {/* Site Engineer-only routes */}
                  <Route element={<ProtectedRoute allowedRoles={['SITE_ENGINEER']} />}>
                    <Route path="/intake" element={<ClaimIntake />} />
                  </Route>
                </Route>

                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Router>
          </ProjectProvider>
        </AuthProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

export default App;