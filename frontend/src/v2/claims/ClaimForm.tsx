import React, { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { claimsApi } from '@/v2/api/endpoints';
import type { CatalogActivity, ClaimIn, ClaimSubmitted } from '@/v2/api/types';
import { Label, Notice, Pills, errText, num, today, F } from '@/v2/ui';
import { ActivityPicker, EvidenceUploader, useIdempotencyKey, ValidationList, type UploadedFile } from './shared';

type Mode = 'QTY' | 'PCT' | 'DATES';
interface Row { include: boolean; qty: string; basis: 'CUMULATIVE' | 'INCREMENTAL' }

export interface ClaimFormProps {
  projectId: string;
  /** correction of a rejected claim: the claim is filed as a NEW claim linked to this one */
  correctionOf?: string;
  initialActivity?: CatalogActivity | null;
  initialText?: string;
  onSubmitted?: (r: ClaimSubmitted) => void;
}

/** One progress claim: quantities per measured resource (units come from the schedule; nothing is converted), or a percentage, or dates. */
export function ClaimForm({ projectId, correctionOf, initialActivity = null, initialText = '', onSubmitted }: ClaimFormProps) {
  const qc = useQueryClient();
  const [activity, setActivity] = useState<CatalogActivity | null>(initialActivity);
  const [mode, setMode] = useState<Mode>('QTY');
  const [rows, setRows] = useState<Record<string, Row>>({});
  const [pctText, setPctText] = useState('');
  const [date, setDate] = useState(today());
  const [text, setText] = useState(initialText);
  const [start, setStart] = useState('');
  const [finish, setFinish] = useState('');
  const [location, setLocation] = useState('');
  const [docs, setDocs] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<ClaimSubmitted | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { keyFor, reset } = useIdempotencyKey(correctionOf ? 'fix' : 'claim');

  const measured = activity?.measured_assignments ?? [];
  useEffect(() => {
    setRows(Object.fromEntries(measured.map((m) => [m.assignment_uid, { include: true, qty: '', basis: 'CUMULATIVE' as const }])));
    setMode(measured.length ? 'QTY' : 'PCT');
    setResult(null); setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activity?.activity_uid]);
  useEffect(() => { if (initialActivity) setActivity(initialActivity); }, [initialActivity?.activity_uid]); // eslint-disable-line react-hooks/exhaustive-deps

  const payload: ClaimIn | null = useMemo(() => {
    if (!activity) return null;
    const body: ClaimIn = { event_date: date, raw_text: text.trim(), activity_uid: activity.activity_uid, evidence_document_ids: docs.map((d) => d.document_id) };
    if (location.trim()) body.location = location.trim();
    if (start) body.claimed_start = start;
    if (finish) body.claimed_finish = finish;
    if (mode === 'QTY') body.quantities = measured.filter((m) => rows[m.assignment_uid]?.include && rows[m.assignment_uid].qty !== '').map((m) => ({ qty: rows[m.assignment_uid].qty, uom: m.unit_of_measure, basis: rows[m.assignment_uid].basis, resource_hint: m.resource_code }));
    if (mode === 'PCT' && pctText !== '') body.claimed_pct = pctText;
    return body;
  }, [activity, date, text, docs, location, start, finish, mode, rows, pctText, measured]);

  const problems: string[] = [];
  if (!activity) problems.push('Choose the activity the work belongs to.');
  if (text.trim().length < 3) problems.push('Describe the work in the remarks.');
  if (date > today()) problems.push('A claim cannot be dated in the future.');
  if (start && finish && finish < start) problems.push('The finish date is before the start date.');
  if (mode === 'QTY') {
    const picked = measured.filter((m) => rows[m.assignment_uid]?.include && rows[m.assignment_uid].qty !== '');
    if (!picked.length) problems.push('Enter at least one quantity.');
    if (picked.some((m) => !(Number(rows[m.assignment_uid].qty) >= 0))) problems.push('Quantities must be zero or more.');
  }
  if (mode === 'PCT' && !(pctText !== '' && Number(pctText) >= 0 && Number(pctText) <= 100)) problems.push('Enter a percentage between 0 and 100.');
  if (mode === 'DATES' && !start && !finish) problems.push('Enter an actual start and/or finish date.');

  const submit = useMutation({
    mutationFn: () => {
      const key = keyFor(payload);
      return correctionOf ? claimsApi.correction(projectId, correctionOf, payload!, key) : claimsApi.submit(projectId, payload!, key);
    },
    onSuccess: (r) => {
      reset(); setResult(r); setError(null); setText(initialText); setPctText(''); setStart(''); setFinish(''); setDocs([]);
      setRows(Object.fromEntries(measured.map((m) => [m.assignment_uid, { include: true, qty: '', basis: 'CUMULATIVE' as const }])));
      qc.invalidateQueries({ queryKey: ['v2'] });
      onSubmitted?.(r);
    },
    onError: (e) => { setResult(null); setError(errText(e)); },
  });

  const modes = [{ value: 'QTY' as Mode, label: 'Quantities' }, { value: 'PCT' as Mode, label: 'Percent only' }, { value: 'DATES' as Mode, label: 'Start / finish dates' }].filter((o) => o.value !== 'QTY' || measured.length > 0 || !activity);

  return (
    <div className="space-y-3.5" data-testid="claim-form">
      {!correctionOf && <div><Label>Activity</Label><ActivityPicker projectId={projectId} value={activity} onChange={setActivity} /></div>}
      {correctionOf && activity && <div className="text-xs"><b>Correcting a rejected claim on {activity.external_activity_id}.</b> This is filed as a new claim linked to the rejected one; the rejected claim is not edited.</div>}
      {activity && (
        <>
          <div>
            <Label>What are you reporting?</Label>
            <Pills value={mode} onChange={setMode} options={modes} />
          </div>

          {mode === 'QTY' && (
            <div className="space-y-2" data-testid="quantity-rows">
              {measured.map((m) => {
                const r = rows[m.assignment_uid] ?? { include: true, qty: '', basis: 'CUMULATIVE' as const };
                const set = (p: Partial<Row>) => setRows({ ...rows, [m.assignment_uid]: { ...r, ...p } });
                return (
                  <div key={m.assignment_uid} className="rounded-lg border border-border p-2.5 grid grid-cols-1 sm:grid-cols-[1fr_auto_auto] gap-2 items-center" data-resource={m.resource_code}>
                    <label className="flex items-start gap-2 text-xs cursor-pointer"><input type="checkbox" className="mt-0.5 accent-[#FF7A18]" checked={r.include} onChange={(e) => set({ include: e.target.checked })} aria-label={`Include ${m.resource_name}`} />
                      <span><b>{m.resource_name}</b> <span className="font-mono text-[10px] text-muted-foreground">{m.resource_code}</span><br />
                        <span className="text-muted-foreground">Baseline {num(m.baseline_qty)} {m.unit_of_measure} · approved so far {m.approved_cumulative_qty === null ? 'none' : `${num(m.approved_cumulative_qty)} ${m.unit_of_measure}`}</span></span></label>
                    <select className={F('w-40')} aria-label={`Basis for ${m.resource_name}`} value={r.basis} disabled={!r.include} onChange={(e) => set({ basis: e.target.value as any })}><option value="CUMULATIVE">Total to date</option><option value="INCREMENTAL">Since last report</option></select>
                    <div className="flex items-center gap-1.5"><Input type="number" min={0} step="any" className="w-32" aria-label={`Quantity of ${m.resource_name}`} placeholder="0" value={r.qty} disabled={!r.include} onChange={(e) => set({ qty: e.target.value })} /><span className="text-xs font-mono w-14">{m.unit_of_measure}</span></div>
                  </div>
                );
              })}
              <div className="text-[10px] text-muted-foreground">Units are the schedule’s units for each resource. Quantities are recorded exactly as you enter them and are never converted or capped.</div>
            </div>
          )}

          {mode === 'PCT' && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2"><Input type="number" min={0} max={100} step="any" className="w-32" aria-label="Percent complete" value={pctText} onChange={(e) => setPctText(e.target.value)} /><span className="text-sm font-bold">% complete</span></div>
              {measured.length > 0 && <Notice tone="info" testid="pct-note">This activity is measured in quantities. A percentage-only claim is stored as reported and is <b>not</b> converted to quantities; a Supervisor must decide explicitly how to apply it. Quantities are usually clearer.</Notice>}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div><Label>Date of the work</Label><Input type="date" aria-label="Report date" value={date} max={today()} onChange={(e) => setDate(e.target.value)} /></div>
            <div><Label hint="(optional)">Actual start</Label><Input type="date" aria-label="Actual start" value={start} max={today()} onChange={(e) => setStart(e.target.value)} /></div>
            <div><Label hint="(optional)">Actual finish</Label><Input type="date" aria-label="Actual finish" value={finish} max={today()} onChange={(e) => setFinish(e.target.value)} /></div>
          </div>
          <div><Label>Remarks</Label><Textarea rows={3} aria-label="Remarks" value={text} onChange={(e) => setText(e.target.value)} placeholder="What was done, where, and how it was measured…" /></div>
          <div><Label hint="(optional)">Location</Label><Input aria-label="Location" value={location} onChange={(e) => setLocation(e.target.value)} placeholder="e.g. KM 12+400" /></div>
          <EvidenceUploader projectId={projectId} onChange={(d, u) => { setDocs(d); setUploading(u); }} />
        </>
      )}

      {error && <Notice tone="bad" testid="claim-error">{error}</Notice>}
      {result && (
        <div className="space-y-1.5" data-testid="claim-success">
          <Notice tone="ok"><b>Claim submitted</b> and waiting for a Supervisor’s decision. It does not change project progress until it is approved.</Notice>
          <ValidationList items={result.validations} />
        </div>
      )}
      {problems.length > 0 && activity && <ul className="text-[11px] text-muted-foreground list-disc ml-5">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
      <Button onClick={() => { setError(null); setResult(null); submit.mutate(); }} disabled={problems.length > 0 || submit.isPending || uploading} className="w-full gap-2 font-bold cursor-pointer" data-testid="submit-claim">
        {submit.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}{correctionOf ? 'Submit corrected claim' : 'Submit claim'}
      </Button>
    </div>
  );
}
