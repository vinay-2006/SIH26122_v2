import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Layers, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/components/ui/drawer';
import { EmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ExecutionStateBadge } from '@/components/ExecutionStateBadge';
import { useProjectState } from '@/context/ProjectContext';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { dashboardApi } from '@/v2/api/endpoints';
import type { ActivityProgress } from '@/v2/api/types';
import { ApproxNote, Loading, Notice, PageHeader, ProgressBar, QueryError, day, num, pct, F } from '@/v2/ui';
import { DISCIPLINES } from '@/v2/reference';

const PAGE = 50;

function ActivityDrawer({ projectId, a, onClose }: { projectId: string; a: ActivityProgress | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ['v2', 'activity-timeline', projectId, a?.activity_uid], queryFn: () => dashboardApi.activityTimeline(projectId, a!.activity_uid), enabled: !!a, retry: false });
  const t = q.data;
  return (
    <Drawer open={!!a} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DrawerContent className="overflow-y-auto" data-testid="activity-drawer">
        {a && (
          <>
            <DrawerHeader>
              <DrawerTitle>{a.external_activity_id} · {a.activity_name}</DrawerTitle>
              <DrawerDescription>{a.wbs_path.replace(/\./g, ' › ')} · {a.discipline_code.replace(/_/g, ' ')}</DrawerDescription>
            </DrawerHeader>
            <div className="space-y-4 text-xs">
              <div className="grid grid-cols-2 gap-2">
                <div><span className="text-muted-foreground">Baseline</span><div className="font-semibold">{day(a.baseline_start)} → {day(a.baseline_finish)}</div></div>
                <div><span className="text-muted-foreground">Actual</span><div className="font-semibold">{day(a.actual_start)} → {day(a.actual_finish)}</div></div>
                <div><span className="text-muted-foreground">Approved progress</span><div className="font-bold">{pct(a.physical_pct)}</div></div>
                <div><span className="text-muted-foreground">Plan (approx.)</span><div className="font-semibold">{pct(a.planned_pct)}</div></div>
              </div>
              <ProgressBar actual={Number(a.physical_pct)} planned={Number(a.planned_pct)} />
              {q.isPending && <Loading />}
              {q.error && <QueryError error={q.error} />}
              {t && (
                <>
                  {t.claims_note && <Notice tone="info">{t.claims_note}</Notice>}
                  <section><h3 className="font-extrabold text-sm mb-1">Approved quantity entries</h3>
                    {t.quantity_entries.length === 0 ? <div className="text-muted-foreground">No approved quantities yet.</div> : (
                      <Table><TableHeader><TableRow><TableHead>Date</TableHead><TableHead>Resource</TableHead><TableHead className="text-right">Cumulative</TableHead><TableHead className="text-right">Increment</TableHead></TableRow></TableHeader>
                        <TableBody>{t.quantity_entries.map((e: any) => (
                          <TableRow key={e.entry_seq}><TableCell>{day(e.as_of_date)}</TableCell><TableCell>{e.resource_code}</TableCell><TableCell className="text-right font-mono">{num(e.cumulative_qty)}</TableCell>
                            <TableCell className="text-right font-mono">{num(e.incremental_qty)}{e.over_baseline ? <span className="ml-1 text-amber-600 font-bold" title={`${num(e.overrun_pct)}% over baseline`}>▲</span> : null}</TableCell></TableRow>))}</TableBody></Table>
                    )}</section>
                  <section><h3 className="font-extrabold text-sm mb-1">Issues</h3>
                    {t.issues.length === 0 ? <div className="text-muted-foreground">None.</div> : t.issues.map((i: any) => <div key={i.issue_id} className="border-b border-border/60 py-1"><b>{i.title}</b> · {i.severity} · {i.status}{i.blocks_work && i.status === 'ACTIVE' ? ' · blocking' : ''}</div>)}</section>
                  {t.claims.length > 0 && (
                    <section><h3 className="font-extrabold text-sm mb-1">Claims</h3>
                      {t.claims.map((c: any) => <div key={c.event_id} className="border-b border-border/60 py-1">{day(c.event_date)} · {c.status} · {c.raw_claim_text}</div>)}</section>
                  )}
                  <section><h3 className="font-extrabold text-sm mb-1">Schedule versions</h3>
                    {t.versions.map((v: any) => <div key={v.version_id} className="py-0.5">v{v.version_no} ({v.status}) · {day(v.baseline_start)} → {day(v.baseline_finish)}</div>)}
                    {t.lineage.length > 0 && <div className="text-muted-foreground mt-1">Lineage: {t.lineage.map((l: any) => l.relation).join(', ')} — progress is never transferred automatically.</div>}</section>
                </>
              )}
            </div>
          </>
        )}
      </DrawerContent>
    </Drawer>
  );
}

function ActivityTable() {
  const { projectId, viewVersionId } = useV2Project();
  const [text, setText] = useState('');
  const [disc, setDisc] = useState('');
  const [state, setState] = useState('');
  const [offset, setOffset] = useState(0);
  const [sel, setSel] = useState<ActivityProgress | null>(null);
  const q = useQuery({
    queryKey: ['v2', 'activities-progress', projectId, viewVersionId, disc, state, offset],
    queryFn: ({ signal }) => dashboardApi.activities(projectId, { version_id: viewVersionId, discipline: disc || undefined, state: state || undefined, limit: PAGE, offset } as any, { signal }),
    retry: false, placeholderData: (p) => p,
  });
  const items = (q.data?.items ?? []).filter((a) => !text.trim() || `${a.external_activity_id} ${a.activity_name}`.toLowerCase().includes(text.trim().toLowerCase()));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative"><Search className="w-3.5 h-3.5 absolute left-2.5 top-3 text-muted-foreground" /><Input className="pl-8 w-64" placeholder="Filter this page by id or name…" aria-label="Filter activities" value={text} onChange={(e) => setText(e.target.value)} /></div>
        <select className={F('w-52')} aria-label="Discipline" value={disc} onChange={(e) => { setDisc(e.target.value); setOffset(0); }}><option value="">All disciplines</option>{DISCIPLINES.map((d) => <option key={d.code} value={d.code}>{d.name}</option>)}</select>
        <select className={F('w-44')} aria-label="State" value={state} onChange={(e) => { setState(e.target.value); setOffset(0); }}><option value="">All states</option><option value="NOT_STARTED">Not started</option><option value="IN_PROGRESS">In progress</option><option value="COMPLETED">Completed</option></select>
      </div>
      {q.isPending && <Loading />}
      {q.error && <QueryError error={q.error} onRetry={() => q.refetch()} />}
      {q.data && items.length === 0 && <EmptyState icon={Layers} title="No activities" description="Nothing matches these filters." />}
      {items.length > 0 && (
        <div className="rounded-xl border border-border overflow-x-auto bg-card">
          <Table data-testid="activities-table">
            <TableHeader><TableRow><TableHead>Activity</TableHead><TableHead>Discipline</TableHead><TableHead>Baseline</TableHead><TableHead className="w-48">Approved progress</TableHead><TableHead>State</TableHead></TableRow></TableHeader>
            <TableBody>
              {items.map((a) => (
                <TableRow key={a.activity_uid} className="cursor-pointer" onClick={() => setSel(a)} data-testid="activity-row" data-activity={a.external_activity_id}>
                  <TableCell><div className="font-bold">{a.external_activity_id}</div><div className="text-[11px] text-muted-foreground max-w-xs truncate">{a.activity_name}</div></TableCell>
                  <TableCell className="text-[11px]">{a.discipline_code.replace(/_/g, ' ')}</TableCell>
                  <TableCell className="text-[11px] whitespace-nowrap">{day(a.baseline_start)} → {day(a.baseline_finish)}</TableCell>
                  <TableCell><div className="flex items-center gap-2"><div className="flex-1"><ProgressBar actual={Number(a.physical_pct)} planned={Number(a.planned_pct)} /></div><span className="font-mono text-[11px] w-12 text-right">{pct(a.physical_pct)}</span></div></TableCell>
                  <TableCell><ExecutionStateBadge state={a.execution_state} size="sm" />{a.any_overrun && <span className="ml-1 text-amber-600 text-[10px] font-bold" title="Approved quantity above baseline">over</span>}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{q.data ? `Showing ${q.data.offset + 1}–${q.data.offset + q.data.items.length}` : ''}</span>
        <div className="flex gap-2"><Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className="cursor-pointer">Previous</Button><Button size="sm" variant="outline" disabled={!q.data?.next_offset} onClick={() => setOffset(q.data!.next_offset!)} className="cursor-pointer">Next</Button></div>
      </div>
      <ActivityDrawer projectId={projectId} a={sel} onClose={() => setSel(null)} />
    </div>
  );
}

function WbsTable() {
  const { projectId, viewVersionId } = useV2Project();
  const q = useQuery({ queryKey: ['v2', 'wbs', projectId, viewVersionId], queryFn: () => dashboardApi.wbs(projectId, { version_id: viewVersionId } as any), retry: false });
  if (q.isPending) return <Loading />;
  if (q.error) return <QueryError error={q.error} onRetry={() => q.refetch()} />;
  return (
    <div className="rounded-xl border border-border overflow-x-auto bg-card">
      <Table data-testid="wbs-table"><TableHeader><TableRow><TableHead>WBS</TableHead><TableHead>Type</TableHead><TableHead className="text-right">Activities</TableHead><TableHead className="w-56">Approved progress</TableHead></TableRow></TableHeader>
        <TableBody>{q.data.items.map((w) => (
          <TableRow key={w.wbs_id}><TableCell><span style={{ paddingLeft: `${Number(w.level) * 14}px` }} className="font-semibold">{w.wbs_name}</span><span className="ml-2 text-[10px] font-mono text-muted-foreground">{w.wbs_code}</span></TableCell>
            <TableCell className="text-[11px]">{w.node_type}</TableCell><TableCell className="text-right font-mono">{w.activities}</TableCell>
            <TableCell><div className="flex items-center gap-2"><div className="flex-1"><ProgressBar actual={Number(w.physical_pct)} planned={Number(w.planned_pct)} /></div><span className="font-mono text-[11px] w-12 text-right">{pct(w.physical_pct)}</span></div></TableCell></TableRow>))}</TableBody></Table>
    </div>
  );
}

export default function ActivitiesPage() {
  const { currentProject } = useProjectState();
  const { viewingHistorical, activeVersionId } = useV2Project();
  return (
    <div className="space-y-6" data-testid="activities-page">
      <PageHeader project={`${currentProject?.name} (${currentProject?.code})`} title="WBS & Activities" subtitle="Approved progress by activity and by WBS node. Open an activity to see its approved entries, issues and history." />
      {viewingHistorical && <Notice tone="warn">Viewing a historical schedule version (read-only).</Notice>}
      {!activeVersionId && <Notice tone="info">This project has no active schedule yet.</Notice>}
      <Tabs defaultValue="activities">
        <TabsList><TabsTrigger value="activities">Activities</TabsTrigger><TabsTrigger value="wbs">WBS</TabsTrigger></TabsList>
        <TabsContent value="activities"><ActivityTable /></TabsContent>
        <TabsContent value="wbs"><WbsTable /></TabsContent>
      </Tabs>
      <ApproxNote />
    </div>
  );
}
