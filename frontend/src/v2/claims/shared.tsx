import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2, Paperclip, Search, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { catalogApi, documentsApi } from '@/v2/api/endpoints';
import { V2Error, newIdempotencyKey } from '@/v2/api/http';
import type { CatalogActivity } from '@/v2/api/types';
import { FIELD, Notice, errText, num } from '@/v2/ui';
import { cn } from '@/lib/utils';

/** One idempotency key per distinct payload: retrying the SAME submission is safe; changing the content makes a new key; success resets. */
export function useIdempotencyKey(prefix: string) {
  const state = useRef<{ payload: string; key: string } | null>(null);
  const keyFor = useCallback((payload: unknown) => {
    const p = JSON.stringify(payload);
    if (!state.current || state.current.payload !== p) state.current = { payload: p, key: newIdempotencyKey(prefix) };
    return state.current.key;
  }, [prefix]);
  const reset = useCallback(() => { state.current = null; }, []);
  return { keyFor, reset };
}

/** find one activity of the active schedule by uid (pages through the catalog; the API has no lookup by uid) */
export function useActivityByUid(projectId: string, uid: string | null | undefined) {
  return useQuery({
    queryKey: ['v2', 'catalog-uid', projectId, uid], enabled: !!uid, retry: false, staleTime: 60_000,
    queryFn: async (): Promise<CatalogActivity | null> => {
      for (let offset = 0, n = 0; n < 10; n++) {
        const r = await catalogApi.activities(projectId, { limit: 200, offset });
        const hit = r.items.find((a) => a.activity_uid === uid);
        if (hit) return hit;
        if (r.next_offset === null) return null;
        offset = r.next_offset;
      }
      return null;
    },
  });
}

export function ActivityPicker({ projectId, value, onChange, disabled }: { projectId: string; value: CatalogActivity | null; onChange: (a: CatalogActivity | null) => void; disabled?: boolean }) {
  const [text, setText] = useState('');
  const [debounced, setDebounced] = useState('');
  useEffect(() => { const t = setTimeout(() => setDebounced(text.trim()), 250); return () => clearTimeout(t); }, [text]);
  const q = useQuery({ queryKey: ['v2', 'catalog', projectId, debounced], queryFn: ({ signal }) => catalogApi.activities(projectId, { q: debounced || undefined, limit: 30 }, { signal }), retry: false, enabled: !value, placeholderData: (p) => p });
  if (value) {
    return (
      <div className="flex items-start justify-between gap-2 rounded-lg border border-border p-2.5 text-xs" data-testid="selected-activity">
        <div className="min-w-0"><div className="font-bold">{value.external_activity_id} · {value.activity_name}</div><div className="text-muted-foreground truncate">{value.wbs_path.replace(/\./g, ' › ')}</div></div>
        {!disabled && <Button type="button" size="sm" variant="ghost" onClick={() => onChange(null)} className="cursor-pointer shrink-0">Change</Button>}
      </div>
    );
  }
  return (
    <div className="space-y-1.5">
      <div className="relative"><Search className="w-3.5 h-3.5 absolute left-2.5 top-3 text-muted-foreground" /><Input className="pl-8" aria-label="Find activity" placeholder="Search by activity id or name…" value={text} onChange={(e) => setText(e.target.value)} disabled={disabled} /></div>
      <div className="max-h-48 overflow-y-auto rounded-lg border border-border divide-y divide-border/60" role="listbox" aria-label="Activities" data-testid="activity-options">
        {q.isPending && <div className="p-2.5 text-xs text-muted-foreground flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" />Loading activities…</div>}
        {q.error && <div className="p-2.5 text-xs text-rose-600">{errText(q.error)}</div>}
        {q.data?.items.length === 0 && <div className="p-2.5 text-xs text-muted-foreground">No activity matches.</div>}
        {q.data?.items.map((a) => (
          <button key={a.activity_uid} type="button" role="option" aria-selected={false} onClick={() => onChange(a)} className="w-full text-left px-2.5 py-1.5 text-xs hover:bg-secondary/70 cursor-pointer" data-testid="activity-option" data-activity={a.external_activity_id}>
            <span className="font-bold">{a.external_activity_id}</span> · {a.activity_name} <span className="text-muted-foreground">({a.execution_state.replace('_', ' ').toLowerCase()} · {num(a.physical_pct)}%)</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export interface UploadedFile { document_id: string; file_name: string; kind: string }
interface Row { id: number; name: string; progress: number; error?: string; doc?: UploadedFile; ctl?: AbortController }

const kindFor = (f: File): string => (f.type.startsWith('image/') || /\.(png|jpe?g)$/i.test(f.name) ? 'PHOTO' : 'EVIDENCE');

/** Uploads happen as soon as files are chosen (multipart, with progress); the caller receives the stored document ids. */
export function EvidenceUploader({ projectId, onChange, label = 'Attach evidence (photos, measurement sheets, PDFs, spreadsheets)', kind, disabled }: {
  projectId: string; onChange: (docs: UploadedFile[], uploading: boolean) => void; label?: string; kind?: (f: File) => string; disabled?: boolean;
}) {
  const [rows, setRows] = useState<Row[]>([]);
  const seq = useRef(0);
  const input = useRef<HTMLInputElement>(null);
  const docs = useMemo(() => rows.flatMap((r) => (r.doc ? [r.doc] : [])), [rows]);
  const uploading = rows.some((r) => !r.doc && !r.error);
  useEffect(() => { onChange(docs, uploading); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [docs.map((d) => d.document_id).join(','), uploading]);
  const patch = (id: number, p: Partial<Row>) => setRows((r) => r.map((x) => (x.id === id ? { ...x, ...p } : x)));
  const add = (files: File[]) => files.forEach(async (f) => {
    const id = ++seq.current;
    const ctl = new AbortController();
    setRows((r) => [...r, { id, name: f.name, progress: 0, ctl }]);
    try {
      const d = await documentsApi.upload(projectId, (kind ?? kindFor)(f), f, (p) => patch(id, { progress: p }), ctl.signal);
      patch(id, { progress: 1, doc: { document_id: d.document_id, file_name: d.file_name, kind: d.kind }, ctl: undefined });
    } catch (e: any) {
      if (e?.name === 'AbortError') return setRows((r) => r.filter((x) => x.id !== id));
      // a duplicate of a file this person already uploaded is reusable: the server tells us its id
      if (e instanceof V2Error && e.code === 'DUPLICATE_UPLOAD' && (e.details as any)?.document_id) return patch(id, { progress: 1, doc: { document_id: (e.details as any).document_id, file_name: f.name, kind: kind ? kind(f) : kindFor(f) }, ctl: undefined });
      patch(id, { error: errText(e), ctl: undefined });
    }
  });
  return (
    <div className="space-y-1.5">
      <input ref={input} type="file" multiple className="hidden" data-testid="evidence-input" aria-label="Evidence files" onChange={(e) => { const picked = Array.from(e.target.files ?? []); e.target.value = ''; if (picked.length) add(picked); }} />
      <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => input.current?.click()} className="gap-1.5 cursor-pointer"><Paperclip className="w-3.5 h-3.5" />{label}</Button>
      <div className="text-[10px] text-muted-foreground">PDF, PNG, JPEG, CSV, TXT or XLSX · up to 25 MB (images 15 MB). Photographs are evidence only; their contents are not read.</div>
      {rows.map((r) => (
        <div key={r.id} className="text-xs rounded-md border border-border px-2 py-1" data-testid="evidence-row">
          <div className="flex items-center justify-between gap-2"><span className="truncate font-semibold">{r.name}</span>
            <span className="shrink-0 flex items-center gap-1.5">{r.error ? <span className="text-rose-600">failed</span> : r.doc ? <span className="text-emerald-600">uploaded</span> : <span>{Math.round(r.progress * 100)}%</span>}
              <button type="button" aria-label={`Remove ${r.name}`} className="cursor-pointer" onClick={() => { r.ctl?.abort(); setRows((x) => x.filter((y) => y.id !== r.id)); }}><X className="w-3 h-3" /></button></span></div>
          {!r.doc && !r.error && <div className="h-1 mt-1 rounded bg-slate-200 dark:bg-[#0E2B47] overflow-hidden"><div className="h-full bg-[#FF7A18]" style={{ width: `${Math.round(r.progress * 100)}%` }} /></div>}
          {r.error && <div className="text-rose-600 mt-0.5" role="alert">{r.error}</div>}
        </div>
      ))}
    </div>
  );
}

export function ValidationList({ items }: { items: { rule: string; severity: string; message: string }[] }) {
  if (!items.length) return null;
  return (
    <div className="space-y-1" data-testid="claim-validations">{items.map((v, i) => <Notice key={i} tone={v.severity === 'ERROR' ? 'bad' : v.severity === 'WARNING' ? 'warn' : 'info'}><b className="font-mono text-[10px] mr-1">{v.rule}</b>{v.message}</Notice>)}</div>
  );
}

export const DocLink = ({ projectId, id, name }: { projectId: string; id: string; name: string }) => {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <span className="inline-flex items-center gap-1.5">
      <button type="button" disabled={busy} className={cn('text-primary underline cursor-pointer', busy && 'opacity-60')} data-testid="evidence-download" onClick={async () => {
        setBusy(true); setErr(null);
        try { const blob = await documentsApi.content(projectId, id); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 5000); }
        catch (e) { setErr(errText(e)); } finally { setBusy(false); }
      }}>{name}</button>
      {err && <span className="text-rose-600 text-[10px]">{err}</span>}
    </span>
  );
};

export { FIELD };

/** uid -> activity for labelling claims (claim lists carry the activity uid; the catalog has the readable id and name) */
export function useActivityMap(projectId: string) {
  const q = useQuery({
    queryKey: ['v2', 'catalog-map', projectId], retry: false, staleTime: 5 * 60_000,
    queryFn: async () => {
      const map = new Map<string, CatalogActivity>();
      for (let offset = 0, n = 0; n < 10; n++) {
        const r = await catalogApi.activities(projectId, { limit: 200, offset });
        r.items.forEach((a) => map.set(a.activity_uid, a));
        if (r.next_offset === null) break;
        offset = r.next_offset;
      }
      return map;
    },
  });
  return q.data ?? new Map<string, CatalogActivity>();
}
