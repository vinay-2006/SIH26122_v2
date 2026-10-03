import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { FileText, Loader2, PlusCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { useProjectState } from '@/context/ProjectContext';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { documentsApi } from '@/v2/api/endpoints';
import type { ExtractionResult } from '@/v2/api/types';
import { ClaimForm } from '@/v2/claims/ClaimForm';
import { EvidenceUploader, type UploadedFile } from '@/v2/claims/shared';
import { Notice, PageHeader, Panel, errText, today, Label } from '@/v2/ui';
import { Input } from '@/components/ui/input';

/** Read a report you uploaded (CSV, XLSX, text, text-layer PDF) and file the claims it contains. Strict: ambiguous rows are listed, never guessed. */
function ReportExtraction({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [docs, setDocs] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [date, setDate] = useState('');
  const [preview, setPreview] = useState<ExtractionResult | null>(null);
  const [filed, setFiled] = useState<ExtractionResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const doc = docs[docs.length - 1];
  const run = useMutation({
    mutationFn: (create: boolean) => documentsApi.extract(projectId, doc.document_id, { create_claims: create, ...(date ? { event_date: date } : {}) }),
    onSuccess: (r) => { setError(null); if (r.preview_only) { setPreview(r); setFiled(null); } else { setFiled(r); setPreview(null); qc.invalidateQueries({ queryKey: ['v2'] }); } },
    onError: (e) => { setError(errText(e)); setPreview(null); setFiled(null); },
  });
  const r = filed ?? preview;
  return (
    <Panel icon={FileText} title="Or file claims from a daily report" description="Upload a CSV, Excel, text or text-based PDF report. Rows with an activity id, a quantity with a recognised unit and an explicit basis (total / today) become pending claims. Scanned documents and photographs are not read.">
      <EvidenceUploader projectId={projectId} onChange={(d, u) => { setDocs(d.slice(-1)); setUploading(u); setPreview(null); setFiled(null); }} label="Choose the report file" kind={() => 'DAILY_REPORT'} />
      {doc && (
        <div className="flex flex-wrap items-end gap-3">
          <div><Label hint="(only if the file has no dates)">Report date</Label><Input type="date" aria-label="Fallback report date" className="w-44" value={date} max={today()} onChange={(e) => setDate(e.target.value)} /></div>
          <Button variant="outline" disabled={uploading || run.isPending} onClick={() => run.mutate(false)} className="cursor-pointer" data-testid="extract-preview">{run.isPending && run.variables === false ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Preview what would be filed</Button>
        </div>
      )}
      {error && <Notice tone="bad" testid="extract-error">{error} <span className="block mt-0.5">No claim was filed.</span></Notice>}
      {r && (
        <div className="space-y-2 text-xs" data-testid="extract-result">
          <Notice tone={filed ? 'ok' : 'info'}>{filed ? `Filed ${r.counts.created} new claim(s)${r.counts.existing ? `, ${r.counts.existing} already existed` : ''}.` : `${r.claims.length} row(s) are ready to file.`} {r.skipped.length > 0 && `${r.skipped.length} row(s) were skipped.`} <span className="text-muted-foreground">Method: {r.method}{r.page_count ? ` · ${r.page_count} page(s)` : ''}</span></Notice>
          {r.skipped.length > 0 && <ul className="list-disc ml-5 space-y-0.5" data-testid="extract-skipped">{r.skipped.map((s, i) => <li key={i}><b>{s.activity_ref || '(no activity id)'}</b> · {s.source_ref}: {s.reasons.join(', ')}</li>)}</ul>}
          {filed && filed.counts.created > 0 && <Link to="/claims/mine" className="font-bold text-primary underline">See my claims</Link>}
          {preview && preview.claims.length > 0 && <Button disabled={run.isPending} onClick={() => run.mutate(true)} className="gap-1.5 cursor-pointer" data-testid="extract-file"><PlusCircle className="w-4 h-4" />File {preview.claims.length} claim(s)</Button>}
        </div>
      )}
    </Panel>
  );
}

export default function ClaimFormPage() {
  const { currentProject } = useProjectState();
  const { projectId, isArchived, activeVersionId } = useV2Project();
  return (
    <div className="space-y-6" data-testid="claim-page">
      <PageHeader project={`${currentProject?.name} (${currentProject?.code})`} title="Submit a Claim" subtitle="Report progress on an activity of the active schedule. Your claim is a proposal: it never changes project progress until a Supervisor approves it." />
      {isArchived && <Notice tone="warn">This project is archived and read-only.</Notice>}
      {!activeVersionId && <Notice tone="info">This project has no active schedule yet, so there is nothing to report against.</Notice>}
      {activeVersionId && !isArchived && (
        <div className="grid grid-cols-1 xl:grid-cols-5 gap-6">
          <div className="xl:col-span-3"><Panel icon={PlusCircle} title="Progress claim"><ClaimForm projectId={projectId} /></Panel></div>
          <div className="xl:col-span-2"><ReportExtraction projectId={projectId} /></div>
        </div>
      )}
    </div>
  );
}
