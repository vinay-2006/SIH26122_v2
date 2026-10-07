import React, { useRef, useState } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Briefcase, Loader2, Plus, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/auth/AuthProvider';
import { useProjectState } from '@/context/ProjectContext';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { dashboardApi, projectsApi } from '@/v2/api/endpoints';
import { newIdempotencyKey } from '@/v2/api/http';
import type { Lifecycle, MyProject, Summary } from '@/v2/api/types';
import { ApproxNote, FIELD, Label, LifecyclePill, Loading, Notice, PageHeader, Panel, ProgressBar, QueryError, Stat, day, errText, pct, when, F } from '@/v2/ui';

const CODE_RE = /^[A-Z0-9][A-Z0-9_-]{2,31}$/;

function NewProjectDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const [f, setF] = useState({ project_code: '', project_name: '', description: '', client_name: '', project_type: '', location: '', planned_start: '', planned_finish: '', lifecycle_status: 'UPCOMING' as Lifecycle });
  const [err, setErr] = useState<string | null>(null);
  const key = useRef(newIdempotencyKey('project'));
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<any>) => setF((p) => ({ ...p, [k]: e.target.value }));
  const create = useMutation({
    mutationFn: () => {
      const body: Record<string, string> = {};
      Object.entries(f).forEach(([k, v]) => { if (String(v).trim()) body[k] = String(v).trim(); });
      return projectsApi.create(body as any, key.current);
    },
    onSuccess: (p) => { key.current = newIdempotencyKey('project'); onCreated(p.project_id); },
    onError: (e) => setErr(errText(e)),
  });
  const codeOk = CODE_RE.test(f.project_code);
  const valid = codeOk && f.project_name.trim().length >= 3 && (!f.planned_start || !f.planned_finish || f.planned_finish >= f.planned_start);
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>New project</DialogTitle>
          <DialogDescription>A new project starts with no schedule and zero progress. You become its Project Manager; import a schedule next.</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div><Label hint="(A–Z, 0–9, - _ ; 3–32 chars)">Project code</Label><Input aria-label="Project code" value={f.project_code} onChange={(e) => setF((p) => ({ ...p, project_code: e.target.value.toUpperCase() }))} placeholder="e.g. NRL-PH2" />
            {f.project_code && !codeOk && <div className="text-[10px] text-rose-600 mt-0.5">Use capital letters, digits, “-” or “_” (3–32 characters).</div>}</div>
          <div><Label>Project name</Label><Input aria-label="Project name" value={f.project_name} onChange={set('project_name')} /></div>
          <div><Label hint="(optional)">Client</Label><Input aria-label="Client" value={f.client_name} onChange={set('client_name')} /></div>
          <div><Label hint="(optional)">Project type</Label><Input aria-label="Project type" value={f.project_type} onChange={set('project_type')} /></div>
          <div><Label hint="(optional)">Location</Label><Input aria-label="Location" value={f.location} onChange={set('location')} /></div>
          <div><Label>Status</Label><select className={FIELD} aria-label="Lifecycle status" value={f.lifecycle_status} onChange={set('lifecycle_status')}><option value="UPCOMING">Upcoming</option><option value="ONGOING">Ongoing</option><option value="COMPLETED">Completed</option></select></div>
          <div><Label hint="(optional)">Planned start</Label><Input type="date" aria-label="Planned start" value={f.planned_start} onChange={set('planned_start')} /></div>
          <div><Label hint="(optional)">Planned finish</Label><Input type="date" aria-label="Planned finish" value={f.planned_finish} min={f.planned_start || undefined} onChange={set('planned_finish')} /></div>
          <div className="sm:col-span-2"><Label hint="(optional)">Description</Label><Textarea rows={2} aria-label="Description" value={f.description} onChange={set('description')} /></div>
        </div>
        {err && <Notice tone="bad">{err}</Notice>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="cursor-pointer">Cancel</Button>
          <Button onClick={() => { setErr(null); create.mutate(); }} disabled={!valid || create.isPending} className="gap-1.5 cursor-pointer">{create.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} Create project</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ProjectCard({ p, s, selected, onOpen }: { p: MyProject; s?: { data?: Summary; error?: unknown; pending: boolean }; selected: boolean; onOpen: () => void }) {
  const d = s?.data;
  const noSchedule = !!s?.error && (s.error as any).code === 'NO_ACTIVE_SCHEDULE';
  return (
    <button type="button" onClick={onOpen} data-testid="project-card" data-project-code={p.project_code}
      className={`text-left rounded-2xl border p-4 space-y-2.5 cursor-pointer transition-all bg-white/95 dark:bg-[#071A2D]/95 hover:border-[#FF7A18] shadow-md ${selected ? 'border-[#FF7A18]' : 'border-slate-200/80 dark:border-[#214766]'}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><div className="text-[10px] font-mono font-bold text-primary">{p.project_code}</div><div className="font-extrabold text-sm leading-snug">{p.project_name}</div>
          <div className="text-[11px] text-muted-foreground">{p.location ?? '—'}</div></div>
        <div className="flex flex-col items-end gap-1"><LifecyclePill value={p.lifecycle_status} />{p.record_status === 'ARCHIVED' && <span className="text-[10px] font-bold text-muted-foreground">ARCHIVED</span>}</div>
      </div>
      {s?.pending && <div className="text-[11px] text-muted-foreground">Loading progress…</div>}
      {noSchedule && <div className="text-[11px] font-semibold text-amber-700 dark:text-amber-400">No active schedule yet</div>}
      {d && (
        <>
          <div className="flex items-baseline justify-between text-xs"><span className="font-mono"><b>{pct(d.physical_pct)}</b> <span className="text-muted-foreground">actual · plan≈{pct(d.planned_pct)}</span></span><span className="text-[10px] text-muted-foreground">v{d.version.version_no} · data date {day(d.data_date)}</span></div>
          <ProgressBar actual={Number(d.physical_pct)} planned={Number(d.planned_pct)} />
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
            <span>{d.activities.completed}/{d.activities.total} complete</span><span><b className="text-foreground">{d.claims.pending_total}</b> claims pending</span>
            <span><b className="text-foreground">{d.issues.active}</b> issues</span>{d.issues.blocking > 0 && <span className="font-bold text-rose-600">{d.issues.blocking} blocking</span>}
          </div>
        </>
      )}
    </button>
  );
}

export default function PortfolioPage() {
  const { user } = useAuth();
  const { projects, setCurrentProjectId, currentProject } = useProjectState();
  const { selectProject, projectId } = useV2Project();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [text, setText] = useState('');
  const [life, setLife] = useState<'ALL' | Lifecycle>('ALL');
  const [creating, setCreating] = useState(false);
  const canCreate = !!user?.capabilities?.includes('CREATE_PROJECT');

  const rows = useQuery({ queryKey: ['v2', 'projects-full', user?.id], queryFn: ({ signal }) => projectsApi.list({ signal }), retry: false });
  const sums = useQueries({ queries: (rows.data ?? []).map((p) => ({ queryKey: ['v2', 'summary', p.project_id, undefined], queryFn: ({ signal }: any) => dashboardApi.summary(p.project_id, {}, { signal }), retry: false, staleTime: 30_000 })) });
  const sumById = new Map((rows.data ?? []).map((p, i) => [p.project_id, { data: sums[i]?.data, error: sums[i]?.error, pending: !!sums[i]?.isPending }]));
  const notes = useQuery({ queryKey: ['v2', 'notifications', projectId, 'recent'], queryFn: () => dashboardApi.notifications(projectId, { limit: 5 }), retry: false });

  const list = (rows.data ?? []).filter((p) => (life === 'ALL' || p.lifecycle_status === life) && `${p.project_name} ${p.project_code} ${p.location ?? ''}`.toLowerCase().includes(text.trim().toLowerCase()));
  const totals = (rows.data ?? []).reduce((a, p) => {
    const d = sumById.get(p.project_id)?.data;
    return { pending: a.pending + (d?.claims.pending_total ?? 0), active: a.active + (d?.issues.active ?? 0), blocking: a.blocking + (d?.issues.blocking ?? 0) };
  }, { pending: 0, active: 0, blocking: 0 });
  const open = (id: string) => { setCurrentProjectId(id); nav('/overview'); };

  return (
    <div className="space-y-6" data-testid="portfolio-page">
      <PageHeader title="Portfolio" subtitle="Every project you manage, with approved progress, pending work and blockers. Claim contents are never shown to Project Managers — only counts."
        actions={canCreate ? <Button onClick={() => setCreating(true)} className="gap-1.5 cursor-pointer" data-testid="new-project"><Plus className="w-4 h-4" /> New project</Button> : undefined} />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Projects" value={projects.length} />
        <Stat label="Claims pending (all)" value={totals.pending} sub="counts only" />
        <Stat label="Active issues" value={totals.active} sub={`${totals.blocking} blocking`} tone={totals.blocking > 0 ? 'bad' : undefined} />
        <Stat label="Signed in as" value={<span className="text-base">{user?.full_name}</span>} sub={user?.email} />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative"><Search className="w-3.5 h-3.5 absolute left-2.5 top-3 text-muted-foreground" /><Input className="pl-8 w-64" placeholder="Search projects…" aria-label="Search projects" value={text} onChange={(e) => setText(e.target.value)} /></div>
        <select className={F('w-44')} aria-label="Filter by status" value={life} onChange={(e) => setLife(e.target.value as any)}><option value="ALL">All statuses</option><option value="UPCOMING">Upcoming</option><option value="ONGOING">Ongoing</option><option value="COMPLETED">Completed</option></select>
      </div>
      {rows.isPending && <Loading what="Loading projects…" />}
      {rows.error && <QueryError error={rows.error} onRetry={() => rows.refetch()} />}
      {rows.data && list.length === 0 && <EmptyState icon={Briefcase} title="No projects match" description={rows.data.length ? 'Try a different search or status.' : 'You are not a member of any project yet.'} />}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {list.map((p) => <ProjectCard key={p.project_id} p={p} s={sumById.get(p.project_id)} selected={p.project_id === currentProject?.id} onOpen={() => open(p.project_id)} />)}
      </div>
      <ApproxNote />
      <Panel title="Recent notifications" description={`For ${currentProject?.name ?? 'the selected project'}`}>
        {notes.isPending && <Loading />}
        {notes.data?.items.length === 0 && <div className="text-xs text-muted-foreground">Nothing new.</div>}
        {notes.data?.items.map((n) => <div key={n.notification_id} className="text-xs flex justify-between gap-3 border-b border-border/60 pb-1.5 last:border-0"><span className="font-semibold">{n.title}</span><span className="text-muted-foreground shrink-0">{when(n.created_at)}</span></div>)}
      </Panel>
      <NewProjectDialog open={creating} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); qc.invalidateQueries({ queryKey: ['v2'] }); selectProject(id); nav('/schedule'); }} />
    </div>
  );
}
