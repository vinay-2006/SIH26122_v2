import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Activity, AlertOctagon, BarChart3, ClipboardList, Layers, ShieldAlert } from 'lucide-react';
import { useProjectState } from '@/context/ProjectContext';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { dashboardApi, issuesApi } from '@/v2/api/endpoints';
import { ApproxNote, Loading, Notice, PageHeader, Panel, ProgressBar, QueryError, Stat, LifecyclePill, day, pct } from '@/v2/ui';
import { EmptyState } from '@/components/ui/empty-state';
import { cn } from '@/lib/utils';

export default function OverviewPage() {
  const { currentProject, can } = useProjectState();
  const { projectId, detail, viewVersionId, viewingHistorical, role } = useV2Project();
  const q = { version_id: viewVersionId };
  const summary = useQuery({ queryKey: ['v2', 'summary', projectId, viewVersionId], queryFn: ({ signal }) => dashboardApi.summary(projectId, q, { signal }), retry: false });
  const stages = useQuery({ queryKey: ['v2', 'stages', projectId, viewVersionId], queryFn: () => dashboardApi.stages(projectId, q), retry: false, enabled: !!summary.data });
  const disciplines = useQuery({ queryKey: ['v2', 'disciplines', projectId, viewVersionId], queryFn: () => dashboardApi.disciplines(projectId, q), retry: false, enabled: !!summary.data });
  const timeline = useQuery({ queryKey: ['v2', 'timeline', projectId, viewVersionId], queryFn: () => dashboardApi.timeline(projectId, { step_days: 30, ...q }), retry: false, enabled: !!summary.data });
  const blockers = useQuery({ queryKey: ['v2', 'blockers', projectId], queryFn: ({ signal }) => issuesApi.blockers(projectId, { signal }), retry: false });

  const s = summary.data;
  const noSchedule = summary.error && (summary.error as any).code === 'NO_ACTIVE_SCHEDULE';
  const claimLabel = s?.claims.scope === 'own' ? 'Your claims awaiting a decision' : 'Claims awaiting a decision (counts only)';

  return (
    <div className="space-y-6" data-testid="overview-page">
      <PageHeader project={`${currentProject?.name} (${currentProject?.code})`} title="Project Overview"
        subtitle="Approved progress only: pending, rejected, withdrawn and clarification-requested claims never count until a Supervisor approves them."
        actions={<LifecyclePill value={detail.lifecycle_status} />} />
      {viewingHistorical && <Notice tone="warn">You are viewing a historical schedule version. Figures use that version’s scope and weights; claims are always filed against the active schedule.</Notice>}
      {summary.isPending && <Loading what="Loading progress…" />}
      {noSchedule && (
        <EmptyState icon={Layers} title="No active schedule yet" description={role === 'PROJECT_MANAGER' ? 'Import and activate a schedule baseline to start tracking progress.' : 'A Project Manager must import and activate a schedule before progress can be tracked.'}
          action={can('MANAGE_SCHEDULE') ? <Link to="/schedule" className="text-xs font-bold text-primary underline">Go to Schedule</Link> : undefined} />
      )}
      {summary.error && !noSchedule && <QueryError error={summary.error} onRetry={() => summary.refetch()} />}
      {s && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Stat testid="stat-actual" label="Actual progress" value={pct(s.physical_pct)} sub={`${s.activities.completed} of ${s.activities.total} activities complete`} />
            <Stat testid="stat-planned" label="Planned (approx.)" value={pct(s.planned_pct)} sub={`linear approximation as of ${day(s.as_of)}`} />
            <Stat testid="stat-spi" label="SPI (approx.)" value={s.spi_approx === null ? '—' : Number(s.spi_approx).toFixed(2)} sub="schedule indicator, not earned value" tone={s.spi_approx !== null && Number(s.spi_approx) < 0.9 ? 'warn' : undefined} />
            <Stat testid="stat-datadate" label="Data date" value={day(s.data_date)} sub={`schedule v${s.version.version_no}${s.version.baseline_name ? ` · ${s.version.baseline_name}` : ''}`} />
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Stat label="In progress / not started" value={`${s.activities.in_progress} / ${s.activities.not_started}`} />
            <Stat testid="stat-claims" label={claimLabel} value={s.claims.pending_total} sub={s.claims.scope === 'own' ? 'only yours are shown' : 'claim contents are visible to Supervisors only'} />
            <Stat testid="stat-issues" label="Active issues" value={s.issues.active} sub={`${s.issues.blocking} blocking · ${s.issues.resolved} resolved`} tone={s.issues.blocking > 0 ? 'bad' : undefined} />
            <Stat label="Weighting basis" value={s.weight_basis} sub={s.weight_basis_explanation} />
          </div>
          <ApproxNote text={`${s.planned_method_note} ${s.spi_note}`} />
          {s.any_overrun && <Notice tone="warn">At least one activity has approved quantities above its baseline. Quantities are never capped; the percentage is capped at 100%.</Notice>}

          {(blockers.data?.blocked_activities.length || blockers.data?.blocked_stages.length) ? (
            <Notice tone="bad" testid="blockers-notice"><ShieldAlert className="inline w-3.5 h-3.5 mr-1" />
              Work is blocked on {blockers.data!.blocked_activities.length} activit{blockers.data!.blocked_activities.length === 1 ? 'y' : 'ies'} and {blockers.data!.blocked_stages.length} stage{blockers.data!.blocked_stages.length === 1 ? '' : 's'} by active blocking issues. <Link to="/issues" className="font-bold underline">See issues</Link></Notice>
          ) : null}

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            <Panel icon={Layers} title="Progress by stage" description="Weighted by the schedule’s weighting basis">
              {stages.isPending && <Loading />}
              {stages.data?.items.length === 0 && <div className="text-xs text-muted-foreground">No stages in this schedule.</div>}
              <div className="space-y-3">
                {stages.data?.items.map((r) => (
                  <div key={r.wbs_id} className="space-y-1" data-testid="stage-row">
                    <div className="flex items-baseline justify-between gap-3 text-xs"><span className="font-bold truncate">{r.wbs_name}</span><span className="font-mono shrink-0"><b>{pct(r.physical_pct)}</b><span className="text-muted-foreground"> / plan≈{pct(r.planned_pct)}</span></span></div>
                    <ProgressBar actual={Number(r.physical_pct)} planned={Number(r.planned_pct)} />
                    <div className="text-[10px] text-muted-foreground">{r.activities} activities</div>
                  </div>
                ))}
              </div>
            </Panel>
            <Panel icon={BarChart3} title="Progress by discipline">
              {disciplines.isPending && <Loading />}
              <div className="space-y-3">
                {disciplines.data?.items.map((r) => (
                  <div key={r.discipline_code} className="space-y-1" data-testid="discipline-row">
                    <div className="flex items-baseline justify-between gap-3 text-xs"><span className="font-bold">{r.discipline_code.replace(/_/g, ' ')}</span><span className="font-mono"><b>{pct(r.physical_pct)}</b><span className="text-muted-foreground"> / plan≈{pct(r.planned_pct)}</span></span></div>
                    <ProgressBar actual={Number(r.physical_pct)} planned={Number(r.planned_pct)} />
                    <div className="text-[10px] text-muted-foreground">{r.activities} activities</div>
                  </div>
                ))}
              </div>
            </Panel>
          </div>

          <Panel icon={Activity} title="Actual vs approximate plan over time" description={`Monthly points up to ${day(s.as_of)}; the plan line is a linear approximation`}>
            {timeline.isPending && <Loading />}
            {timeline.error && <QueryError error={timeline.error} />}
            {timeline.data && timeline.data.points.length > 0 && (
              <div className="h-64" data-testid="timeline-chart">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={timeline.data.points.map((p) => ({ date: p.as_of, Actual: Number(p.physical_pct), 'Plan (approx.)': Number(p.planned_pct) }))} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#94A8B8" strokeOpacity={0.3} />
                    <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={(d: string) => d.slice(0, 7)} />
                    <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} unit="%" />
                    <Tooltip formatter={(v: any) => `${Number(v).toFixed(1)}%`} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Line type="monotone" dataKey="Actual" stroke="#FF7A18" strokeWidth={2.5} dot={false} />
                    <Line type="monotone" dataKey="Plan (approx.)" stroke="#0284C7" strokeWidth={2} strokeDasharray="5 4" dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </Panel>

          <div className="flex flex-wrap gap-3 text-xs">
            {can('REVIEW_CLAIM') && <Link to="/review" className={cn('inline-flex items-center gap-1.5 font-bold text-primary underline')}><ClipboardList className="w-3.5 h-3.5" />Review queue</Link>}
            {can('CREATE_EXECUTION_EVENT') && <Link to="/claims/new" className="inline-flex items-center gap-1.5 font-bold text-primary underline"><ClipboardList className="w-3.5 h-3.5" />Submit a claim</Link>}
            <Link to="/issues" className="inline-flex items-center gap-1.5 font-bold text-primary underline"><AlertOctagon className="w-3.5 h-3.5" />Issues & delays</Link>
          </div>
        </>
      )}
    </div>
  );
}
