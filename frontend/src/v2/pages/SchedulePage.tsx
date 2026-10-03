import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, FileUp, FolderTree, GitCompare, History, Loader2, Rocket, Trash2, Undo2, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { useProjectState } from '@/context/ProjectContext';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { dashboardApi, scheduleApi } from '@/v2/api/endpoints';
import type { ScheduleImport, VersionRow } from '@/v2/api/types';
import { DISCIPLINES, UNITS, WBS_TYPES } from '@/v2/reference';
import { FilePick, Label, Loading, Notice, PageHeader, Panel, QueryError, day, errText, when, F } from '@/v2/ui';
import { cn } from '@/lib/utils';

const ACCEPT = '.csv,.xer,.xml';
const importKey = (pid: string) => `setu_v2_import:${pid}`;

const VERSION_TONE: Record<string, string> = {
  ACTIVE: 'border-emerald-300 text-emerald-700 bg-emerald-50 dark:border-emerald-800 dark:text-emerald-300 dark:bg-emerald-950/40',
  VALIDATED: 'border-blue-300 text-blue-700 bg-blue-50 dark:border-blue-800 dark:text-blue-300 dark:bg-blue-950/40',
  DRAFT: 'border-slate-300 text-slate-700 bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:bg-slate-900/30',
  SUPERSEDED: 'border-slate-300 text-slate-500 bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:bg-slate-900/30',
};

// ------------------------------------------------------------------------------------------------ activation / rollback (confirmation)
function ActivateDialog({ version, active, onClose, onDone }: { version: VersionRow | null; active: VersionRow | undefined; onClose: () => void; onDone: () => void }) {
  const { projectId } = useV2Project();
  const [reason, setReason] = useState('');
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setReason(''); setErr(null); }, [version?.version_id]);
  const rollback = version?.status === 'SUPERSEDED';
  const go = useMutation({
    mutationFn: () => scheduleApi.activate(projectId, version!.version_id, rollback ? reason.trim() : undefined),
    onSuccess: () => { onDone(); onClose(); }, onError: (e) => setErr(errText(e)),
  });
  if (!version) return null;
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-md" data-testid="activate-dialog">
        <DialogHeader>
          <DialogTitle>{rollback ? `Roll back to v${version.version_no}?` : `Activate v${version.version_no}?`}</DialogTitle>
          <DialogDescription>
            {active ? <>This replaces <b>v{active.version_no}</b> as the project’s active schedule. </> : 'This becomes the project’s first active schedule. '}
            The version is locked once active. Approved progress stays with each activity’s stable identity; nothing is moved between activities automatically, and the previous version remains available for comparison.
          </DialogDescription>
        </DialogHeader>
        {rollback && <div><Label>Reason for the rollback (required, audited)</Label><Textarea rows={3} aria-label="Rollback reason" value={reason} onChange={(e) => setReason(e.target.value)} /></div>}
        {err && <Notice tone="bad">{err}</Notice>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="cursor-pointer">Cancel</Button>
          <Button onClick={() => go.mutate()} disabled={go.isPending || (rollback && reason.trim().length < 3)} className="gap-1.5 cursor-pointer" data-testid="confirm-activate">
            {go.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Rocket className="w-4 h-4" />} {rollback ? 'Roll back' : 'Activate'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------------------------------------ versions
function VersionsPanel({ versions, onChanged }: { versions: VersionRow[]; onChanged: () => void }) {
  const { projectId, isArchived } = useV2Project();
  const [target, setTarget] = useState<VersionRow | null>(null);
  const [cmp, setCmp] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const discard = useMutation({ mutationFn: (id: string) => scheduleApi.discardVersion(projectId, id), onSuccess: onChanged, onError: (e) => setErr(errText(e)) });
  const active = versions.find((v) => v.status === 'ACTIVE');
  const toggle = (id: string) => setCmp((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id].slice(-2)));
  return (
    <Panel icon={FolderTree} title="Schedule versions" description="One version is active at a time. A new version is built from an import, reviewed, then activated."
      right={<Button size="sm" variant="outline" className="gap-1.5 cursor-pointer" disabled={cmp.length !== 2} onClick={() => setCompareOpen(true)} data-testid="compare-btn"><GitCompare className="w-3.5 h-3.5" />Compare selected</Button>}>
      {err && <Notice tone="bad">{err}</Notice>}
      {versions.length === 0 ? <div className="text-xs text-muted-foreground" data-testid="no-versions">No schedule versions yet. Import a schedule below.</div> : (
        <div className="rounded-xl border border-border overflow-x-auto bg-card">
          <Table data-testid="versions-table">
            <TableHeader><TableRow><TableHead className="w-8" /><TableHead>Version</TableHead><TableHead>Status</TableHead><TableHead>Data date</TableHead><TableHead className="text-right">Activities</TableHead><TableHead>Activated</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
            <TableBody>
              {[...versions].reverse().map((v) => (
                <TableRow key={v.version_id} data-testid="version-row" data-version={v.version_no} data-status={v.status}>
                  <TableCell><input type="checkbox" aria-label={`Select v${v.version_no} to compare`} checked={cmp.includes(v.version_id)} onChange={() => toggle(v.version_id)} className="accent-[#FF7A18]" /></TableCell>
                  <TableCell><div className="font-bold">v{v.version_no} <span className="text-[10px] font-mono text-muted-foreground">{v.kind}</span></div><div className="text-[11px] text-muted-foreground">{v.baseline_name ?? v.label ?? '—'}</div></TableCell>
                  <TableCell><span className={cn('px-2 py-0.5 rounded-md border text-[10px] font-bold', VERSION_TONE[v.status])}>{v.status}</span></TableCell>
                  <TableCell className="text-[11px]">{day(v.data_date)}</TableCell>
                  <TableCell className="text-right font-mono">{v.activities}</TableCell>
                  <TableCell className="text-[11px]">{when(v.activated_at)}</TableCell>
                  <TableCell className="text-right whitespace-nowrap space-x-1">
                    {v.status === 'VALIDATED' && <Button size="sm" className="gap-1 cursor-pointer" disabled={isArchived} onClick={() => setTarget(v)} data-testid="activate-btn"><Rocket className="w-3.5 h-3.5" />Activate</Button>}
                    {v.status === 'SUPERSEDED' && <Button size="sm" variant="outline" className="gap-1 cursor-pointer" disabled={isArchived} onClick={() => setTarget(v)} data-testid="rollback-btn"><Undo2 className="w-3.5 h-3.5" />Roll back</Button>}
                    {(v.status === 'VALIDATED' || v.status === 'DRAFT') && !v.locked_at && <Button size="sm" variant="ghost" className="gap-1 text-rose-600 cursor-pointer" disabled={discard.isPending} onClick={() => { if (window.confirm(`Discard v${v.version_no}? Its import can be built again.`)) discard.mutate(v.version_id); }}><Trash2 className="w-3.5 h-3.5" />Discard</Button>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <ActivateDialog version={target} active={active} onClose={() => setTarget(null)} onDone={onChanged} />
      {compareOpen && cmp.length === 2 && <ComparePanel ids={cmp} versions={versions} onClose={() => setCompareOpen(false)} />}
    </Panel>
  );
}

function ComparePanel({ ids, versions, onClose }: { ids: string[]; versions: VersionRow[]; onClose: () => void }) {
  const { projectId } = useV2Project();
  const [a, b] = [...ids].map((id) => versions.find((v) => v.version_id === id)!).sort((x, y) => x.version_no - y.version_no);
  const q = useQuery({ queryKey: ['v2', 'compare', projectId, a.version_id, b.version_id], queryFn: () => scheduleApi.compare(projectId, a.version_id, b.version_id), retry: false });
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto" data-testid="compare-dialog">
        <DialogHeader><DialogTitle>Compare v{a.version_no} → v{b.version_no}</DialogTitle><DialogDescription>Activities are matched by stable identity, not by id or name. Approved progress is never carried across a split, merge or retirement.</DialogDescription></DialogHeader>
        {q.isPending && <Loading />}
        {q.error && <QueryError error={q.error} />}
        {q.data && (
          <div className="space-y-3 text-xs">
            <div className="flex flex-wrap gap-2">{Object.entries(q.data.summary).map(([k, n]) => <span key={k} className="px-2 py-0.5 rounded-md border border-border font-mono">{k}: <b>{n}</b></span>)}{Object.keys(q.data.summary).length === 0 && <span>No differences.</span>}</div>
            {q.data.activities.length > 0 && (
              <Table><TableHeader><TableRow><TableHead>Change</TableHead><TableHead>Activity</TableHead><TableHead>Details</TableHead></TableRow></TableHeader>
                <TableBody>{q.data.activities.slice(0, 200).map((r: any, i) => (
                  <TableRow key={i}><TableCell className="font-mono text-[10px]">{r.change_kind}</TableCell><TableCell>{r.new_external_id ?? r.old_external_id}</TableCell>
                    <TableCell className="text-[11px] text-muted-foreground">{[r.old_external_id && r.new_external_id && r.old_external_id !== r.new_external_id ? `id ${r.old_external_id} → ${r.new_external_id}` : null, r.start_shift_days ? `start ${r.start_shift_days > 0 ? '+' : ''}${r.start_shift_days} d` : null, r.finish_shift_days ? `finish ${r.finish_shift_days > 0 ? '+' : ''}${r.finish_shift_days} d` : null].filter(Boolean).join('; ') || '—'}</TableCell></TableRow>))}</TableBody></Table>
            )}
            {q.data.assignments.length > 0 && <div className="text-muted-foreground">{q.data.assignments.length} resource-assignment change(s) (quantities, added or removed resources).</div>}
          </div>
        )}
        <DialogFooter><Button variant="outline" onClick={onClose} className="cursor-pointer">Close</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------------------------------------ reconciliation decisions
type Recon = { accept?: Record<string, string>; new?: string[]; retire?: string[]; split?: { from: string; to: { ext: string; fraction: number }[] }[]; merge?: { from: string[]; to: string }[] };

function ReconPanel({ imp, save, busy }: { imp: ScheduleImport; save: (r: Recon) => void; busy: boolean }) {
  const rec = imp.reconciliation!;
  const d: Recon = imp.decisions.reconcile ?? {};
  const inSplit = new Set((d.split ?? []).flatMap((x) => x.to.map((t) => t.ext)));
  const inMerge = new Set((d.merge ?? []).map((m) => m.to));
  const items = rec.items.filter((i: any) => ['PROPOSED_RENAME', 'ID_REUSED', 'AMBIGUOUS'].includes(i.outcome) && !inSplit.has(i.new) && !inMerge.has(i.new));
  const removedWithProgress = ((rec as any).removed ?? []).filter((r: any) => r.has_progress);
  const sum = (rec as any).summary ?? {};
  const splitOn = (uid: string) => d.split?.some((x) => x.from === uid);
  const mergeOn = (to: string) => d.merge?.some((x) => x.to === to);
  return (
    <Panel icon={GitCompare} title="Reconciliation with the active schedule" description="Progress follows each activity’s stable identity. Anything that is renamed, split, merged or retired needs your explicit decision; nothing is transferred automatically." >
      <div className="flex flex-wrap gap-2 text-[11px]" data-testid="recon-summary">{Object.entries(sum).map(([k, v]) => <span key={k} className="px-2 py-0.5 rounded-md border border-border font-mono">{k}: <b>{String(v)}</b></span>)}</div>
      {rec.blockers.length === 0 ? <Notice tone="ok" testid="recon-clear">All decisions are made; this revision can be built.</Notice> : <Notice tone="warn" testid="recon-blockers">{rec.blockers.length} item(s) need a decision before building.</Notice>}

      {rec.split_proposals.length > 0 && (
        <div className="space-y-2" data-testid="recon-splits"><div className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Proposed splits</div>
          {rec.split_proposals.map((sp: any) => (
            <div key={sp.from_uid} className="rounded-lg border border-border p-2.5 text-xs flex flex-wrap items-center justify-between gap-2" data-testid="recon-split">
              <span><b>{sp.from_external_id}</b> looks split into {sp.to.map((t: any) => `${t.ext} (${Math.round(t.fraction * 100)}%)`).join(' and ')}. <span className="text-muted-foreground">The old activity keeps its history; the new ones start at zero.</span></span>
              <Button size="sm" variant={splitOn(sp.from_uid) ? 'default' : 'outline'} disabled={busy} className="cursor-pointer" onClick={() => save({ ...d, split: splitOn(sp.from_uid) ? (d.split ?? []).filter((x) => x.from !== sp.from_uid) : [...(d.split ?? []), { from: sp.from_uid, to: sp.to }] })}>{splitOn(sp.from_uid) ? 'Split confirmed' : 'Confirm split'}</Button>
            </div>))}
        </div>
      )}
      {rec.merge_proposals.length > 0 && (
        <div className="space-y-2" data-testid="recon-merges"><div className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Proposed merges</div>
          {rec.merge_proposals.map((m: any) => (
            <div key={m.to} className="rounded-lg border border-border p-2.5 text-xs flex flex-wrap items-center justify-between gap-2" data-testid="recon-merge">
              <span><b>{m.from_external_ids.join(' + ')}</b> look merged into <b>{m.to}</b>. <span className="text-muted-foreground">The old activities keep their history; the new one starts at zero.</span></span>
              <Button size="sm" variant={mergeOn(m.to) ? 'default' : 'outline'} disabled={busy} className="cursor-pointer" onClick={() => save({ ...d, merge: mergeOn(m.to) ? (d.merge ?? []).filter((x) => x.to !== m.to) : [...(d.merge ?? []), { from: m.from_uids, to: m.to }] })}>{mergeOn(m.to) ? 'Merge confirmed' : 'Confirm merge'}</Button>
            </div>))}
        </div>
      )}
      {items.length > 0 && <div className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Matches to confirm</div>}
      {items.map((i: any) => (
        <div key={i.new} className="rounded-lg border border-border p-2.5 text-xs space-y-1.5" data-testid="recon-item">
          <div><b>{i.new}</b> <span className="font-mono text-[10px] text-muted-foreground">{i.outcome}</span>{i.old_external_id && <> — looks like existing <b>{i.old_external_id}</b>{typeof i.score === 'number' && ` (match ${Math.round(i.score * 100)}%)`}</>}</div>
          <div className="text-muted-foreground">{i.message}</div>
          <div className="flex gap-2">
            {i.old_uid && <Button size="sm" variant={d.accept?.[i.new] ? 'default' : 'outline'} disabled={busy} className="cursor-pointer" onClick={() => save({ ...d, accept: { ...d.accept, [i.new]: i.old_uid }, new: (d.new ?? []).filter((x) => x !== i.new) })}>Same activity (keep its progress)</Button>}
            <Button size="sm" variant={d.new?.includes(i.new) ? 'default' : 'outline'} disabled={busy} className="cursor-pointer" onClick={() => { const acc = { ...d.accept }; delete acc[i.new]; save({ ...d, accept: acc, new: [...(d.new ?? []).filter((x) => x !== i.new), i.new] }); }}>Different activity (starts at zero)</Button>
          </div>
        </div>
      ))}
      {removedWithProgress.length > 0 && <div className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Missing from the new file, with approved progress</div>}
      {removedWithProgress.map((r: any) => (
        <div key={r.uid} className="rounded-lg border border-border p-2.5 text-xs space-y-1.5" data-testid="recon-removed">
          <div><b>{r.external_id}</b> “{r.name}” has approved progress but is not in the new file. Confirm a split or merge above, or retire it here.</div>
          <Button size="sm" variant={d.retire?.includes(r.uid) ? 'default' : 'outline'} disabled={busy} className="cursor-pointer" onClick={() => save({ ...d, retire: Array.from(new Set([...(d.retire ?? []), r.uid])) })}>Retire it (history kept, nothing moves)</Button>
        </div>
      ))}
      {(rec as any).changes?.length > 0 && <details className="text-xs"><summary className="cursor-pointer font-bold">{(rec as any).changes.length} changed activities</summary>
        <ul className="mt-1 space-y-0.5 text-muted-foreground">{(rec as any).changes.slice(0, 50).map((c: any) => <li key={c.new}><b className="text-foreground">{c.new}</b>: {c.fields.join(', ')}</li>)}</ul></details>}
    </Panel>
  );
}

// ------------------------------------------------------------------------------------------------ import wizard
function ImportPanel({ onVersionsChanged }: { onVersionsChanged: () => void }) {
  const { projectId, isArchived } = useV2Project();
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [resFile, setResFile] = useState<File | null>(null);
  const [hdr, setHdr] = useState({ baseline_name: '', data_date: '', planned_start: '', planned_finish: '' });
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errReport, setErrReport] = useState<{ code: string; message: string }[] | null>(null);
  const [importId, setImportId] = useState<string | null>(() => { try { return sessionStorage.getItem(importKey(projectId)); } catch { return null; } });
  const [built, setBuilt] = useState<{ version_no: number } | null>(null);
  const abort = useRef<AbortController | null>(null);
  const remember = (id: string | null) => { setImportId(id); try { if (id) sessionStorage.setItem(importKey(projectId), id); else sessionStorage.removeItem(importKey(projectId)); } catch { /* storage unavailable */ } };

  const imp = useQuery({ queryKey: ['v2', 'import', projectId, importId], queryFn: () => scheduleApi.getImport(projectId, importId!), enabled: !!importId, retry: false });
  useEffect(() => { if (imp.error && (imp.error as any).status === 404) remember(null); }, [imp.error]);
  const data = imp.data && imp.data.status === 'PARSED' ? imp.data : null;

  const ext = (f: File | null) => f?.name.toLowerCase().split('.').pop() ?? '';
  const fileProblem = file && ext(file) === 'mpp' ? 'Native .mpp files are not supported. In Microsoft Project use File ▸ Save As ▸ XML, then upload that XML.' : file && !['csv', 'xer', 'xml'].includes(ext(file)) ? 'Supported files: CSV, Primavera XER, Microsoft Project XML.' : null;

  const stage = useMutation({
    mutationFn: async () => { abort.current = new AbortController(); setProgress(0); return scheduleApi.stage(projectId, file!, ext(file) === 'csv' ? resFile : null, Object.fromEntries(Object.entries(hdr).filter(([, v]) => v)), setProgress, abort.current.signal); },
    onSuccess: (r) => { setError(null); setErrReport(null); setFile(null); setResFile(null); remember(r.import_id); qc.setQueryData(['v2', 'import', projectId, r.import_id], r); },
    onError: (e: any) => { if (e?.name === 'AbortError') return; setError(errText(e)); setErrReport(e?.code === 'IMPORT_INVALID' ? (e.details?.errors ?? []) : null); },
    onSettled: () => setProgress(null),
  });
  const decide = useMutation({
    mutationFn: (body: Record<string, unknown>) => scheduleApi.decisions(projectId, importId!, body),
    onSuccess: (r) => { setError(null); qc.setQueryData(['v2', 'import', projectId, importId], r); }, onError: (e) => setError(errText(e)),
  });
  const build = useMutation({
    mutationFn: () => scheduleApi.build(projectId, importId!),
    onSuccess: (v) => { setBuilt({ version_no: v.version_no }); remember(null); setError(null); onVersionsChanged(); }, onError: (e: any) => setError(errText(e)),
  });
  const discard = useMutation({ mutationFn: () => scheduleApi.discardImport(projectId, importId!), onSuccess: () => { remember(null); setError(null); } });

  const unDisc = data?.report.mapping?.unmapped_disciplines ?? [];
  const unUnits = data?.report.mapping?.unmapped_units ?? [];
  const [dmap, setDmap] = useState<Record<string, string>>({});
  const [umap, setUmap] = useState<Record<string, string>>({});
  const mappingReady = unDisc.every((l) => dmap[l]) && unUnits.every((l) => umap[l]);
  const blockers = data?.reconciliation?.blockers.length ?? 0;
  const canBuild = !!data && data.report.valid && data.report.ready_to_build && blockers === 0 && !isArchived;
  const stageTypes = useMemo(() => data?.wbs ?? [], [data]);

  return (
    <div className="space-y-4">
      <Panel icon={Upload} title="Import a schedule" description="Supported: CSV (optionally with a resource CSV), Primavera P6 XER, Microsoft Project XML. Native .mpp is not supported. XER / XML support has been verified on sample and synthetic exports only.">
        {built && <Notice tone="ok" testid="built-notice">Built schedule v{built.version_no}. Review it in the versions table above and activate it when you are ready.</Notice>}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div><Label>Schedule file</Label><FilePick label="Choose schedule file" ariaLabel="Schedule file" accept={ACCEPT} file={file} onFile={(f) => { setFile(f); setError(null); setErrReport(null); }} testid="schedule-file" /></div>
          <div><Label hint="(CSV only, optional)">Resource assignments CSV</Label><FilePick label="Choose resource CSV" ariaLabel="Resource file" accept=".csv" file={resFile} onFile={setResFile} disabled={ext(file) !== 'csv'} testid="resource-file" /></div>
          <div><Label hint="(optional)">Baseline name</Label><Input aria-label="Baseline name" value={hdr.baseline_name} onChange={(e) => setHdr({ ...hdr, baseline_name: e.target.value })} /></div>
          <div><Label hint="(optional; else from the file)">Data date</Label><Input type="date" aria-label="Data date" value={hdr.data_date} onChange={(e) => setHdr({ ...hdr, data_date: e.target.value })} /></div>
          <div><Label hint="(optional)">Planned start</Label><Input type="date" aria-label="Planned start" value={hdr.planned_start} onChange={(e) => setHdr({ ...hdr, planned_start: e.target.value })} /></div>
          <div><Label hint="(optional)">Planned finish</Label><Input type="date" aria-label="Planned finish" value={hdr.planned_finish} onChange={(e) => setHdr({ ...hdr, planned_finish: e.target.value })} /></div>
        </div>
        {fileProblem && <Notice tone="warn">{fileProblem}</Notice>}
        {error && !data && <Notice tone="bad" testid="import-error">{error}</Notice>}
        {errReport && (
          <div className="rounded-lg border border-rose-300 dark:border-rose-900 p-2.5 text-xs space-y-1" data-testid="import-errors">
            <div className="font-bold text-rose-700 dark:text-rose-300">{errReport.length} validation error(s) — nothing was imported:</div>
            <ul className="list-disc ml-5 space-y-0.5 max-h-48 overflow-y-auto">{errReport.slice(0, 100).map((x, i) => <li key={i}><span className="font-mono text-[10px]">{x.code}</span> {x.message}</li>)}</ul>
          </div>
        )}
        {progress !== null && <div className="h-2 rounded-full bg-slate-200 dark:bg-[#0E2B47] overflow-hidden" role="progressbar" aria-valuenow={Math.round(progress * 100)}><div className="h-full bg-[#FF7A18]" style={{ width: `${Math.round(progress * 100)}%` }} /></div>}
        <div className="flex gap-2">
          <Button onClick={() => { setBuilt(null); stage.mutate(); }} disabled={!file || !!fileProblem || stage.isPending || isArchived || !!data} className="gap-1.5 cursor-pointer" data-testid="upload-schedule">
            {stage.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileUp className="w-4 h-4" />} Upload and validate
          </Button>
          {stage.isPending && <Button variant="outline" onClick={() => abort.current?.abort()} className="cursor-pointer">Cancel</Button>}
          {data && <span className="text-xs text-muted-foreground self-center">An import is in progress below. Build or discard it before uploading another.</span>}
        </div>
      </Panel>

      {imp.isPending && importId && <Loading what="Loading the staged import…" />}
      {data && (
        <>
          <Panel icon={CheckCircle2} title={`Staged import · ${data.file_name ?? data.format}`} description={`${data.format} · ${data.report.stats?.activities ?? '?'} activities · ${data.report.stats?.assignments ?? '?'} resource assignments · ${data.report.stats?.dependencies ?? '?'} dependencies · ${data.wbs.length} WBS nodes`}
            right={<Button size="sm" variant="ghost" className="text-rose-600 gap-1 cursor-pointer" onClick={() => { if (window.confirm('Discard this staged import?')) discard.mutate(); }}><Trash2 className="w-3.5 h-3.5" />Discard</Button>}>
            <div data-testid="import-report" className="space-y-2 text-xs">
              <div className="flex flex-wrap gap-2">
                <span className={cn('px-2 py-0.5 rounded-md border font-bold', data.report.valid ? 'border-emerald-300 text-emerald-700' : 'border-rose-300 text-rose-700')}>{data.report.valid ? 'Valid' : 'Has errors'}</span>
                <span className={cn('px-2 py-0.5 rounded-md border font-bold', data.report.ready_to_build ? 'border-emerald-300 text-emerald-700' : 'border-amber-300 text-amber-700')}>{data.report.ready_to_build ? 'Ready to build' : 'Needs mapping'}</span>
                <span className="px-2 py-0.5 rounded-md border border-border">Data date {day(data.header.data_date)}</span>
                <span className="px-2 py-0.5 rounded-md border border-border">{day(data.header.planned_start)} → {day(data.header.planned_finish)}</span>
              </div>
              {data.report.warnings.length > 0 && <details><summary className="cursor-pointer font-bold text-amber-700 dark:text-amber-400">{data.report.warnings.length} warning(s)</summary><ul className="list-disc ml-5 mt-1 space-y-0.5 max-h-40 overflow-y-auto">{data.report.warnings.slice(0, 100).map((w, i) => <li key={i}><span className="font-mono text-[10px]">{w.code}</span> {w.message}</li>)}</ul></details>}
              {data.report.errors.length > 0 && <ul className="list-disc ml-5 text-rose-700 dark:text-rose-300">{data.report.errors.slice(0, 50).map((w, i) => <li key={i}><span className="font-mono text-[10px]">{w.code}</span> {w.message}</li>)}</ul>}
            </div>
            {(unDisc.length > 0 || unUnits.length > 0) && (
              <div className="rounded-lg border border-amber-300 dark:border-amber-800 p-3 space-y-2 text-xs" data-testid="mapping-panel">
                <div className="font-bold">Map the labels in this file to controlled values</div>
                {unDisc.map((l) => <div key={l} className="flex items-center gap-2"><span className="w-56 truncate font-mono">discipline “{l}”</span><select className={F('w-64')} aria-label={`Discipline for ${l}`} value={dmap[l] ?? ''} onChange={(e) => setDmap({ ...dmap, [l]: e.target.value })}><option value="">Choose…</option>{DISCIPLINES.map((x) => <option key={x.code} value={x.code}>{x.name}</option>)}</select></div>)}
                {unUnits.map((l) => <div key={l} className="flex items-center gap-2"><span className="w-56 truncate font-mono">unit “{l}”</span><select className={F('w-64')} aria-label={`Unit for ${l}`} value={umap[l] ?? ''} onChange={(e) => setUmap({ ...umap, [l]: e.target.value })}><option value="">Choose…</option>{UNITS.map((x) => <option key={x.code} value={x.code}>{x.name} ({x.code})</option>)}</select></div>)}
                <Button size="sm" disabled={!mappingReady || decide.isPending} className="cursor-pointer" onClick={() => decide.mutate({ discipline_map: dmap, uom_map: umap })} data-testid="apply-mapping">Apply mapping</Button>
              </div>
            )}
            <details className="text-xs"><summary className="cursor-pointer font-bold">WBS structure and node types ({stageTypes.length})</summary>
              <div className="mt-1 max-h-56 overflow-y-auto space-y-1">{stageTypes.map((w) => (
                <div key={w.code} className="flex items-center gap-2"><span className="flex-1 truncate">{w.name} <span className="font-mono text-[10px] text-muted-foreground">{w.code}</span></span>
                  <select className={F('w-36 h-7 text-xs')} aria-label={`Type of ${w.name}`} value={w.type ?? ''} onChange={(e) => decide.mutate({ wbs_types: { [w.code]: e.target.value } })}>{WBS_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}</select></div>))}</div></details>
            {error && <Notice tone="bad">{error}</Notice>}
            <div className="flex gap-2 pt-1">
              <Button onClick={() => build.mutate()} disabled={!canBuild || build.isPending} className="gap-1.5 cursor-pointer" data-testid="build-version">{build.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <FolderTree className="w-4 h-4" />} Build schedule version</Button>
              {!canBuild && <span className="text-xs text-muted-foreground self-center">{!data.report.valid ? 'Fix the errors and upload again.' : !data.report.ready_to_build ? 'Complete the mapping first.' : blockers > 0 ? 'Resolve the reconciliation decisions first.' : ''}</span>}
            </div>
          </Panel>
          {data.reconciliation && <ReconPanel imp={data} busy={decide.isPending} save={(r) => decide.mutate({ reconcile: r })} />}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ history
function HistoryPanel() {
  const { projectId } = useV2Project();
  const imports = useQuery({ queryKey: ['v2', 'audit', projectId, 'SCHEDULE_IMPORT'], queryFn: () => dashboardApi.audit(projectId, { entity_type: 'SCHEDULE_IMPORT', limit: 50 }), retry: false });
  const versions = useQuery({ queryKey: ['v2', 'audit', projectId, 'SCHEDULE_VERSION'], queryFn: () => dashboardApi.audit(projectId, { entity_type: 'SCHEDULE_VERSION', limit: 50 }), retry: false });
  const rows = [...(imports.data?.items ?? []), ...(versions.data?.items ?? [])].sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1));
  return (
    <Panel icon={History} title="Import & version history" description="From the tamper-evident audit trail">
      {(imports.isPending || versions.isPending) && <Loading />}
      {rows.length === 0 && !imports.isPending && <div className="text-xs text-muted-foreground">No schedule activity yet.</div>}
      <div className="max-h-72 overflow-y-auto space-y-1" data-testid="schedule-history">
        {rows.map((r) => <div key={String(r.log_id)} className="text-xs flex justify-between gap-3 border-b border-border/60 pb-1"><span><b>{r.action.replace(/_/g, ' ').toLowerCase()}</b></span><span className="text-muted-foreground shrink-0">{when(r.occurred_at)}</span></div>)}
      </div>
    </Panel>
  );
}

export default function SchedulePage() {
  const { currentProject } = useProjectState();
  const { projectId, isArchived } = useV2Project();
  const qc = useQueryClient();
  const versions = useQuery({ queryKey: ['v2', 'versions', projectId], queryFn: ({ signal }) => scheduleApi.versions(projectId, { signal }), retry: false });
  const changed = () => { qc.invalidateQueries({ queryKey: ['v2'] }); };
  return (
    <div className="space-y-6" data-testid="schedule-page">
      <PageHeader project={`${currentProject?.name} (${currentProject?.code})`} title="Schedule" subtitle="Import a baseline or a revision, review the validation and reconciliation, build a version, then activate it. Only Project Managers manage schedules." />
      {isArchived && <Notice tone="warn">This project is archived and read-only.</Notice>}
      {versions.isPending && <Loading />}
      {versions.error && <QueryError error={versions.error} onRetry={() => versions.refetch()} />}
      {versions.data && <VersionsPanel versions={versions.data} onChanged={changed} />}
      <ImportPanel onVersionsChanged={changed} />
      <HistoryPanel />
    </div>
  );
}
