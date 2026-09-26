import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  GitBranch,
  Info,
  AlertTriangle,
  TrendingUp,
  Loader2,
  Network,
  Table as TableIcon,
  Calendar,
} from 'lucide-react';

import {
  schedulesApi,
  getActiveScheduleId,
  ImpactPreviewResult,
  ScheduleActivity,
} from '@/api';

import { useProject } from '@/context/ProjectContext';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import { cn } from '@/lib/utils';

import { ImpactNetworkGraph } from '@/components/impact/ImpactNetworkGraph';
import { ImpactTimelineView } from '@/components/impact/ImpactTimelineView';
import { ImpactTable } from '@/components/impact/ImpactTable';

type ViewMode = 'network' | 'timeline' | 'table';

export default function ImpactPreview() {
  const { t } = useTranslation();
  const { currentProject, currentScheduleVersion } = useProject();

  const [activityId, setActivityId] = useState('ACT-101');
  const [scheduleId, setScheduleId] = useState(currentScheduleVersion.id);
  const [delayDays, setDelayDays] = useState(5);
  const [projectActivities, setProjectActivities] = useState<ScheduleActivity[]>([]);

  const [isSimulating, setIsSimulating] = useState(false);
  const [simulationResult, setSimulationResult] =
    useState<ImpactPreviewResult | null>(null);

  const [error, setError] = useState<string | null>(null);

  const [activeView, setActiveView] =
    useState<ViewMode>('network');

  const [selectedActivityId, setSelectedActivityId] =
    useState<string | null>(null);

  const runSimulation = async (
    targetAct = activityId,
    targetDelay = delayDays,
    targetSched = scheduleId
  ) => {
    if (!targetAct.trim()) return;

    setIsSimulating(true);
    setError(null);
    setSelectedActivityId(null);

    try {
      const res = await schedulesApi.getImpactPreview(
        targetAct.trim(),
        targetDelay,
        targetSched || currentScheduleVersion.id
      );

      setSimulationResult(res);
    } catch (e: any) {
      setError(
        t('impact.failedToCompute', {
          message: e?.message || 'Simulation failed',
        })
      );
    } finally {
      setIsSimulating(false);
    }
  };

  const handleSimulate = () => {
    runSimulation(activityId, delayDays, scheduleId);
  };

  useEffect(() => {
    let cancelled = false;
    schedulesApi.getActivities(currentScheduleVersion.id)
      .then((acts) => {
        if (cancelled) return;
        setProjectActivities(acts);
        const firstAct = acts[0]?.activity_id || 'ACT-101';
        setActivityId(firstAct);
        setScheduleId(currentScheduleVersion.id);
        runSimulation(firstAct, 5, currentScheduleVersion.id);
      })
      .catch(() => {
        if (!cancelled) runSimulation('ACT-101', 5, currentScheduleVersion.id);
      });
    return () => {
      cancelled = true;
    };
  }, [currentProject.id, currentScheduleVersion.id]);

  const kpiStats = React.useMemo(() => {
    if (!simulationResult) return null;

    const impacts = simulationResult.impacts || [];

    const totalImpacted = impacts.length;

    const maxDepth = impacts.reduce(
      (max, imp) =>
        Math.max(max, imp.propagation_depth || 1),
      1
    );

    const criticalSlips = impacts.filter(
      (imp) =>
        imp.net_delay_days &&
        imp.net_delay_days > 0
    );

    const maxNetSlip = impacts.reduce(
      (max, imp) =>
        Math.max(max, imp.net_delay_days || 0),
      0
    );

    const absorbedCount = impacts.filter(
      (imp) =>
        imp.gross_delay_days > 0 &&
        (!imp.net_delay_days ||
          imp.net_delay_days === 0)
    ).length;

    return {
      totalImpacted,
      maxDepth,
      criticalSlipsCount: criticalSlips.length,
      maxNetSlip,
      absorbedCount,
    };
  }, [simulationResult]);

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4 border-b border-slate-300 dark:border-[#214766]/60 pb-4">
        <div>
          <h1 className="text-2xl font-extrabold text-[#071A2D] dark:text-[#F5F7FA] tracking-tight flex items-center gap-2.5">
            <div className="p-1.5 rounded-xl bg-teal-50 dark:bg-[#0A2340] border border-teal-300 dark:border-[#1E3A5F] shadow-xs">
              <GitBranch className="w-5 h-5 text-[#14B8A6] dark:text-[#22D3EE]" />
            </div>

            {t('impact.title')}
          </h1>

          <p className="text-[#334155] dark:text-[#CBD5E1] text-xs font-semibold mt-1">
            {t('impact.subtitle')}
          </p>
        </div>
      </div>

      {/* Scope Disclaimer */}
      <div className="p-3.5 rounded-xl bg-sky-50 dark:bg-sky-950/40 border border-sky-200 dark:border-sky-800/60 text-sky-900 dark:text-sky-300 text-xs flex items-center gap-2.5 shadow-xs">
        <Info className="w-4 h-4 text-sky-600 dark:text-sky-400 shrink-0" />

        <span>
          <strong>A1 Constraint Engine:</strong>{' '}
          Evaluates FS, SS, FF, SF relationships, lag/lead,
          multiple predecessor constraints, float absorption,
          and bounded multi-hop propagation.
        </span>
      </div>

      {/* Simulation Input Strip */}
      <Card className="bg-white dark:bg-[#001E60]/80 border-slate-200 dark:border-blue-900/50 shadow-sm">
        <CardContent className="pt-5 pb-5">

          <div className="grid grid-cols-1 sm:grid-cols-12 gap-4 items-end">

            {/* Activity ID */}
            <div className="sm:col-span-6 space-y-1.5">
              <Label className="text-xs text-slate-700 dark:text-slate-300 font-semibold">
                {t('impact.activityIdLabel')}
              </Label>

              <Input
                value={activityId}
                onChange={(e) =>
                  setActivityId(e.target.value)
                }
                placeholder="e.g. CIV-PS3-FND-001, CIV-PS3-FND-002"
                className="bg-slate-50 dark:bg-[#001438] border-slate-300 dark:border-blue-800 font-mono text-slate-900 dark:text-slate-200 focus:border-violet-500 text-xs h-9"
              />

              {/* Quick Select */}
              <div className="flex flex-wrap items-center gap-1.5 pt-1">

                <span className="text-[10px] text-slate-400 dark:text-slate-500 font-medium">
                  Quick Select:
                </span>

                {projectActivities.slice(0, 4).map((act) => (
                  <button
                    key={act.activity_id}
                    type="button"
                    onClick={() => {
                      setActivityId(act.activity_id);
                      setDelayDays(5);
                      runSimulation(
                        act.activity_id,
                        5,
                        scheduleId
                      );
                    }}
                    className={cn(
                      'px-2 py-0.5 rounded text-[10px] font-mono border transition-all cursor-pointer',
                      activityId === act.activity_id
                        ? 'bg-[#FF7A18] text-white border-[#FF7A18] font-semibold shadow-xs'
                        : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 border-slate-200 dark:border-slate-700 hover:border-[#FF7A18]/60'
                    )}
                  >
                    {act.activity_id}
                  </button>
                ))}
              </div>
            </div>

            {/* Delay */}
            <div className="sm:col-span-3 space-y-1.5">

              <Label className="text-xs text-slate-700 dark:text-slate-300 font-semibold">
                {t('impact.delayLabel')} (Days)
              </Label>

              <Input
                type="number"
                min={0}
                max={180}
                value={delayDays}
                onChange={(e) =>
                  setDelayDays(Number(e.target.value))
                }
                className="bg-slate-50 dark:bg-[#001438] border-slate-300 dark:border-blue-800 font-mono text-slate-900 dark:text-slate-200 focus:border-violet-500 text-xs h-9"
              />
            </div>

            {/* Simulate Button */}
            <div className="sm:col-span-3">

              <Button
                onClick={handleSimulate}
                disabled={
                  isSimulating ||
                  !activityId.trim()
                }
                className="w-full bg-violet-600 hover:bg-violet-500 text-white font-medium shadow-md shadow-violet-600/20 h-9 gap-2 text-xs"
              >
                {isSimulating ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Computing A1 Impact...
                  </>
                ) : (
                  <>
                    <TrendingUp className="w-3.5 h-3.5" />
                    Simulate Schedule Impact
                  </>
                )}
              </Button>

            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="mt-4 p-3 bg-rose-50 dark:bg-rose-500/10 border border-rose-200 dark:border-rose-500/30 text-rose-700 dark:text-rose-300 rounded-xl flex items-start gap-2 text-xs">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              {error}
            </div>
          )}

        </CardContent>
      </Card>

      {/* Results */}
      {!simulationResult ? (

        <Card className="bg-white dark:bg-[#001E60]/80 border-slate-200 dark:border-blue-900/50 shadow-sm min-h-[320px] flex items-center justify-center text-center p-8">

          <div className="space-y-3 max-w-md">

            <div className="w-14 h-14 rounded-2xl bg-violet-100 dark:bg-violet-500/10 border border-violet-200 dark:border-violet-500/20 flex items-center justify-center mx-auto">
              <GitBranch className="w-7 h-7 text-violet-500 dark:text-violet-400" />
            </div>

            <h3 className="text-slate-800 dark:text-slate-200 font-bold text-sm">
              Ready for Multi-Hop Schedule Impact Simulation
            </h3>

            <p className="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">
              Enter an activity ID and hypothetical delay
              to inspect downstream propagation paths,
              controlling predecessors, float absorption,
              and net project completion impact.
            </p>

          </div>

        </Card>

      ) : (

        <div className="space-y-5 animate-in fade-in duration-200">

          {/* KPI Strip */}
          {kpiStats && (
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">

              {/* Total Impacted */}
              <div className="p-3.5 rounded-xl bg-white dark:bg-[#001E60] border border-slate-200 dark:border-blue-900/50 shadow-xs">

                <span className="text-[10px] uppercase font-bold text-slate-400 block">
                  Downstream Impacted
                </span>

                <span className="text-lg font-mono font-bold text-slate-900 dark:text-slate-100">
                  {kpiStats.totalImpacted}
                </span>

                <span className="text-[10px] text-slate-400 block mt-0.5">
                  activities reached
                </span>

              </div>

              {/* Max Propagation */}
              <div className="p-3.5 rounded-xl bg-white dark:bg-[#001E60] border border-slate-200 dark:border-blue-900/50 shadow-xs">

                <span className="text-[10px] uppercase font-bold text-slate-400 block">
                  Max Propagation
                </span>

                <span className="text-lg font-mono font-bold text-slate-900 dark:text-slate-100">
                  Hop {kpiStats.maxDepth}
                </span>

                <span className="text-[10px] text-slate-400 block mt-0.5">
                  causal depth
                </span>

              </div>

              {/* Critical Slips */}
              <div className="p-3.5 rounded-xl bg-white dark:bg-[#001E60] border border-slate-200 dark:border-blue-900/50 shadow-xs">

                <span className="text-[10px] uppercase font-bold text-slate-400 block">
                  Critical Slips
                </span>

                <span className="text-lg font-mono font-bold text-rose-600 dark:text-rose-400">
                  {kpiStats.criticalSlipsCount}
                </span>

                <span className="text-[10px] text-rose-600/80 dark:text-rose-400/80 block mt-0.5">
                  exhausted float
                </span>

              </div>

              {/* Maximum Net Slip */}
              <div className="p-3.5 rounded-xl bg-white dark:bg-[#001E60] border border-slate-200 dark:border-blue-900/50 shadow-xs">

                <span className="text-[10px] uppercase font-bold text-slate-400 block">
                  Max Downstream Net Slip
                </span>

                <span className="text-lg font-mono font-bold text-rose-600 dark:text-rose-400">
                  +{kpiStats.maxNetSlip}d
                </span>

                <span className="text-[10px] text-slate-400 block mt-0.5">
                  highest successor net delay
                </span>

              </div>

              {/* Float Absorption */}
              <div className="p-3.5 rounded-xl bg-white dark:bg-[#001E60] border border-slate-200 dark:border-blue-900/50 shadow-xs col-span-2 sm:col-span-1">

                <span className="text-[10px] uppercase font-bold text-slate-400 block">
                  Float Absorption
                </span>

                <span className="text-lg font-mono font-bold text-emerald-600 dark:text-emerald-400">
                  {kpiStats.absorbedCount}
                </span>

                <span className="text-[10px] text-emerald-600/80 dark:text-emerald-400/80 block mt-0.5">
                  absorbed delays
                </span>

              </div>

            </div>
          )}

          {/* View Mode Header */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-200 dark:border-blue-900/50 pb-3">

            <div className="flex items-center gap-1.5 p-1 bg-slate-100 dark:bg-[#001438] rounded-xl border border-slate-200 dark:border-blue-900/50">

              {/* Network */}
              <button
                type="button"
                onClick={() => setActiveView('network')}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer',
                  activeView === 'network'
                    ? 'bg-white dark:bg-[#001E60] text-violet-700 dark:text-violet-300 shadow-xs'
                    : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
                )}
              >
                <Network className="w-3.5 h-3.5" />
                <span>Network View</span>
              </button>

              {/* Timeline */}
              <button
                type="button"
                onClick={() => setActiveView('timeline')}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer',
                  activeView === 'timeline'
                    ? 'bg-white dark:bg-[#001E60] text-violet-700 dark:text-violet-300 shadow-xs'
                    : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
                )}
              >
                <Calendar className="w-3.5 h-3.5" />
                <span>Timeline View</span>
              </button>

              {/* Table */}
              <button
                type="button"
                onClick={() => setActiveView('table')}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer',
                  activeView === 'table'
                    ? 'bg-white dark:bg-[#001E60] text-violet-700 dark:text-violet-300 shadow-xs'
                    : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
                )}
              >
                <TableIcon className="w-3.5 h-3.5" />
                <span>Impact Table</span>
              </button>

            </div>

            {/* Target */}
            <div className="flex items-center gap-2 text-xs font-mono text-slate-500">

              <span>Target:</span>

              <span className="font-bold text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-950/40 px-2 py-0.5 rounded border border-violet-200 dark:border-violet-900/50">
                {simulationResult.activity_id} (+{simulationResult.delay_days}d)
              </span>

            </div>

          </div>

          {/* Active View */}

          {activeView === 'network' && (
            <ImpactNetworkGraph
              data={simulationResult}
              selectedActivityId={selectedActivityId}
              onSelectActivity={setSelectedActivityId}
            />
          )}

          {activeView === 'timeline' && (
            <ImpactTimelineView
              data={simulationResult}
              onSelectActivity={setSelectedActivityId}
            />
          )}

          {activeView === 'table' && (
            <ImpactTable
              data={simulationResult}
              selectedActivityId={selectedActivityId}
              onSelectActivity={setSelectedActivityId}
            />
          )}

        </div>
      )}

    </div>
  );
}