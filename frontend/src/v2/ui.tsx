/**
 * Small presentational helpers for the v2 pages. They only package class strings and layout patterns that already exist in the legacy pages
 * (IssuesDelays, ProjectProgressPanel, ExecutionStateBadge): same cards, fields, pills, bars and headings. No new visual language.
 */
import React from 'react';
import { AlertTriangle, CheckCircle2, Hourglass, Loader2, PlayCircle } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ErrorState } from '@/components/ui/error-state';
import { cn } from '@/lib/utils';
import { V2Error } from '@/v2/api/http';
import type { ClaimStatus, Lifecycle, Num, Severity } from '@/v2/api/types';

export const CARD = 'border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl';
export const FIELD = 'w-full h-9 rounded-lg border border-slate-300 dark:border-[#1E3A5F] bg-white dark:bg-[#0A2340] px-2.5 text-sm text-slate-900 dark:text-[#F5F7FA] focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-[#FF7A18] disabled:opacity-60';

/** the shared field style with overrides merged properly (a later width or height wins) */
export const F = (extra = '') => cn(FIELD, extra);

export const pct = (v: Num | null | undefined, digits = 1): string => {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return `${Number.isInteger(n) ? n : n.toFixed(digits)}%`;
};
export const num = (v: Num | null | undefined): string => {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 3 }) : String(v);
};
export const day = (v: string | null | undefined): string => (v ? new Date(v.length === 10 ? `${v}T00:00:00` : v).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) : '—');
export const when = (v: string | null | undefined): string => (v ? new Date(v).toLocaleString(undefined, { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—');
export const today = () => new Date().toISOString().slice(0, 10);
export const errText = (e: unknown, fallback = 'Something went wrong'): string => (e instanceof V2Error || e instanceof Error ? e.message : fallback);

/** hidden native input + the app's outline button (the pattern the legacy upload panels use) */
export function FilePick({ label, accept, file, onFile, disabled, testid, ariaLabel }: { label: string; accept?: string; file: File | null; onFile: (f: File | null) => void; disabled?: boolean; testid?: string; ariaLabel?: string }) {
  const ref = React.useRef<HTMLInputElement>(null);
  return (
    <div className="flex items-center gap-2 min-w-0">
      <input ref={ref} type="file" accept={accept} className="hidden" aria-label={ariaLabel ?? label} data-testid={testid} disabled={disabled} onChange={(e) => { onFile(e.target.files?.[0] ?? null); e.target.value = ''; }} />
      <button type="button" disabled={disabled} onClick={() => ref.current?.click()} className="h-9 px-3 rounded-lg border border-border bg-card text-sm font-medium hover:bg-secondary disabled:opacity-60 cursor-pointer shrink-0">{label}</button>
      <span className="text-xs text-muted-foreground truncate" data-testid={testid ? `${testid}-name` : undefined}>{file ? file.name : 'No file chosen'}</span>
    </div>
  );
}

export function PageHeader({ project, title, subtitle, actions }: { project?: string; title: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        {project && <div className="text-[11px] font-mono font-bold text-primary">{project}</div>}
        <h1 className="text-2xl font-extrabold tracking-tight">{title}</h1>
        {subtitle && <p className="text-xs font-semibold text-muted-foreground mt-0.5 max-w-3xl">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2 flex-wrap">{actions}</div>}
    </div>
  );
}

export function Panel({ icon: Icon, title, description, right, children, className }: { icon?: React.ElementType; title: React.ReactNode; description?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <Card className={cn(CARD, className)}>
      <CardHeader className="p-5 pb-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="text-base font-extrabold flex items-center gap-2">{Icon && <Icon className="w-5 h-5 text-[#FF7A18] shrink-0" />}{title}</CardTitle>
            {description && <CardDescription className="text-xs font-semibold mt-1">{description}</CardDescription>}
          </div>
          {right}
        </div>
      </CardHeader>
      <CardContent className="p-5 pt-0 space-y-3">{children}</CardContent>
    </Card>
  );
}

export function Label({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <label className="block text-[11px] font-bold text-[#071A2D] dark:text-[#F5F7FA] mb-1">
      {children}{hint && <span className="ml-1 font-normal text-muted-foreground">{hint}</span>}
    </label>
  );
}

export function Stat({ label, value, sub, tone, testid }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: 'warn' | 'bad' | 'good'; testid?: string }) {
  return (
    <div className={cn('rounded-xl border p-3.5', CARD.replace('shadow-xl', 'shadow-sm'))} data-testid={testid}>
      <div className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={cn('text-2xl font-extrabold mt-0.5', tone === 'bad' && 'text-rose-600', tone === 'warn' && 'text-amber-600', tone === 'good' && 'text-emerald-600')}>{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground mt-0.5 leading-snug">{sub}</div>}
    </div>
  );
}

/** actual progress filled, with a tick where the (approximate, linear) plan says it should be */
export function ProgressBar({ actual, planned, label }: { actual: number; planned?: number | null; label?: string }) {
  const a = Math.min(100, Math.max(0, Number(actual) || 0));
  const p = planned === null || planned === undefined ? null : Math.min(100, Math.max(0, Number(planned) || 0));
  return (
    <div className="relative h-2.5 rounded-full bg-slate-200 dark:bg-[#0E2B47] overflow-hidden" role="img" aria-label={label ?? `${a}% complete${p !== null ? `, approximate plan ${p}%` : ''}`}>
      <div className={cn('h-full rounded-full transition-all', a >= 100 ? 'bg-emerald-500' : 'bg-[#FF7A18]')} style={{ width: `${a}%` }} />
      {p !== null && p > 0 && p < 100 && <div className="absolute top-0 h-full w-0.5 bg-[#071A2D] dark:bg-white/80" style={{ left: `${p}%` }} title={`Approximate plan: ${pct(p)}`} />}
    </div>
  );
}

const LIFECYCLE: Record<Lifecycle, { label: string; tone: string; icon: React.ElementType }> = {
  COMPLETED: { label: 'Completed', tone: 'text-emerald-700 border-emerald-400 bg-emerald-50 dark:text-emerald-300 dark:border-emerald-800 dark:bg-emerald-950/30', icon: CheckCircle2 },
  ONGOING: { label: 'Ongoing', tone: 'text-blue-700 border-blue-400 bg-blue-50 dark:text-blue-300 dark:border-blue-800 dark:bg-blue-950/30', icon: PlayCircle },
  UPCOMING: { label: 'Upcoming', tone: 'text-slate-700 border-slate-400 bg-slate-50 dark:text-slate-300 dark:border-slate-700 dark:bg-slate-900/30', icon: Hourglass },
};
export function LifecyclePill({ value }: { value: Lifecycle }) {
  const c = LIFECYCLE[value] ?? LIFECYCLE.UPCOMING;
  const Icon = c.icon;
  return <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[10px] font-bold', c.tone)}><Icon className="w-3 h-3" />{c.label}</span>;
}

export const SEVERITY_TONE: Record<Severity, string> = {
  LOW: 'bg-slate-100 text-slate-700 border-slate-300 dark:bg-[#0B2742] dark:text-slate-300 dark:border-[#214766]',
  MEDIUM: 'bg-amber-50 text-amber-800 border-amber-300 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800',
  HIGH: 'bg-orange-50 text-orange-800 border-orange-300 dark:bg-orange-950/40 dark:text-orange-300 dark:border-orange-800',
  CRITICAL: 'bg-rose-50 text-rose-800 border-rose-300 dark:bg-rose-950/40 dark:text-rose-300 dark:border-rose-800',
};

const CLAIM_TONE: Record<ClaimStatus, string> = {
  REPORTED: 'border-slate-300 text-slate-700 bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:bg-slate-900/30',
  EXTRACTED: 'border-slate-300 text-slate-700 bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:bg-slate-900/30',
  MATCHED: 'border-blue-300 text-blue-700 bg-blue-50 dark:border-blue-800 dark:text-blue-300 dark:bg-blue-950/40',
  VALIDATED: 'border-blue-300 text-blue-700 bg-blue-50 dark:border-blue-800 dark:text-blue-300 dark:bg-blue-950/40',
  DISPUTED: 'border-amber-300 text-amber-800 bg-amber-50 dark:border-amber-800 dark:text-amber-300 dark:bg-amber-950/40',
  APPROVED: 'border-emerald-300 text-emerald-700 bg-emerald-50 dark:border-emerald-800 dark:text-emerald-300 dark:bg-emerald-950/40',
  REJECTED: 'border-rose-300 text-rose-700 bg-rose-50 dark:border-rose-900 dark:text-rose-300 dark:bg-rose-950/40',
  WITHDRAWN: 'border-slate-300 text-slate-500 bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:bg-slate-900/30',
};
const CLAIM_LABEL: Record<ClaimStatus, string> = {
  REPORTED: 'Pending review', EXTRACTED: 'Needs matching', MATCHED: 'Pending review', VALIDATED: 'Pending review', DISPUTED: 'Clarification requested', APPROVED: 'Approved', REJECTED: 'Rejected', WITHDRAWN: 'Withdrawn',
};
export function ClaimStatusPill({ status, clarification }: { status: ClaimStatus; clarification?: string | null }) {
  const label = status === 'DISPUTED' && clarification === 'ANSWERED' ? 'Answered · awaiting review' : CLAIM_LABEL[status] ?? status;
  return <span className={cn('inline-flex px-2 py-0.5 rounded-md border text-[10px] font-bold whitespace-nowrap', CLAIM_TONE[status] ?? CLAIM_TONE.REPORTED)} data-status={status}>{label}</span>;
}

export const isPending = (s: ClaimStatus) => !['APPROVED', 'REJECTED', 'WITHDRAWN'].includes(s);

export function Loading({ what = 'Loading…' }: { what?: string }) {
  return <div className="text-xs text-muted-foreground flex items-center gap-2 py-6" role="status"><Loader2 className="w-4 h-4 animate-spin" />{what}</div>;
}

export function QueryError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof V2Error ? error : null;
  return <ErrorState message={e ? `${e.message}` : errText(error, 'Could not load this information')} onRetry={onRetry} />;
}

export function Notice({ tone, children, testid }: { tone: 'ok' | 'bad' | 'info' | 'warn'; children: React.ReactNode; testid?: string }) {
  const t = {
    ok: 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300',
    bad: 'border-rose-300 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300',
    warn: 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300',
    info: 'border-blue-300 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950/30 dark:text-blue-300',
  }[tone];
  return <div role={tone === 'bad' ? 'alert' : 'status'} data-testid={testid} className={cn('text-xs p-2.5 rounded-lg border flex items-start gap-2', t)}>{tone === 'warn' && <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />}<div className="min-w-0">{children}</div></div>;
}

/** pill filter row used by the legacy Issues page */
export function Pills<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[] }) {
  return (
    <div className="flex items-center gap-1.5 bg-card p-1 rounded-xl border border-border w-fit flex-wrap">
      {options.map((o) => (
        <button key={o.value} type="button" onClick={() => onChange(o.value)} className={cn('px-3 py-1.5 text-xs font-bold rounded-lg cursor-pointer', value === o.value ? 'bg-[#FF7A18] text-white' : 'text-muted-foreground hover:text-foreground')}>{o.label}</button>
      ))}
    </div>
  );
}

/** the approximate-plan disclosure shown wherever planned progress or SPI appears (text comes from the API; this is the fallback) */
export function ApproxNote({ text }: { text?: string }) {
  return <p className="text-[11px] text-muted-foreground leading-snug" data-testid="approx-note">{text ?? 'Planned progress is an approximation (linear between baseline dates). SPI is a schedule indicator only, not earned value.'}</p>;
}
