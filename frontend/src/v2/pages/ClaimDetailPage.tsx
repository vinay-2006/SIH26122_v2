import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, FileCheck2, Loader2, MessageSquareReply, Undo2 } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { useProjectState } from '@/context/ProjectContext';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { claimsApi } from '@/v2/api/endpoints';
import type { ClaimDetail } from '@/v2/api/types';
import { ClaimForm } from '@/v2/claims/ClaimForm';
import { AppliedTable, DecisionPanel, MatchTools } from '@/v2/claims/DecisionPanel';
import { DocLink, EvidenceUploader, useActivityByUid, ValidationList } from '@/v2/claims/shared';
import { ClaimStatusPill, Label, Loading, Notice, PageHeader, Panel, QueryError, day, errText, isPending, num, pct, when } from '@/v2/ui';

function Facts({ c, projectId, activity }: { c: ClaimDetail; projectId: string; activity?: { external_activity_id: string; activity_name: string } | null }) {
  return (
    <Panel title="What was reported" description="Exactly as filed. These figures never change after submission.">
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div><span className="text-muted-foreground">Activity</span><div className="font-bold">{activity ? `${activity.external_activity_id} · ${activity.activity_name}` : c.matched_activity_uid ? '…' : 'not matched yet'}</div></div>
        <div><span className="text-muted-foreground">Date of the work</span><div className="font-semibold">{day(c.event_date)}</div></div>
        <div><span className="text-muted-foreground">Claimed start / finish</span><div className="font-semibold">{day(c.claimed_start)} / {day(c.claimed_finish)}</div></div>
        <div><span className="text-muted-foreground">Location · channel</span><div className="font-semibold">{c.location ?? '—'} · {c.input_channel}</div></div>
      </div>
      <div className="text-xs rounded-lg bg-secondary/50 p-2.5 whitespace-pre-wrap" data-testid="claim-text">{c.raw_claim_text}</div>
      {c.claimed_pct !== null && c.claimed_pct !== undefined && <div className="text-xs">Reported percent complete: <b data-testid="claimed-pct">{pct(c.claimed_pct)}</b> <span className="text-muted-foreground">(percent only; stored as reported)</span></div>}
      {c.quantities.length > 0 && (
        <table className="w-full text-xs" data-testid="reported-quantities"><thead className="text-[10px] uppercase tracking-wider text-muted-foreground"><tr><th className="text-left py-1">Resource</th><th className="text-right">Reported</th><th className="text-left pl-3">Basis</th><th className="text-right">In schedule unit</th></tr></thead>
          <tbody>{c.quantities.map((q) => <tr key={q.claim_quantity_id} className="border-t border-border/60"><td className="py-1">{q.resource_code ?? <span className="text-amber-600">not bound</span>}</td><td className="text-right font-mono">{num(q.reported_qty)} {q.reported_uom}</td><td className="pl-3">{q.qty_basis === 'CUMULATIVE' ? 'total to date' : 'since last report'}</td><td className="text-right font-mono">{q.normalized_qty != null ? `${num(q.normalized_qty)} ${q.normalized_uom}` : '—'}</td></tr>)}</tbody></table>
      )}
      <div className="text-xs"><div className="font-bold mb-0.5">Evidence</div>{c.evidence.length === 0 ? <span className="text-muted-foreground">None attached.</span> : <ul className="space-y-0.5">{c.evidence.map((e) => <li key={e.document_id}><DocLink projectId={projectId} id={e.document_id} name={e.file_name} /> <span className="text-muted-foreground font-mono text-[10px]">{e.kind}</span></li>)}</ul>}</div>
    </Panel>
  );
}

function History({ c, reported }: { c: ClaimDetail; reported: Record<string, string> }) {
  if (c.decisions.length === 0) return null;
  return (
    <Panel icon={FileCheck2} title="Decisions" description="What was approved, next to what was reported">
      {c.decisions.map((d) => (
        <div key={d.decision_id} className="space-y-1.5 text-xs border-b border-border/60 pb-3 last:border-0" data-testid="decision-entry" data-action={d.action}>
          <div><b>{d.action}</b> <span className="font-mono text-[10px] text-muted-foreground">{d.method}</span> · {when(d.decided_at)}</div>
          {d.justification && <div className="text-muted-foreground">“{d.justification}”</div>}
          {d.overrun_ack && <Notice tone="warn">Over-baseline approval acknowledged by the deciding Supervisor: “{d.overrun_ack_note}”</Notice>}
          {d.action !== 'HOLD' && d.action !== 'REJECT' && d.result.activity_pct_after !== undefined && <div>Activity progress {pct(d.result.activity_pct_before)} → <b>{pct(d.result.activity_pct_after)}</b></div>}
          {d.applied?.length > 0 && <AppliedTable rows={d.applied} reported={reported} />}
        </div>))}
    </Panel>
  );
}

function EngineerActions({ projectId, c, activity }: { projectId: string; c: ClaimDetail; activity: any }) {
  const qc = useQueryClient();
  const [dlg, setDlg] = useState<null | 'withdraw' | 'answer' | 'correct'>(null);
  const [text, setText] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [docs, setDocs] = useState<{ document_id: string }[]>([]);
  const [uploading, setUploading] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ['v2'] });
  const withdraw = useMutation({ mutationFn: () => claimsApi.withdraw(projectId, c.event_id, text.trim()), onSuccess: () => { setDlg(null); setOk('Claim withdrawn. It stays on record with its evidence.'); refresh(); }, onError: (e) => setErr(errText(e)) });
  const answer = useMutation({ mutationFn: () => claimsApi.answer(projectId, c.event_id, text.trim(), docs.map((d) => d.document_id)), onSuccess: () => { setDlg(null); setOk('Answer sent to the Supervisor.'); refresh(); }, onError: (e) => setErr(errText(e)) });
  const attach = useMutation({ mutationFn: (id: string) => claimsApi.attach(projectId, c.event_id, id), onSuccess: () => { setOk('Evidence attached.'); refresh(); }, onError: (e) => setErr(errText(e)) });
  const pending = isPending(c.status);
  const asked = c.status === 'DISPUTED' && c.clarification_status === 'ASKED';
  const open = (k: 'withdraw' | 'answer' | 'correct') => { setDlg(k); setText(''); setErr(null); setOk(null); setDocs([]); };
  return (
    <Panel title="Your actions">
      {asked && <Notice tone="warn" testid="clarification-question"><b>The Supervisor asked:</b> {c.clarification_question}</Notice>}
      {c.clarification_status === 'ANSWERED' && c.clarification_answer && <Notice tone="info">You answered: {c.clarification_answer}</Notice>}
      {ok && <Notice tone="ok">{ok}</Notice>}
      {err && !dlg && <Notice tone="bad">{err}</Notice>}
      <div className="flex flex-wrap gap-2">
        {asked && <Button onClick={() => open('answer')} className="gap-1.5 cursor-pointer" data-testid="answer-btn"><MessageSquareReply className="w-4 h-4" />Answer the question</Button>}
        {pending && <Button variant="outline" onClick={() => open('withdraw')} className="gap-1.5 cursor-pointer" data-testid="withdraw-btn"><Undo2 className="w-4 h-4" />Withdraw this claim</Button>}
        {c.status === 'REJECTED' && <Button onClick={() => open('correct')} className="gap-1.5 cursor-pointer" data-testid="correct-btn">Submit a corrected claim</Button>}
      </div>
      {pending && <div className="text-xs"><div className="font-bold mb-1">Add more evidence</div><EvidenceUploader projectId={projectId} label="Attach a file to this claim" onChange={(d, u) => { setUploading(u); const fresh = d.filter((x) => !c.evidence.some((e) => e.document_id === x.document_id)); fresh.forEach((x) => { if (!attach.isPending) attach.mutate(x.document_id); }); }} /></div>}
      {c.status === 'APPROVED' && <div className="text-xs text-muted-foreground">This claim was approved and can no longer be changed or withdrawn.</div>}

      <Dialog open={dlg === 'withdraw' || dlg === 'answer'} onOpenChange={(o) => { if (!o) setDlg(null); }}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>{dlg === 'withdraw' ? 'Withdraw this claim?' : 'Answer the Supervisor'}</DialogTitle>
            <DialogDescription>{dlg === 'withdraw' ? 'Only a pending claim can be withdrawn. The claim and its evidence are kept for the record; a withdrawn claim never counts toward progress.' : c.clarification_question}</DialogDescription></DialogHeader>
          <div><Label>{dlg === 'withdraw' ? 'Reason (required)' : 'Your answer (required)'}</Label><Textarea rows={3} aria-label={dlg === 'withdraw' ? 'Withdrawal reason' : 'Answer'} value={text} onChange={(e) => setText(e.target.value)} /></div>
          {dlg === 'answer' && <EvidenceUploader projectId={projectId} onChange={(d, u) => { setDocs(d); setUploading(u); }} />}
          {err && <Notice tone="bad">{err}</Notice>}
          <DialogFooter><Button variant="outline" onClick={() => setDlg(null)} className="cursor-pointer">Cancel</Button>
            <Button onClick={() => (dlg === 'withdraw' ? withdraw.mutate() : answer.mutate())} disabled={text.trim().length < 3 || withdraw.isPending || answer.isPending || uploading} className="gap-1.5 cursor-pointer" data-testid="confirm-action">
              {(withdraw.isPending || answer.isPending) && <Loader2 className="w-4 h-4 animate-spin" />}{dlg === 'withdraw' ? 'Withdraw' : 'Send answer'}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={dlg === 'correct'} onOpenChange={(o) => { if (!o) setDlg(null); }}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>Corrected claim</DialogTitle><DialogDescription>Rejected: “{c.decisions.find((d) => d.action === 'REJECT')?.justification ?? 'see the decision'}”. File the corrected figures as a new claim.</DialogDescription></DialogHeader>
          {dlg === 'correct' && <ClaimForm projectId={projectId} correctionOf={c.event_id} initialActivity={activity ?? null} onSubmitted={() => { refresh(); }} />}
        </DialogContent>
      </Dialog>
    </Panel>
  );
}

export default function ClaimDetailPage() {
  const { id } = useParams();
  const { currentProject, can } = useProjectState();
  const { projectId } = useV2Project();
  const q = useQuery({ queryKey: ['v2', 'claim', projectId, id], queryFn: ({ signal }) => claimsApi.get(projectId, id!, { signal }), enabled: !!id, retry: false });
  const c = q.data;
  const act = useActivityByUid(projectId, c?.matched_activity_uid);
  const reviewer = can('REVIEW_CLAIM');
  const reported = Object.fromEntries((c?.quantities ?? []).filter((x) => x.assignment_uid).map((x) => [x.assignment_uid!, `${num(x.reported_qty)} ${x.reported_uom}`]));
  return (
    <div className="space-y-6" data-testid="claim-detail-page">
      <PageHeader project={`${currentProject?.name} (${currentProject?.code})`} title="Claim" subtitle={c ? <>Filed {when(c.created_at)} {c.resubmits_event_id && <> · a correction of an earlier rejected claim</>}</> : undefined}
        actions={<><Link to={reviewer ? '/review' : '/claims/mine'} className="inline-flex items-center gap-1 text-xs font-bold text-primary underline"><ArrowLeft className="w-3.5 h-3.5" />Back</Link>{c && <ClaimStatusPill status={c.status} clarification={c.clarification_status} />}</>} />
      {q.isPending && <Loading />}
      {q.error && <QueryError error={q.error} onRetry={() => q.refetch()} />}
      {c && (
        <div className="grid grid-cols-1 xl:grid-cols-5 gap-6">
          <div className="xl:col-span-3 space-y-6">
            <Facts c={c} projectId={projectId} activity={act.data} />
            {c.validations.length > 0 && <Panel title="Automatic checks"><ValidationList items={c.validations.map((v) => ({ rule: v.rule_code, severity: v.severity, message: v.description }))} /></Panel>}
            {c.clarification_question && <Panel title="Clarification"><div className="text-xs space-y-1"><div><b>Question:</b> {c.clarification_question}</div><div><b>Answer:</b> {c.clarification_answer ?? <span className="text-muted-foreground">waiting for the engineer</span>}</div></div></Panel>}
            <History c={c} reported={reported} />
          </div>
          <div className="xl:col-span-2 space-y-6">
            {reviewer ? (
              <>
                <MatchTools projectId={projectId} claim={c} activity={act.data} onChanged={() => q.refetch()} />
                {c.matched_activity_uid && act.isPending ? <Loading /> : <DecisionPanel projectId={projectId} claim={c} activity={act.data} onDone={() => q.refetch()} />}
              </>
            ) : <EngineerActions projectId={projectId} c={c} activity={act.data} />}
          </div>
        </div>
      )}
    </div>
  );
}
