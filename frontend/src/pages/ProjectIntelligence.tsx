import React, { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Bot, AlertTriangle, ShieldCheck, Send, Loader2, RefreshCw, Info } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useProject } from '@/context/ProjectContext';
import { agentApi, type AgentFinding, type AnswerSource, type FindingSeverity } from '@/api/intelligence';
import { useSearchParams } from 'react-router-dom';
import { IS_V2 } from '@/config';
import { useProjectState } from '@/context/ProjectContext';
import KnowledgePage from '@/v2/pages/KnowledgePage';

const SEVERITY: Record<FindingSeverity, string> = {
  CRITICAL: 'bg-red-500/10 text-red-600 dark:text-red-400 border-red-400/50',
  HIGH: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-400/50',
  MEDIUM: 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-400/50',
  LOW: 'bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-400/50',
  INFO: 'bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-400/50',
};

function Finding({ f }: { f: AgentFinding }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-border rounded-lg bg-card/60 p-3 space-y-1.5">
      <button type="button" onClick={() => setOpen(!open)} className="w-full text-left flex items-start justify-between gap-3 cursor-pointer">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={cn('text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase', SEVERITY[f.severity] ?? SEVERITY.LOW)}>{f.severity}</span>
            <span className="text-[10px] font-mono text-muted-foreground">{f.category.replace(/_/g, ' ')}</span>
          </div>
          <div className="text-sm font-semibold text-foreground">{f.title}</div>
        </div>
        <span className="text-[11px] text-muted-foreground shrink-0">{open ? 'Hide' : 'Why & evidence'}</span>
      </button>
      <p className="text-xs text-muted-foreground">{f.description}</p>
      {open && (
        <div className="pt-1.5 space-y-2 text-xs">
          <p><span className="font-semibold text-foreground">Why it matters: </span>{f.why_it_matters}</p>
          <p><span className="font-semibold text-foreground">Recommended action: </span>{f.recommended_action}</p>
          {f.evidence.length > 0 && (
            <div className="space-y-1">
              <div className="font-semibold text-foreground">Evidence</div>
              {f.evidence.map((e, i) => (
                <div key={i} className="font-mono text-[11px] px-2 py-1 rounded bg-secondary/50 border border-border">
                  {e.entity_type} · {e.reference_code ?? e.entity_id}{e.details ? ` — ${e.details}` : ''}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Supervising agent: evidence-backed briefing + questions. Advisory only; every claim links to a record. */
const PROV: Record<string, string> = { FROM_RECORDS: 'from the project records', AUTHORED: 'authored', ILLUSTRATIVE: 'illustrative, not a contractual fact', NOT_SPECIFIED: 'not specified' };

function SourceChips({ sources }: { sources?: AnswerSource[] }) {
  if (!sources || sources.length === 0) return null;
  return (
    <div data-testid="answer-sources" className="flex flex-wrap items-center gap-1.5">
      <span className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">Sources</span>
      {sources.map((s, i) => (
        <span key={i} title={s.as_of ? `Computed ${s.as_of}` : undefined}
          className={cn('text-[10px] font-semibold px-2 py-0.5 rounded-full border', s.kind === 'LIVE_DATA'
            ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-400/50' : 'bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-400/50')}>
          {s.kind === 'LIVE_DATA' ? `Live: ${s.label}` : `Project knowledge: ${s.section_label} · ${s.title} (${PROV[s.provenance ?? ''] ?? s.provenance})`}
        </span>
      ))}
    </div>
  );
}

/** The Project Manager gets one page with two tabs: what the supervising agent says about the project, and the project's authored knowledge that the agent and the Time Agent read.
 *  Everyone else (Site Engineer) sees the agent view alone. The briefing is only requested while its tab is open. */
export default function ProjectIntelligence() {
  const { can } = useProjectState();
  const [params, setParams] = useSearchParams();
  const isPm = IS_V2 && can('MANAGE_PROJECT');
  if (!isPm) return <IntelligenceView />;
  const tab = params.get('tab') === 'knowledge' ? 'knowledge' : 'intelligence';
  const choose = (t: 'intelligence' | 'knowledge') => setParams(t === 'knowledge' ? { tab: 'knowledge' } : {}, { replace: true });
  const TabBtn = ({ id, label }: { id: 'intelligence' | 'knowledge'; label: string }) => (
    <button type="button" role="tab" aria-selected={tab === id} data-testid={`pi-tab-${id}`} onClick={() => choose(id)}
      className={cn('px-4 h-9 rounded-lg text-sm font-bold cursor-pointer border transition-colors',
        tab === id ? 'bg-primary text-primary-foreground border-primary' : 'bg-card border-border text-muted-foreground hover:text-foreground')}>{label}</button>
  );
  return (
    <div className="space-y-5 max-w-5xl">
      <div role="tablist" aria-label="Project Intelligence" className="flex gap-2">
        <TabBtn id="intelligence" label="Intelligence" />
        <TabBtn id="knowledge" label="Project Knowledge" />
      </div>
      {tab === 'knowledge' ? <KnowledgePage /> : <IntelligenceView />}
    </div>
  );
}

function IntelligenceView() {
  const { currentProject } = useProject();
  const [question, setQuestion] = useState('');
  const briefing = useQuery({
    queryKey: ['v7', 'agent-briefing', currentProject.id],
    queryFn: () => agentApi.briefing(currentProject.id),
    staleTime: 60_000,
    retry: false,
  });
  const ask = useMutation({ mutationFn: (q: string) => agentApi.ask(currentProject.id, q) });
  const b = briefing.data;

  return (
    <div className="space-y-5 max-w-5xl">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-primary/10 border border-primary/20 text-primary"><Bot className="w-6 h-6" /></div>
          <div>
            <h1 className="text-xl font-black tracking-tight">Project Intelligence</h1>
            <p className="text-xs text-muted-foreground">
              Supervising agent — reads the project's real state and explains it. It cannot approve, reject or change anything.
            </p>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={() => briefing.refetch()} disabled={briefing.isFetching} className="gap-1.5 cursor-pointer">
          <RefreshCw className={cn('w-3.5 h-3.5', briefing.isFetching && 'animate-spin')} /> Refresh
        </Button>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-primary" /> Supervisory briefing
            {b && (
              <span className={cn('text-[10px] font-bold px-2 py-0.5 rounded-full border', b.agent_status === 'HEALTHY'
                ? 'bg-emerald-500/10 text-emerald-600 border-emerald-400/50' : 'bg-amber-500/10 text-amber-600 border-amber-400/50')}>
                {b.agent_status === 'HEALTHY' ? 'AI-assisted' : 'Deterministic facts only (AI unavailable)'}
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {briefing.isLoading && <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin" /> Assembling briefing from project records…</div>}
          {briefing.isError && <div className="text-xs text-red-600">Briefing unavailable: {briefing.error instanceof Error ? briefing.error.message : 'error'}</div>}
          {b && (
            <>
              <p className="text-sm leading-relaxed">{b.summary}</p>
              {b.recommended_reviews.length > 0 && (
                <div className="text-xs">
                  <div className="font-semibold mb-1">Suggested reviews for a human</div>
                  <ul className="list-disc pl-5 space-y-0.5 text-muted-foreground">
                    {b.recommended_reviews.slice(0, 8).map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                </div>
              )}
              <div className="space-y-2" data-testid="project-context">
                <div className="text-xs font-bold uppercase tracking-wider">Project context <span className="font-normal normal-case text-muted-foreground">(authored text, kept apart from the live figures above)</span></div>
                {b.project_context && b.project_context.length > 0 ? b.project_context.map((c, i) => (
                  <div key={i} className="rounded-lg border border-border p-2.5 text-xs space-y-0.5">
                    <div className="font-semibold">{c.section_label} · {c.title} <span className="font-normal text-muted-foreground">({PROV[c.provenance ?? ''] ?? c.provenance})</span></div>
                    <div className="text-muted-foreground leading-relaxed">{c.excerpt}</div>
                  </div>
                )) : <div className="text-xs text-muted-foreground">No project knowledge has been written for this project yet. The Project Manager adds it under Project Knowledge.</div>}
              </div>
              <div className="space-y-2">
                <div className="text-xs font-bold uppercase tracking-wider flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-500" /> Findings ({b.findings.length})
                </div>
                {b.findings.length === 0
                  ? <div className="text-xs text-muted-foreground">No findings: nothing needs attention.</div>
                  : b.findings.map((f) => <Finding key={f.finding_id} f={f} />)}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-sm">Ask about this project</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <form
            onSubmit={(e) => { e.preventDefault(); if (question.trim()) ask.mutate(question.trim()); }}
            className="flex gap-2"
          >
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              maxLength={500}
              placeholder="e.g. What is holding up the foundation pour, and what should I review first?"
              aria-label="Question for the supervising agent"
              className="flex-1 h-10 rounded-md border border-border bg-background px-3 text-sm"
            />
            <Button type="submit" disabled={ask.isPending || !question.trim()} className="gap-1.5 cursor-pointer">
              {ask.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} Ask
            </Button>
          </form>
          {ask.isError && <div className="text-xs text-red-600">{ask.error instanceof Error ? ask.error.message : 'The agent could not answer.'}</div>}
          {ask.data && (
            <div className="space-y-2">
              <div className="text-sm leading-relaxed whitespace-pre-wrap">{ask.data.answer}</div>
              <SourceChips sources={ask.data.sources} />
              {ask.data.recommendations.length > 0 && (
                <ul className="list-disc pl-5 text-xs text-muted-foreground space-y-0.5">{ask.data.recommendations.map((r, i) => <li key={i}>{r}</li>)}</ul>
              )}
              {ask.data.evidence.length > 0 && (
                <div className="space-y-1">
                  <div className="text-xs font-semibold">Evidence</div>
                  {ask.data.evidence.slice(0, 8).map((e, i) => (
                    <div key={i} className="font-mono text-[11px] px-2 py-1 rounded bg-secondary/50 border border-border">
                      {e.entity_type} · {e.reference_code ?? e.entity_id}{e.details ? ` — ${e.details}` : ''}
                    </div>
                  ))}
                </div>
              )}
              {ask.data.agent_status === 'DEGRADED' && (
                <div className="text-[11px] text-amber-600 flex items-center gap-1"><Info className="w-3 h-3" /> This answer is composed from the project's live data and authored knowledge; no figure is generated.</div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
