import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, Loader2, Pencil, Plus, Save, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { knowledgeApi, type KnowledgeEntry, type KnowledgeInput, type KnowledgeProvenance, type KnowledgeSection } from '@/v2/api/endpoints';
import { FIELD, Label, Loading, Notice, PageHeader, Panel, QueryError, errText, when } from '@/v2/ui';

const PROVENANCE: Record<KnowledgeProvenance, { label: string; hint: string; cls: string }> = {
  FROM_RECORDS: { label: 'From project records', hint: 'Generated from the project and its schedule; refreshed automatically, not typed in', cls: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-400/50' },
  AUTHORED: { label: 'Authored', hint: 'Written by a person', cls: 'bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-400/50' },
  ILLUSTRATIVE: { label: 'Illustrative', hint: 'A plausible description, not a contractual or measured fact', cls: 'bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-400/50' },
  NOT_SPECIFIED: { label: 'Not specified', hint: 'An explicit statement that the information is not available', cls: 'bg-slate-500/10 text-slate-600 dark:text-slate-300 border-slate-400/50' },
};
const EDITABLE: Exclude<KnowledgeProvenance, 'FROM_RECORDS'>[] = ['AUTHORED', 'ILLUSTRATIVE', 'NOT_SPECIFIED'];

function Chip({ p }: { p: KnowledgeProvenance }) {
  const m = PROVENANCE[p];
  return <span title={m.hint} className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${m.cls}`}>{m.label}</span>;
}

function Editor({ initial, sections, onCancel, onSave, saving, error }: {
  initial: KnowledgeInput; sections: { section: KnowledgeSection; label: string }[]; onCancel: () => void; onSave: (v: KnowledgeInput) => void; saving: boolean; error: string | null;
}) {
  const [v, setV] = useState<KnowledgeInput>(initial);
  const set = <K extends keyof KnowledgeInput>(k: K, val: KnowledgeInput[K]) => setV((p) => ({ ...p, [k]: val }));
  return (
    <div className="space-y-3 p-3 rounded-xl border border-primary/40 bg-primary/5" data-testid="knowledge-editor">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div><Label>Section</Label>
          <select className={FIELD} aria-label="Section" value={v.section} onChange={(e) => set('section', e.target.value as KnowledgeSection)}>{sections.map((s) => <option key={s.section} value={s.section}>{s.label}</option>)}</select></div>
        <div className="sm:col-span-2"><Label>Title</Label><Input aria-label="Title" value={v.title} onChange={(e) => set('title', e.target.value)} maxLength={160} /></div>
      </div>
      <div><Label>Text</Label><Textarea rows={7} aria-label="Text" value={v.body} onChange={(e) => set('body', e.target.value)} maxLength={8000} /></div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div><Label hint="Say honestly where the statement comes from">Provenance</Label>
          <select className={FIELD} aria-label="Provenance" value={v.provenance} onChange={(e) => set('provenance', e.target.value as KnowledgeInput['provenance'])}>
            {EDITABLE.map((p) => <option key={p} value={p}>{PROVENANCE[p].label}</option>)}</select></div>
        <div className="sm:col-span-2"><Label hint="Comma-separated; help the agents find this entry">Tags</Label>
          <Input aria-label="Tags" value={v.tags.join(', ')} onChange={(e) => set('tags', e.target.value.split(',').map((t) => t.trim()).filter(Boolean).slice(0, 20))} /></div>
      </div>
      <p className="text-[11px] text-muted-foreground">{PROVENANCE[v.provenance].hint}. Project Intelligence and the Time Agent quote this text and show the label above beside it.</p>
      {error && <Notice tone="bad">{error}</Notice>}
      <div className="flex gap-2">
        <Button onClick={() => onSave(v)} disabled={saving || v.title.trim().length < 3 || v.body.trim().length < 3} className="gap-1.5 cursor-pointer" data-testid="save-knowledge">
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save</Button>
        <Button variant="outline" onClick={onCancel} className="gap-1.5 cursor-pointer"><X className="w-4 h-4" /> Cancel</Button>
      </div>
    </div>
  );
}

export default function KnowledgePage() {
  const { projectId, detail, isArchived } = useV2Project();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['v2', 'knowledge', projectId], queryFn: () => knowledgeApi.list(projectId), retry: false });
  const [editing, setEditing] = useState<string | 'new' | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<KnowledgeSection | 'ALL'>('ALL');
  const refresh = () => qc.invalidateQueries({ queryKey: ['v2', 'knowledge', projectId] });
  const save = useMutation({
    mutationFn: ({ id, body }: { id: string | null; body: KnowledgeInput }) => (id ? knowledgeApi.update(projectId, id, body) : knowledgeApi.create(projectId, body)),
    onSuccess: () => { setEditing(null); setErr(null); refresh(); }, onError: (e) => setErr(errText(e)),
  });
  const retire = useMutation({ mutationFn: (id: string) => knowledgeApi.retire(projectId, id), onSuccess: refresh, onError: (e) => setErr(errText(e)) });

  const grouped = useMemo(() => {
    const by = new Map<KnowledgeSection, KnowledgeEntry[]>();
    (q.data?.items ?? []).forEach((i) => { if (filter === 'ALL' || filter === i.section) by.set(i.section, [...(by.get(i.section) ?? []), i]); });
    return by;
  }, [q.data, filter]);

  if (q.isLoading) return <Loading what="Loading project knowledge…" />;
  if (q.isError) return <QueryError error={q.error} onRetry={() => q.refetch()} />;
  const sections = q.data!.sections;
  const blank: KnowledgeInput = { section: filter === 'ALL' ? 'OVERVIEW' : filter, title: '', body: '', provenance: 'AUTHORED', tags: [], sort_order: 100 };
  return (
    <div className="space-y-5 max-w-5xl" data-testid="knowledge-page">
      <PageHeader project={`${detail.project_name} (${detail.project_code})`} title="Project Knowledge"
        subtitle="The authored context of this project. Project Intelligence and the Time Agent read it together with live data, quote it, and cite which section they used. Progress, delays and claims are never copied here: they stay live."
        actions={!isArchived && editing === null ? <Button onClick={() => { setErr(null); setEditing('new'); }} className="gap-1.5 cursor-pointer" data-testid="add-knowledge"><Plus className="w-4 h-4" /> Add entry</Button> : undefined} />
      <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Sections">
        {(['ALL', ...sections.map((s) => s.section)] as (KnowledgeSection | 'ALL')[]).map((s) => (
          <button key={s} type="button" onClick={() => setFilter(s)} data-testid={`filter-${s}`}
            className={`px-3 py-1 rounded-full text-xs font-semibold border cursor-pointer ${filter === s ? 'bg-primary text-primary-foreground border-primary' : 'border-border text-muted-foreground hover:text-foreground'}`}>
            {s === 'ALL' ? 'All' : sections.find((x) => x.section === s)!.label}</button>
        ))}
      </div>
      {editing === 'new' && <Editor initial={blank} sections={sections} onCancel={() => setEditing(null)} saving={save.isPending} error={err} onSave={(body) => save.mutate({ id: null, body })} />}
      {grouped.size === 0 && <Notice tone="info">No entries yet.</Notice>}
      {sections.filter((s) => grouped.has(s.section)).map((s) => (
        <Panel key={s.section} icon={BookOpen} title={s.label}>
          <div className="space-y-3">
            {grouped.get(s.section)!.map((e) => (editing === e.knowledge_id ? (
              <Editor key={e.knowledge_id} initial={{ section: e.section, title: e.title, body: e.body, provenance: e.provenance === 'FROM_RECORDS' ? 'AUTHORED' : e.provenance, tags: e.tags, sort_order: e.sort_order }}
                sections={sections} onCancel={() => setEditing(null)} saving={save.isPending} error={err} onSave={(body) => save.mutate({ id: e.knowledge_id, body })} />
            ) : (
              <div key={e.knowledge_id} className="rounded-xl border border-border p-3 space-y-1.5" data-testid="knowledge-entry" data-title={e.title}>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap"><span className="font-bold text-sm">{e.title}</span><Chip p={e.provenance} /><span className="text-[10px] text-muted-foreground">v{e.version} · {when(e.updated_at)}</span></div>
                  {!isArchived && (
                    <div className="flex gap-1 shrink-0">
                      <Button size="sm" variant="outline" className="h-7 gap-1 cursor-pointer" aria-label={`Edit ${e.title}`} onClick={() => { setErr(null); setEditing(e.knowledge_id); }}><Pencil className="w-3 h-3" /> Edit</Button>
                      <Button size="sm" variant="outline" className="h-7 cursor-pointer" aria-label={`Retire ${e.title}`} disabled={retire.isPending}
                        onClick={() => { if (window.confirm('Retire this entry? It is kept in the history but no longer used by the agents.')) retire.mutate(e.knowledge_id); }}>Retire</Button>
                    </div>
                  )}
                </div>
                <p className="text-sm whitespace-pre-wrap leading-relaxed text-foreground/90">{e.body}</p>
              </div>
            )))}
          </div>
        </Panel>
      ))}
      {err && editing === null && <Notice tone="bad">{err}</Notice>}
    </div>
  );
}
