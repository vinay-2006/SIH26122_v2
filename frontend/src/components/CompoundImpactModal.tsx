import React, { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { AlertTriangle, GitFork, Info, Layers, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { impactApi, type ActivityImpactItem, type CompoundImpact } from '@/api';
import { useProjectState } from '@/context/ProjectContext';

interface CompoundImpactModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** A watch-list row (its activity is previewed) ... */
  impact?: CompoundImpact | null;
  /** ... or an explicit activity. */
  target?: { activityId: string; activityName: string } | null;
}

const BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-500/10 text-red-700 dark:text-red-400 border-red-300 dark:border-red-800',
  HIGH: 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-300 dark:border-amber-800',
  MEDIUM: 'bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-300 dark:border-blue-800',
  LOW: 'bg-slate-500/10 text-slate-700 dark:text-slate-400 border-slate-300 dark:border-slate-800',
  NONE: 'bg-slate-500/10 text-slate-700 dark:text-slate-400 border-slate-300 dark:border-slate-800',
};

/**
 * What-if downstream impact of delaying ONE activity, computed by the backend impact engine over the stored
 * network of the selected schedule version (float absorption, controlling predecessor, causal path). Read-only:
 * nothing is written and no schedule date changes.
 */
export function CompoundImpactModal({ isOpen, onClose, impact, target }: CompoundImpactModalProps) {
  const { currentProject, currentScheduleVersion } = useProjectState();
  const activityId = impact?.activityId ?? target?.activityId ?? null;
  const activityName = impact?.activityName ?? target?.activityName ?? '';
  const [days, setDays] = useState<number>(impact?.sensitivityDays ?? 3);

  useEffect(() => {
    if (isOpen) setDays(impact?.sensitivityDays ?? 3);
  }, [isOpen, activityId, impact?.sensitivityDays]);

  const projectId = currentProject?.id;
  const scheduleId = currentScheduleVersion?.id;
  const query = useQuery({
    queryKey: ['v7', 'impact-preview', projectId, scheduleId, activityId, days],
    queryFn: () => impactApi.preview(projectId!, scheduleId!, activityId!, days),
    enabled: isOpen && !!projectId && !!scheduleId && !!activityId && Number.isFinite(days) && days >= 0,
    staleTime: 15_000,
  });
  const res = query.data;

  if (!activityId) return null;

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-3xl max-h-[90vh] flex flex-col p-0 overflow-hidden bg-background text-foreground border-border shadow-2xl">
        <DialogHeader className="p-5 border-b border-border bg-card/60 shrink-0">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-lg bg-primary/10 border border-primary/20 text-primary">
                <GitFork className="w-5 h-5" />
              </div>
              <div>
                <DialogTitle className="text-base font-bold flex items-center gap-2">
                  <span>Downstream Schedule Impact</span>
                  {res && (
                    <span className={cn('text-xs px-2.5 py-0.5 rounded-full font-mono font-bold border uppercase', BADGE[res.severity] ?? BADGE.LOW)}>
                      {res.severity} IMPACT
                    </span>
                  )}
                </DialogTitle>
                <DialogDescription className="text-xs text-muted-foreground mt-0.5">
                  Source activity: <span className="font-mono font-bold text-foreground">{activityId}</span> — {activityName}
                </DialogDescription>
              </div>
            </div>
            <label className="text-[11px] font-semibold text-muted-foreground flex items-center gap-2 shrink-0">
              What if it slips
              <input
                type="number"
                min={0}
                max={365}
                value={days}
                onChange={(e) => setDays(Math.max(0, Math.min(365, Math.floor(Number(e.target.value) || 0))))}
                className="w-16 h-8 rounded-md border border-border bg-background px-2 text-xs font-mono text-foreground"
                aria-label="Delay in days"
              />
              days
            </label>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {query.isLoading && (
            <div className="flex items-center justify-center gap-2 py-10 text-xs text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" /> Computing downstream impact…
            </div>
          )}
          {query.isError && (
            <div className="p-3 rounded-lg border border-red-300 bg-red-50 dark:bg-red-950/30 text-xs text-red-700 dark:text-red-300">
              Impact preview failed: {query.error instanceof Error ? query.error.message : 'unknown error'}
            </div>
          )}

          {res && (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <Stat label="Activities affected" value={String(res.affected_activities.length)} />
                <Stat label="Stages affected" value={String(res.affected_stages.length)} />
                <Stat label="Critical-path delays" value={String(res.float_analysis.critical_path_delays_count)} />
                <Stat label="Project completion" value={res.project_completion_impact_days > 0 ? `+${res.project_completion_impact_days} d` : 'No change'} />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <Stat label="Float absorbed" value={`${res.float_analysis.total_float_absorbed_days} d`} />
                <Stat label="Float unknown" value={`${res.float_analysis.activities_with_unknown_float} activities`} />
              </div>

              {res.affected_stages.length > 0 && (
                <div className="p-3.5 rounded-lg border border-border bg-card/40 space-y-2">
                  <h4 className="text-xs font-bold uppercase tracking-wider flex items-center gap-1.5">
                    <Layers className="w-3.5 h-3.5 text-blue-500" /> Stages affected
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {res.affected_stages.map((s) => (
                      <div key={s.stage_id} className="px-2.5 py-1 rounded-md border border-border bg-background text-xs font-medium flex items-center gap-1.5">
                        <span className="font-bold text-primary">{s.stage_name}</span>
                        <span className="text-muted-foreground text-[10px]">
                          {s.affected_activities_count} act · up to {s.max_stage_delay_days} d · {s.stage_progression_impact}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="space-y-2.5">
                <h4 className="text-xs font-bold uppercase tracking-wider flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-500" /> Downstream activities ({res.affected_activities.length})
                </h4>
                {res.affected_activities.length === 0 ? (
                  <div className="p-4 rounded-lg border border-dashed border-border text-center text-xs text-muted-foreground">
                    A {days}-day slip to this activity does not move any successor (absorbed by float or no successors).
                  </div>
                ) : (
                  <div className="border border-border rounded-lg divide-y divide-border overflow-hidden bg-card">
                    {res.affected_activities.map((a) => (
                      <ImpactRow key={a.activity_id} a={a} />
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          <div className="p-3 rounded-lg border border-blue-200 dark:border-blue-900/60 bg-blue-50 dark:bg-blue-950/30 text-blue-900 dark:text-blue-200 text-xs flex items-start gap-2">
            <Info className="w-4 h-4 text-blue-500 shrink-0 mt-0.5" />
            <p className="text-[11px] leading-relaxed opacity-90">
              <span className="font-bold">Deterministic decision support.</span> Propagation follows the stored FS/SS/FF/SF logic and lags of this schedule
              version, absorbing float where known. It is advisory: it never approves, rejects or changes an actual or a schedule date.
            </p>
          </div>
        </div>

        <DialogFooter className="p-3.5 border-t border-border bg-card/60 shrink-0 flex items-center justify-between">
          <div className="text-[11px] text-muted-foreground">
            Schedule: <span className="font-semibold text-foreground">{currentScheduleVersion?.versionNumber ?? scheduleId}</span>
            {res && <span> · engine {res.algorithm_version}</span>}
          </div>
          <Button type="button" variant="outline" size="sm" onClick={onClose} className="cursor-pointer">
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-3 rounded-lg border border-border bg-card/40 space-y-1">
      <span className="text-[10px] text-muted-foreground font-semibold uppercase tracking-wider">{label}</span>
      <div className="text-xs font-mono font-bold text-foreground">{value}</div>
    </div>
  );
}

function ImpactRow({ a }: { a: ActivityImpactItem }) {
  return (
    <div className="p-3 hover:bg-secondary/30 transition-colors space-y-1">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="font-mono font-bold text-xs text-primary px-2 py-0.5 rounded bg-primary/10 border border-primary/20">{a.activity_id}</span>
          <span className="text-xs font-bold text-foreground">{a.activity_name}</span>
          {a.is_critical && <span className="text-[10px] font-bold px-1.5 rounded bg-red-500/10 text-red-600 border border-red-300">CRITICAL</span>}
        </div>
        <div className="flex items-center gap-2 text-[10px]">
          <span className="px-2 py-0.5 rounded bg-secondary text-secondary-foreground font-mono font-bold">
            {a.controlling_relationship || 'FS'}{a.lag_days ? ` ${a.lag_days > 0 ? '+' : ''}${a.lag_days}d` : ''}
          </span>
          <span className="px-2 py-0.5 rounded border border-border text-muted-foreground font-semibold">Hop #{a.propagation_depth}</span>
          <span className={cn('px-2 py-0.5 rounded font-bold', a.residual_delay_days ? 'bg-amber-500/10 text-amber-700' : 'bg-emerald-500/10 text-emerald-700')}>
            {a.residual_delay_days ? `+${a.residual_delay_days} d delay` : 'absorbed'}
          </span>
        </div>
      </div>
      <p className="text-[11px] text-muted-foreground">{a.explanation}</p>
      <div className="flex items-center justify-between text-[10px] text-muted-foreground font-mono">
        <span>{a.stage_name ?? 'Unassigned'} · {a.canonical_state}{a.workflow_condition !== 'NONE' ? ` · ${a.workflow_condition}` : ''}</span>
        {a.shifted_start && a.baseline_start && (
          <span>Start {a.baseline_start} → {a.shifted_start}</span>
        )}
      </div>
    </div>
  );
}
