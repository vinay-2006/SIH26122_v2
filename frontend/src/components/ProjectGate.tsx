import React from 'react';
import { AlertTriangle, Building2, Loader2, RefreshCw } from 'lucide-react';
import { useProjectState } from '@/context/ProjectContext';
import { Button } from '@/components/ui/button';

/**
 * Renders its children ONLY when a project AND a schedule version are explicitly selected, so no page can
 * ever issue a project-owned request without context. Otherwise shows exactly what is missing.
 */
export function ProjectGate({ children }: { children: React.ReactNode }) {
  const { status, error, currentProject, scheduleVersions, setCurrentScheduleVersionId, refresh } = useProjectState();

  if (status === 'ready') return <>{children}</>;

  if (status === 'loading' || status === 'unauthenticated') {
    return (
      <div className="flex items-center justify-center py-24 text-muted-foreground" role="status" aria-live="polite">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        Loading your projects…
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="max-w-lg mx-auto py-20 text-center space-y-3" role="alert">
        <AlertTriangle className="w-8 h-8 text-rose-500 mx-auto" />
        <p className="text-sm font-semibold">{error}</p>
        <Button size="sm" variant="outline" onClick={refresh}>
          <RefreshCw className="w-3.5 h-3.5 mr-1" /> Retry
        </Button>
      </div>
    );
  }

  if (status === 'no-projects') {
    return (
      <div className="max-w-lg mx-auto py-20 text-center space-y-3" data-testid="no-projects">
        <Building2 className="w-8 h-8 text-muted-foreground mx-auto" />
        <p className="text-sm font-semibold">You are not a member of any project yet.</p>
        <p className="text-xs text-muted-foreground">Ask a project manager to add you to a project.</p>
      </div>
    );
  }

  // needs-schedule: never guessed; the user chooses.
  return (
    <div className="max-w-lg mx-auto py-16 space-y-4" data-testid="needs-schedule">
      <div className="text-center space-y-1">
        <p className="text-sm font-semibold">Select a schedule version for {currentProject?.name}</p>
        <p className="text-xs text-muted-foreground">
          {scheduleVersions.length === 0
            ? 'This project has no schedule versions yet.'
            : 'This project has no single active version, so you need to choose one explicitly.'}
        </p>
      </div>
      <div className="space-y-2">
        {scheduleVersions.map((v) => (
          <button
            key={v.id}
            type="button"
            onClick={() => setCurrentScheduleVersionId(v.id)}
            className="w-full text-left p-3 rounded-xl border border-slate-300 dark:border-[#1E3A5F] hover:bg-slate-100 dark:hover:bg-[#0B2D4A] text-xs flex items-center justify-between cursor-pointer"
          >
            <span className="font-bold">{v.versionNumber}</span>
            <span className="font-mono text-muted-foreground">
              {v.status} · {v.activitiesCount} activities
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
