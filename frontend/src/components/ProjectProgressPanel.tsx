import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CalendarClock, CheckCircle2, Hourglass, Loader2, PlayCircle } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { useProject } from '@/context/ProjectContext';
import { projectDashboardApi, type DisciplineRollup, type ProgressRollup, type ProjectLifecycle, type StageRollup } from '@/api/prototype';

const LIFECYCLE: Record<ProjectLifecycle, { label: string; tone: string; icon: React.ElementType }> = {
  COMPLETED: { label: 'Completed', tone: 'text-emerald-700 border-emerald-400 bg-emerald-50 dark:text-emerald-300 dark:border-emerald-800 dark:bg-emerald-950/30', icon: CheckCircle2 },
  ONGOING: { label: 'Ongoing', tone: 'text-blue-700 border-blue-400 bg-blue-50 dark:text-blue-300 dark:border-blue-800 dark:bg-blue-950/30', icon: PlayCircle },
  UPCOMING: { label: 'Upcoming', tone: 'text-slate-700 border-slate-400 bg-slate-50 dark:text-slate-300 dark:border-slate-700 dark:bg-slate-900/30', icon: Hourglass },
};

const fmt = (v: number) => `${Number.isInteger(v) ? v : v.toFixed(1)}%`;

/** A bar with the ACTUAL progress filled and a tick at where the plan says it should be by the as-of date. */
function ProgressBar({ actual, planned, notStarted }: { actual: number; planned: number; notStarted: boolean }) {
  return (
    <div className="relative h-2.5 rounded-full bg-slate-200 dark:bg-[#0E2B47] overflow-hidden" role="img" aria-label={notStarted ? 'Planned, not started' : `${fmt(actual)} complete, plan ${fmt(planned)}`}>
      {!notStarted && <div className={cn('h-full rounded-full transition-all', actual >= 100 ? 'bg-emerald-500' : 'bg-[#FF7A18]')} style={{ width: `${Math.min(100, Math.max(0, actual))}%` }} />}
      {planned > 0 && planned < 100 && <div className="absolute top-0 h-full w-0.5 bg-[#071A2D] dark:bg-white/80" style={{ left: `${planned}%` }} title={`Plan: ${fmt(planned)}`} />}
    </div>
  );
}

function Row({ name, sub, r, extra, notStarted }: { name: string; sub?: string; r: ProgressRollup; extra?: React.ReactNode; notStarted: boolean }) {
  const behind = !notStarted && r.variance_pct <= -5;
  return (
    <div className="space-y-1" data-testid="progress-row">
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <div className="min-w-0">
          <span className="font-bold">{name}</span>
          {sub && <span className="ml-2 text-muted-foreground">{sub}</span>}
        </div>
        <div className="shrink-0 font-mono">
          {notStarted ? <span className="text-muted-foreground font-sans font-semibold">Planned · not started</span> : (
            <><b>{fmt(r.actual_pct)}</b><span className="text-muted-foreground"> / plan {fmt(r.planned_pct)}</span></>
          )}
        </div>
      </div>
      <ProgressBar actual={r.actual_pct} planned={r.planned_pct} notStarted={notStarted} />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
        <span>{r.completed_count}/{r.activity_count} activities complete</span>
        {r.in_progress_count > 0 && <span>{r.in_progress_count} in progress</span>}
        {r.blocked_count > 0 && <span className="font-bold text-rose-600">{r.blocked_count} blocked</span>}
        {behind && <span className="inline-flex items-center gap-0.5 font-bold text-amber-700 dark:text-amber-400"><AlertTriangle className="w-3 h-3" />{Math.abs(Math.round(r.variance_pct))} pts behind plan</span>}
        {extra}
      </div>
    </div>
  );
}

/** Project → stage → discipline progress, straight from the database (GET .../dashboard). Nothing is computed in the browser. */
export function ProjectProgressPanel() {
  const { currentProject, currentScheduleVersion } = useProject();
  const q = useQuery({
    queryKey: ['v7', 'dashboard', currentProject.id, currentScheduleVersion.id],
    queryFn: () => projectDashboardApi.get(currentProject.id, currentScheduleVersion.id),
    retry: false,
    staleTime: 15_000,
  });

  if (q.isLoading) return <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin" /> Loading project progress…</div>;
  if (q.isError || !q.data) return <div role="alert" className="text-xs text-rose-600">Could not load project progress: {q.error instanceof Error ? q.error.message : 'unknown error'}</div>;

  const d = q.data;
  const life = LIFECYCLE[d.lifecycle_status];
  const LifeIcon = life.icon;
  const notStarted = d.lifecycle_status === 'UPCOMING' && d.overall.actual_pct === 0;

  return (
    <div className="space-y-4" data-testid="project-progress-panel">
      <Card className="bg-card border-border shadow-xs">
        <CardContent className="p-5">
          <div className="flex flex-col lg:flex-row lg:items-center gap-5">
            <div className="flex-1 min-w-0 space-y-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className={cn('inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md border text-[11px] font-extrabold', life.tone)} data-testid="lifecycle-badge"><LifeIcon className="w-3.5 h-3.5" />{life.label}</span>
                <span className="text-[11px] font-mono text-muted-foreground">{d.project_code}</span>
              </div>
              <div className="text-lg font-extrabold leading-tight">{d.project_name}</div>
              <div className="text-xs text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-0.5">
                {d.location && <span>{d.location}</span>}
                {d.planned_start && d.planned_finish && <span className="inline-flex items-center gap-1"><CalendarClock className="w-3 h-3" />{new Date(d.planned_start).toLocaleDateString()} – {new Date(d.planned_finish).toLocaleDateString()}</span>}
                <span>progress as of {new Date(d.as_of_date).toLocaleDateString()}</span>
              </div>
            </div>
            <div className="lg:w-80 shrink-0 space-y-1.5">
              <div className="flex items-baseline justify-between">
                <span className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Overall progress</span>
                <span className="text-2xl font-black font-mono" data-testid="overall-progress">{notStarted ? '0%' : fmt(d.overall.actual_pct)}</span>
              </div>
              <ProgressBar actual={d.overall.actual_pct} planned={d.overall.planned_pct} notStarted={notStarted} />
              <div className="text-[10px] text-muted-foreground">{notStarted ? `${d.overall.activity_count} activities planned; none started` : `Plan: ${fmt(d.overall.planned_pct)} · ${d.overall.completed_count}/${d.overall.activity_count} activities complete`}{d.issues.open > 0 ? ` · ${d.issues.open} open issue${d.issues.open === 1 ? '' : 's'}` : ''}</div>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card className="bg-card border-border shadow-xs">
          <CardHeader className="pb-2"><CardTitle className="text-sm">Stage-wise progress</CardTitle></CardHeader>
          <CardContent className="space-y-3.5">
            {d.stages.map((s: StageRollup) => (
              <Row key={s.stage_id} name={`${s.sequence_order}. ${s.stage_name}`} r={s} notStarted={notStarted || s.actual_pct === 0 && s.completed_count === 0 && s.in_progress_count === 0}
                extra={s.open_issue_count > 0 ? <span className="font-bold text-amber-700 dark:text-amber-400">{s.open_issue_count} open issue{s.open_issue_count === 1 ? '' : 's'}</span> : null} />
            ))}
          </CardContent>
        </Card>
        <Card className="bg-card border-border shadow-xs">
          <CardHeader className="pb-2"><CardTitle className="text-sm">Discipline-wise progress</CardTitle></CardHeader>
          <CardContent className="space-y-3.5">
            {d.disciplines.map((x: DisciplineRollup) => (
              <Row key={x.discipline} name={x.discipline_name} sub={`${x.activity_count} activities`} r={x} notStarted={notStarted || x.actual_pct === 0 && x.completed_count === 0 && x.in_progress_count === 0} />
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
