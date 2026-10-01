/**
 * WBSActivityExplorer — Read-only WBS Activity Grouping Display
 *
 * Feature 30A (Stage 7). Displays the flat WBS grouping returned by
 * GET /api/v1/schedules/{schedule_id}/wbs-tree. Client-side joins
 * activity_id → activity_name via schedulesApi.getActivities().
 *
 * Strictly read-only: no editing, no splitting, no drag-drop.
 */
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';
import {
  FolderTree,
  ChevronDown,
  ChevronRight,
  Package,
  Hash,
  Loader2,
  AlertCircle,
  Search,
  Building2,
  Calendar,
  Layers,
  Lock,
  Sparkles,
  Check,
  Filter,
  Unlock,
  ShieldAlert,
  GitFork,
} from 'lucide-react';
import { useAuth } from '@/auth/AuthProvider';
import { useProject, useProjectState } from '@/context/ProjectContext';
import { Button } from '@/components/ui/button';
import {
  schedulesApi,
  reopenApi,
  qualityGatesApi,
  type WBSTreeResponse,
  type WBSGroup,
  type ScheduleActivity,
  type ExecutionState,
  type ReopenRequest,
  type QualityGate,
} from '@/api';
import type { ActivityProgressItem } from '@/api/projects';
import { ExecutionStateBadge } from '@/components/ExecutionStateBadge';
import { ReopenRequestModal } from '@/components/ReopenRequestModal';
import { ReopenReviewModal } from '@/components/ReopenReviewModal';
import { QualityGateModal } from '@/components/QualityGateModal';
import { CompoundImpactModal } from '@/components/CompoundImpactModal';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';

// ── Enriched activity with joined name, progress & execution state ───────────

interface EnrichedActivity {
  activity_id: string;
  activity_name: string;
  planned_quantity: number | null;
  discipline: string | null;
  location: string | null;
  execution_state?: ExecutionState;
  baseline_pct_complete?: number;
  /** Authoritative approved progress from the backend (never computed in the browser). */
  actual_progress: number;
  weight: number;
  stage_id: string | null;
  actual_start?: string | null;
  actual_finish?: string | null;
  contractor_name?: string | null;
  work_package_code?: string | null;
  fullActivity?: ScheduleActivity;
}

interface EnrichedGroup {
  wbs_code: string;
  activities: EnrichedActivity[];
  totalQuantity: number | null;
  total_weight: number;
  actual_progress: number;
}

// ── WBSGroupCard — collapsible group row ────────────────────────────────────

function WBSGroupCard({
  group,
  onRequestReopen,
  onReviewReopen,
  onOpenQualityModal,
  onInspectImpact,
  pendingReopenMap,
  qualityGatesMap,
  userRole,
}: {
  group: EnrichedGroup;
  onRequestReopen: (activity: ScheduleActivity) => void;
  onReviewReopen: (request: ReopenRequest) => void;
  onOpenQualityModal: (activity: ScheduleActivity) => void;
  onInspectImpact: (target: { activityId: string; activityName: string }) => void;
  pendingReopenMap: Map<string, ReopenRequest>;
  qualityGatesMap: Map<string, QualityGate[]>;
  userRole?: string;
}) {
  const { t } = useTranslation();
  const { can } = useProjectState();
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="border border-slate-200 dark:border-[#214766] rounded-xl overflow-hidden transition-all shadow-2xs hover:border-[#FF7A18]/50">
      {/* Group Header */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left bg-slate-50/90 dark:bg-[#0A2238] hover:bg-slate-100 dark:hover:bg-[#0D2942] transition-colors flex-wrap"
      >
        <div className="flex items-center gap-2">
          {isOpen ? (
            <ChevronDown className="w-4 h-4 text-[#475569] dark:text-[#CBD5E1] shrink-0" />
          ) : (
            <ChevronRight className="w-4 h-4 text-[#475569] dark:text-[#CBD5E1] shrink-0" />
          )}
          <FolderTree className="w-4 h-4 text-[#FF7A18] dark:text-[#FF941F] shrink-0" />
          <span className="font-mono text-sm font-bold text-[#071A2D] dark:text-[#F5F7FA]">
            {group.wbs_code}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <span className="font-mono text-xs px-2 py-0.5 rounded bg-blue-50 dark:bg-blue-950/60 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-900/60 font-semibold">
            {group.actual_progress}% Actual
          </span>
        </div>

        <span className="ml-auto flex items-center gap-3 text-xs text-[#475569] dark:text-[#CBD5E1] font-semibold">
          <span className="flex items-center gap-1">
            <Package className="w-3.5 h-3.5" />
            {t('wbs.activityCount', { count: group.activities.length })}
          </span>
          {group.totalQuantity !== null && (
            <span className="flex items-center gap-1 font-mono">
              <Hash className="w-3.5 h-3.5" />
              {t('wbs.totalQty', { qty: group.totalQuantity.toLocaleString() })}
            </span>
          )}
        </span>
      </button>

      {/* Expanded Activities */}
      {isOpen && (
        <div className="divide-y divide-slate-200/70 dark:divide-[#214766]/60 bg-white/70 dark:bg-[#071A2D]/80">
          {group.activities.map((a) => {
            const pendingReq = pendingReopenMap.get(a.activity_id);
            const isCompleted = a.execution_state === 'COMPLETED';
            const actGates = qualityGatesMap.get(a.activity_id) || [];
            const hasGates = actGates.length > 0;
            const completedGatesCount = actGates.filter((g) => g.status === 'COMPLETED' || g.status === 'WAIVED').length;
            const hasPendingHoldPoint = actGates.some(
              (g) => g.gateType === 'HOLD_POINT' && g.required && (g.status === 'PENDING' || g.status === 'BLOCKED')
            );
            const hasBlockedGate = actGates.some((g) => g.status === 'BLOCKED');

            return (
              <div
                key={a.activity_id}
                className="px-4 py-2.5 pl-8 sm:pl-12 flex items-center justify-between gap-3 text-sm hover:bg-slate-50 dark:hover:bg-[#0D2942] transition-colors flex-wrap"
              >
                <div className="flex items-center gap-3 min-w-0 flex-1 flex-wrap">
                  <span
                    className="font-mono text-xs font-bold text-[#0284C7] dark:text-[#38BDF8] w-24 shrink-0 truncate"
                    title={a.activity_id}
                  >
                    {a.activity_id}
                  </span>
                  <span className="truncate font-medium text-[#071A2D] dark:text-[#F5F7FA]">
                    {a.activity_name}
                  </span>
                  {a.discipline && (
                    <span className="text-[11px] px-2 py-0.5 rounded-full bg-orange-50 dark:bg-orange-950/60 text-[#FF7A18] dark:text-[#FF941F] border border-orange-300 dark:border-orange-800/60 font-semibold shrink-0">
                      {a.discipline}
                    </span>
                  )}
                  {a.contractor_name && (
                    <span
                      className="text-[10px] px-2 py-0.5 rounded bg-blue-50 dark:bg-blue-950/60 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-900/60 font-semibold shrink-0"
                      title={`Contractor: ${a.contractor_name}`}
                    >
                      {a.contractor_name}
                    </span>
                  )}
                  {a.work_package_code && (
                    <span
                      className="text-[10px] px-1.5 py-0.5 rounded font-mono font-bold bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-900/60 shrink-0"
                      title={`Work Package: ${a.work_package_code}`}
                    >
                      {a.work_package_code}
                    </span>
                  )}

                  {/* Quality Gate / Hold Point Status Badges */}
                  {hasGates && (
                    <button
                      type="button"
                      onClick={() => a.fullActivity && onOpenQualityModal(a.fullActivity)}
                      className={cn(
                        "text-[10px] px-2 py-0.5 rounded font-bold transition-colors flex items-center gap-1 cursor-pointer shrink-0 border",
                        hasPendingHoldPoint
                          ? "bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300 border-rose-300 dark:border-rose-800"
                          : hasBlockedGate
                          ? "bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300 border-rose-300 dark:border-rose-800"
                          : completedGatesCount === actGates.length
                          ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-400 border-emerald-300 dark:border-emerald-800"
                          : "bg-teal-50 text-teal-700 dark:bg-teal-950/60 dark:text-teal-300 border-teal-300 dark:border-teal-800"
                      )}
                      title={`Quality Gates & ITP: ${completedGatesCount}/${actGates.length} Cleared - Click to view/manage`}
                    >
                      {hasPendingHoldPoint ? (
                        <>
                          <Lock className="w-3 h-3 text-rose-500" />
                          <span>Hold Point: PENDING</span>
                        </>
                      ) : hasBlockedGate ? (
                        <>
                          <AlertCircle className="w-3 h-3 text-rose-500" />
                          <span>Quality: BLOCKED</span>
                        </>
                      ) : completedGatesCount === actGates.length ? (
                        <>
                          <Check className="w-3 h-3 text-emerald-500" />
                          <span>Quality: {completedGatesCount}/{actGates.length} Cleared</span>
                        </>
                      ) : (
                        <>
                          <Sparkles className="w-3 h-3 text-teal-500" />
                          <span>Quality: {completedGatesCount}/{actGates.length}</span>
                        </>
                      )}
                    </button>
                  )}

                  {/* Downstream impact (server-computed, what-if delay) */}
                  <button
                    type="button"
                    onClick={() => onInspectImpact({ activityId: a.activity_id, activityName: a.activity_name })}
                    className="text-[10px] px-2 py-0.5 rounded font-bold transition-colors flex items-center gap-1 cursor-pointer shrink-0 border bg-slate-50 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border-slate-300 dark:border-slate-700 hover:bg-slate-100"
                    title="Preview downstream schedule impact of a delay to this activity"
                  >
                    <GitFork className="w-3 h-3" />
                    <span>Impact</span>
                  </button>
                </div>

                <div className="flex items-center gap-3 shrink-0">
                  <div className="text-right font-mono text-xs">
                    <span className="font-bold text-foreground">{a.actual_progress}%</span>
                  </div>

                  <ExecutionStateBadge state={a.execution_state || 'NOT_STARTED'} size="sm" />

                  {a.planned_quantity !== null && (
                    <span className="text-xs text-[#475569] dark:text-[#CBD5E1] font-mono font-semibold">
                      {t('wbs.qty', { qty: a.planned_quantity })}
                    </span>
                  )}

                  {/* Reopen Workflow Triggers */}
                  {pendingReq ? (
                    can('APPROVE_REOPEN') ? (
                      <button
                        type="button"
                        onClick={() => onReviewReopen(pendingReq)}
                        className="text-[10px] font-bold px-2 py-1 rounded-md bg-purple-600 text-white hover:bg-purple-700 transition-colors flex items-center gap-1 shadow-2xs cursor-pointer"
                      >
                        <ShieldAlert className="w-3 h-3" />
                        Review Reopen
                      </button>
                    ) : (
                      <span className="text-[10px] font-semibold text-purple-700 dark:text-purple-300 bg-purple-50 dark:bg-purple-950/50 px-2 py-0.5 rounded border border-purple-200 dark:border-purple-800">
                        Reopen Pending
                      </span>
                    )
                  ) : isCompleted && can('REQUEST_REOPEN') ? (
                    <button
                      type="button"
                      onClick={() => a.fullActivity && onRequestReopen(a.fullActivity)}
                      className="text-[10px] font-semibold px-2 py-1 rounded-md border border-purple-300 dark:border-purple-700 text-purple-700 dark:text-purple-300 hover:bg-purple-50 dark:hover:bg-purple-950/40 transition-colors flex items-center gap-1 cursor-pointer"
                    >
                      <Unlock className="w-3 h-3" />
                      Reopen
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Main Explorer Component ─────────────────────────────────────────────────

interface WBSActivityExplorerProps {
  scheduleId?: string;
  className?: string;
}

export default function WBSActivityExplorer({
  scheduleId,
  className,
}: WBSActivityExplorerProps) {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { currentProject, currentScheduleVersion, selectedStageId, setSelectedStageId, progress } = useProject();
  const { user } = useAuth();

  const [wbsData, setWbsData] = useState<WBSTreeResponse | null>(null);
  const [activities, setActivities] = useState<ScheduleActivity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  // Reopen Modal States
  const [reopenModalActivity, setReopenModalActivity] = useState<ScheduleActivity | null>(null);
  const [reviewModalRequest, setReviewModalRequest] = useState<ReopenRequest | null>(null);
  const [pendingReopenRequests, setPendingReopenRequests] = useState<ReopenRequest[]>([]);

  // Quality Gate Modal States
  const [qualityGates, setQualityGates] = useState<QualityGate[]>([]);
  const [qualityModalTargetActivity, setQualityModalTargetActivity] = useState<ScheduleActivity | null>(null);

  // Downstream Compound Impact States
  const [impactModalTarget, setImpactModalTarget] = useState<{ activityId: string; activityName: string } | null>(null);

  const loadQualityGates = useCallback(async () => {
    try {
      const targetScheduleId = scheduleId || currentScheduleVersion.id;
      const list = await qualityGatesApi.getGates({ scheduleId: targetScheduleId });
      setQualityGates(list);
    } catch {
      // ignore
    }
  }, [scheduleId, currentScheduleVersion.id]);

  useEffect(() => {
    loadQualityGates();
  }, [loadQualityGates]);

  const loadReopenRequests = useCallback(async () => {
    try {
      const list = await reopenApi.getRequests({ status: 'PENDING' });
      setPendingReopenRequests(list);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    loadReopenRequests();
  }, [loadReopenRequests, currentProject.id]);

  const pendingReopenMap = useMemo(() => {
    const map = new Map<string, ReopenRequest>();
    for (const r of pendingReopenRequests) {
      map.set(r.activity_id, r);
    }
    return map;
  }, [pendingReopenRequests]);

  const qualityGatesMap = useMemo(() => {
    const map = new Map<string, QualityGate[]>();
    for (const g of qualityGates) {
      const existing = map.get(g.activityId) || [];
      existing.push(g);
      map.set(g.activityId, existing);
    }
    return map;
  }, [qualityGates]);

  // Synchronize stage filter from URL params if present
  useEffect(() => {
    const stageParam = searchParams.get('stage');
    if (stageParam && stageParam !== selectedStageId) {
      setSelectedStageId(stageParam);
    }
  }, [searchParams]);

  const loadActivities = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const targetScheduleId = scheduleId || currentScheduleVersion.id;
      const [tree, acts] = await Promise.all([
        schedulesApi.getWbsTree(targetScheduleId),
        schedulesApi.getActivities(targetScheduleId),
      ]);
      setWbsData(tree);
      setActivities(acts);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load WBS data');
    } finally {
      setLoading(false);
    }
  }, [scheduleId, currentProject.id, currentScheduleVersion.id]);

  useEffect(() => {
    loadActivities();
  }, [loadActivities]);

  useEffect(() => {
    const handleUpdate = () => {
      loadActivities();
      loadReopenRequests();
      loadQualityGates();
    };
    window.addEventListener('setu:activity-progress-changed', handleUpdate);
    window.addEventListener('setu:activity-state-changed', handleUpdate);
    window.addEventListener('setu:reopen-changed', handleUpdate);
    window.addEventListener('setu:quality-gate-changed', handleUpdate);
    return () => {
      window.removeEventListener('setu:activity-progress-changed', handleUpdate);
      window.removeEventListener('setu:activity-state-changed', handleUpdate);
      window.removeEventListener('setu:reopen-changed', handleUpdate);
      window.removeEventListener('setu:quality-gate-changed', handleUpdate);
    };
  }, [loadActivities, loadReopenRequests, loadQualityGates]);

  // Build lookup map: activity_id → ScheduleActivity
  const activityMap = useMemo(() => {
    const map = new Map<string, ScheduleActivity>();
    for (const a of activities) {
      map.set(a.activity_id, a);
    }
    return map;
  }, [activities]);

  // Server-computed progress per activity (ProgressService); the browser never derives progress.
  const progressItems = useMemo(() => {
    const map = new Map<string, ActivityProgressItem>();
    for (const st of progress?.stages ?? []) for (const it of st.activities) map.set(it.activity_id, it);
    for (const it of progress?.unassigned_activities ?? []) map.set(it.activity_id, it);
    return map;
  }, [progress]);

  // Enrich WBS groups with joined activity names, execution state, and weighted progress
  const enrichedGroups: EnrichedGroup[] = useMemo(() => {
    if (!wbsData) return [];
    return wbsData.wbs_groups.map((g: WBSGroup) => {
      const groupItems: ActivityProgressItem[] = [];

      const enrichedActivities: EnrichedActivity[] = g.activities.map((wa) => {
        const full = activityMap.get(wa.activity_id);
        const item = progressItems.get(wa.activity_id);
        if (item) groupItems.push(item);

        const execState: ExecutionState = (full?.execution_state || item?.canonical_state || 'NOT_STARTED') as ExecutionState;

        return {
          activity_id: wa.activity_id,
          activity_name: full?.activity_name ?? wa.activity_id,
          planned_quantity: wa.planned_quantity,
          discipline: full?.discipline ?? null,
          location: full?.location ?? null,
          execution_state: execState,
          baseline_pct_complete: full?.baseline_pct_complete || 0,
          actual_progress: item?.progress_pct ?? 0,
          weight: item?.weight_factor ?? 1,
          stage_id: item?.stage_id ?? null,
          actual_start: full?.actual_start || null,
          actual_finish: full?.actual_finish || null,
          contractor_name: full?.contractor_name || null,
          work_package_code: full?.work_package_code || null,
          fullActivity: full,
        };
      });

      const totalWeight = groupItems.reduce((sum, it) => sum + (it.weight_factor || 0), 0);
      const actualProgress = totalWeight > 0
        ? Math.round((groupItems.reduce((sum, it) => sum + it.progress_pct * (it.weight_factor || 0), 0) / totalWeight) * 10) / 10
        : 0;

      const quantities = enrichedActivities
        .map((a) => a.planned_quantity)
        .filter((q): q is number => q !== null);
      const totalQuantity = quantities.length > 0
        ? quantities.reduce((sum, q) => sum + q, 0)
        : null;

      return {
        wbs_code: g.wbs_code,
        activities: enrichedActivities,
        totalQuantity,
        total_weight: totalWeight,
        actual_progress: actualProgress,
      };
    });
  }, [wbsData, activityMap, progressItems]);

  // Filter groups by stage and search term
  const filteredGroups = useMemo(() => {
    let groups = enrichedGroups;

    // Filter by selected stage if active
    if (selectedStageId) {
      // A stage is a real server-side entity: keep the WBS groups that contain activities attributed to it.
      groups = groups.filter((g) => g.activities.some((a) => a.stage_id === selectedStageId));
    }

    if (!searchTerm.trim()) return groups;
    const lc = searchTerm.toLowerCase();
    return groups.filter(
      (g) =>
        g.wbs_code.toLowerCase().includes(lc) ||
        g.activities.some(
          (a) =>
            a.activity_id.toLowerCase().includes(lc) ||
            a.activity_name.toLowerCase().includes(lc) ||
            (a.contractor_name && a.contractor_name.toLowerCase().includes(lc)) ||
            (a.work_package_code && a.work_package_code.toLowerCase().includes(lc))
        )
    );
  }, [enrichedGroups, selectedStageId, currentProject.stages, searchTerm]);

  const handleStageSelect = (stageId: string | null) => {
    setSelectedStageId(stageId);
    if (stageId) {
      setSearchParams({ stage: stageId });
    } else {
      setSearchParams({});
    }
  };

  // ── Loading state ─────────────────────────────────────────────────────────
  if (loading) {
    return (
      <Card className={cn('animate-pulse', className)}>
        <CardContent className="flex items-center justify-center py-12 gap-3 text-muted-foreground">
          <Loader2 className="w-5 h-5 animate-spin" />
          <span className="text-sm">{t('wbs.loading')}</span>
        </CardContent>
      </Card>
    );
  }

  // ── Error state ───────────────────────────────────────────────────────────
  if (error) {
    return (
      <Card className={cn('border-destructive/40', className)}>
        <CardContent className="flex items-center gap-3 py-8 text-destructive">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <span className="text-sm">{error}</span>
        </CardContent>
      </Card>
    );
  }

  // ── Empty state ───────────────────────────────────────────────────────────
  if (enrichedGroups.length === 0) {
    return (
      <Card className={className}>
        <CardContent className="flex flex-col items-center justify-center py-12 gap-2 text-muted-foreground">
          <FolderTree className="w-8 h-8 opacity-40" />
          <p className="text-sm">{t('wbs.empty')}</p>
        </CardContent>
      </Card>
    );
  }

  // ── Main render ───────────────────────────────────────────────────────────
  const totalActivities = enrichedGroups.reduce((s, g) => s + g.activities.length, 0);

  return (
    <Card className={cn('border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl overflow-hidden', className)}>
      {/* V7 Project & Schedule Version Hierarchy Header */}
      <div className="bg-gradient-to-r from-[#002266] to-[#001440] dark:from-[#061526] dark:to-[#0A2238] p-4 text-white border-b border-white/10 space-y-2">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
          {/* Breadcrumb Hierarchy */}
          <div className="flex flex-wrap items-center gap-1.5 font-mono text-[11px]">
            <span className="flex items-center gap-1 text-orange-400 font-bold">
              <Building2 className="w-3.5 h-3.5" />
              {currentProject.name}
            </span>
            <span className="text-white/40">→</span>
            <span className="flex items-center gap-1 text-blue-300 font-bold">
              <Calendar className="w-3.5 h-3.5" />
              {currentScheduleVersion.name} ({currentScheduleVersion.versionNumber})
            </span>
            <span className="text-white/40">→</span>
            <span className="flex items-center gap-1 text-emerald-300 font-bold">
              <Layers className="w-3.5 h-3.5" />
              {selectedStageId
                ? currentProject.stages.find((s) => s.id === selectedStageId)?.name || 'Selected Stage'
                : 'All Stages'}
            </span>
            <span className="text-white/40">→</span>
            <span className="text-white/80">WBS & Activities</span>
          </div>

          {/* Schedule Immutability State */}
          <div className="flex items-center gap-1.5 shrink-0">
            {currentScheduleVersion.isCurrent ? (
              <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-400/40">
                ACTIVE BASELINE
              </span>
            ) : (
              <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-slate-500/20 text-slate-300 border border-slate-400/40 flex items-center gap-1">
                <Lock className="w-3 h-3" />
                HISTORICAL / READ-ONLY
              </span>
            )}
          </div>
        </div>
      </div>

      <CardHeader className="pb-4 border-b border-slate-300 dark:border-[#214766]/60">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <FolderTree className="w-5 h-5 text-[#FF7A18] dark:text-[#FF941F]" />
            <CardTitle className="text-base font-extrabold text-[#071A2D] dark:text-[#F5F7FA]">{t('wbs.title')}</CardTitle>
          </div>
          <div className="flex items-center gap-3 text-xs text-[#475569] dark:text-[#CBD5E1] font-semibold">
            <span>{t('wbs.groupCount', { count: filteredGroups.length })} Groups</span>
            <span>·</span>
            <span>{filteredGroups.reduce((s, g) => s + g.activities.length, 0)} of {totalActivities} Activities</span>
          </div>
        </div>
        <p className="text-xs text-[#334155] dark:text-[#CBD5E1] font-medium mt-0.5">{t('wbs.subtitle')}</p>

        {/* Stage Filter Tabs */}
        <div className="pt-2 space-y-1.5">
          <span className="text-[10px] font-mono uppercase font-bold text-muted-foreground flex items-center gap-1">
            <Filter className="w-3 h-3 text-[#FF7A18]" />
            Filter by Project Stage:
          </span>
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={() => handleStageSelect(null)}
              className={cn(
                'px-2.5 py-1 rounded-lg text-xs font-semibold transition-all cursor-pointer border',
                selectedStageId === null
                  ? 'bg-[#FF7A18] text-white border-[#FF7A18] shadow-xs font-bold'
                  : 'bg-slate-100 dark:bg-[#0B2742] text-muted-foreground border-slate-200 dark:border-[#214766] hover:text-foreground'
              )}
            >
              All Stages ({currentProject.totalStages})
            </button>
            {currentProject.stages.map((stg) => {
              const isSelected = selectedStageId === stg.id;
              return (
                <button
                  key={stg.id}
                  type="button"
                  onClick={() => handleStageSelect(stg.id)}
                  className={cn(
                    'px-2.5 py-1 rounded-lg text-xs font-semibold transition-all cursor-pointer border flex items-center gap-1.5',
                    isSelected
                      ? 'bg-primary text-white border-primary shadow-xs font-bold'
                      : 'bg-slate-100 dark:bg-[#0B2742] text-muted-foreground border-slate-200 dark:border-[#214766] hover:text-foreground'
                  )}
                >
                  <span>{stg.name}</span>
                  <span className="opacity-75 text-[10px]">({stg.actualPct}%)</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Search Input */}
        <div className="relative mt-2">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 dark:text-[#8FA6BA]" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder={t('wbs.searchPlaceholder')}
            className="w-full pl-9 pr-3 py-2 text-xs font-medium rounded-xl border border-slate-300 dark:border-[#214766] bg-white dark:bg-[#0B2742] text-[#071A2D] dark:text-[#F5F7FA] placeholder:text-slate-500 dark:placeholder:text-[#8FA6BA] focus:outline-hidden focus:ring-2 focus:ring-[#FF7A18]/30 focus:border-[#FF7A18]"
          />
        </div>
      </CardHeader>

      <CardContent className="space-y-2.5 pt-4">
        {filteredGroups.length === 0 ? (
          <p className="text-xs font-semibold text-[#475569] dark:text-[#CBD5E1] text-center py-6">
            {t('wbs.noResults')}
          </p>
        ) : (
          filteredGroups.map((group) => (
            <WBSGroupCard
              key={group.wbs_code}
              group={group}
              onRequestReopen={setReopenModalActivity}
              onReviewReopen={setReviewModalRequest}
              onOpenQualityModal={setQualityModalTargetActivity}
              onInspectImpact={(imp) => setImpactModalTarget(imp)}
              pendingReopenMap={pendingReopenMap}
              qualityGatesMap={qualityGatesMap}
              userRole={user?.role}
            />
          ))
        )}
      </CardContent>

      {/* Quality Gate Modal */}
      {qualityModalTargetActivity && (
        <QualityGateModal
          isOpen={!!qualityModalTargetActivity}
          onClose={() => setQualityModalTargetActivity(null)}
          activityId={qualityModalTargetActivity.activity_id}
          activityName={qualityModalTargetActivity.activity_name}
          gates={qualityGatesMap.get(qualityModalTargetActivity.activity_id) || []}
          userRole={user?.role}
          onGateUpdated={() => {
            loadQualityGates();
            loadActivities();
          }}
        />
      )}

      {/* Reopen Workflow Modals */}
      {reopenModalActivity && (
        <ReopenRequestModal
          activity={reopenModalActivity}
          isOpen={!!reopenModalActivity}
          onClose={() => setReopenModalActivity(null)}
          onSuccess={() => {
            loadReopenRequests();
            loadActivities();
          }}
        />
      )}

      {reviewModalRequest && (
        <ReopenReviewModal
          request={reviewModalRequest}
          isOpen={!!reviewModalRequest}
          onClose={() => setReviewModalRequest(null)}
          onReviewed={() => {
            loadReopenRequests();
            loadActivities();
          }}
        />
      )}

      {/* Compound Impact Inspection Modal */}
      <CompoundImpactModal
        isOpen={!!impactModalTarget}
        onClose={() => setImpactModalTarget(null)}
        target={impactModalTarget}
      />
    </Card>
  );
}
