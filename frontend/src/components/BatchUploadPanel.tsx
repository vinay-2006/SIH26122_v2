import React, { useCallback, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  FileWarning,
  Files,
  GitMerge,
  Loader2,
  Upload,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfidenceBar } from '@/components/ConfidenceBar';
import { cn } from '@/lib/utils';
import { useProject } from '@/context/ProjectContext';
import { ACCEPT_ATTR as ACCEPT, FILE_TYPES_TEXT, fileProblem, kindLabel } from '@/lib/reportFiles';
import { IS_V2 } from '@/config';
import { batchApi, type BatchClaim, type BatchFile, type BatchReport } from '@/api/prototype';

const MAX_FILES = 25;

const METHOD_LABEL: Record<string, string> = {
  STRUCTURED: 'Structured sheet',
  LLM: 'AI extraction',
  RULES_FALLBACK: 'Rule-based extraction',
};

function bytes(n: number) {
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

function Chip({ children, tone = 'slate' }: { children: React.ReactNode; tone?: 'slate' | 'green' | 'amber' | 'red' | 'blue' }) {
  const tones = {
    slate: 'bg-slate-100 dark:bg-[#0B2742] text-slate-700 dark:text-slate-300 border-slate-300 dark:border-[#214766]',
    green: 'bg-emerald-50 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-300 border-emerald-300 dark:border-emerald-800',
    amber: 'bg-amber-50 dark:bg-amber-950/50 text-amber-800 dark:text-amber-300 border-amber-300 dark:border-amber-800',
    red: 'bg-rose-50 dark:bg-rose-950/50 text-rose-700 dark:text-rose-300 border-rose-300 dark:border-rose-800',
    blue: 'bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 border-blue-300 dark:border-blue-800',
  };
  return <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[10px] font-bold', tones[tone])}>{children}</span>;
}

function FileRow({ f }: { f: BatchFile }) {
  const ok = f.extraction_status === 'EXTRACTED';
  const failed = f.extraction_status === 'FAILED';
  return (
    <div className={cn('flex items-start gap-3 p-3 rounded-xl border text-xs', failed ? 'border-rose-300 dark:border-rose-900 bg-rose-50/50 dark:bg-rose-950/20' : 'border-slate-200 dark:border-[#214766]')}>
      <div className="mt-0.5 shrink-0">
        {ok ? <CheckCircle2 className="w-4 h-4 text-emerald-500" /> : failed ? <FileWarning className="w-4 h-4 text-rose-500" /> : <AlertTriangle className="w-4 h-4 text-amber-500" />}
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="font-bold truncate">{f.file_name}</div>
        <div className="flex flex-wrap items-center gap-1.5">
          {IS_V2 && <Chip tone="slate">{kindLabel(f.file_name)}</Chip>}
          {IS_V2 && <Chip tone={ok ? 'green' : failed ? 'red' : 'amber'}>{ok ? 'Read · claims await review' : failed ? 'Not read' : 'Read · nothing to claim'}</Chip>}
          {ok && <Chip tone="green">{f.claims_extracted} claim{f.claims_extracted === 1 ? '' : 's'} found</Chip>}
          {f.extraction_method && <Chip tone="blue">{METHOD_LABEL[f.extraction_method] ?? f.extraction_method}</Chip>}
          {f.merged_into_claim_ids.length > 0 && (
            <Chip tone="amber"><GitMerge className="w-3 h-3" />{f.merged_into_claim_ids.length} already reported by another file</Chip>
          )}
        </div>
        {f.error && <div className={cn('text-[11px]', failed ? 'text-rose-700 dark:text-rose-300' : 'text-muted-foreground')}>{f.error}</div>}
      </div>
    </div>
  );
}

function ClaimRow({ c }: { c: BatchClaim }) {
  const [open, setOpen] = useState(false);
  const pending = c.clarification_status === 'PENDING';
  const matched = !!c.matched_activity_id;
  const value = c.claimed_pct != null ? `${c.claimed_pct}%` : c.claimed_quantity != null ? `${c.claimed_quantity} ${c.claimed_uom ?? ''}` : '—';
  return (
    <div className="rounded-xl border border-slate-200 dark:border-[#214766] text-xs">
      <button type="button" onClick={() => setOpen((o) => !o)} className="w-full flex items-start gap-2 p-3 text-left cursor-pointer">
        {open ? <ChevronDown className="w-4 h-4 mt-0.5 shrink-0" /> : <ChevronRight className="w-4 h-4 mt-0.5 shrink-0" />}
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="font-semibold leading-snug">{c.raw_claim_text}</div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Chip tone="slate">reported {value}</Chip>
            {matched ? (
              <Chip tone="green">→ {c.matched_activity_id} {c.activity_name ? `· ${c.activity_name}` : ''}</Chip>
            ) : pending ? (
              <Chip tone="amber">needs your clarification</Chip>
            ) : (
              <Chip tone="red">no confident activity match: the supervisor will choose</Chip>
            )}
            {c.reported_by_multiple_files && <Chip tone="blue"><GitMerge className="w-3 h-3" />{c.file_names.length} files report this</Chip>}
            {matched && <Chip tone="slate">{c.status === 'VALIDATED' || c.status === 'REVIEW_REQUIRED' ? 'sent for supervisor review' : c.status.toLowerCase()}</Chip>}
          </div>
        </div>
      </button>
      {open && (
        <div className="px-3 pb-3 pl-9 space-y-3">
          {pending && c.clarification_question && (
            <div className="p-2 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-300 dark:border-amber-800 text-[11px]">{c.clarification_question}</div>
          )}
          {c.error && <div className="text-[11px] text-rose-600">{c.error}</div>}
          {c.candidates.length > 0 && (
            <div className="space-y-2">
              <div className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">Ranked activity candidates</div>
              {c.candidates.map((k) => (
                <div key={k.activity_id} className={cn('p-2 rounded-lg border', k.rank === 1 ? 'border-primary/40 bg-primary/5' : 'border-slate-200 dark:border-[#214766]')}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-bold">#{k.rank} {k.activity_id}{k.activity_name ? ` · ${k.activity_name}` : ''}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">{k.tier}</span>
                  </div>
                  <ConfidenceBar score={k.confidence} label="Match confidence" className="mt-1" />
                  {k.supporting && <div className="mt-1 text-[10px] text-emerald-700 dark:text-emerald-400">✓ {k.supporting.split(';').map((s) => s.trim()).filter(Boolean).join(' · ')}</div>}
                  {k.disqualifying && <div className="mt-0.5 text-[10px] text-rose-600 dark:text-rose-400">✗ {k.disqualifying.split(';').map((s) => s.trim()).filter(Boolean).join(' · ')}</div>}
                </div>
              ))}
            </div>
          )}
          {c.sources.length > 0 && (
            <div className="space-y-1">
              <div className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">Source{c.sources.length > 1 ? 's' : ''}</div>
              {c.sources.map((s, i) => (
                <div key={`${s.document_id}-${i}`} className="text-[11px]"><FileText className="inline w-3 h-3 mr-1" /><b>{s.file_name}</b>{s.snippet ? <span className="text-muted-foreground"> — “{s.snippet.slice(0, 140)}”</span> : null}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function BatchResult({ report }: { report: BatchReport }) {
  const stat = (label: string, value: number, tone?: string) => (
    <div className={cn('rounded-xl border p-3 text-center', tone ?? 'border-slate-200 dark:border-[#214766]')}>
      <div className="text-xl font-black">{value}</div>
      <div className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">{label}</div>
    </div>
  );
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
        {stat('Files', report.file_count)}
        {stat('Claims', report.claim_count)}
        {stat('Matched', report.matched_count, 'border-emerald-300 dark:border-emerald-800')}
        {stat('Merged duplicates', report.merged_count, report.merged_count ? 'border-blue-300 dark:border-blue-800' : undefined)}
        {stat('Need attention', report.unmatched_count + report.needs_clarification_count, report.unmatched_count + report.needs_clarification_count ? 'border-amber-300 dark:border-amber-800' : undefined)}
      </div>

      <section className="space-y-2">
        <h3 className="text-xs font-extrabold uppercase tracking-wide">Files</h3>
        <div className="space-y-2">{report.files.map((f) => <FileRow key={f.document_id} f={f} />)}</div>
      </section>

      {report.activities.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-xs font-extrabold uppercase tracking-wide">Activities identified ({report.activities.length})</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {report.activities.map((a) => (
              <div key={a.activity_id} className="rounded-xl border border-slate-200 dark:border-[#214766] p-3 text-xs space-y-1">
                <div className="font-bold">{a.activity_id}</div>
                <div className="text-muted-foreground">{a.activity_name}{a.stage_name ? ` · ${a.stage_name}` : ''}</div>
                <div className="flex flex-wrap gap-1">{a.file_names.map((n) => <Chip key={n}><FileText className="w-3 h-3" />{n}</Chip>)}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="space-y-2">
        <h3 className="text-xs font-extrabold uppercase tracking-wide">Claims ({report.claims.length})</h3>
        <div className="space-y-2">{report.claims.map((c) => <ClaimRow key={c.event_id} c={c} />)}</div>
      </section>
    </div>
  );
}

/** Multi-file intake: several reports / files / photos in one operation; the batch is extracted, normalised and matched. */
export function BatchUploadPanel() {
  const { currentProject, currentScheduleVersion } = useProject();
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [report, setReport] = useState<BatchReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rejected, setRejected] = useState<{ name: string; reason: string }[]>([]);

  const history = useQuery({
    queryKey: ['v7', 'batches', currentProject.id],
    queryFn: () => batchApi.list(currentProject.id, true),
    retry: false,
  });

  const addFiles = useCallback((incoming: FileList | File[]) => {
    setError(null);
    // copy NOW: a FileList is live, and the input's value is cleared right after this call
    const all = Array.from(incoming);
    const bad = all.map((f) => ({ name: f.name, reason: fileProblem(f) })).filter((x): x is { name: string; reason: string } => !!x.reason);
    const picked = all.filter((f) => !fileProblem(f));      // v2: unsupported / oversized / schedule files are refused here, with the reason
    setRejected(bad);
    setFiles((prev) => {
      const next = [...prev];
      for (const f of picked) {
        if (!next.some((x) => x.name === f.name && x.size === f.size)) next.push(f);
      }
      if (next.length > MAX_FILES) setError(`A batch can contain at most ${MAX_FILES} files.`);
      return next.slice(0, MAX_FILES);
    });
  }, []);

  const upload = useMutation({
    mutationFn: () => batchApi.upload(files, currentScheduleVersion.id),
    onSuccess: (r) => {
      setReport(r);
      setFiles([]);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ['v7', 'batches', currentProject.id] });
      queryClient.invalidateQueries();   // review queue / digest / dashboard counters
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : 'Upload failed'),
  });

  const openBatch = async (id: string) => {
    try {
      setReport(await batchApi.get(currentProject.id, id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open that batch');
    }
  };

  return (
    <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl">
      <CardHeader className="p-6 pb-4">
        <CardTitle className="text-base font-extrabold flex items-center gap-2"><Files className="w-5 h-5 text-[#FF7A18]" /> {IS_V2 ? 'Upload Progress Report' : 'Upload reports in a batch'}</CardTitle>
        <CardDescription className="text-xs font-semibold mt-1">
          {IS_V2
            ? 'Add one or more progress reports and evidence files: spreadsheets, documents, photographs, or scanned / handwritten site reports. This is not for baseline schedules; a Project Manager imports those under Schedule. Each file is read, every reported item becomes a claim that waits for Supervisor review, and the same item reported in two files is kept once.'
            : 'Drop several daily reports, spreadsheets, diaries or site photos at once. Each file is read, every reported item becomes a claim, each claim is matched to the schedule, and the same item reported in two files is kept once.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="p-6 pt-0 space-y-4">
        <input ref={inputRef} type="file" multiple accept={ACCEPT} className="hidden" data-testid="batch-file-input"
          onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ''; }} />
        <div
          role="button"
          tabIndex={0}
          aria-label="Select files to upload"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current?.click(); } }}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}
          className={cn('border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            dragging ? 'border-primary bg-primary/5' : 'border-slate-300 dark:border-[#214766] hover:border-primary/60')}
        >
          <Upload className="w-7 h-7 mx-auto text-[#FF7A18]" />
          <div className="mt-2 text-sm font-bold">Click to choose files, or drop them here</div>
          <div className="text-[11px] text-muted-foreground">{FILE_TYPES_TEXT} — up to {MAX_FILES} files</div>
        </div>

        {files.length > 0 && (
          <div className="space-y-1.5">
            {files.map((f) => (
              <div key={`${f.name}-${f.size}`} className="flex items-center gap-2 text-xs p-2 rounded-lg border border-slate-200 dark:border-[#214766]">
                <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
                <span className="truncate flex-1 font-semibold">{f.name}</span>
                {IS_V2 && <Chip tone="blue">{kindLabel(f.name)}</Chip>}
                {IS_V2 && <Chip tone="green">Ready to upload</Chip>}
                <span className="text-muted-foreground font-mono">{bytes(f.size)}</span>
                <button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFiles((p) => p.filter((x) => x !== f))} className="cursor-pointer text-muted-foreground hover:text-rose-600"><X className="w-4 h-4" /></button>
              </div>
            ))}
            <Button onClick={() => upload.mutate()} disabled={upload.isPending} className="w-full gap-2 cursor-pointer font-bold">
              {upload.isPending ? <><Loader2 className="w-4 h-4 animate-spin" /> Reading {files.length} file{files.length === 1 ? '' : 's'} and matching to the schedule…</> : <><Upload className="w-4 h-4" /> Process {files.length} file{files.length === 1 ? '' : 's'}</>}
            </Button>
          </div>
        )}

{rejected.length > 0 && (
          <div data-testid="rejected-files" className="space-y-1.5">
            {rejected.map((x) => (
              <div key={x.name} role="alert" className="flex items-start gap-2 text-xs p-2.5 rounded-lg border border-rose-300 dark:border-rose-900 bg-rose-50 dark:bg-rose-950/30 text-rose-700 dark:text-rose-300">
                <FileWarning className="w-4 h-4 shrink-0 mt-0.5" />
                <div className="min-w-0"><div className="font-bold truncate">{x.name} · not uploaded</div><div className="text-[11px]">{x.reason}</div></div>
              </div>
            ))}
          </div>
        )}

        {error && <div role="alert" className="text-xs p-3 rounded-lg border border-rose-300 dark:border-rose-900 bg-rose-50 dark:bg-rose-950/30 text-rose-700 dark:text-rose-300">{error}</div>}

        {report && (
          <div className="pt-2 border-t border-border space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-xs font-bold">Batch result · <span className="font-mono text-muted-foreground">{report.batch_id.slice(0, 8)}</span> · {new Date(report.created_at).toLocaleString()}</div>
              <Chip tone={report.status === 'COMPLETED' ? 'green' : report.status === 'PARTIAL' ? 'amber' : 'red'}>{report.status}</Chip>
            </div>
            {IS_V2 && <div className="text-[11px] text-muted-foreground">Claims found in these files are waiting for Supervisor review; nothing here changes approved progress. A file marked as failed was not read: check the reason shown and upload it again in a supported form.</div>}
            <BatchResult report={report} />
          </div>
        )}

        {history.data && history.data.length > 0 && (
          <div className="pt-2 border-t border-border space-y-1.5">
            <div className="text-[10px] font-extrabold uppercase tracking-wide text-muted-foreground">Your earlier batches</div>
            {history.data.slice(0, 6).map((b) => (
              <button key={b.batch_id} type="button" onClick={() => openBatch(b.batch_id)} className="w-full flex items-center justify-between gap-2 text-xs p-2 rounded-lg border border-slate-200 dark:border-[#214766] hover:border-primary/50 text-left cursor-pointer">
                <span>{new Date(b.created_at).toLocaleString()} · {b.file_count} files → {b.claim_count} claims{b.merged_count ? ` (${b.merged_count} merged)` : ''}</span>
                <Chip tone={b.status === 'COMPLETED' ? 'green' : 'amber'}>{b.status}</Chip>
              </button>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
