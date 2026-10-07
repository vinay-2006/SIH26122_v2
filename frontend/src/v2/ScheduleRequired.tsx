/** Wraps the ORIGINAL pages in v2 mode. They all need a schedule version; a Project Manager can open a project that has none (to import one), so the pages say what is missing instead of failing. */
import React from 'react';
import { Outlet, Link } from 'react-router-dom';
import { useProjectState } from '@/context/ProjectContext';

export function ScheduleRequired() {
  const { currentScheduleVersion, can } = useProjectState();
  if (currentScheduleVersion) return <Outlet />;
  return (
    <div className="max-w-lg mx-auto py-16 text-center space-y-3" data-testid="no-active-schedule">
      <p className="text-sm font-semibold">This project has no active schedule yet.</p>
      <p className="text-xs text-muted-foreground">{can('MANAGE_SCHEDULE') ? 'Import and activate a schedule to start using the project pages.' : 'A Project Manager must import and activate a schedule first.'}</p>
      {can('MANAGE_SCHEDULE') && <Link className="text-xs font-bold text-[#FF7A18] underline" to="/schedule">Go to Schedule</Link>}
    </div>
  );
}
