import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { ShieldCheck, ShieldAlert, Loader2, Fingerprint, Download } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useProject } from '@/context/ProjectContext';
import { auditTrailApi, type AuditVerification } from '@/api/intelligence';

const STATUS_STYLE: Record<string, { cls: string; text: string }> = {
  VALID: { cls: 'text-emerald-600 border-emerald-400/60 bg-emerald-500/10', text: 'Chain verified: no record has been altered or removed' },
  LEGACY_ONLY: { cls: 'text-amber-600 border-amber-400/60 bg-amber-500/10', text: 'Only pre-V7 records exist: history is kept but is not chain-verifiable' },
  EMPTY: { cls: 'text-slate-500 border-slate-400/60 bg-slate-500/10', text: 'No audit records yet' },
  BROKEN: { cls: 'text-red-600 border-red-400/60 bg-red-500/10', text: 'Chain broken: an audit record was altered, removed or inserted' },
};

function Verdict({ v }: { v: AuditVerification }) {
  const s = STATUS_STYLE[v.status] ?? STATUS_STYLE.EMPTY;
  return (
    <div className={cn('rounded-lg border p-4 flex items-start gap-3', s.cls)}>
      {v.status === 'BROKEN' ? <ShieldAlert className="w-6 h-6 shrink-0" /> : <ShieldCheck className="w-6 h-6 shrink-0" />}
      <div className="space-y-1">
        <div className="text-sm font-bold">{v.status}</div>
        <div className="text-xs">{s.text}</div>
        <div className="text-[11px] font-mono opacity-80">
          {v.records_checked} records checked · {v.v7_records} V7 · {v.legacy_records} legacy
          {v.broken_at_log_id != null && ` · broken at #${v.broken_at_log_id} (${v.failure_type})`}
        </div>
        {v.reason && <div className="text-[11px]">{v.reason}</div>}
      </div>
    </div>
  );
}

/** Tamper-evident audit trail: hash-chain verification plus the latest records. Read-only (VIEW_AUDIT). */
export default function AuditTrail() {
  const { currentProject } = useProject();
  const verify = useQuery({ queryKey: ['v7', 'audit-verify', currentProject.id], queryFn: () => auditTrailApi.verify(currentProject.id), retry: false });
  const recent = useQuery({ queryKey: ['v7', 'audit-recent', currentProject.id], queryFn: () => auditTrailApi.recent(), retry: false });

  const downloadDossier = async () => {
    const data = await auditTrailApi.dossier(currentProject.id);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `dossier_${currentProject.code}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="space-y-5 max-w-5xl">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-primary/10 border border-primary/20 text-primary"><Fingerprint className="w-6 h-6" /></div>
          <div>
            <h1 className="text-xl font-black tracking-tight">Audit Trail</h1>
            <p className="text-xs text-muted-foreground">Every approval, release, reopen and attribution change is appended to a hash-linked log.</p>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={downloadDossier} className="gap-1.5 cursor-pointer"><Download className="w-3.5 h-3.5" /> Download dossier (JSON)</Button>
      </div>

      {verify.isLoading && <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin" /> Verifying the hash chain…</div>}
      {verify.isError && <div className="text-xs text-red-600">Verification failed: {verify.error instanceof Error ? verify.error.message : 'error'}</div>}
      {verify.data && <Verdict v={verify.data} />}

      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-sm">Latest records</CardTitle></CardHeader>
        <CardContent>
          {recent.isLoading && <div className="text-xs text-muted-foreground">Loading…</div>}
          {recent.data && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-muted-foreground border-b border-border">
                    <th className="py-1.5 pr-3">#</th><th className="pr-3">When</th><th className="pr-3">Action</th><th className="pr-3">Entity</th><th>Hash</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.data.slice(0, 40).map((r) => (
                    <tr key={r.log_id} className="border-b border-border/50">
                      <td className="py-1.5 pr-3 font-mono">{r.log_id}</td>
                      <td className="pr-3 whitespace-nowrap">{new Date(r.timestamp).toLocaleString()}</td>
                      <td className="pr-3 font-semibold">{r.action.replace(/_/g, ' ')}</td>
                      <td className="pr-3 font-mono">{r.entity_type} · {String(r.entity_id).slice(0, 24)}</td>
                      <td className="font-mono text-muted-foreground" title={r.current_hash ?? ''}>{r.current_hash ? `${r.current_hash.slice(0, 10)}…` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
