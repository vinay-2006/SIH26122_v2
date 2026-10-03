import React, { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Gavel, HelpCircle, Loader2, ShieldAlert, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { claimsApi } from '@/v2/api/endpoints';
import { V2Error } from '@/v2/api/http';
import type { AppliedRow, CatalogActivity, ClaimDetail, DecisionDone, DecisionIn, DecisionPreview, DecisionResult } from '@/v2/api/types';
import { Label, Loading, Notice, Panel, Pills, errText, num, pct, F } from '@/v2/ui';
import { ActivityPicker } from './shared';
import { cn } from '@/lib/utils';

type Tab = 'APPROVE' | 'EDIT' | 'REJECT' | 'HOLD';

/** What was reported next to what is (or would be) approved. Over-baseline rows are flagged; nothing is capped here. */
export function AppliedTable({ rows, reported }: { rows: AppliedRow[]; reported?: Record<string, string> }) {
  return (
    <div className="rounded-lg border border-border overflow-x-auto" data-testid="applied-table">
      <table className="w-full text-xs">
        <thead className="bg-secondary/60 text-[10px] uppercase tracking-wider text-muted-foreground"><tr><th className="text-left p-1.5">Resource</th><th className="text-right p-1.5">Reported</th><th className="text-right p-1.5">Approved to date</th><th className="text-right p-1.5">This decision adds</th><th className="text-right p-1.5">Baseline</th><th className="text-right p-1.5">vs baseline</th></tr></thead>
        <tbody>{rows.map((a) => (
          <tr key={a.assignment_uid} className={cn('border-t border-border/60', a.beyond_tolerance && 'bg-amber-50/70 dark:bg-amber-950/20')} data-resource={a.resource}>
            <td className="p-1.5 font-semibold">{a.resource}<span className="ml-1 font-mono text-[10px] text-muted-foreground">{a.source}</span></td>
            <td className="p-1.5 text-right font-mono">{reported?.[a.assignment_uid] ?? '—'}</td>
            <td className="p-1.5 text-right font-mono font-bold">{num(a.approved_cumulative)} {a.unit}</td>
            <td className="p-1.5 text-right font-mono">{num(a.incremental)}</td>
            <td className="p-1.5 text-right font-mono">{num(a.baseline_qty)}</td>
            <td className={cn('p-1.5 text-right font-mono', Number(a.overrun_pct) > 0 && 'text-amber-700 dark:text-amber-400 font-bold')}>{Number(a.overrun_pct) > 0 ? `+${num(a.overrun_pct)}%` : '—'}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

export function DecisionPanel({ projectId, claim, activity, onDone }: { projectId: string; claim: ClaimDetail; activity: CatalogActivity | null | undefined; onDone: () => void }) {
  const qc = useQueryClient();
  const decidable = ['EXTRACTED', 'MATCHED', 'VALIDATED', 'DISPUTED'].includes(claim.status);
  const measured = activity?.measured_assignments ?? [];
  const pctOnlyMeasured = claim.claimed_pct !== null && claim.claimed_pct !== undefined && claim.quantities.length === 0 && measured.length > 0;
  const [tab, setTab] = useState<Tab>('APPROVE');
  const [pctMethod, setPctMethod] = useState<'' | 'APPLY_PCT_TO_ASSIGNMENTS'>('');
  const [start, setStart] = useState(claim.claimed_start ?? '');
  const [finish, setFinish] = useState(claim.claimed_finish ?? '');
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [just, setJust] = useState('');
  const [reason, setReason] = useState('');
  const [question, setQuestion] = useState('');
  const [ack, setAck] = useState('');
  const [shortClose, setShortClose] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [done, setDone] = useState<DecisionDone | null>(null);
  const [error, setError] = useState<{ code?: string; text: string } | null>(null);

  useEffect(() => {
    const init: Record<string, string> = {};
    measured.forEach((m) => { const q = claim.quantities.find((x) => x.assignment_uid === m.assignment_uid && x.qty_basis === 'CUMULATIVE'); init[m.assignment_uid] = q?.normalized_qty != null ? String(Number(q.normalized_qty)) : ''; });
    setEdit(init); setDone(null); setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claim.event_id, activity?.activity_uid]);

  const params: DecisionIn | null = useMemo(() => {
    const dates = { ...(start ? { actual_start: start } : {}), ...(finish ? { actual_finish: finish } : {}) };
    if (tab === 'APPROVE') {
      if (pctOnlyMeasured && !pctMethod) return null;
      return { action: 'APPROVE', ...(pctOnlyMeasured ? { method: pctMethod as 'APPLY_PCT_TO_ASSIGNMENTS' } : {}), ...dates };
    }
    if (tab === 'EDIT') {
      const aq: NonNullable<DecisionIn['approved_quantities']> = {};
      Object.entries(edit).forEach(([uid, v]) => { if (v !== '' && Number(v) >= 0) aq[uid] = { cumulative: v }; });
      if (!Object.keys(aq).length) return null;
      return { action: 'EDIT', method: 'MANUAL_QUANTITIES', approved_quantities: aq, justification: just.trim(), ...dates };
    }
    return null;
  }, [tab, pctOnlyMeasured, pctMethod, start, finish, edit, just]);

  const full = params ? { ...params, ...(ack.trim() ? { overrun_ack_note: ack.trim() } : {}), ...(shortClose.trim() ? { short_close_note: shortClose.trim() } : {}) } : null;
  const preview = useQuery<DecisionPreview>({
    queryKey: ['v2', 'decision-preview', projectId, claim.event_id, JSON.stringify(full)], queryFn: () => claimsApi.preview(projectId, claim.event_id, full!),
    enabled: decidable && !!full && !done, retry: false, staleTime: 0, placeholderData: (p) => p,
  });
  const pv = preview.data;
  const needsAck = !!pv?.ok && !!pv.requires_overrun_acknowledgement;
  const needsShort = pv?.ok === false && pv.error?.code === 'FINISH_BELOW_THRESHOLD';
  const reported = useMemo(() => Object.fromEntries(claim.quantities.filter((q) => q.assignment_uid).map((q) => [q.assignment_uid!, `${num(q.reported_qty)} ${q.reported_uom}${q.qty_basis === 'INCREMENTAL' ? ' (since last)' : ''}`])), [claim.quantities]);

  const submit = useMutation({
    mutationFn: async (): Promise<DecisionDone> => {
      if (tab === 'REJECT') return claimsApi.decide(projectId, claim.event_id, { action: 'REJECT', justification: reason.trim() });
      if (tab === 'HOLD') return claimsApi.ask(projectId, claim.event_id, question.trim());
      return claimsApi.decide(projectId, claim.event_id, full!);
    },
    onSuccess: (r) => { setDone(r); setError(null); setConfirm(false); qc.invalidateQueries({ queryKey: ['v2'] }); onDone(); },
    onError: (e) => { setConfirm(false); setError({ code: e instanceof V2Error ? e.code : undefined, text: errText(e) }); },
  });

  const canSend = tab === 'REJECT' ? reason.trim().length >= 3 : tab === 'HOLD' ? question.trim().length >= 3 : !!full && !!pv?.ok && (!needsAck || ack.trim().length >= 3) && (tab !== 'EDIT' || just.trim().length >= 3);
  const verb = tab === 'APPROVE' ? 'Approve' : tab === 'EDIT' ? 'Approve with changes' : tab === 'REJECT' ? 'Reject' : 'Ask a question';

  if (!decidable && !done) {
    return <Notice tone="info" testid="not-decidable">This claim is {claim.status.toLowerCase()} and can no longer be decided. {claim.status === 'APPROVED' ? 'Its approved quantities are in the ledger.' : ''}</Notice>;
  }
  if (done) {
    return (
      <div className="space-y-2" data-testid="decision-done">
        <Notice tone="ok"><b>{done.action === 'HOLD' ? 'Question sent to the engineer.' : done.action === 'REJECT' ? 'Claim rejected.' : 'Decision recorded.'}</b> {done.status === 'APPROVED' && done.result.activity_pct_after !== undefined && <>Activity progress is now <b>{pct(done.result.activity_pct_after)}</b> (was {pct(done.result.activity_pct_before)}).</>}</Notice>
        {done.applied.length > 0 && <AppliedTable rows={done.applied} reported={reported} />}
      </div>
    );
  }
  return (
    <Panel icon={Gavel} title="Your decision" description="Reported figures are never overwritten: your decision stores what you approve beside what was reported." className="border-[#FF7A18]/50">
      <Pills value={tab} onChange={(t) => { setTab(t); setError(null); }} options={[{ value: 'APPROVE', label: 'Approve as reported' }, { value: 'EDIT', label: 'Approve with changes' }, { value: 'REJECT', label: 'Reject' }, { value: 'HOLD', label: 'Ask a question' }]} />

      {(tab === 'APPROVE' || tab === 'EDIT') && (
        <div className="space-y-3">
          {tab === 'APPROVE' && pctOnlyMeasured && (
            <div className="rounded-lg border border-amber-300 dark:border-amber-800 p-3 space-y-1.5 text-xs" data-testid="pct-method">
              <div className="font-bold flex items-center gap-1.5"><ShieldAlert className="w-3.5 h-3.5 text-amber-600" />Percentage-only claim on a measured activity</div>
              <div>The engineer reported <b>{pct(claim.claimed_pct)}</b> without quantities. Nothing is converted automatically: choose how it is applied.</div>
              <label className="flex items-start gap-2 cursor-pointer"><input type="radio" name="pctm" className="mt-0.5 accent-[#FF7A18]" checked={pctMethod === 'APPLY_PCT_TO_ASSIGNMENTS'} onChange={() => setPctMethod('APPLY_PCT_TO_ASSIGNMENTS')} /><span>Apply {pct(claim.claimed_pct)} to <b>each</b> measured resource ({measured.map((m) => m.resource_code).join(', ')}). The resulting quantities are recorded with the decision.</span></label>
              <div className="text-muted-foreground">Or use “Approve with changes” to enter the approved quantities yourself.</div>
            </div>
          )}
          {tab === 'EDIT' && (
            <div className="space-y-2" data-testid="edit-rows">
              <div className="text-xs text-muted-foreground">Enter the approved <b>total to date</b> for each measured resource, in the schedule’s unit. Leave a resource blank to leave it unchanged.</div>
              {measured.map((m) => (
                <div key={m.assignment_uid} className="grid grid-cols-[1fr_auto] gap-2 items-center text-xs">
                  <div><b>{m.resource_name}</b> <span className="font-mono text-[10px] text-muted-foreground">{m.resource_code}</span><div className="text-muted-foreground">reported {reported[m.assignment_uid] ?? '—'} · approved so far {m.approved_cumulative_qty === null ? 'none' : num(m.approved_cumulative_qty)} · baseline {num(m.baseline_qty)} {m.unit_of_measure}</div></div>
                  <div className="flex items-center gap-1.5"><Input type="number" min={0} step="any" className="w-32" aria-label={`Approved quantity of ${m.resource_name}`} value={edit[m.assignment_uid] ?? ''} onChange={(e) => setEdit({ ...edit, [m.assignment_uid]: e.target.value })} /><span className="font-mono w-14">{m.unit_of_measure}</span></div>
                </div>))}
              <div><Label>Why are you changing what was reported? (required)</Label><Textarea rows={2} aria-label="Justification" value={just} onChange={(e) => setJust(e.target.value)} /></div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div><Label hint="(optional)">Actual start</Label><Input type="date" aria-label="Approved actual start" value={start} onChange={(e) => setStart(e.target.value)} /></div>
            <div><Label hint="(optional)">Actual finish</Label><Input type="date" aria-label="Approved actual finish" value={finish} onChange={(e) => setFinish(e.target.value)} /></div>
          </div>

          <div className="space-y-2" data-testid="preview">
            <div className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Effect of this decision (preview — nothing is saved)</div>
            {!full && <div className="text-xs text-muted-foreground">{tab === 'EDIT' ? 'Enter at least one approved quantity.' : 'Choose how the percentage is applied.'}</div>}
            {preview.isFetching && !pv && <Loading what="Calculating…" />}
            {pv && pv.ok && pv.applied && pv.result && (
              <div className={cn('space-y-2', preview.isFetching && 'opacity-60')}>
                <div className="text-xs">Method <b className="font-mono">{pv.method}</b> · activity progress <b>{pct((pv.result as DecisionResult).activity_pct_before)}</b> → <b data-testid="pct-after">{pct((pv.result as DecisionResult).activity_pct_after)}</b></div>
                {pv.applied.length > 0 && <AppliedTable rows={pv.applied} reported={reported} />}
              </div>
            )}
            {pv && !pv.ok && <Notice tone={needsShort ? 'warn' : 'bad'} testid="preview-error"><b className="font-mono text-[10px] mr-1">{pv.error?.code}</b>{pv.error?.message}</Notice>}
            {needsAck && (
              <div className="rounded-lg border border-amber-300 dark:border-amber-800 p-3 space-y-1.5" data-testid="overrun-ack">
                <div className="text-xs font-bold flex items-center gap-1.5"><ShieldAlert className="w-3.5 h-3.5 text-amber-600" />Above the baseline by more than the project tolerance ({pct(pv!.result!.tolerance_pct)})</div>
                <div className="text-xs text-muted-foreground">The quantity is recorded as approved, not capped (progress shows at most 100%). You must acknowledge it; your name and this note stay with the decision.</div>
                <Textarea rows={2} aria-label="Over-baseline acknowledgement" placeholder="Why is this quantity above the baseline?" value={ack} onChange={(e) => setAck(e.target.value)} />
              </div>
            )}
            {needsShort && (
              <div className="rounded-lg border border-amber-300 dark:border-amber-800 p-3 space-y-1.5" data-testid="short-close">
                <div className="text-xs font-bold">Finishing below the completion threshold</div>
                <Textarea rows={2} aria-label="Short-close note" placeholder="Why is the activity closed before the threshold?" value={shortClose} onChange={(e) => setShortClose(e.target.value)} />
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'REJECT' && <div><Label>Reason (required; the engineer will see it)</Label><Textarea rows={3} aria-label="Rejection reason" value={reason} onChange={(e) => setReason(e.target.value)} /></div>}
      {tab === 'HOLD' && <div><Label>Question for the engineer (the claim stays open and does not count)</Label><Textarea rows={3} aria-label="Clarification question" value={question} onChange={(e) => setQuestion(e.target.value)} /></div>}

      {error && <Notice tone="bad" testid="decision-error">{error.text}</Notice>}
      <Button onClick={() => (tab === 'REJECT' || tab === 'HOLD' ? submit.mutate() : setConfirm(true))} disabled={!canSend || submit.isPending} variant={tab === 'REJECT' ? 'destructive' : 'default'} className="gap-1.5 cursor-pointer" data-testid="decide-btn">
        {submit.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : tab === 'REJECT' ? <XCircle className="w-4 h-4" /> : tab === 'HOLD' ? <HelpCircle className="w-4 h-4" /> : <CheckCircle2 className="w-4 h-4" />}{verb}
      </Button>

      <Dialog open={confirm} onOpenChange={setConfirm}>
        <DialogContent className="max-w-md" data-testid="confirm-decision">
          <DialogHeader><DialogTitle>{verb}?</DialogTitle><DialogDescription>This creates approved progress in the ledger, notifies the engineer and is recorded in the audit trail. It cannot be undone; a mistake is corrected by a governed reopen.</DialogDescription></DialogHeader>
          {pv?.ok && pv.result && <div className="text-xs">Activity progress will go from <b>{pct(pv.result.activity_pct_before)}</b> to <b>{pct(pv.result.activity_pct_after)}</b>.</div>}
          <DialogFooter><Button variant="outline" onClick={() => setConfirm(false)} className="cursor-pointer">Cancel</Button><Button onClick={() => submit.mutate()} disabled={submit.isPending} className="gap-1.5 cursor-pointer" data-testid="confirm-decide">{submit.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : null}Confirm</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </Panel>
  );
}

/** re-match to another activity / bind unbound quantities (Supervisor, before a decision) */
export function MatchTools({ projectId, claim, activity, onChanged }: { projectId: string; claim: ClaimDetail; activity: CatalogActivity | null | undefined; onChanged: () => void }) {
  const qc = useQueryClient();
  const [pick, setPick] = useState<CatalogActivity | null>(null);
  const [binds, setBinds] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const unbound = claim.quantities.filter((q) => !q.assignment_uid);
  const done = (t: string) => { setMsg({ ok: true, text: t }); qc.invalidateQueries({ queryKey: ['v2'] }); onChanged(); };
  const rematch = useMutation({ mutationFn: () => claimsApi.rematch(projectId, claim.event_id, pick!.activity_uid), onSuccess: () => { setPick(null); done('Claim re-matched; its quantities were re-derived.'); }, onError: (e) => setMsg({ ok: false, text: errText(e) }) });
  const bind = useMutation({ mutationFn: () => claimsApi.bind(projectId, claim.event_id, Object.fromEntries(Object.entries(binds).filter(([, v]) => v))), onSuccess: () => { setBinds({}); done('Quantities bound.'); }, onError: (e) => setMsg({ ok: false, text: errText(e) }) });
  if (!['EXTRACTED', 'MATCHED', 'VALIDATED', 'DISPUTED'].includes(claim.status)) return null;
  return (
    <Panel title="Matching" description={activity ? `Matched to ${activity.external_activity_id} · ${activity.activity_name}` : 'Not matched to an activity yet'}>
      {unbound.length > 0 && (
        <div className="space-y-2 text-xs" data-testid="bind-tools">
          <Notice tone="warn">{unbound.length} reported quantit{unbound.length === 1 ? 'y is' : 'ies are'} not bound to a measured resource. Bind {unbound.length === 1 ? 'it' : 'them'} here, or enter approved quantities under “Approve with changes”.</Notice>
          {unbound.map((q) => (
            <div key={q.claim_quantity_id} className="flex items-center gap-2"><span className="w-48">{num(q.reported_qty)} {q.reported_uom}</span>
              <select className={F('w-64')} aria-label={`Bind ${num(q.reported_qty)} ${q.reported_uom}`} value={binds[q.claim_quantity_id] ?? ''} onChange={(e) => setBinds({ ...binds, [q.claim_quantity_id]: e.target.value })}><option value="">Choose a resource…</option>{(activity?.measured_assignments ?? []).map((m) => <option key={m.assignment_uid} value={m.assignment_uid}>{m.resource_name} ({m.unit_of_measure})</option>)}</select></div>))}
          <Button size="sm" disabled={!Object.values(binds).some(Boolean) || bind.isPending} onClick={() => bind.mutate()} className="cursor-pointer">Bind</Button>
        </div>
      )}
      <details className="text-xs"><summary className="cursor-pointer font-bold">Match to a different activity</summary>
        <div className="mt-2 space-y-2"><ActivityPicker projectId={projectId} value={pick} onChange={setPick} />
          <Button size="sm" variant="outline" disabled={!pick || rematch.isPending} onClick={() => rematch.mutate()} className="cursor-pointer" data-testid="rematch-btn">Re-match claim</Button></div></details>
      {msg && <Notice tone={msg.ok ? 'ok' : 'bad'}>{msg.text}</Notice>}
    </Panel>
  );
}
