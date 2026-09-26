/**
 * WBSActivityExplorer — Read-only WBS Activity Grouping Display
 *
 * Feature 30A (Stage 7). Displays the flat WBS grouping returned by
 * GET /api/v1/schedules/{schedule_id}/wbs-tree. Client-side joins
 * activity_id → activity_name via schedulesApi.getActivities().
 *
 * Strictly read-only: no editing, no splitting, no drag-drop.
 */
import React, { useState, useEffect, useMemo } from 'react';
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
} from 'lucide-react';
import { useProject } from '@/context/ProjectContext';
import { Button } from '@/components/ui/button';
import {
  schedulesApi,
  type WBSTreeResponse,
  type WBSGroup,
  type ScheduleActivity,
} from '@/api';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';

// ── Enriched activity with joined name ──────────────────────────────────────

interface EnrichedActivity {
  activity_id: string;
  activity_name: string;
  planned_quantity: number | null;
  discipline: string | null;
  location: string | null;
}

interface EnrichedGroup {
  wbs_code: string;
  activities: EnrichedActivity[];
  totalQuantity: number | null;
}

// ── WBSGroupCard — collapsible group row ────────────────────────────────────

function WBSGroupCard({ group }: { group: EnrichedGroup }) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="border border-slate-200 dark:border-[#214766] rounded-xl overflow-hidden transition-all shadow-2xs hover:border-[#FF7A18]/50">
      {/* Group Header */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left bg-slate-50/90 dark:bg-[#0A2238] hover:bg-slate-100 dark:hover:bg-[#0D2942] transition-colors"
      >
        {isOpen ? (
          <ChevronDown className="w-4 h-4 text-[#475569] dark:text-[#CBD5E1] shrink-0" />
        ) : (
          <ChevronRight className="w-4 h-4 text-[#475569] dark:text-[#CBD5E1] shrink-0" />
        )}
        <FolderTree className="w-4 h-4 text-[#FF7A18] dark:text-[#FF941F] shrink-0" />
        <span className="font-mono text-sm font-bold text-[#071A2D] dark:text-[#F5F7FA]">
          {group.wbs_code}
        </span>
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
          {group.activities.map((a) => (
            <div
              key={a.activity_id}
              className="px-4 py-2.5 pl-12 flex items-center gap-3 text-sm hover:bg-slate-50 dark:hover:bg-[#0D2942] transition-colors"
            >
              <span className="font-mono text-xs font-bold text-[#0284C7] dark:text-[#38BDF8] w-24 shrink-0 truncate" title={a.activity_id}>
                {a.activity_id}
              </span>
              <span className="flex-1 truncate font-medium text-[#071A2D] dark:text-[#F5F7FA]">
                {a.activity_name}
              </span>
              {a.discipline && (
                <span className="text-[11px] px-2 py-0.5 rounded-full bg-orange-50 dark:bg-orange-950/60 text-[#FF7A18] dark:text-[#FF941F] border border-orange-300 dark:border-orange-800/60 font-semibold shrink-0">
                  {a.discipline}
                </span>
              )}
              {a.planned_quantity !== null && (
                <span className="text-xs text-[#475569] dark:text-[#CBD5E1] font-mono font-semibold shrink-0">
                  {t('wbs.qty', { qty: a.planned_quantity })}
                </span>
              )}
            </div>
          ))}
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
  const { currentProject, currentScheduleVersion, selectedStageId, setSelectedStageId } = useProject();

  const [wbsData, setWbsData] = useState<WBSTreeResponse | null>(null);
  const [activities, setActivities] = useState<ScheduleActivity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  // Synchronize stage filter from URL params if present
  useEffect(() => {
    const stageParam = searchParams.get('stage');
    if (stageParam && stageParam !== selectedStageId) {
      setSelectedStageId(stageParam);
    }
  }, [searchParams]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const targetScheduleId = scheduleId || currentScheduleVersion.id;
        const [tree, acts] = await Promise.all([
          schedulesApi.getWbsTree(targetScheduleId),
          schedulesApi.getActivities(targetScheduleId),
        ]);
        if (!cancelled) {
          setWbsData(tree);
          setActivities(acts);
        }
      } catch (err: unknown) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load WBS data');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => { cancelled = true; };
  }, [scheduleId, currentProject.id, currentScheduleVersion.id]);

  // Build lookup map: activity_id → ScheduleActivity
  const activityMap = useMemo(() => {
    const map = new Map<string, ScheduleActivity>();
    for (const a of activities) {
      map.set(a.activity_id, a);
    }
    return map;
  }, [activities]);

  // Enrich WBS groups with joined activity names
  const enrichedGroups: EnrichedGroup[] = useMemo(() => {
    if (!wbsData) return [];
    return wbsData.wbs_groups.map((g: WBSGroup) => {
      const enrichedActivities: EnrichedActivity[] = g.activities.map((wa) => {
        const full = activityMap.get(wa.activity_id);
        return {
          activity_id: wa.activity_id,
          activity_name: full?.activity_name ?? wa.activity_id,
          planned_quantity: wa.planned_quantity,
          discipline: full?.discipline ?? null,
          location: full?.location ?? null,
        };
      });

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
      };
    });
  }, [wbsData, activityMap]);

  // Filter groups by stage and search term
  const filteredGroups = useMemo(() => {
    let groups = enrichedGroups;

    // Filter by selected stage if active
    if (selectedStageId) {
      const stage = currentProject.stages.find((s) => s.id === selectedStageId);
      if (stage) {
        const prefix = stage.wbsPrefix.toLowerCase();
        const stageDiscipline = stage.discipline.toLowerCase();
        groups = groups.filter((g) => {
          const wbsMatch = g.wbs_code.toLowerCase().includes(prefix.replace('wbs-', ''));
          const actMatch = g.activities.some((a) =>
            stageDiscipline === 'general' ||
            (a.discipline && a.discipline.toLowerCase().includes(stageDiscipline))
          );
          return wbsMatch || actMatch;
        });
      }
    }

    if (!searchTerm.trim()) return groups;
    const lc = searchTerm.toLowerCase();
    return groups.filter(
      (g) =>
        g.wbs_code.toLowerCase().includes(lc) ||
        g.activities.some(
          (a) =>
            a.activity_id.toLowerCase().includes(lc) ||
            a.activity_name.toLowerCase().includes(lc)
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
                  <span>Stage {stg.stageNumber}</span>
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
            <WBSGroupCard key={group.wbs_code} group={group} />
          ))
        )}
      </CardContent>
    </Card>
  );
}
