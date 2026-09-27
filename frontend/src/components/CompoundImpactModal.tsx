import React from 'react';
import { useTranslation } from 'react-i18next';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import {
  AlertTriangle,
  GitFork,
  Layers,
  Info,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { CompoundImpact, DownstreamImpactedActivity } from '@/lib/impactEngine';

interface CompoundImpactModalProps {
  isOpen: boolean;
  onClose: () => void;
  impact: CompoundImpact | null;
}

export function CompoundImpactModal({
  isOpen,
  onClose,
  impact,
}: CompoundImpactModalProps) {
  const { t } = useTranslation();

  if (!impact) return null;

  const getImpactBadgeColor = (level: string) => {
    switch (level) {
      case 'CRITICAL':
        return 'bg-red-500/10 text-red-700 dark:text-red-400 border-red-300 dark:border-red-800';
      case 'HIGH':
        return 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-300 dark:border-amber-800';
      case 'MEDIUM':
        return 'bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-300 dark:border-blue-800';
      case 'LOW':
      default:
        return 'bg-slate-500/10 text-slate-700 dark:text-slate-400 border-slate-300 dark:border-slate-800';
    }
  };

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
                  <span
                    className={cn(
                      'text-xs px-2.5 py-0.5 rounded-full font-mono font-bold border uppercase',
                      getImpactBadgeColor(impact.impactLevel)
                    )}
                  >
                    {impact.impactLevel} IMPACT
                  </span>
                </DialogTitle>
                <DialogDescription className="text-xs text-muted-foreground mt-0.5">
                  Source Activity: <span className="font-mono font-bold text-foreground">{impact.activityId}</span> — {impact.activityName}
                </DialogDescription>
              </div>
            </div>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {/* Metadata Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="p-3 rounded-lg border border-border bg-card/40 space-y-1">
              <span className="text-[10px] text-muted-foreground font-semibold uppercase tracking-wider">Discipline</span>
              <div className="text-xs font-bold text-foreground">{impact.discipline || 'UNASSIGNED'}</div>
            </div>
            <div className="p-3 rounded-lg border border-border bg-card/40 space-y-1">
              <span className="text-[10px] text-muted-foreground font-semibold uppercase tracking-wider">WBS Code</span>
              <div className="text-xs font-mono font-bold text-foreground">{impact.wbsCode || 'N/A'}</div>
            </div>
            <div className="p-3 rounded-lg border border-border bg-card/40 space-y-1">
              <span className="text-[10px] text-muted-foreground font-semibold uppercase tracking-wider">Direct Successors</span>
              <div className="text-xs font-mono font-bold text-primary">{impact.directSuccessorCount} Direct</div>
            </div>
            <div className="p-3 rounded-lg border border-border bg-card/40 space-y-1">
              <span className="text-[10px] text-muted-foreground font-semibold uppercase tracking-wider">Total Downstream</span>
              <div className="text-xs font-mono font-bold text-primary">{impact.totalDownstreamCount} Activities</div>
            </div>
          </div>

          {/* Root Triggers & Contributing Factors */}
          <div className="space-y-2">
            <h4 className="text-xs font-bold uppercase tracking-wider text-foreground flex items-center gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
              <span>Delay Risk Triggers & Constraints</span>
            </h4>
            <div className="space-y-1.5">
              {impact.riskFactors.map((reason, idx) => (
                <div
                  key={idx}
                  className="p-2.5 rounded-lg border border-amber-300/40 bg-amber-500/5 text-amber-900 dark:text-amber-200 text-xs flex items-start gap-2"
                >
                  <span className="text-amber-500 font-bold">•</span>
                  <span>{reason}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Downstream Stage Distribution */}
          {impact.impactedStageIds.length > 0 && (
            <div className="p-3.5 rounded-lg border border-border bg-card/40 space-y-2">
              <h4 className="text-xs font-bold uppercase tracking-wider text-foreground flex items-center gap-1.5">
                <Layers className="w-3.5 h-3.5 text-blue-500" />
                <span>Stages Affected ({impact.impactedStageIds.length} Stages)</span>
              </h4>
              <div className="flex flex-wrap gap-2">
                {impact.impactedStageIds.map((stg) => {
                  const count = impact.downstreamActivities.filter((d) => d.stageId === stg).length;
                  return (
                    <div
                      key={stg}
                      className="px-2.5 py-1 rounded-md border border-border bg-background text-xs font-medium flex items-center gap-1.5"
                    >
                      <span className="font-mono text-primary font-bold">{stg}</span>
                      <span className="text-muted-foreground text-[10px]">({count} {count === 1 ? 'activity' : 'activities'})</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Successor Activity Network */}
          <div className="space-y-2.5">
            <h4 className="text-xs font-bold uppercase tracking-wider text-foreground flex items-center gap-1.5">
              <GitFork className="w-3.5 h-3.5 text-primary" />
              <span>Downstream Dependent Activities ({impact.downstreamActivities.length})</span>
            </h4>

            {impact.downstreamActivities.length === 0 ? (
              <div className="p-4 rounded-lg border border-dashed border-border text-center text-xs text-muted-foreground">
                No downstream successors found for this activity in current schedule baseline.
              </div>
            ) : (
              <div className="border border-border rounded-lg divide-y divide-border overflow-hidden bg-card">
                {impact.downstreamActivities.map((act: DownstreamImpactedActivity) => (
                  <div key={act.activityId} className="p-3 hover:bg-secondary/30 transition-colors space-y-1">
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold text-xs text-primary px-2 py-0.5 rounded bg-primary/10 border border-primary/20">
                          {act.activityId}
                        </span>
                        <span className="text-xs font-bold text-foreground">{act.activityName}</span>
                      </div>
                      <div className="flex items-center gap-2 text-[10px]">
                        <span className="px-2 py-0.5 rounded bg-secondary text-secondary-foreground font-mono font-bold">
                          {act.relationshipType || 'FS'}{act.lagDays ? ` +${act.lagDays}d` : ''}
                        </span>
                        <span className="px-2 py-0.5 rounded border border-border text-muted-foreground font-semibold">
                          Hop #{act.depth}
                        </span>
                        {act.stageId && (
                          <span className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-700 dark:text-blue-300 font-semibold">
                            {act.stageId}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground pt-0.5">
                      <div className="flex items-center gap-3">
                        <span>Discipline: <span className="text-foreground font-medium">{act.discipline || 'N/A'}</span></span>
                        <span>WBS: <span className="font-mono text-foreground font-medium">{act.wbsCode || 'N/A'}</span></span>
                      </div>
                      {act.plannedStart && (
                        <div className="text-[10px] font-mono">
                          Planned: {act.plannedStart} → {act.plannedFinish}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Decision Support Advisory Note */}
          <div className="p-3 rounded-lg border border-blue-200 dark:border-blue-900/60 bg-blue-50 dark:bg-blue-950/30 text-blue-900 dark:text-blue-200 text-xs flex items-start gap-2">
            <Info className="w-4 h-4 text-blue-500 shrink-0 mt-0.5" />
            <div className="space-y-0.5">
              <span className="font-bold">Deterministic Decision Support:</span>
              <p className="text-[11px] leading-relaxed opacity-90">
                Downstream impact calculations are deterministically derived from Primavera P6 schedule logic. They serve as advisory risk intelligence and do NOT automatically block submissions or overwrite schedule actuals without Supervisor discretion.
              </p>
            </div>
          </div>
        </div>

        <DialogFooter className="p-3.5 border-t border-border bg-card/60 shrink-0 flex items-center justify-between">
          <div className="text-[11px] text-muted-foreground">
            Project: <span className="font-semibold text-foreground">{impact.projectId}</span> | Schedule: <span className="font-semibold text-foreground">{impact.scheduleId}</span>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={onClose} className="cursor-pointer">
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
