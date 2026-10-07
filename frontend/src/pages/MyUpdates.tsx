import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BellRing, CheckCheck, CheckCircle2, Clock, Loader2, MessageSquareWarning, PauseCircle, Pencil, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { cn } from '@/lib/utils';
import { useProject } from '@/context/ProjectContext';
import { updatesApi, type DecisionAction, type MyClaim, type NotificationItem } from '@/api/prototype';

const DECISION: Record<DecisionAction, { label: string; tone: string; icon: React.ElementType }> = {
  APPROVE: { label: 'Approved', tone: 'text-emerald-700 border-emerald-300 bg-emerald-50 dark:text-emerald-300 dark:border-emerald-800 dark:bg-emerald-950/30', icon: CheckCircle2 },
  EDIT: { label: 'Approved with edits', tone: 'text-blue-700 border-blue-300 bg-blue-50 dark:text-blue-300 dark:border-blue-800 dark:bg-blue-950/30', icon: Pencil },
  REJECT: { label: 'Rejected', tone: 'text-rose-700 border-rose-300 bg-rose-50 dark:text-rose-300 dark:border-rose-900 dark:bg-rose-950/30', icon: XCircle },
  HOLD: { label: 'On hold: changes requested', tone: 'text-amber-800 border-amber-300 bg-amber-50 dark:text-amber-300 dark:border-amber-800 dark:bg-amber-950/30', icon: PauseCircle },
};

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—');
const pct = (v: number | null) => (v == null ? '—' : `${v}%`);

function DecisionBadge({ action }: { action: DecisionAction }) {
  const d = DECISION[action];
  const Icon = d.icon;
  return <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-bold', d.tone)}><Icon className="w-3 h-3" />{d.label}</span>;
}

function NotificationCard({ n, onRead }: { n: NotificationItem; onRead: (id: string) => void }) {
  const unread = !n.read_at;
  const isDecision = n.notification_type === 'CLAIM_DECISION' && n.decision_action;
  return (
    <div className={cn('rounded-xl border p-3.5 text-xs space-y-2', unread ? 'border-[#FF7A18]/60 bg-[#FF7A18]/5' : 'border-slate-200 dark:border-[#214766]')} data-testid="update-card">
      <div className="flex items-start justify-between gap-2">
        <div className="space-y-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {isDecision ? <DecisionBadge action={n.decision_action!} /> : <span className="px-2 py-0.5 rounded-md border text-[11px] font-bold border-blue-300 text-blue-700 dark:text-blue-300">Issue update</span>}
            {unread && <span className="text-[10px] font-extrabold text-[#FF7A18]">NEW</span>}
          </div>
          <div className="font-extrabold text-sm">{n.title}</div>
        </div>
        {unread && <Button size="sm" variant="ghost" onClick={() => onRead(n.notification_id)} className="text-[11px] cursor-pointer shrink-0">Mark read</Button>}
      </div>
      {isDecision && (
        <>
          {n.raw_claim_text && <div className="text-muted-foreground">Your claim: “{n.raw_claim_text}”</div>}
          <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11px]">
            <span><b>Reported:</b> {pct(n.claimed_pct)}</span>
            {(n.decision_action === 'APPROVE' || n.decision_action === 'EDIT') && <span><b>Approved:</b> {pct(n.approved_pct)}</span>}
            <span><b>Claim status:</b> {n.claim_status}</span>
          </div>
          {n.decision_comment && (
            <div className="p-2 rounded-lg bg-slate-50 dark:bg-[#0A2238] border border-slate-200 dark:border-[#214766] flex gap-2"><MessageSquareWarning className="w-3.5 h-3.5 mt-0.5 shrink-0 text-muted-foreground" /><span><b>Supervisor:</b> {n.decision_comment}</span></div>
          )}
        </>
      )}
      {!isDecision && n.body && <div>{n.body}</div>}
      <div className="text-[11px] text-muted-foreground inline-flex items-center gap-1"><Clock className="w-3 h-3" />{isDecision ? `Decided ${when(n.decided_at)}${n.decided_by_name ? ` by ${n.decided_by_name}` : ''}` : when(n.created_at)}</div>
    </div>
  );
}

function ClaimRow({ c }: { c: MyClaim }) {
  return (
    <tr className="border-b border-border/50 align-top">
      <td className="py-2 pr-3 max-w-xs"><div className="font-semibold">{c.activity_id ?? 'Unmatched'}</div><div className="text-muted-foreground truncate" title={c.raw_claim_text}>{c.activity_name ?? c.raw_claim_text}</div></td>
      <td className="pr-3 font-mono">{pct(c.claimed_pct)}</td>
      <td className="pr-3">{c.decision_action ? <DecisionBadge action={c.decision_action} /> : <span className="inline-flex items-center gap-1 text-muted-foreground"><Clock className="w-3 h-3" />{c.status === 'EXTRACTED' ? 'Not yet reviewed' : 'Awaiting supervisor'}</span>}</td>
      <td className="pr-3 max-w-xs">{c.decision_comment ?? '—'}</td>
      <td className="whitespace-nowrap text-muted-foreground">{when(c.decided_at ?? c.created_at)}</td>
    </tr>
  );
}

/** What happened to my claims: supervisor decisions, comments and timestamps, persisted by the backend. */
export default function MyUpdates() {
  const { currentProject } = useProject();
  const queryClient = useQueryClient();
  const notes = useQuery({ queryKey: ['v7', 'notifications', currentProject.id], queryFn: () => updatesApi.notifications(currentProject.id), retry: false, refetchInterval: 30_000 });
  const claims = useQuery({ queryKey: ['v7', 'my-claims', currentProject.id], queryFn: () => updatesApi.myClaims(currentProject.id), retry: false, refetchInterval: 30_000 });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['v7', 'notifications'] });
  const markRead = useMutation({ mutationFn: (id: string) => updatesApi.markRead(currentProject.id, id), onSuccess: invalidate });
  const markAll = useMutation({ mutationFn: () => updatesApi.markAllRead(currentProject.id), onSuccess: invalidate });

  const items = notes.data?.items ?? [];
  const counts = (claims.data ?? []).reduce<Record<string, number>>((m, c) => { const k = c.decision_action ?? 'PENDING'; m[k] = (m[k] ?? 0) + 1; return m; }, {});
  const chip = (label: string, n: number, tone: string) => (
    <div className={cn('rounded-xl border px-4 py-2.5 text-center min-w-[96px]', tone)}><div className="text-xl font-black">{n}</div><div className="text-[10px] font-bold uppercase tracking-wide">{label}</div></div>
  );

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="text-[11px] font-mono font-bold text-primary">{currentProject.name} ({currentProject.code})</div>
          <h1 className="text-2xl font-extrabold tracking-tight flex items-center gap-2"><BellRing className="w-6 h-6 text-[#FF7A18]" /> My Updates</h1>
          <p className="text-xs font-semibold text-muted-foreground mt-0.5">Decisions your supervisor made on the claims you submitted: approved, edited, rejected or put on hold, with their comment.</p>
        </div>
        {(notes.data?.unread_count ?? 0) > 0 && <Button variant="outline" size="sm" onClick={() => markAll.mutate()} disabled={markAll.isPending} className="gap-1.5 cursor-pointer"><CheckCheck className="w-4 h-4" /> Mark all read ({notes.data!.unread_count})</Button>}
      </div>

      <div className="flex flex-wrap gap-2">
        {chip('Approved', (counts.APPROVE ?? 0) + (counts.EDIT ?? 0), 'border-emerald-300 text-emerald-700 dark:text-emerald-300')}
        {chip('Rejected', counts.REJECT ?? 0, 'border-rose-300 text-rose-700 dark:text-rose-300')}
        {chip('On hold', counts.HOLD ?? 0, 'border-amber-300 text-amber-700 dark:text-amber-300')}
        {chip('Awaiting review', counts.PENDING ?? 0, 'border-slate-300 dark:border-[#214766]')}
      </div>

      <section className="space-y-3">
        <h2 className="text-xs font-extrabold uppercase tracking-wide">Decisions & notices {notes.data ? `(${items.length})` : ''}</h2>
        {notes.isLoading && <div className="text-xs text-muted-foreground flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>}
        {notes.isError && <div role="alert" className="text-xs text-rose-600">{notes.error instanceof Error ? notes.error.message : 'Could not load updates'}</div>}
        {notes.data && items.length === 0 && <EmptyState icon={BellRing} title="No updates yet" description="When a supervisor decides on one of your claims it appears here." />}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">{items.map((n) => <NotificationCard key={n.notification_id} n={n} onRead={(id) => markRead.mutate(id)} />)}</div>
      </section>

      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-sm">My claims</CardTitle></CardHeader>
        <CardContent>
          {claims.isLoading && <div className="text-xs text-muted-foreground">Loading…</div>}
          {claims.data && claims.data.length === 0 && <div className="text-xs text-muted-foreground">You have not submitted any claims in this project yet.</div>}
          {claims.data && claims.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="text-left text-muted-foreground border-b border-border"><th className="py-1.5 pr-3">Activity</th><th className="pr-3">Reported</th><th className="pr-3">Decision</th><th className="pr-3">Supervisor comment</th><th>When</th></tr></thead>
                <tbody>{claims.data.map((c) => <ClaimRow key={c.event_id} c={c} />)}</tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
