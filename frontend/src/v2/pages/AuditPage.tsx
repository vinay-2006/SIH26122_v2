import React, { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Fingerprint, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useProjectState } from '@/context/ProjectContext';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { dashboardApi } from '@/v2/api/endpoints';
import { Loading, Notice, PageHeader, QueryError, errText, when, F } from '@/v2/ui';

const TYPES = ['', 'CLAIM', 'PLANNER_DECISION', 'ISSUE', 'SCHEDULE_IMPORT', 'SCHEDULE_VERSION', 'MEMBERSHIP', 'PROJECT', 'SOURCE_DOCUMENT'];

export default function AuditPage() {
  const { currentProject } = useProjectState();
  const { projectId } = useV2Project();
  const [type, setType] = useState('');
  const q = useQuery({ queryKey: ['v2', 'audit', projectId, type], queryFn: () => dashboardApi.audit(projectId, { entity_type: type || undefined, limit: 200 }), retry: false });
  const verify = useMutation({ mutationFn: () => dashboardApi.verifyAudit(projectId) });
  return (
    <div className="space-y-6" data-testid="audit-page">
      <PageHeader project={`${currentProject?.name} (${currentProject?.code})`} title="Audit Trail" subtitle="Every consequential action, in a tamper-evident hash chain. Newest first."
        actions={<Button variant="outline" onClick={() => verify.mutate()} disabled={verify.isPending} className="gap-1.5 cursor-pointer" data-testid="verify-chain"><ShieldCheck className="w-4 h-4" />Verify integrity</Button>} />
      {verify.data && <Notice tone={verify.data.valid ? 'ok' : 'bad'} testid="verify-result">{verify.data.valid ? `The audit chain is intact (${verify.data.entries} entries checked).` : 'The audit chain is BROKEN. Report this to the platform administrator.'}</Notice>}
      {verify.error && <Notice tone="bad">{errText(verify.error)}</Notice>}
      <select className={F('w-64')} aria-label="Entity type" value={type} onChange={(e) => setType(e.target.value)}>{TYPES.map((t) => <option key={t} value={t}>{t ? t.replace(/_/g, ' ').toLowerCase() : 'All entity types'}</option>)}</select>
      {q.isPending && <Loading />}
      {q.error && <QueryError error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <div className="rounded-xl border border-border overflow-x-auto bg-card">
          <Table data-testid="audit-table"><TableHeader><TableRow><TableHead>When</TableHead><TableHead>Action</TableHead><TableHead>Entity</TableHead><TableHead>Role</TableHead></TableRow></TableHeader>
            <TableBody>{q.data.items.map((r) => <TableRow key={String(r.log_id)}><TableCell className="whitespace-nowrap text-[11px]">{when(r.occurred_at)}</TableCell><TableCell className="font-semibold text-xs">{r.action.replace(/_/g, ' ').toLowerCase()}</TableCell><TableCell className="text-[11px]">{r.entity_type.toLowerCase().replace(/_/g, ' ')} <span className="font-mono text-muted-foreground">{String(r.entity_id ?? '').slice(0, 8)}</span></TableCell><TableCell className="text-[11px]">{r.role ?? '—'}</TableCell></TableRow>)}</TableBody></Table>
          {q.data.items.length === 0 && <div className="p-6 text-xs text-muted-foreground flex items-center gap-2"><Fingerprint className="w-4 h-4" />No entries.</div>}
        </div>
      )}
    </div>
  );
}
