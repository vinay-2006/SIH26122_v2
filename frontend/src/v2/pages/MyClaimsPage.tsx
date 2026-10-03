import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ClipboardList } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useProjectState } from '@/context/ProjectContext';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { claimsApi } from '@/v2/api/endpoints';
import type { ClaimListItem } from '@/v2/api/types';
import { useActivityMap } from '@/v2/claims/shared';
import { ClaimStatusPill, Loading, PageHeader, Pills, QueryError, day, isPending, pct } from '@/v2/ui';

type F = 'ALL' | 'PENDING' | 'CLARIFY' | 'APPROVED' | 'REJECTED' | 'WITHDRAWN';
const PAGE = 50;

export default function MyClaimsPage() {
  const { currentProject } = useProjectState();
  const { projectId } = useV2Project();
  const nav = useNavigate();
  const [f, setF] = useState<F>('ALL');
  const [offset, setOffset] = useState(0);
  const status = f === 'APPROVED' || f === 'REJECTED' || f === 'WITHDRAWN' ? f : undefined;
  const q = useQuery({ queryKey: ['v2', 'my-claims', projectId, status, offset], queryFn: ({ signal }) => claimsApi.mine(projectId, { status, limit: PAGE, offset }, { signal }), retry: false, placeholderData: (p) => p });
  const acts = useActivityMap(projectId);
  let items: ClaimListItem[] = q.data?.items ?? [];
  if (f === 'PENDING') items = items.filter((c) => isPending(c.status) && c.status !== 'DISPUTED');
  if (f === 'CLARIFY') items = items.filter((c) => c.status === 'DISPUTED');
  const needsAnswer = (q.data?.items ?? []).filter((c) => c.status === 'DISPUTED' && c.clarification_status === 'ASKED').length;
  return (
    <div className="space-y-6" data-testid="my-claims-page">
      <PageHeader project={`${currentProject?.name} (${currentProject?.code})`} title="My Claims" subtitle="Only your own claims. Pending, rejected, withdrawn and clarification-requested claims do not count toward project progress."
        actions={<Link to="/claims/new"><Button className="cursor-pointer">New claim</Button></Link>} />
      {needsAnswer > 0 && <div className="text-xs font-bold text-amber-700 dark:text-amber-400" data-testid="needs-answer">{needsAnswer} claim(s) have a question from the Supervisor waiting for your answer.</div>}
      <Pills value={f} onChange={(v) => { setF(v); setOffset(0); }} options={[{ value: 'ALL', label: 'All' }, { value: 'PENDING', label: 'Pending' }, { value: 'CLARIFY', label: 'Clarification' }, { value: 'APPROVED', label: 'Approved' }, { value: 'REJECTED', label: 'Rejected' }, { value: 'WITHDRAWN', label: 'Withdrawn' }]} />
      {q.isPending && <Loading />}
      {q.error && <QueryError error={q.error} onRetry={() => q.refetch()} />}
      {q.data && items.length === 0 && <EmptyState icon={ClipboardList} title="No claims here" description="Claims you submit appear here with their current status." />}
      {items.length > 0 && (
        <div className="rounded-xl border border-border overflow-x-auto bg-card">
          <Table data-testid="my-claims-table">
            <TableHeader><TableRow><TableHead>Date</TableHead><TableHead>Activity</TableHead><TableHead>Reported</TableHead><TableHead>Status</TableHead><TableHead>Submitted</TableHead></TableRow></TableHeader>
            <TableBody>
              {items.map((c) => {
                const a = c.matched_activity_uid ? acts.get(c.matched_activity_uid) : undefined;
                return (
                  <TableRow key={c.event_id} className="cursor-pointer" onClick={() => nav(`/claims/${c.event_id}`)} data-testid="claim-row" data-status={c.status}>
                    <TableCell className="whitespace-nowrap">{day(c.event_date)}</TableCell>
                    <TableCell><div className="font-bold">{a?.external_activity_id ?? '—'}</div><div className="text-[11px] text-muted-foreground max-w-xs truncate">{a?.activity_name}</div></TableCell>
                    <TableCell className="text-[11px]">{c.claimed_pct !== null && c.claimed_pct !== undefined ? `${pct(c.claimed_pct)} (percent only)` : 'quantities'}{c.resubmits_event_id ? ' · correction' : ''}</TableCell>
                    <TableCell><ClaimStatusPill status={c.status} clarification={c.clarification_status} /></TableCell>
                    <TableCell className="text-[11px] whitespace-nowrap">{day(c.created_at)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
      <div className="flex justify-end gap-2"><Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className="cursor-pointer">Previous</Button><Button size="sm" variant="outline" disabled={!q.data?.next_offset} onClick={() => setOffset(q.data!.next_offset!)} className="cursor-pointer">Next</Button></div>
    </div>
  );
}
