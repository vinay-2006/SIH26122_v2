import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertOctagon, BookOpenCheck, CheckCircle2, Clock, GitBranch, Loader2, Send, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useProjectState } from '@/context/ProjectContext';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { issuesApi, scheduleApi } from '@/v2/api/endpoints';
import type { CatalogActivity, Issue, Severity } from '@/v2/api/types';
import { ActivityPicker, EvidenceUploader, useActivityMap, useIdempotencyKey, type UploadedFile } from '@/v2/claims/shared';
import { ISSUE_CATEGORIES, categoryName } from '@/v2/reference';
import { FIELD, Label, Loading, Notice, PageHeader, Panel, Pills, QueryError, SEVERITY_TONE, day, errText, num, today, F } from '@/v2/ui';
import { cn } from '@/lib/utils';

const SEVERITIES: { value: Severity; label: string }[] = [
  { value: 'LOW', label: 'Low — minor, no schedule effect' }, { value: 'MEDIUM', label: 'Medium — may slip the activity' },
  { value: 'HIGH', label: 'High — will delay the activity' }, { value: 'CRITICAL', label: 'Critical — stops work / safety' },
];

function ReportForm({ projectId, activeVersionId }: { projectId: string; activeVersionId: string | null }) {
  const qc = useQueryClient();
  const [target, setTarget] = useState<'ACTIVITY' | 'STAGE'>('ACTIVITY');
  const [activity, setActivity] = useState<CatalogActivity | null>(null);
  const [stage, setStage] = useState('');
  const [category, setCategory] = useState('');
  const [severity, setSeverity] = useState<Severity>('MEDIUM');
  const [title, setTitle] = useState('');
  const [desc, setDesc] = useState('');
  const [started, setStarted] = useState(today());
  const [impact, setImpact] = useState('');
  const [blocks, setBlocks] = useState(true);
  const [docs, setDocs] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const { keyFor, reset } = useIdempotencyKey('issue');
  const wbs = useQuery({ queryKey: ['v2', 'wbs-nodes', projectId, activeVersionId], queryFn: () => scheduleApi.wbs(projectId, activeVersionId!), enabled: !!activeVersionId && target === 'STAGE', retry: false });
  const stages = (wbs.data ?? []).filter((w) => w.node_type === 'STAGE');
  const body = {
    title: title.trim(), category_code: category, severity, blocks_work: blocks, description: desc.trim() || undefined, delay_started_on: started || undefined,
    impact_days_estimated: impact !== '' ? Number(impact) : undefined, evidence_document_ids: docs.map((d) => d.document_id),
    ...(target === 'ACTIVITY' ? { activity_uid: activity?.activity_uid } : { stage_wbs_uid: stage || undefined }),
  };
  const hasTarget = target === 'ACTIVITY' ? !!activity : !!stage;
  const valid = category && title.trim().length >= 3 && hasTarget && (impact === '' || Number(impact) >= 0) && !uploading;
  const submit = useMutation({
    mutationFn: () => issuesApi.report(projectId, body, keyFor(body)),
    onSuccess: () => { reset(); setMsg({ ok: true, text: 'Issue reported. A Supervisor will see it; while it is active and blocking it marks the work as blocked.' }); setTitle(''); setDesc(''); setImpact(''); setDocs([]); setActivity(null); setStage(''); qc.invalidateQueries({ queryKey: ['v2'] }); },
    onError: (e) => setMsg({ ok: false, text: errText(e) }),
  });
  return (
    <Panel icon={AlertOctagon} title="Report an issue or delay" description="What is slowing the work? It is tied to an activity or a stage, and feeds root-cause analysis.">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div><Label>Category</Label><select className={FIELD} aria-label="Issue category" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">Select a category…</option>{ISSUE_CATEGORIES.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}</select></div>
        <div><Label>Severity</Label><select className={FIELD} aria-label="Severity" value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>{SEVERITIES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</select></div>
      </div>
      <div><Label>Affects</Label><Pills value={target} onChange={setTarget} options={[{ value: 'ACTIVITY', label: 'One activity' }, { value: 'STAGE', label: 'A whole stage' }]} /></div>
      {target === 'ACTIVITY' ? <ActivityPicker projectId={projectId} value={activity} onChange={setActivity} /> : (
        <div>{wbs.isPending && <Loading />}<select className={FIELD} aria-label="Affected stage" value={stage} onChange={(e) => setStage(e.target.value)}><option value="">Select a stage…</option>{stages.map((s) => <option key={s.wbs_uid} value={s.wbs_uid}>{s.wbs_name}</option>)}</select></div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div><Label>Delay started on</Label><Input type="date" aria-label="Delay started" value={started} max={today()} onChange={(e) => setStarted(e.target.value)} /></div>
        <div><Label hint="(days, if known)">Estimated impact</Label><Input type="number" min={0} step="0.5" aria-label="Estimated impact in days" value={impact} onChange={(e) => setImpact(e.target.value)} /></div>
      </div>
      <div><Label>Short title</Label><Input aria-label="Issue title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="e.g. Welder shortage on piping spools" /></div>
      <div><Label hint="(optional)">Description</Label><Textarea rows={3} aria-label="Issue description" value={desc} onChange={(e) => setDesc(e.target.value)} /></div>
      <label className="flex items-start gap-2 text-xs cursor-pointer"><input type="checkbox" checked={blocks} onChange={(e) => setBlocks(e.target.checked)} className="mt-0.5 accent-[#FF7A18]" /><span><b>Work is stopped</b> — mark it as blocked until the issue is resolved</span></label>
      <EvidenceUploader projectId={projectId} onChange={(d, u) => { setDocs(d); setUploading(u); }} kind={(f) => (f.type.startsWith('image/') ? 'EVIDENCE' : 'ISSUE_REPORT')} label="Attach evidence (photos, letters)" />
      {msg && <Notice tone={msg.ok ? 'ok' : 'bad'} testid="issue-msg">{msg.text}</Notice>}
      <Button onClick={() => { setMsg(null); submit.mutate(); }} disabled={!valid || submit.isPending} className="w-full gap-2 font-bold cursor-pointer" data-testid="report-issue">{submit.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}Report issue</Button>
    </Panel>
  );
}

function ResolveDialog({ projectId, issue, onClose }: { projectId: string; issue: Issue | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [notes, setNotes] = useState('');
  const [ended, setEnded] = useState(today());
  const [actual, setActual] = useState('');
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setNotes(''); setEnded(today()); setActual(''); setErr(null); }, [issue?.issue_id]);
  const go = useMutation({
    mutationFn: () => issuesApi.resolve(projectId, issue!.issue_id, { notes: notes.trim(), delay_ended_on: ended || undefined, impact_days_actual: actual !== '' ? Number(actual) : undefined }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['v2'] }); onClose(); }, onError: (e) => setErr(errText(e)),
  });
  if (!issue) return null;
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg" data-testid="resolve-dialog">
        <DialogHeader><DialogTitle>Resolve: {issue.title}</DialogTitle><DialogDescription>Record what resolved it. A resolved issue stays on record and is not reopened; if the problem returns, report a new issue.</DialogDescription></DialogHeader>
        <div className="space-y-3">
          <div><Label>What resolved it</Label><Textarea rows={3} aria-label="Resolution" value={notes} onChange={(e) => setNotes(e.target.value)} /></div>
          <div className="grid grid-cols-2 gap-3"><div><Label>Delay ended on</Label><Input type="date" aria-label="Delay ended" value={ended} min={issue.delay_started_on ?? undefined} max={today()} onChange={(e) => setEnded(e.target.value)} /></div>
            <div><Label hint="(days)">Actual impact</Label><Input type="number" min={0} step="0.5" aria-label="Actual impact in days" value={actual} onChange={(e) => setActual(e.target.value)} /></div></div>
          {err && <Notice tone="bad">{err}</Notice>}
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose} className="cursor-pointer">Cancel</Button>
          <Button onClick={() => go.mutate()} disabled={notes.trim().length < 2 || go.isPending} className="gap-1.5 cursor-pointer" data-testid="confirm-resolve">{go.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}Resolve</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MemoryDialog({ projectId, issue, onClose }: { projectId: string; issue: Issue | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [lesson, setLesson] = useState('');
  const [action, setAction] = useState('');
  const [outcome, setOutcome] = useState('');
  const [share, setShare] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setLesson(''); setAction(''); setOutcome(''); setShare(false); setErr(null); }, [issue?.issue_id]);
  const go = useMutation({
    mutationFn: () => issuesApi.toMemory(projectId, issue!.issue_id, { lessons_learned: lesson.trim(), corrective_action: action.trim() || undefined, outcome: outcome.trim() || undefined, visibility: share ? 'ORGANISATION' : 'PROJECT' }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['v2'] }); onClose(); }, onError: (e) => setErr(errText(e)),
  });
  if (!issue) return null;
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader><DialogTitle>Keep as institutional memory</DialogTitle><DialogDescription>{issue.title}</DialogDescription></DialogHeader>
        <div className="space-y-3">
          <div><Label>Lesson learned (required)</Label><Textarea rows={3} aria-label="Lesson learned" value={lesson} onChange={(e) => setLesson(e.target.value)} /></div>
          <div><Label hint="(optional)">Corrective action</Label><Input aria-label="Corrective action" value={action} onChange={(e) => setAction(e.target.value)} /></div>
          <div><Label hint="(optional)">Outcome</Label><Input aria-label="Outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)} /></div>
          <label className="flex items-center gap-2 text-xs cursor-pointer"><input type="checkbox" className="accent-[#FF7A18]" checked={share} onChange={(e) => setShare(e.target.checked)} />Share this lesson with other projects in the organisation</label>
          {err && <Notice tone="bad">{err}</Notice>}
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose} className="cursor-pointer">Cancel</Button><Button onClick={() => go.mutate()} disabled={lesson.trim().length < 5 || go.isPending} className="cursor-pointer" data-testid="confirm-memory">Save lesson</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function IssueCard({ projectId, issue, label, canResolve, rootCauses, onResolve, onMemory }: { projectId: string; issue: Issue; label: string; canResolve: boolean; rootCauses: { root_cause_id: string; title: string }[]; onResolve: (i: Issue) => void; onMemory: (i: Issue) => void }) {
  const qc = useQueryClient();
  const open = issue.status === 'ACTIVE';
  const assign = useMutation({ mutationFn: (rc: string) => issuesApi.assignRootCause(projectId, issue.issue_id, rc), onSuccess: () => qc.invalidateQueries({ queryKey: ['v2'] }) });
  const rc = rootCauses.find((r) => r.root_cause_id === issue.root_cause_id);
  return (
    <div className={cn('rounded-xl border p-3.5 text-xs space-y-2', open ? 'border-slate-200 dark:border-[#214766]' : 'border-slate-200/70 dark:border-[#214766]/70 opacity-90')} data-testid="issue-card" data-status={issue.status}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><div className="font-extrabold text-sm leading-snug">{issue.title}</div><div className="text-muted-foreground">{categoryName(issue.category_code)} · {label}</div></div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span className={cn('px-2 py-0.5 rounded-md border text-[10px] font-bold', SEVERITY_TONE[issue.severity])}>{issue.severity}</span>
          <span className={cn('px-2 py-0.5 rounded-md border text-[10px] font-bold', open ? 'border-amber-300 text-amber-800 dark:text-amber-300' : 'border-emerald-300 text-emerald-700 dark:text-emerald-300')}>{open ? 'OPEN' : 'RESOLVED'}</span>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1"><Clock className="w-3 h-3" />reported {day(issue.reported_date)}</span>
        {issue.delay_started_on && <span>delay from {day(issue.delay_started_on)}{issue.delay_ended_on ? ` to ${day(issue.delay_ended_on)}` : ''}</span>}
        {issue.impact_days_estimated != null && <span>est. impact {num(issue.impact_days_estimated)} d</span>}
        {issue.impact_days_actual != null && <span>actual {num(issue.impact_days_actual)} d</span>}
        {issue.blocks_work && open && <span className="inline-flex items-center gap-1 font-bold text-rose-600"><ShieldAlert className="w-3 h-3" />Work blocked</span>}
      </div>
      {rc && <div className="text-[11px]"><b>Root cause:</b> {rc.title}</div>}
      {canResolve && !issue.root_cause_id && rootCauses.length > 0 && (
        <select className={F('h-7 text-xs w-72')} aria-label="Assign root cause" value="" onChange={(e) => e.target.value && assign.mutate(e.target.value)}><option value="">Assign a root cause…</option>{rootCauses.map((r) => <option key={r.root_cause_id} value={r.root_cause_id}>{r.title}</option>)}</select>)}
      <div className="flex gap-2">
        {open && canResolve && <Button size="sm" variant="outline" onClick={() => onResolve(issue)} className="gap-1.5 cursor-pointer" data-testid="resolve-btn"><CheckCircle2 className="w-3.5 h-3.5" />Resolve</Button>}
        {!open && canResolve && <Button size="sm" variant="outline" onClick={() => onMemory(issue)} className="gap-1.5 cursor-pointer" data-testid="memory-btn"><BookOpenCheck className="w-3.5 h-3.5" />Keep as lesson</Button>}
      </div>
    </div>
  );
}

function IssueList({ projectId, canResolve }: { projectId: string; canResolve: boolean }) {
  const [filter, setFilter] = useState<'ACTIVE' | 'RESOLVED' | 'ALL'>('ACTIVE');
  const [offset, setOffset] = useState(0);
  const [resolving, setResolving] = useState<Issue | null>(null);
  const [remember, setRemember] = useState<Issue | null>(null);
  const acts = useActivityMap(projectId);
  const q = useQuery({ queryKey: ['v2', 'issues', projectId, filter, offset], queryFn: () => issuesApi.list(projectId, { status: filter === 'ALL' ? undefined : filter, limit: 50, offset }), retry: false, placeholderData: (p) => p });
  const rcs = useQuery({ queryKey: ['v2', 'root-causes', projectId], queryFn: () => issuesApi.rootCauses(projectId), enabled: canResolve, retry: false });
  return (
    <div className="space-y-3">
      <Pills value={filter} onChange={(v) => { setFilter(v); setOffset(0); }} options={[{ value: 'ACTIVE', label: 'Open' }, { value: 'RESOLVED', label: 'Resolved' }, { value: 'ALL', label: 'All' }]} />
      {q.isPending && <Loading />}
      {q.error && <QueryError error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.items.length === 0 && <EmptyState icon={CheckCircle2} title="No issues here" description={filter === 'ACTIVE' ? 'Nothing is currently holding up the work.' : 'Nothing to show for this filter.'} />}
      <div className="space-y-3">{q.data?.items.map((i) => {
        const a = i.activity_uid ? acts.get(i.activity_uid) : undefined;
        return <IssueCard key={i.issue_id} projectId={projectId} issue={i} label={a ? `${a.external_activity_id} ${a.activity_name}` : i.activity_uid ? 'an activity' : 'a whole stage'} canResolve={canResolve} rootCauses={rcs.data?.items ?? []} onResolve={setResolving} onMemory={setRemember} />;
      })}</div>
      <div className="flex justify-end gap-2"><Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))} className="cursor-pointer">Previous</Button><Button size="sm" variant="outline" disabled={!q.data?.next_offset} onClick={() => setOffset(q.data!.next_offset!)} className="cursor-pointer">Next</Button></div>
      <ResolveDialog projectId={projectId} issue={resolving} onClose={() => setResolving(null)} />
      <MemoryDialog projectId={projectId} issue={remember} onClose={() => setRemember(null)} />
    </div>
  );
}

function RootCausesTab({ projectId, canManage }: { projectId: string; canManage: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['v2', 'root-causes', projectId], queryFn: () => issuesApi.rootCauses(projectId), retry: false });
  const [title, setTitle] = useState('');
  const [cat, setCat] = useState('');
  const [summary, setSummary] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const create = useMutation({ mutationFn: () => issuesApi.createRootCause(projectId, { title: title.trim(), category_code: cat, summary: summary.trim() || undefined }), onSuccess: () => { setTitle(''); setSummary(''); setCat(''); setErr(null); qc.invalidateQueries({ queryKey: ['v2', 'root-causes'] }); }, onError: (e) => setErr(errText(e)) });
  return (
    <div className="space-y-4">
      {canManage && (
        <Panel icon={GitBranch} title="New root cause" description="Group related issues under the underlying cause.">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3"><div><Label>Title</Label><Input aria-label="Root cause title" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
            <div><Label>Category</Label><select className={FIELD} aria-label="Root cause category" value={cat} onChange={(e) => setCat(e.target.value)}><option value="">Select…</option>{ISSUE_CATEGORIES.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}</select></div></div>
          <div><Label hint="(optional)">Summary</Label><Textarea rows={2} aria-label="Root cause summary" value={summary} onChange={(e) => setSummary(e.target.value)} /></div>
          {err && <Notice tone="bad">{err}</Notice>}
          <Button onClick={() => create.mutate()} disabled={title.trim().length < 3 || !cat || create.isPending} className="cursor-pointer" data-testid="create-root-cause">Create root cause</Button>
        </Panel>
      )}
      {q.isPending && <Loading />}
      {q.error && <QueryError error={q.error} />}
      {q.data?.items.length === 0 && <EmptyState icon={GitBranch} title="No root causes yet" />}
      <div className="space-y-2">{q.data?.items.map((r) => <div key={r.root_cause_id} className="rounded-xl border border-border p-3 text-xs" data-testid="root-cause"><div className="font-bold">{r.title} <span className="font-mono text-[10px] text-muted-foreground">{categoryName(r.category_code)} · {r.issues} issue(s)</span></div>{r.summary && <div className="text-muted-foreground">{r.summary}</div>}</div>)}</div>
    </div>
  );
}

function MemoryTab({ projectId }: { projectId: string }) {
  const q = useQuery({ queryKey: ['v2', 'memory', projectId], queryFn: () => issuesApi.memory(projectId), retry: false });
  if (q.isPending) return <Loading />;
  if (q.error) return <QueryError error={q.error} />;
  if (!q.data.items.length) return <EmptyState icon={BookOpenCheck} title="No lessons recorded yet" description="Supervisors can keep a resolved issue as a lesson for future work." />;
  return <div className="space-y-2">{q.data.items.map((m) => <div key={m.memory_id} className="rounded-xl border border-border p-3 text-xs space-y-0.5" data-testid="memory-entry"><div className="font-bold">{m.title}</div><div>{m.lessons_learned}</div>{m.corrective_action && <div className="text-muted-foreground">Action: {m.corrective_action}</div>}{m.outcome && <div className="text-muted-foreground">Outcome: {m.outcome}</div>}<div className="text-[10px] text-muted-foreground">{categoryName(m.category_code)} · {m.delay_days != null ? `${num(m.delay_days)} d delay · ` : ''}{m.visibility.toLowerCase()}</div></div>)}</div>;
}

export default function IssuesPage() {
  const { currentProject, can } = useProjectState();
  const { projectId, activeVersionId, isArchived, role } = useV2Project();
  const canReport = can('REPORT_ISSUE') && !isArchived;
  const canResolve = can('MANAGE_BLOCKERS') && !isArchived;
  const blockers = useQuery({ queryKey: ['v2', 'blockers', projectId], queryFn: () => issuesApi.blockers(projectId), retry: false });
  const showCauses = role !== 'SITE_ENGINEER';
  return (
    <div className="space-y-6" data-testid="issues-page">
      <PageHeader project={`${currentProject?.name} (${currentProject?.code})`} title="Issues & Delays" subtitle={role === 'PROJECT_MANAGER' ? 'Read-only for Project Managers: every issue, blocker and lesson on this project.' : 'Shortages, weather, access, permits and anything else holding up an activity. Blocked is derived from open blocking issues.'} />
      {(blockers.data?.blocked_activities.length || blockers.data?.blocked_stages.length) ? <Notice tone="bad" testid="blocked-summary">Currently blocked: {blockers.data!.blocked_activities.length} activit{blockers.data!.blocked_activities.length === 1 ? 'y' : 'ies'}, {blockers.data!.blocked_stages.length} stage(s).</Notice> : null}
      <div className={cn('grid grid-cols-1 gap-6', canReport && 'xl:grid-cols-5')}>
        {canReport && <div className="xl:col-span-2"><ReportForm projectId={projectId} activeVersionId={activeVersionId} /></div>}
        <div className={cn(canReport && 'xl:col-span-3')}>
          <Tabs defaultValue="issues">
            <TabsList><TabsTrigger value="issues">Issues</TabsTrigger>{showCauses && <TabsTrigger value="causes">Root causes</TabsTrigger>}<TabsTrigger value="memory">Lessons</TabsTrigger></TabsList>
            <TabsContent value="issues"><IssueList projectId={projectId} canResolve={canResolve} /></TabsContent>
            {showCauses && <TabsContent value="causes"><RootCausesTab projectId={projectId} canManage={canResolve} /></TabsContent>}
            <TabsContent value="memory"><MemoryTab projectId={projectId} /></TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  );
}
