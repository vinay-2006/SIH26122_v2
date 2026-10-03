import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BellRing } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { useProjectState } from '@/context/ProjectContext';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { dashboardApi } from '@/v2/api/endpoints';
import type { Notification } from '@/v2/api/types';
import { Loading, PageHeader, Pills, QueryError, when } from '@/v2/ui';
import { cn } from '@/lib/utils';

export default function NotificationsPage() {
  const { currentProject, can } = useProjectState();
  const { projectId } = useV2Project();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [f, setF] = useState<'ALL' | 'UNREAD'>('ALL');
  const [offset, setOffset] = useState(0);
  const q = useQuery({ queryKey: ['v2', 'notifications', projectId, f, offset], queryFn: ({ signal }) => dashboardApi.notifications(projectId, { unread_only: f === 'UNREAD', limit: 50, offset }, { signal }), retry: false, placeholderData: (p) => p });
  const read = useMutation({ mutationFn: (id: string) => dashboardApi.markRead(projectId, id), onSuccess: () => qc.invalidateQueries({ queryKey: ['v2'] }) });
  const open = (n: Notification) => {
    if (!n.read_at) read.mutate(n.notification_id);
    if (n.claim_id && (can('CREATE_EXECUTION_EVENT') || can('REVIEW_CLAIM'))) nav(`/claims/${n.claim_id}`);
    else if (n.issue_id) nav('/issues');
  };
  return (
    <div className="space-y-6" data-testid="notifications-page">
      <PageHeader project={`${currentProject?.name} (${currentProject?.code})`} title="Notifications" subtitle="Decisions on your claims, clarification questions and answers, new claims to review, and issue updates." />
      <Pills value={f} onChange={(v) => { setF(v); setOffset(0); }} options={[{ value: 'ALL', label: 'All' }, { value: 'UNREAD', label: 'Unread' }]} />
      {q.isPending && <Loading />}
      {q.error && <QueryError error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.items.length === 0 && <EmptyState icon={BellRing} title="Nothing here" description={f === 'UNREAD' ? 'You are all caught up.' : 'Notifications appear as work moves.'} />}
      <div className="space-y-2">{q.data?.items.map((n) => (
        <button key={n.notification_id} type="button" onClick={() => open(n)} data-testid="notification" data-unread={!n.read_at}
          className={cn('w-full text-left rounded-xl border p-3 text-xs cursor-pointer flex items-start justify-between gap-3 hover:border-[#FF7A18]', n.read_at ? 'border-border opacity-80' : 'border-[#FF7A18]/60 bg-[#FF7A18]/5')}>
          <span className="min-w-0"><span className="font-bold block">{n.title}</span>{n.body && <span className="text-muted-foreground block truncate">{n.body}</span>}</span>
          <span className="shrink-0 text-[10px] text-muted-foreground">{when(n.created_at)}</span>
        </button>))}</div>
      <div className="flex justify-end gap-2"><Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))} className="cursor-pointer">Previous</Button><Button size="sm" variant="outline" disabled={!q.data?.next_offset} onClick={() => setOffset(q.data!.next_offset!)} className="cursor-pointer">Next</Button></div>
    </div>
  );
}
