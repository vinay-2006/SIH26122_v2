import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ClipboardList } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useProjectState } from '@/context/ProjectContext';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { claimsApi } from '@/v2/api/endpoints';
import { ClaimStatusPill, Loading, PageHeader, Pills, QueryError, day, pct } from '@/v2/ui';

type F = 'DECIDABLE' | 'MATCHED' | 'EXTRACTED' | 'DISPUTED';
const PAGE = 50;

export default function ReviewQueuePage() {
  const { currentProject } = useProjectState();
  const { projectId } = useV2Project();
  const nav = useNavigate();
  const [f, setF] = useState<F>('DECIDABLE');
  const [offset, setOffset] = useState(0);
  const q = useQuery({ queryKey: ['v2', 'queue', projectId, f, offset], queryFn: ({ signal }) => claimsApi.queue(projectId, { status: f === 'DECIDABLE' ? undefined : [f], limit: PAGE, offset }, { signal }), retry: false, placeholderData: (p) => p });
  const counts = useQuery({ queryKey: ['v2', 'claim-counts', projectId], queryFn: () => claimsApi.counts(projectId), retry: false });
  return (
    <div className="space-y-6" data-testid="review-page">
      <PageHeader project={`${currentProject?.name} (${currentProject?.code})`} title="Review Queue" subtitle="Highest priority first. Open a claim to compare what was reported with what you would approve — nothing counts toward progress until you approve it." />
      {counts.data && <div className="flex flex-wrap gap-2 text-[11px]" data-testid="queue-counts">{(['EXTRACTED', 'MATCHED', 'VALIDATED', 'DISPUTED', 'APPROVED', 'REJECTED', 'WITHDRAWN'] as const).map((k) => <span key={k} className="px-2 py-0.5 rounded-md border border-border font-mono">{k.toLowerCase()}: <b>{counts.data![k]}</b></span>)}</div>}
      <Pills value={f} onChange={(v) => { setF(v); setOffset(0); }} options={[{ value: 'DECIDABLE', label: 'Awaiting decision' }, { value: 'MATCHED', label: 'Matched' }, { value: 'EXTRACTED', label: 'Needs matching' }, { value: 'DISPUTED', label: 'Clarification' }]} />
      {q.isPending && <Loading />}
      {q.error && <QueryError error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.items.length === 0 && <EmptyState icon={ClipboardList} title="Nothing waiting" description="No claims need a decision right now." />}
      {q.data && q.data.items.length > 0 && (
        <div className="rounded-xl border border-border overflow-x-auto bg-card">
          <Table data-testid="queue-table">
            <TableHeader><TableRow><TableHead>Date</TableHead><TableHead>Activity</TableHead><TableHead>Reported</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Priority</TableHead></TableRow></TableHeader>
            <TableBody>{q.data.items.map((c) => (
              <TableRow key={c.event_id} className="cursor-pointer" onClick={() => nav(`/claims/${c.event_id}`)} data-testid="queue-row" data-activity={c.external_activity_id ?? ''}>
                <TableCell className="whitespace-nowrap">{day(c.event_date)}</TableCell>
                <TableCell><div className="font-bold">{c.external_activity_id ?? <span className="text-amber-600">not matched</span>}</div></TableCell>
                <TableCell className="text-[11px]">{c.claimed_pct !== null && c.claimed_pct !== undefined ? `${pct(c.claimed_pct)} (percent only)` : 'quantities'}{c.errors ? ` · ${c.errors} check(s) failed` : ''}</TableCell>
                <TableCell><ClaimStatusPill status={c.status} clarification={c.clarification_status} /></TableCell>
                <TableCell className="text-right font-mono text-[11px]" title={c.priority_reasons ?? ''}>{Number(c.priority_score ?? 0)}</TableCell>
              </TableRow>))}</TableBody>
          </Table>
        </div>
      )}
      <div className="flex justify-end gap-2"><Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className="cursor-pointer">Previous</Button><Button size="sm" variant="outline" disabled={!q.data?.next_offset} onClick={() => setOffset(q.data!.next_offset!)} className="cursor-pointer">Next</Button></div>
    </div>
  );
}
