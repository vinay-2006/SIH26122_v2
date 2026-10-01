import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertOctagon,
  BookOpenCheck,
  CheckCircle2,
  Clock,
  History,
  Loader2,
  Paperclip,
  Send,
  ShieldAlert,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { EmptyState } from '@/components/ui/empty-state';
import { cn } from '@/lib/utils';
import { useProject } from '@/context/ProjectContext';
import {
  issuesApi,
  memoryApi,
  type Issue,
  type IssueCategoryCode,
  type IssueSeverity,
  type MemoryResultDto,
} from '@/api/prototype';

const SEVERITIES: { value: IssueSeverity; label: string }[] = [
  { value: 'LOW', label: 'Low — minor, no schedule effect' },
  { value: 'MEDIUM', label: 'Medium — may slip the activity' },
  { value: 'HIGH', label: 'High — will delay the activity' },
  { value: 'CRITICAL', label: 'Critical — stops work / safety' },
];

const SEVERITY_TONE: Record<IssueSeverity, string> = {
  LOW: 'bg-slate-100 text-slate-700 border-slate-300 dark:bg-[#0B2742] dark:text-slate-300 dark:border-[#214766]',
  MEDIUM: 'bg-amber-50 text-amber-800 border-amber-300 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800',
  HIGH: 'bg-orange-50 text-orange-800 border-orange-300 dark:bg-orange-950/40 dark:text-orange-300 dark:border-orange-800',
  CRITICAL: 'bg-rose-50 text-rose-800 border-rose-300 dark:bg-rose-950/40 dark:text-rose-300 dark:border-rose-800',
};

const FIELD = 'w-full h-9 rounded-lg border border-slate-300 dark:border-[#1E3A5F] bg-white dark:bg-[#0A2340] px-2.5 text-sm text-slate-900 dark:text-[#F5F7FA] focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-[#FF7A18]';

const today = () => new Date().toISOString().slice(0, 10);

function Label({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <label className="block text-[11px] font-bold text-[#071A2D] dark:text-[#F5F7FA] mb-1">
      {children}
      {hint && <span className="ml-1 font-normal text-muted-foreground">{hint}</span>}
    </label>
  );
}

/** Past incidents the memory finds for what the engineer is describing: the retrieval in the reporting workflow. */
function SimilarIncidents({ results, loading }: { results: MemoryResultDto[]; loading: boolean }) {
  if (loading) return <div className="text-[11px] text-muted-foreground flex items-center gap-1.5"><Loader2 className="w-3 h-3 animate-spin" /> Checking institutional memory…</div>;
  if (results.length === 0) return null;
  return (
    <div className="rounded-xl border border-blue-300 dark:border-blue-900 bg-blue-50/60 dark:bg-blue-950/20 p-3 space-y-2" data-testid="similar-incidents">
      <div className="flex items-center gap-1.5 text-[11px] font-extrabold text-blue-800 dark:text-blue-300"><History className="w-3.5 h-3.5" /> Similar past incidents</div>
      {results.slice(0, 3).map((r) => {
        const m = r.record.metadata;
        return (
          <div key={r.record.memory_id} className="text-[11px] space-y-0.5 border-t border-blue-200/70 dark:border-blue-900/70 pt-2 first:border-0 first:pt-0">
            <div className="font-bold">{r.record.title}{m.shared_from_other_project && m.project_name ? <span className="font-normal text-muted-foreground"> — from {m.project_name}</span> : null}</div>
            {m.root_cause && <div><b>Cause:</b> {m.root_cause}</div>}
            {m.resolution && <div><b>Resolution:</b> {m.resolution}</div>}
            {m.outcome && <div><b>Outcome:</b> {m.outcome}</div>}
          </div>
        );
      })}
    </div>
  );
}

function ReportForm({ onReported }: { onReported: () => void }) {
  const { currentProject, currentScheduleVersion, progress } = useProject();
  const [category, setCategory] = useState<IssueCategoryCode | ''>('');
  const [severity, setSeverity] = useState<IssueSeverity>('MEDIUM');
  const [stageId, setStageId] = useState('');
  const [activityId, setActivityId] = useState('');
  const [date, setDate] = useState(today());
  const [duration, setDuration] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [blocks, setBlocks] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const categories = useQuery({ queryKey: ['v7', 'issue-categories', currentProject.id], queryFn: () => issuesApi.categories(currentProject.id), staleTime: 300_000 });

  const stages = progress?.stages ?? [];
  const activities = useMemo(() => {
    const pool = stageId ? stages.filter((s) => s.stage_id === stageId) : stages;
    return pool.flatMap((s) => s.activities.map((a) => ({ ...a, stageName: s.stage_name })));
  }, [stages, stageId]);

  // retrieval while the engineer describes the problem (debounced; never blocks the form)
  const [similar, setSimilar] = useState<MemoryResultDto[]>([]);
  const [searching, setSearching] = useState(false);
  useEffect(() => {
    setSimilar([]);
    if (!category || `${title} ${description}`.trim().length < 6) return;
    let cancelled = false;
    setSearching(true);
    const t = window.setTimeout(async () => {
      try {
        const res = await memoryApi.forIssue(currentProject.id, {
          category_code: category,
          query: `${title} ${description}`.trim(),
          activity_id: activityId || null,
          stage_id: stageId || null,
          top_k: 3,
        });
        if (!cancelled) setSimilar(res.results);
      } catch {
        if (!cancelled) setSimilar([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 500);
    return () => { cancelled = true; window.clearTimeout(t); setSearching(false); };
  }, [category, title, description, activityId, stageId, currentProject.id]);

  const submit = useMutation({
    mutationFn: async () => {
      const issue = await issuesApi.report(currentProject.id, currentScheduleVersion.id, {
        activity_id: activityId || null,
        stage_id: activityId ? null : stageId || null,
        category_code: category as IssueCategoryCode,
        title: title.trim(),
        description: description.trim(),
        severity,
        reported_date: date || null,
        expected_duration_days: duration === '' ? null : Number(duration),
        blocks_work: blocks,
      });
      for (const f of files) await issuesApi.addEvidence(currentProject.id, currentScheduleVersion.id, issue.issue_id, f);
      return issue;
    },
    onSuccess: (issue) => {
      setMessage({ ok: true, text: `Reported: “${issue.title}” on ${issue.activity_id ?? issue.stage_name}.` });
      setTitle(''); setDescription(''); setDuration(''); setBlocks(false); setFiles([]); setActivityId(''); setStageId(''); setCategory(''); setSeverity('MEDIUM'); setDate(today());
      onReported();
    },
    onError: (e: unknown) => setMessage({ ok: false, text: e instanceof Error ? e.message : 'Could not report the issue' }),
  });

  const valid = category && title.trim().length >= 3 && description.trim().length >= 3 && (activityId || stageId);

  return (
    <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl">
      <CardHeader className="p-5 pb-3">
        <CardTitle className="text-base font-extrabold flex items-center gap-2"><AlertOctagon className="w-5 h-5 text-[#FF7A18]" /> Report an issue or delay</CardTitle>
        <CardDescription className="text-xs font-semibold">What is slowing the work? It is recorded against the stage and activity and feeds the project's root-cause analysis.</CardDescription>
      </CardHeader>
      <CardContent className="p-5 pt-0 space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <Label>Issue category</Label>
            <select className={FIELD} value={category} onChange={(e) => setCategory(e.target.value as IssueCategoryCode)} aria-label="Issue category">
              <option value="">Select a category…</option>
              {(categories.data ?? []).map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </select>
          </div>
          <div>
            <Label>Severity / impact</Label>
            <select className={FIELD} value={severity} onChange={(e) => setSeverity(e.target.value as IssueSeverity)} aria-label="Severity">
              {SEVERITIES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
          <div>
            <Label>Affected stage</Label>
            <select className={FIELD} value={stageId} onChange={(e) => { setStageId(e.target.value); setActivityId(''); }} aria-label="Affected stage">
              <option value="">Select a stage…</option>
              {stages.map((s) => <option key={s.stage_id} value={s.stage_id}>{s.sequence_order}. {s.stage_name}</option>)}
            </select>
          </div>
          <div>
            <Label hint="(optional, narrows the issue)">Affected activity</Label>
            <select className={FIELD} value={activityId} onChange={(e) => setActivityId(e.target.value)} aria-label="Affected activity">
              <option value="">Whole stage</option>
              {activities.map((a) => <option key={a.activity_id} value={a.activity_id}>{a.activity_id} · {a.activity_name}</option>)}
            </select>
          </div>
          <div>
            <Label>Date</Label>
            <Input type="date" value={date} max={today()} onChange={(e) => setDate(e.target.value)} aria-label="Date" />
          </div>
          <div>
            <Label hint="(days, if known)">Expected duration</Label>
            <Input type="number" min={0} step="0.5" value={duration} onChange={(e) => setDuration(e.target.value)} placeholder="e.g. 7" aria-label="Expected duration in days" />
          </div>
        </div>
        <div>
          <Label>Short title</Label>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Welder shortage on piping spools" maxLength={200} aria-label="Title" />
        </div>
        <div>
          <Label>Description</Label>
          <Textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What happened, how many people / what quantity, since when…" rows={3} aria-label="Description" />
        </div>

        <SimilarIncidents results={similar} loading={searching} />

        <label className="flex items-start gap-2 text-xs cursor-pointer">
          <input type="checkbox" checked={blocks} onChange={(e) => setBlocks(e.target.checked)} className="mt-0.5 accent-[#FF7A18]" />
          <span><b>Work is stopped</b> — mark the activity as blocked until this is resolved</span>
        </label>

        <div>
          <input ref={fileRef} type="file" multiple className="hidden" onChange={(e) => { const picked = Array.from(e.target.files ?? []); e.target.value = ''; if (picked.length) setFiles((p) => [...p, ...picked]); }} />
          <Button type="button" variant="outline" size="sm" onClick={() => fileRef.current?.click()} className="gap-1.5 cursor-pointer"><Paperclip className="w-3.5 h-3.5" /> Attach evidence (photos, letters)</Button>
          {files.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {files.map((f) => (
                <span key={`${f.name}-${f.size}`} className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-md border border-slate-300 dark:border-[#214766]">
                  {f.name}<button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFiles((p) => p.filter((x) => x !== f))} className="cursor-pointer"><X className="w-3 h-3" /></button>
                </span>
              ))}
            </div>
          )}
        </div>

        {message && (
          <div role="status" className={cn('text-xs p-2.5 rounded-lg border', message.ok ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300' : 'border-rose-300 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300')}>{message.text}</div>
        )}
        <Button onClick={() => { setMessage(null); submit.mutate(); }} disabled={!valid || submit.isPending} className="w-full gap-2 font-bold cursor-pointer">
          {submit.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} Report issue
        </Button>
      </CardContent>
    </Card>
  );
}

function ResolveDialog({ issue, open, onClose, onDone }: { issue: Issue | null; open: boolean; onClose: () => void; onDone: () => void }) {
  const { currentProject, currentScheduleVersion } = useProject();
  const [notes, setNotes] = useState('');
  const [cause, setCause] = useState('');
  const [outcome, setOutcome] = useState('');
  const [memory, setMemory] = useState(true);
  const [share, setShare] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (open) { setNotes(''); setCause(issue?.root_cause_title ?? ''); setOutcome(''); setMemory(true); setShare(false); setError(null); } }, [open, issue]);

  const resolve = useMutation({
    mutationFn: () => issuesApi.resolve(currentProject.id, currentScheduleVersion.id, issue!.issue_id, {
      resolution_notes: notes.trim(), cause: cause.trim() || null, outcome: outcome.trim() || null, add_to_memory: memory, share_with_organisation: memory && share,
    }),
    onSuccess: () => { onDone(); onClose(); },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : 'Could not resolve the issue'),
  });
  if (!issue) return null;
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Resolve: {issue.title}</DialogTitle>
          <DialogDescription>Record what resolved it. A resolved issue can be kept as institutional knowledge for future issues.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div><Label>What resolved it</Label><Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} aria-label="Resolution" /></div>
          <div><Label hint="(optional)">Root cause</Label><Input value={cause} onChange={(e) => setCause(e.target.value)} aria-label="Root cause" /></div>
          <div><Label hint="(optional)">Outcome</Label><Input value={outcome} onChange={(e) => setOutcome(e.target.value)} placeholder="e.g. Delay held to 4 days" aria-label="Outcome" /></div>
          <label className="flex items-start gap-2 text-xs cursor-pointer"><input type="checkbox" checked={memory} onChange={(e) => setMemory(e.target.checked)} className="mt-0.5 accent-[#FF7A18]" /><span><b>Save to institutional memory</b></span></label>
          {memory && <label className="flex items-start gap-2 text-xs cursor-pointer ml-5"><input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} className="mt-0.5 accent-[#FF7A18]" /><span>Share this lesson with other projects</span></label>}
          {error && <div role="alert" className="text-xs text-rose-600">{error}</div>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="cursor-pointer">Cancel</Button>
          <Button onClick={() => resolve.mutate()} disabled={notes.trim().length < 3 || resolve.isPending} className="gap-1.5 cursor-pointer">{resolve.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />} Resolve</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function IssueCard({ issue, canResolve, onResolve }: { issue: Issue; canResolve: boolean; onResolve: (i: Issue) => void }) {
  const open = issue.status === 'ACTIVE';
  return (
    <div className={cn('rounded-xl border p-3.5 text-xs space-y-2', open ? 'border-slate-200 dark:border-[#214766]' : 'border-slate-200/70 dark:border-[#214766]/70 opacity-90')} data-testid="issue-card">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-extrabold text-sm leading-snug">{issue.title}</div>
          <div className="text-muted-foreground">{issue.category_name} · {issue.activity_id ? `${issue.activity_id} ${issue.activity_name ?? ''}` : 'Whole stage'} · {issue.stage_name}</div>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span className={cn('px-2 py-0.5 rounded-md border text-[10px] font-bold', SEVERITY_TONE[issue.severity])}>{issue.severity}</span>
          <span className={cn('px-2 py-0.5 rounded-md border text-[10px] font-bold', open ? 'border-amber-300 text-amber-800 dark:text-amber-300' : 'border-emerald-300 text-emerald-700 dark:text-emerald-300')}>{open ? 'OPEN' : 'RESOLVED'}</span>
        </div>
      </div>
      <p className="leading-relaxed">{issue.description}</p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1"><Clock className="w-3 h-3" />{new Date(issue.reported_date).toLocaleDateString()}</span>
        {issue.expected_duration_days != null && <span>~{issue.expected_duration_days} days</span>}
        <span>by {issue.reported_by_name ?? 'unknown'}</span>
        {issue.blocks_work && <span className="inline-flex items-center gap-1 font-bold text-rose-600"><ShieldAlert className="w-3 h-3" />Work stopped</span>}
        {issue.evidence.length > 0 && <span className="inline-flex items-center gap-1"><Paperclip className="w-3 h-3" />{issue.evidence.length} file{issue.evidence.length === 1 ? '' : 's'}</span>}
      </div>
      {issue.root_cause_title && <div className="text-[11px]"><b>Root cause:</b> {issue.root_cause_title}</div>}
      {!open && issue.resolution_notes && (
        <div className="p-2 rounded-lg bg-emerald-50/70 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-900 text-[11px] space-y-0.5">
          <div><b>Resolved</b>{issue.resolved_by_name ? ` by ${issue.resolved_by_name}` : ''}{issue.resolved_at ? ` · ${new Date(issue.resolved_at).toLocaleDateString()}` : ''}: {issue.resolution_notes}</div>
          {issue.memory_incident_id && <div className="inline-flex items-center gap-1 text-blue-700 dark:text-blue-300"><BookOpenCheck className="w-3 h-3" />Saved to institutional memory</div>}
        </div>
      )}
      {open && canResolve && <Button size="sm" variant="outline" onClick={() => onResolve(issue)} className="gap-1.5 cursor-pointer"><CheckCircle2 className="w-3.5 h-3.5" /> Resolve</Button>}
    </div>
  );
}

/** Issues & delays: the site engineer reports what slows the work; the supervisor resolves it and keeps the lesson. */
export default function IssuesDelays() {
  const { currentProject, currentScheduleVersion, can } = useProject();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<'ACTIVE' | 'RESOLVED' | 'ALL'>('ACTIVE');
  const [resolving, setResolving] = useState<Issue | null>(null);
  // reporting is the field role's job (site engineer); reviewers get the resolve-focused view of the same list
  const canReport = can('REPORT_ISSUE') && can('CREATE_EXECUTION_EVENT');
  const canResolve = can('MANAGE_BLOCKERS');

  const issues = useQuery({
    queryKey: ['v7', 'issues', currentProject.id, currentScheduleVersion.id, filter],
    queryFn: () => issuesApi.list(currentProject.id, currentScheduleVersion.id, { status: filter }),
    retry: false,
  });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['v7', 'issues'] });
    queryClient.invalidateQueries({ queryKey: ['v7', 'root-cause'] });
    queryClient.invalidateQueries({ queryKey: ['v7', 'progress'] });
    queryClient.invalidateQueries({ queryKey: ['v7', 'dashboard'] });
  };

  return (
    <div className="space-y-6">
      <div>
        <div className="text-[11px] font-mono font-bold text-primary">{currentProject.name} ({currentProject.code})</div>
        <h1 className="text-2xl font-extrabold tracking-tight">Issues & Delays</h1>
        <p className="text-xs font-semibold text-muted-foreground mt-0.5">Shortages, weather, access, permits and anything else holding up an activity. Each issue is tied to its stage and activity.</p>
      </div>
      <div className={cn('grid grid-cols-1 gap-6', canReport && 'xl:grid-cols-5')}>
        {canReport && <div className="xl:col-span-2"><ReportForm onReported={refresh} /></div>}
        <div className={cn('space-y-3', canReport ? 'xl:col-span-3' : '')}>
          <div className="flex items-center gap-1.5 bg-card p-1 rounded-xl border border-border w-fit">
            {(['ACTIVE', 'RESOLVED', 'ALL'] as const).map((f) => (
              <button key={f} type="button" onClick={() => setFilter(f)} className={cn('px-3 py-1.5 text-xs font-bold rounded-lg cursor-pointer', filter === f ? 'bg-[#FF7A18] text-white' : 'text-muted-foreground hover:text-foreground')}>
                {f === 'ACTIVE' ? 'Open' : f === 'RESOLVED' ? 'Resolved' : 'All'}
              </button>
            ))}
          </div>
          {issues.isLoading && <div className="text-xs text-muted-foreground flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading issues…</div>}
          {issues.isError && <div role="alert" className="text-xs text-rose-600">{issues.error instanceof Error ? issues.error.message : 'Could not load issues'}</div>}
          {issues.data && issues.data.length === 0 && <EmptyState icon={CheckCircle2} title="No issues here" description={filter === 'ACTIVE' ? 'Nothing is currently holding up the work.' : 'Nothing to show for this filter.'} />}
          <div className="space-y-3">{(issues.data ?? []).map((i) => <IssueCard key={i.issue_id} issue={i} canResolve={canResolve} onResolve={setResolving} />)}</div>
        </div>
      </div>
      <ResolveDialog issue={resolving} open={!!resolving} onClose={() => setResolving(null)} onDone={refresh} />
    </div>
  );
}
