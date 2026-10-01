import React from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import { useProjectState } from '@/context/ProjectContext';
import type { Permission } from '@/api/projects';
import { Loader2 } from 'lucide-react';

interface ProtectedRouteProps {
  /** Any-of. The caller's PROJECT permissions (server-authoritative), not a global role. */
  requires?: Permission[];
}

/** Where a user with these permissions lands. */
export function landingFor(can: (p: Permission) => boolean): string {
  if (can('REVIEW_CLAIM')) return '/dashboard';
  if (can('CREATE_EXECUTION_EVENT')) return '/intake';
  if (can('VIEW_AUDIT') && !can('MANAGE_QUALITY')) return '/audit'; // auditor: read-only oversight
  return '/wbs';
}

export default function ProtectedRoute({ requires }: ProtectedRouteProps) {
  const { user, loading } = useAuth();
  const { status, can } = useProjectState();
  const location = useLocation();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Permissions are only known once a project is selected; the ProjectGate renders the wait/empty state.
  if (requires && status === 'ready' && !requires.some((p) => can(p))) {
    return <Navigate to={landingFor(can)} replace />;
  }

  return <Outlet />;
}
