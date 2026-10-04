import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpenCheck, CheckCircle2, GitBranch, Lightbulb, Link2, Loader2, Plus, Radar as RadarIcon, Repeat, Search, TrendingDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { useProject, useProjectState } from '@/context/ProjectContext';
import { issuesApi, memoryApi, memoryIntelApi, type CaptureItem, type CategoryPattern, type Issue, type IssueCategoryCode, type RadarItem } from '@/api/prototype';

const FIELD = 'w-full h-9 rounded-lg border border-slate-300 dark:border-[#1E3A5F] bg-white dark:bg-[#0A2340] px-2.5 text-sm focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-[#FF7A18]';

function GroupDialog({ pattern, issues, open, onClose, onDone }: { pattern: CategoryPattern | null; issues: Issue[]; open: boolean; onClose: () => void; onDone: () => void }) {
  const { currentProject, currentScheduleVersion } = useProject();
  const candidates = useMemo(() => issues.filter((i) => pattern && i.category_code === pattern.category_code && !i.root_cause_id), [issues, pattern]);
  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  React.useEffect(() => { if (open) { setTitle(pattern ? `Repeated ${pattern.category_name.toLowerCase()}` : ''); setSummary(''); setPicked(new Set(candidates.map((c) => c.issue_id))); setError(null); } }, [open, pattern, candidates]);
  const create = useMutation({
    mutationFn: () => issuesApi.createRootCause(currentProject.id, currentScheduleVersion.id, { category_code: pattern!.category_code as IssueCategoryCode, title: title.trim(), summary: summary.trim() || null, issue_ids: [...picked] }),
    onSuccess: () => { onDone(); onClose(); },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : 'Could not save the root cause'),
  });
  if (!pattern) return null;
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader><DialogTitle>Identify a root cause</DialogTitle><DialogDescription>Group the {pattern.category_name.toLowerCase()} issues that share one underlying cause.</DialogDescription></DialogHeader>
        <div className="space-y-3 text-xs">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Root cause title" />
          <Textarea rows={2} value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="Why is this happening? (optional)" aria-label="Root cause summary" />
          <div className="space-y-1.5 max-h-48 overflow-y-auto">
            {candidates.length === 0 && <div className="text-muted-foreground">All issues of this category are already grouped.</div>}
            {candidates.map((i) => (
              <label key={i.issue_id} className="flex items-start gap-2 cursor-pointer">
                <input type="checkbox" className="mt-0.5 accent-[#FF7A18]" checked={picked.has(i.issue_id)} onChange={(e) => setPicked((p) => { const n = new Set(p); e.target.checked ? n.add(i.issue_id) : n.delete(i.issue_id); return n; })} />
                <span><b>{i.activity_id ?? i.stage_name}</b> — {i.title} <span className="text-muted-foreground">({i.status === 'ACTIVE' ? 'open' : 'resolved'})</span></span>
              </label>
            ))}
          </div>
          {error && <div role="alert" className="text-rose-600">{error}</div>}
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose} className="cursor-pointer">Cancel</Button><Button onClick={() => create.mutate()} disabled={title.trim().length < 3 || create.isPending} className="gap-1.5 cursor-pointer">{create.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Link2 className="w-4 h-4" />} Save root cause</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RecordLessonDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const { currentProject } = useProject();
  const cats = useQuery({ queryKey: ['v7', 'issue-categories', currentProject.id], queryFn: () => issuesApi.categories(currentProject.id), staleTime: 300_000 });
  const [f, setF] = useState({ category: '', title: '', narrative: '', root_cause: '', resolution: '', outcome: '', share: false });
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => memoryApi.record(currentProject.id, { category_code: f.category as IssueCategoryCode, title: f.title.trim(), narrative: f.narrative.trim(), root_cause: f.root_cause.trim() || null, resolution: f.resolution.trim() || null, outcome: f.outcome.trim() || null, share_with_organisation: f.share }),
    onSuccess: () => { onDone(); onClose(); setF({ category: '', title: '', narrative: '', root_cause: '', resolution: '', outcome: '', share: false }); },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : 'Could not record the lesson'),
  });
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader><DialogTitle>Record a lesson</DialogTitle><DialogDescription>Add knowledge that did not come from a tracked issue.</DialogDescription></DialogHeader>
        <div className="space-y-2.5 text-xs">
          <select className={FIELD} value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} aria-label="Category"><option value="">Category…</option>{(cats.data ?? []).map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}</select>
          <Input placeholder="Title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} aria-label="Title" />
          <Textarea rows={2} placeholder="What happened" value={f.narrative} onChange={(e) => setF({ ...f, narrative: e.target.value })} aria-label="What happened" />
          <Input placeholder="Root cause" value={f.root_cause} onChange={(e) => setF({ ...f, root_cause: e.target.value })} aria-label="Root cause" />
          <Input placeholder="Resolution" value={f.resolution} onChange={(e) => setF({ ...f, resolution: e.target.value })} aria-label="Resolution" />
          <Input placeholder="Outcome" value={f.outcome} onChange={(e) => setF({ ...f, outcome: e.target.value })} aria-label="Outcome" />
          <label className="flex items-center gap-2 cursor-pointer"><input type="checkbox" className="accent-[#FF7A18]" checked={f.share} onChange={(e) => setF({ ...f, share: e.target.checked })} /> Share with other projects</label>
          {error && <div role="alert" className="text-rose-600">{error}</div>}
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose} className="cursor-pointer">Cancel</Button><Button onClick={() => save.mutate()} disabled={!f.category || f.title.trim().length < 3 || f.narrative.trim().length < 3 || save.isPending} className="cursor-pointer">Save</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const dayLabel = (n: number) => `${n % 1 === 0 ? n : n.toFixed(1)} day${n === 1 ? '' : 's'}`;

/** Memory that looks forward: the lessons this organisation has already paid for, matched to the work that is under way or about to start. */
function LessonsRadar() {
  const { currentProject } = useProject();
  const [horizon, setHorizon] = useState(180);
  const radar = useQuery({ queryKey: ['v7', 'memory-radar', currentProject.id, horizon], queryFn: () => memoryIntelApi.radar(currentProject.id, horizon), retry: false });
  const r = radar.data;
  return (
    <Card data-testid="lessons-radar">
      <CardHeader className="pb-2 flex flex-row items-start justify-between gap-2">
        <div>
          <CardTitle className="text-sm flex items-center gap-2"><RadarIcon className="w-4 h-4 text-[#FF7A18]" /> Lessons that apply now</CardTitle>
          <p className="text-[11px] text-muted-foreground mt-1">Past incidents (this project's and those other projects chose to share) matched to the work under way or starting soon, before they are repeated.</p>
        </div>
        <select className={cn(FIELD, 'w-44 shrink-0')} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} aria-label="Look ahead">
          {[30, 90, 180, 365].map((d) => <option key={d} value={d}>Next {d} days</option>)}
        </select>
      </CardHeader>
      <CardContent className="space-y-3">
        {radar.isLoading && <div className="text-xs text-muted-foreground flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Matching lessons to the schedule…</div>}
        {radar.isError && <div role="alert" className="text-xs text-rose-600">{radar.error instanceof Error ? radar.error.message : 'Could not load the radar'}</div>}
        {r && (
          <>
            <div className="text-[11px] text-muted-foreground" data-testid="radar-summary">
              {r.activities_with_lessons} of {r.activities_in_window} activities in the window have a relevant lesson · {r.lessons_considered} lessons considered · matched by {r.retrieval_mode}
              {r.reference_date ? ` · from ${r.reference_date}` : ''}
            </div>
            {r.note && <div className="text-[11px] p-2 rounded-lg border border-border bg-muted/40">{r.note}</div>}
            {r.items.length === 0 && !r.note && <EmptyState title="No recorded lesson applies yet" description="Lessons appear here as soon as the organisation keeps them: resolve an issue and keep it as a lesson below." />}
            {r.items.map((it) => <RadarCard key={it.activity_id} it={it} />)}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function RadarCard({ it }: { it: RadarItem }) {
  return (
    <div className="rounded-xl border border-slate-200 dark:border-[#214766] p-3 text-xs space-y-2" data-testid="radar-item" data-activity={it.activity_id}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-extrabold text-sm">{it.activity_id} · {it.activity_name}</div>
        <div className="flex items-center gap-1.5">
          <span className={cn('px-2 py-0.5 rounded-md border text-[10px] font-bold', it.timing === 'IN_PROGRESS' ? 'border-[#FF7A18] text-[#C2570C] dark:text-[#FF9A4D]' : 'border-slate-300 text-muted-foreground')}>
            {it.timing === 'IN_PROGRESS' ? 'Under way' : `Starts in ${it.starts_in_days} day${it.starts_in_days === 1 ? '' : 's'}`}
          </span>
          {it.expected_delay && (
            <span className="px-2 py-0.5 rounded-md border border-rose-300 text-rose-700 dark:text-rose-300 text-[10px] font-bold" title={`${it.expected_delay.samples} recorded incident(s)`}>
              Last time: {dayLabel(it.expected_delay.median_days)} of delay{it.expected_delay.samples > 1 ? ` (median of ${it.expected_delay.samples})` : ''}
            </span>
          )}
        </div>
      </div>
      <div className="text-muted-foreground">{it.stage_name ?? '—'} · planned {it.planned_start} to {it.planned_finish}</div>
      {it.preventive_actions.length > 0 && (
        <div className="rounded-lg bg-emerald-500/10 border border-emerald-400/40 p-2.5 space-y-1">
          <div className="font-bold flex items-center gap-1.5 text-emerald-800 dark:text-emerald-300"><Lightbulb className="w-3.5 h-3.5" /> What worked before</div>
          <ul className="list-disc pl-5 space-y-0.5">{it.preventive_actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </div>
      )}
      <div className="space-y-1.5">
        {it.lessons.map((l) => (
          <div key={l.memory_id} className="border-t border-slate-200/70 dark:border-[#214766]/70 pt-1.5" data-testid="radar-lesson">
            <div className="flex flex-wrap items-center gap-1.5">
              <b>{l.title}</b>
              <span className={cn('px-1.5 py-0.5 rounded border text-[10px] font-bold', l.from_other_project ? 'border-blue-300 text-blue-700 dark:text-blue-300' : 'border-slate-300 text-muted-foreground')}>
                {l.from_other_project ? `Shared by ${l.project_name}` : 'This project'}
              </span>
              {l.delay_days != null && <span className="text-muted-foreground">{dayLabel(l.delay_days)} of delay</span>}
            </div>
            {l.root_cause && <div><b>Cause:</b> {l.root_cause}</div>}
            {l.outcome && <div><b>Outcome:</b> {l.outcome}</div>}
            <div className="text-[10px] text-muted-foreground">Why it matched ({Math.round(l.score * 100)}%): {l.why_matched.join(' · ')}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** The loop that keeps memory alive: resolved issues that have not become a lesson yet. */
function CaptureQueue({ onDone }: { onDone: () => void }) {
  const { currentProject } = useProject();
  const q = useQuery({ queryKey: ['v7', 'memory-capture', currentProject.id], queryFn: () => memoryIntelApi.captureQueue(currentProject.id), retry: false });
  const [open, setOpen] = useState<string | null>(null);
  const [text, setText] = useState('');
  const [outcome, setOutcome] = useState('');
  const [share, setShare] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const capture = useMutation({
    mutationFn: (it: CaptureItem) => memoryIntelApi.capture(currentProject.id, it.issue_id, { lessons_learned: text.trim(), outcome: outcome.trim() || undefined, share_with_organisation: share }),
    onSuccess: () => { setOpen(null); setError(null); qc.invalidateQueries({ queryKey: ['v7', 'memory-capture'] }); onDone(); },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : 'Could not keep the lesson'),
  });
  const items = q.data?.items ?? [];
  return (
    <Card data-testid="capture-queue">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-[#FF7A18]" /> Resolved issues waiting to become lessons ({items.length})</CardTitle>
        <p className="text-[11px] text-muted-foreground mt-1">Every resolved issue is a lesson the organisation has already paid for. Keep it, and it will warn the next project that meets the same work.</p>
      </CardHeader>
      <CardContent className="space-y-2">
        {q.isLoading && <div className="text-xs text-muted-foreground">Loading…</div>}
        {!q.isLoading && items.length === 0 && <div className="text-xs text-muted-foreground">Nothing waiting: every resolved issue has been kept as a lesson.</div>}
        {items.map((it) => (
          <div key={it.issue_id} className="rounded-xl border border-slate-200 dark:border-[#214766] p-3 text-xs space-y-1.5" data-testid="capture-item" data-title={it.title}>
            <div className="flex items-start justify-between gap-2">
              <div><div className="font-extrabold text-sm">{it.title}</div>
                <div className="text-muted-foreground">{it.category_code.replace(/_/g, ' ').toLowerCase()} · {it.severity.toLowerCase()}{it.activity_id ? ` · ${it.activity_id}` : ''}{it.impact_days_actual != null ? ` · ${dayLabel(it.impact_days_actual)} of delay` : ''}</div></div>
              {open !== it.issue_id && <Button size="sm" variant="outline" className="cursor-pointer shrink-0" aria-label={`Keep ${it.title} as a lesson`}
                onClick={() => { setOpen(it.issue_id); setText(it.resolution_notes ?? ''); setOutcome(''); setShare(true); setError(null); }}>Keep as lesson</Button>}
            </div>
            {it.resolution_notes && <div><b>How it was resolved:</b> {it.resolution_notes}</div>}
            {open === it.issue_id && (
              <div className="space-y-2 pt-1" data-testid="capture-form">
                <div className="text-[11px] font-bold">What should the next project know?</div>
                <Textarea rows={3} aria-label="Lesson" value={text} onChange={(e) => setText(e.target.value)} />
                <Input aria-label="Outcome" placeholder="Outcome (optional): e.g. recovered in two weeks" value={outcome} onChange={(e) => setOutcome(e.target.value)} />
                <label className="flex items-center gap-2 text-[11px]"><input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} /> Share with the organisation's other projects</label>
                {error && <div role="alert" className="text-rose-600">{error}</div>}
                <div className="flex gap-2">
                  <Button size="sm" className="cursor-pointer" disabled={capture.isPending || text.trim().length < 5} onClick={() => capture.mutate(it)} data-testid="save-lesson">{capture.isPending ? 'Saving…' : 'Save lesson'}</Button>
                  <Button size="sm" variant="outline" className="cursor-pointer" onClick={() => setOpen(null)}>Cancel</Button>
                </div>
              </div>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

/** What the organisation has learned, in numbers: where delay keeps coming from, how long it costs, and whether lessons are being kept. */
function MemoryInsightsCard() {
  const { currentProject } = useProject();
  const ins = useQuery({ queryKey: ['v7', 'memory-insights', currentProject.id], queryFn: () => memoryIntelApi.insights(currentProject.id), retry: false });
  const i = ins.data;
  if (!i) return null;
  const stat = (label: string, value: React.ReactNode, sub?: string) => (
    <div className="rounded-xl border border-slate-200 dark:border-[#214766] p-3"><div className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-xl font-black">{value}</div>{sub && <div className="text-[10px] text-muted-foreground">{sub}</div>}</div>
  );
  return (
    <Card data-testid="memory-insights">
      <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><BookOpenCheck className="w-4 h-4 text-[#FF7A18]" /> What the organisation has learned</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
          {stat('Lessons available', i.lessons.visible, `${i.lessons.own_project} from this project · ${i.lessons.from_other_projects} shared by others`)}
          {stat('Resolved issues kept', i.capture.rate_pct == null ? '—' : `${i.capture.rate_pct}%`, `${i.capture.captured} of ${i.capture.resolved_issues} became lessons`)}
          {stat('Waiting to be kept', i.capture.waiting, 'resolved issues without a lesson')}
          {stat('Recurring categories', i.categories.filter((c) => c.recurring).length, 'seen more than once, or on more than one project')}
        </div>
        {i.categories.length > 0 && (
          <div className="text-xs space-y-1">
            {i.categories.map((c) => (
              <div key={c.category_code} className="flex items-center justify-between gap-2 border-t border-slate-200/70 dark:border-[#214766]/70 pt-1">
                <span className="font-semibold">{c.category_name}{c.recurring && <span className="ml-2 text-[10px] font-bold text-[#C2570C] dark:text-[#FF9A4D]">recurring</span>}</span>
                <span className="text-muted-foreground">{c.lessons} lesson{c.lessons === 1 ? '' : 's'} · {c.projects} project{c.projects === 1 ? '' : 's'}{c.median_delay_days != null ? ` · typically ${dayLabel(c.median_delay_days)} lost` : ''}</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Supervisor view: where do delays keep coming from (patterns, root causes), and what has this organisation learned. */
export default function RootCauseMemory() {
  const { currentProject, currentScheduleVersion } = useProject();
  // v2: the Project Manager reads root causes and memory (VIEW_MONITORING) but only reviewers change them (MANAGE_BLOCKERS)
  const canManage = useProjectState().can('MANAGE_BLOCKERS');
  const queryClient = useQueryClient();
  const [grouping, setGrouping] = useState<CategoryPattern | null>(null);
  const [recording, setRecording] = useState(false);
  const [q, setQ] = useState('');
  const [cat, setCat] = useState('');

  const analysis = useQuery({ queryKey: ['v7', 'root-cause', currentProject.id, currentScheduleVersion.id], queryFn: () => issuesApi.analysis(currentProject.id, currentScheduleVersion.id), retry: false });
  const allIssues = useQuery({ queryKey: ['v7', 'issues', currentProject.id, currentScheduleVersion.id, 'ALL'], queryFn: () => issuesApi.list(currentProject.id, currentScheduleVersion.id, { status: 'ALL' }), retry: false });
  const memory = useQuery({ queryKey: ['v7', 'memory', currentProject.id, q, cat], queryFn: () => memoryApi.browse(currentProject.id, { q, category: cat || undefined, limit: 30 }), retry: false });
  const refresh = () => { queryClient.invalidateQueries({ queryKey: ['v7', 'memory-radar'] }); queryClient.invalidateQueries({ queryKey: ['v7', 'memory-insights'] }); queryClient.invalidateQueries({ queryKey: ['v7', 'root-cause'] }); queryClient.invalidateQueries({ queryKey: ['v7', 'issues'] }); queryClient.invalidateQueries({ queryKey: ['v7', 'memory'] }); };

  const a = analysis.data;
  const maxCount = Math.max(1, ...(a?.categories ?? []).map((c) => c.issue_count));

  return (
    <div className="space-y-6">
      <div>
        <div className="text-[11px] font-mono font-bold text-primary">{currentProject.name} ({currentProject.code})</div>
        <h1 className="text-2xl font-extrabold tracking-tight flex items-center gap-2"><GitBranch className="w-6 h-6 text-[#FF7A18]" /> Root Cause & Memory</h1>
        <p className="text-xs font-semibold text-muted-foreground mt-0.5">Counts and groupings of the issues reported on this project, and the lessons the organisation has kept.</p>
      </div>

      {analysis.isLoading && <div className="text-xs text-muted-foreground flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Analysing issues…</div>}
      {analysis.isError && <div role="alert" className="text-xs text-rose-600">{analysis.error instanceof Error ? analysis.error.message : 'Could not load the analysis'}</div>}

      {a && (
        <div className="grid grid-cols-1 xl:grid-cols-5 gap-6">
          <Card className="xl:col-span-3">
            <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><Repeat className="w-4 h-4 text-[#FF7A18]" /> Issues by category ({a.total_issues} total · {a.open_issues} open)</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              {a.categories.length === 0 && <EmptyState title="No issues reported" description="Patterns appear here once issues are reported." />}
              {a.categories.map((c) => (
                <div key={c.category_code} className={cn('rounded-xl border p-3 text-xs space-y-1.5', c.is_repeated_pattern ? 'border-[#FF7A18]/60 bg-[#FF7A18]/5' : 'border-slate-200 dark:border-[#214766]')} data-testid="category-pattern">
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-extrabold text-sm">{c.category_name}</div>
                    <div className="font-mono font-bold">{c.issue_count}</div>
                  </div>
                  <div className="h-1.5 rounded-full bg-muted overflow-hidden"><div className={cn('h-full rounded-full', c.is_repeated_pattern ? 'bg-[#FF7A18]' : 'bg-slate-400')} style={{ width: `${(c.issue_count / maxCount) * 100}%` }} /></div>
                  <div className="text-muted-foreground">{c.open_count} open · {c.activity_count} activit{c.activity_count === 1 ? 'y' : 'ies'} · {c.stage_count} stage{c.stage_count === 1 ? '' : 's'} · ~{Math.round(c.expected_delay_days)} days of expected delay</div>
                  {c.is_repeated_pattern && (
                    <div className="flex items-center justify-between gap-2 pt-1">
                      <span className="font-bold text-[#C2570C] dark:text-[#FF9A4D]">Repeated pattern: likely a common root cause across {c.activity_count} activities.</span>
                      {canManage && c.linked_to_root_cause < c.issue_count && <Button size="sm" variant="outline" onClick={() => setGrouping(c)} className="gap-1.5 cursor-pointer shrink-0"><Link2 className="w-3.5 h-3.5" /> Group under a root cause</Button>}
                    </div>
                  )}
                </div>
              ))}
            </CardContent>
          </Card>

          <div className="xl:col-span-2 space-y-6">
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">Identified root causes</CardTitle></CardHeader>
              <CardContent className="space-y-2.5">
                {a.root_causes.length === 0 && <div className="text-xs text-muted-foreground">None yet. Group a repeated pattern to record one.</div>}
                {a.root_causes.map((r) => (
                  <div key={r.root_cause_id} className="text-xs rounded-xl border border-slate-200 dark:border-[#214766] p-3 space-y-1" data-testid="root-cause">
                    <div className="font-extrabold">{r.title}</div>
                    {r.summary && <div className="text-muted-foreground">{r.summary}</div>}
                    <div>{r.issue_count} issues ({r.open_issue_count} open) · {r.activity_ids.length} activities · {r.stage_names.length} stages</div>
                  </div>
                ))}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><TrendingDown className="w-4 h-4 text-[#FF7A18]" /> Stages carrying the most open delay</CardTitle></CardHeader>
              <CardContent className="space-y-1.5 text-xs">
                {a.delayed_stages.length === 0 && <div className="text-muted-foreground">No stage has issues.</div>}
                {a.delayed_stages.slice(0, 5).map((s) => <div key={s.stage_id} className="flex justify-between gap-2"><span className="truncate">{s.stage_name}</span><span className="font-mono font-bold shrink-0">{Math.round(s.open_expected_delay_days)} d · {s.open_count} open</span></div>)}
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      <MemoryInsightsCard />
      <LessonsRadar />
      {canManage && <CaptureQueue onDone={refresh} />}

      <Card>
        <CardHeader className="pb-2 flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-sm flex items-center gap-2"><BookOpenCheck className="w-4 h-4 text-[#FF7A18]" /> Search the organisation's memory</CardTitle>
          {canManage && <Button size="sm" variant="outline" onClick={() => setRecording(true)} className="gap-1.5 cursor-pointer"><Plus className="w-3.5 h-3.5" /> Record a lesson</Button>}
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <div className="relative flex-1 min-w-[200px]"><Search className="w-3.5 h-3.5 absolute left-2.5 top-3 text-muted-foreground" /><Input className="pl-8" placeholder="Search past incidents (e.g. steel delivery delay)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search memory" /></div>
            <select className={cn(FIELD, 'w-56')} value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Category filter">
              <option value="">All categories</option>
              {(a?.categories ?? []).map((c) => <option key={c.category_code} value={c.category_code}>{c.category_name}</option>)}
              <option value="MATERIAL_DELIVERY_DELAY">Material delivery delay</option><option value="WEATHER">Weather disruption</option><option value="PERMIT_APPROVAL">Permit / approval delay</option><option value="TECHNICAL">Technical issue</option>
            </select>
          </div>
          {memory.isLoading && <div className="text-xs text-muted-foreground">Searching…</div>}
          {memory.data && memory.data.results.length === 0 && <div className="text-xs text-muted-foreground">No matching records.</div>}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {(memory.data?.results ?? []).map((r) => {
              const m = r.record.metadata;
              return (
                <div key={r.record.memory_id} className="rounded-xl border border-slate-200 dark:border-[#214766] p-3 text-xs space-y-1" data-testid="memory-record">
                  <div className="flex items-start justify-between gap-2">
                    <div className="font-extrabold text-sm">{r.record.title}</div>
                    <span className={cn('shrink-0 px-2 py-0.5 rounded-md border text-[10px] font-bold', m.shared_from_other_project ? 'border-blue-300 text-blue-700 dark:text-blue-300' : 'border-slate-300 dark:border-[#214766]')}>{m.shared_from_other_project ? `Shared · ${m.project_name}` : m.scope === 'ORGANISATION' ? 'Shared by this project' : 'This project'}</span>
                  </div>
                  {m.root_cause && <div><b>Cause:</b> {m.root_cause}</div>}
                  {m.resolution && <div><b>Resolution:</b> {m.resolution}</div>}
                  {m.outcome && <div><b>Outcome:</b> {m.outcome}</div>}
                  {m.delay_days != null && <div className="text-muted-foreground">{m.delay_days} days of delay</div>}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <GroupDialog pattern={grouping} issues={allIssues.data ?? []} open={!!grouping} onClose={() => setGrouping(null)} onDone={refresh} />
      <RecordLessonDialog open={recording} onClose={() => setRecording(false)} onDone={refresh} />
    </div>
  );
}
