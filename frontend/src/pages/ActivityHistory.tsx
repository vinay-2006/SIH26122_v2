import React, { useEffect, useState, useMemo, useCallback } from 'react';
import { useSearchParams, Link as RouterLink } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  activitiesApi,
  auditApi,
  claimsApi,
  ScheduleActivity,
  AuditLogEntry,
  ActivityTimelineItem,
  ActivitySummary,
  ActivityMetrics,
  ExecutionState,
} from '@/api';
import {
  Clock,
  Search,
  ShieldCheck,
  User,
  CalendarDays,
  GitCommit,
  Hash,
  FileText,
  CheckCircle2,
  XCircle,
  RotateCcw,
  Link as LinkIcon,
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  Filter,
  AlertTriangle,
  Layers,
  Flame,
  Zap,
  Check,
  ArrowRight,
  Eye,
  RefreshCw,
  X,
  SlidersHorizontal,
  ListFilter,
  Sparkles,
  MapPin,
  Tag,
  Building2,
  Maximize2,
  Minimize2,
  ArrowUpRight,
  FolderTree,
  Paperclip,
  Download,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useProject } from '@/context/ProjectContext';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from '@/components/ui/card';
import { StatusBadge } from '@/components/StatusBadge';
import { ProvenanceBadge } from '@/components/ProvenanceBadge';
import { ExecutionStateBadge } from '@/components/ExecutionStateBadge';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { Skeleton } from '@/components/ui/skeleton';
import { ImageLightbox } from '@/components/ImageLightbox';
import { cn } from '@/lib/utils';

const DIRECTORY_PAGE_SIZE = 15;
const TIMELINE_PAGE_SIZE = 5;

type TimelineFilterTab = 'all' | 'claims' | 'decisions' | 'actuals' | 'audit';

function TimelinePhoto({ eventId, photoPath }: { eventId: string; photoPath?: string | null }) {
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);

  const filename = photoPath ? (photoPath.split(/[\/\\]/).pop() || 'evidence') : null;
  const isImage = photoPath ? /\.(jpg|jpeg|png|webp|gif|bmp)$/i.test(photoPath) : false;
  const isPdf = photoPath ? /\.pdf$/i.test(photoPath) : false;

  useEffect(() => {
    // Contract: Do NOT call /claims/{eventId}/photo when photoPath is absent
    if (!photoPath) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    claimsApi
      .getPhotoBlobUrl(eventId)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setPhotoUrl(url);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [eventId, photoPath]);

  // Case 1: No evidence attached -> Render deterministic placeholder card
  if (!photoPath) {
    return (
      <div className="pt-2 text-[11px]">
        <div className="text-muted-foreground flex items-center gap-1 font-semibold mb-1.5">
          <Paperclip className="w-3.5 h-3.5 text-muted-foreground" />
          <span>Evidence</span>
        </div>
        <div className="w-full max-w-[280px] p-3 rounded-lg border border-dashed border-slate-300 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-900/40 flex flex-col items-center justify-center text-center gap-1">
          <FileText className="w-5 h-5 text-slate-400 dark:text-slate-500 mb-0.5" />
          <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">File not uploaded</span>
          <span className="text-[10px] text-muted-foreground">No evidence file attached</span>
        </div>
      </div>
    );
  }

  // Case 2: Evidence exists (Image)
  if (isImage) {
    return (
      <div className="flex items-center gap-2 pt-2 text-[11px]">
        <span className="text-muted-foreground flex items-center gap-1 font-semibold">
          <Paperclip className="w-3.5 h-3.5 text-[#FC4C02]" />
          <span>Evidence File:</span>
        </span>
        <span className="font-mono text-slate-800 dark:text-slate-200 font-bold truncate max-w-[180px]" title={filename || ''}>
          {filename}
        </span>
        {photoUrl && (
          <ImageLightbox src={photoUrl} alt={`Evidence photo for event ${eventId}`}>
            <img
              src={photoUrl}
              alt={`Thumbnail for event ${eventId}`}
              className="w-8 h-8 rounded-md object-cover border border-slate-300 dark:border-slate-700 hover:opacity-80 transition-opacity cursor-pointer"
            />
          </ImageLightbox>
        )}
      </div>
    );
  }

  // Case 3: Evidence exists (PDF or Document - not an image!)
  return (
    <div className="pt-2 text-[11px]">
      <div className="text-muted-foreground flex items-center gap-1 font-semibold mb-1">
        <Paperclip className="w-3.5 h-3.5 text-primary" />
        <span>Evidence Document:</span>
      </div>
      <div className="inline-flex items-center gap-2.5 p-2 rounded-lg border border-border bg-card shadow-2xs">
        <div className="p-1 rounded bg-blue-50 dark:bg-blue-950/60 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-900/50">
          <FileText className="w-4 h-4" />
        </div>
        <div className="flex flex-col">
          <span className="font-mono font-bold text-xs text-foreground truncate max-w-[200px]" title={filename || ''}>
            {filename}
          </span>
          <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-semibold">
            {isPdf ? 'PDF · Evidence attached' : 'Document · Evidence attached'}
          </span>
        </div>
        {photoUrl && (
          <a
            href={photoUrl}
            download={filename || 'evidence.pdf'}
            className="ml-1 p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
            title="Download document"
          >
            <Download className="w-3.5 h-3.5" />
          </a>
        )}
      </div>
    </div>
  );
}

export default function ActivityHistory() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { currentProject, currentScheduleVersion } = useProject();

  // URL Deep Link
  const activityIdFromUrl = searchParams.get('activity_id') || '';

  // Activity Directory State
  const [activities, setActivities] = useState<ActivitySummary[]>([]);
  const [metrics, setMetrics] = useState<ActivityMetrics>({
    total: 0,
    in_progress: 0,
    completed: 0,
    not_started: 0,
    critical: 0,
    changed: 0,
  });
  const [isLoadingDirectory, setIsLoadingDirectory] = useState<boolean>(true);
  const [directoryError, setDirectoryError] = useState<string | null>(null);

  // Filters State
  const [searchFilter, setSearchFilter] = useState<string>('');
  const [selectedDiscipline, setSelectedDiscipline] = useState<string>('ALL');
  const [selectedLocation, setSelectedLocation] = useState<string>('ALL');
  const [selectedWbs, setSelectedWbs] = useState<string>('ALL');
  const [selectedState, setSelectedState] = useState<string>('ALL');
  const [selectedCriticality, setSelectedCriticality] = useState<string>('ALL');
  const [selectedFloat, setSelectedFloat] = useState<string>('ALL');
  const [selectedRecency, setSelectedRecency] = useState<string>('ALL');

  // Directory Pagination
  const [directoryPage, setDirectoryPage] = useState<number>(1);
  const [directoryTotal, setDirectoryTotal] = useState<number>(0);

  // Selected Activity & History State
  const [selectedActivityId, setSelectedActivityId] = useState<string>(activityIdFromUrl);


  const [activityMetadata, setActivityMetadata] = useState<ScheduleActivity | null>(null);
  const [timeline, setTimeline] = useState<ActivityTimelineItem[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState<boolean>(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  // Timeline Sub-Tab & View Layout
  const [timelineTab, setTimelineTab] = useState<TimelineFilterTab>('all');
  const [isDetailExpanded, setIsDetailExpanded] = useState<boolean>(false);
  const [expandedEvidence, setExpandedEvidence] = useState<Record<string, boolean>>({});

  // Timeline Pagination
  const [auditLogsPage, setAuditLogsPage] = useState<number>(1);

  // Toggle Evidence Snippets
  const toggleEvidence = (id: string) => {
    setExpandedEvidence((prev) => ({
      ...prev,
      [id]: !prev[id],
    }));
  };

  // Load Activities Directory from Server
  const loadDirectory = useCallback(async () => {
    setIsLoadingDirectory(true);
    setDirectoryError(null);

    try {
      const res = await activitiesApi.getActivities({
        schedule_id: currentScheduleVersion.id,
        search: searchFilter || undefined,
        discipline: selectedDiscipline !== 'ALL' ? selectedDiscipline : undefined,
        location: selectedLocation !== 'ALL' ? selectedLocation : undefined,
        wbs_code: selectedWbs !== 'ALL' ? selectedWbs : undefined,
        execution_state: selectedState !== 'ALL' ? selectedState : undefined,
        is_critical: selectedCriticality !== 'ALL' ? selectedCriticality : undefined,
        float_range: selectedFloat !== 'ALL' ? selectedFloat : undefined,
        change_recency: selectedRecency !== 'ALL' ? selectedRecency : undefined,
        page: directoryPage,
        page_size: DIRECTORY_PAGE_SIZE,
        sort_by: 'activity_id',
        sort_order: 'asc',
      });

      setActivities(res.items || []);
      setDirectoryTotal(res.total || 0);
      setMetrics(res.metrics || {
        total: res.total || 0,
        in_progress: 0,
        completed: 0,
        not_started: 0,
        critical: 0,
        changed: 0,
      });

    } catch (err: any) {
      setDirectoryError(err.message || 'Failed to load schedule activities');
    } finally {
      setIsLoadingDirectory(false);
    }
  }, [
    currentProject.id,
    currentScheduleVersion.id,
    searchFilter,
    selectedDiscipline,
    selectedLocation,
    selectedWbs,
    selectedState,
    selectedCriticality,
    selectedFloat,
    selectedRecency,
    directoryPage,
    setSearchParams,
  ]);

  // Load Directory on mount and when filters/page change
  useEffect(() => {
    loadDirectory();
  }, [loadDirectory]);

  // Load Detailed Activity Timeline & Audit Logs
  const loadActivityDetails = useCallback(async (actId: string) => {
    if (!actId) return;

    setIsLoadingHistory(true);
    setHistoryError(null);

    try {
      const schedId = activities.find((a) => a.activity_id === actId)?.schedule_id;
      const [histData, logs] = await Promise.all([
        activitiesApi.getHistory(actId, schedId),
        auditApi.getLogs(),
      ]);

      setActivityMetadata(histData.activity);
      setTimeline(histData.timeline || []);
      setAuditLogs(logs || []);
      setAuditLogsPage(1);
    } catch (err: any) {
      setHistoryError(err.message || t('history.failedToFetch', { message: actId }));
    } finally {
      setIsLoadingHistory(false);
    }
  }, [t]);

  // Auto-select first activity ONLY if no activity is in URL and none is selected
  useEffect(() => {
    if (!activityIdFromUrl && !selectedActivityId && activities.length > 0) {
      const firstId = activities[0].activity_id;
      setSelectedActivityId(firstId);
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set('activity_id', firstId);
        return next;
      });
    }
  }, [activityIdFromUrl, selectedActivityId, activities, setSearchParams]);

  // Trigger Detail Fetch when selectedActivityId changes
  useEffect(() => {
    if (selectedActivityId) {
      loadActivityDetails(selectedActivityId);
    }
  }, [selectedActivityId, loadActivityDetails]);

  // Handle URL change from outside
  useEffect(() => {
    if (activityIdFromUrl && activityIdFromUrl !== selectedActivityId) {
      setSelectedActivityId(activityIdFromUrl);
    }
  }, [activityIdFromUrl, selectedActivityId]);

  // Select an Activity handler
  const handleSelectActivity = (actId: string) => {
    setSelectedActivityId(actId);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('activity_id', actId);
      return next;
    });
  };

  // Direct Search / Lookup Submit
  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const query = searchFilter.trim();
    if (!query) return;

    // Check if query exactly matches an activity in current list
    const exactMatch = activities.find(
      (a) => a.activity_id.toLowerCase() === query.toLowerCase()
    );

    if (exactMatch) {
      handleSelectActivity(exactMatch.activity_id);
    } else {
      // Direct lookup by ID
      handleSelectActivity(query);
    }
  };

  // Reset Filters rule: always resets page to 1
  const handleFilterChange = (setter: (val: any) => void, val: any) => {
    setter(val);
    setDirectoryPage(1);
  };

  const handleClearFilters = () => {
    setSearchFilter('');
    setSelectedDiscipline('ALL');
    setSelectedLocation('ALL');
    setSelectedWbs('ALL');
    setSelectedState('ALL');
    setSelectedCriticality('ALL');
    setSelectedFloat('ALL');
    setSelectedRecency('ALL');
    setDirectoryPage(1);
  };

  const hasActiveFilters =
    searchFilter.trim() !== '' ||
    selectedDiscipline !== 'ALL' ||
    selectedLocation !== 'ALL' ||
    selectedWbs !== 'ALL' ||
    selectedState !== 'ALL' ||
    selectedCriticality !== 'ALL' ||
    selectedFloat !== 'ALL' ||
    selectedRecency !== 'ALL';

  // Extract distinct locations and WBS codes from directory items for dynamic dropdowns
  const distinctLocations = useMemo(() => {
    const locs = new Set<string>();
    activities.forEach((a) => {
      if (a.location) locs.add(a.location);
    });
    return Array.from(locs).sort();
  }, [activities]);

  const distinctWbs = useMemo(() => {
    const wbs = new Set<string>();
    activities.forEach((a) => {
      if (a.wbs_code) wbs.add(a.wbs_code);
    });
    return Array.from(wbs).sort();
  }, [activities]);

  // Quick Review Chip toggles
  const handleQuickFilterToggle = (type: 'in_progress' | 'critical' | 'changed' | 'completed' | 'all') => {
    setDirectoryPage(1);
    if (type === 'all') {
      setSelectedState('ALL');
      setSelectedCriticality('ALL');
      setSelectedRecency('ALL');
    } else if (type === 'in_progress') {
      setSelectedState((prev) => (prev === 'IN_PROGRESS' ? 'ALL' : 'IN_PROGRESS'));
    } else if (type === 'completed') {
      setSelectedState((prev) => (prev === 'COMPLETED' ? 'ALL' : 'COMPLETED'));
    } else if (type === 'critical') {
      setSelectedCriticality((prev) => (prev === 'CRITICAL' ? 'ALL' : 'CRITICAL'));
    } else if (type === 'changed') {
      setSelectedRecency((prev) => (prev === 'NO_CHANGES' ? 'ALL' : prev === 'ALL' ? 'LAST_30_DAYS' : 'ALL'));
    }
  };

  // Filtered Timeline Items according to sub-tab
  const filteredTimeline = useMemo(() => {
    if (timelineTab === 'claims') {
      return timeline.filter((item) => item.type === 'execution_event');
    }
    if (timelineTab === 'decisions') {
      return timeline.filter((item) => item.type === 'planner_decision');
    }
    if (timelineTab === 'actuals') {
      return timeline.filter((item) => item.type === 'approved_actual');
    }
    return timeline;
  }, [timeline, timelineTab]);

  const claimCount = timeline.filter((i) => i.type === 'execution_event').length;
  const decisionCount = timeline.filter((i) => i.type === 'planner_decision').length;
  const actualCount = timeline.filter((i) => i.type === 'approved_actual').length;

  const totalAuditPages = Math.max(1, Math.ceil(auditLogs.length / TIMELINE_PAGE_SIZE));
  const paginatedAuditLogs = auditLogs.slice(
    (auditLogsPage - 1) * TIMELINE_PAGE_SIZE,
    auditLogsPage * TIMELINE_PAGE_SIZE
  );

  const totalDirectoryPages = Math.max(1, Math.ceil(directoryTotal / DIRECTORY_PAGE_SIZE));

  return (
    <div className="space-y-4">
      {/* Top Header & Search Bar */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 border-b border-slate-300 dark:border-[#214766]/60 pb-3">
        <div>
          <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground pb-1 flex-wrap">
            <span className="flex items-center gap-1 font-bold text-primary">
              <Building2 className="w-3.5 h-3.5 text-[#FF7A18]" />
              {currentProject.name} ({currentProject.code})
            </span>
            <span>·</span>
            <span className="text-[11px] px-2 py-0.2 rounded bg-slate-100 dark:bg-[#0B2742] text-muted-foreground border border-slate-300 dark:border-[#214766]">
              {currentScheduleVersion.versionNumber}
            </span>
          </div>
          <h1 className="text-xl sm:text-2xl font-extrabold text-[#071A2D] dark:text-[#F5F7FA] tracking-tight flex items-center gap-2">
            <div className="p-1.5 rounded-lg bg-[#FC4C02]/10 border border-[#FC4C02]/20">
              <Clock className="w-5 h-5 text-[#FC4C02]" />
            </div>
            {t('history.title')}
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-xs font-medium mt-0.5">
            {t('history.subtitle')}
          </p>
        </div>

        {/* Global Direct Search Form & View Toggle */}
        <div className="flex items-center gap-2">
          <form onSubmit={handleSearchSubmit} className="flex items-center gap-1.5">
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-muted-foreground" />
              <Input
                value={searchFilter}
                onChange={(e) => handleFilterChange(setSearchFilter, e.target.value)}
                placeholder={t('history.searchDirectory')}
                className="pl-8 bg-white dark:bg-[#001438] border-slate-300 dark:border-blue-800 text-slate-900 dark:text-slate-100 text-xs h-8 w-56 sm:w-64 font-mono focus:border-[#FC4C02]"
              />
              {searchFilter && (
                <button
                  type="button"
                  onClick={() => handleFilterChange(setSearchFilter, '')}
                  className="absolute right-2 top-2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>

            <Button
              type="submit"
              size="sm"
              className="bg-[#FC4C02] hover:bg-[#e04302] text-white text-xs h-8 font-semibold px-3"
            >
              {t('history.lookup')}
            </Button>
          </form>

          {/* Expand/Collapse Timeline Layout Toggle */}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setIsDetailExpanded(!isDetailExpanded)}
            className="h-8 text-xs font-semibold px-2.5 hidden lg:flex items-center gap-1 border-slate-300 dark:border-blue-800"
            title={isDetailExpanded ? t('history.collapseView') : t('history.expandView')}
          >
            {isDetailExpanded ? (
              <>
                <Minimize2 className="w-3.5 h-3.5" />
                <span className="hidden xl:inline">{t('history.collapseView')}</span>
              </>
            ) : (
              <>
                <Maximize2 className="w-3.5 h-3.5" />
                <span className="hidden xl:inline">{t('history.expandView')}</span>
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Scope-Aware Quick Review Metrics Strip */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1 text-xs">
        <button
          type="button"
          onClick={() => handleQuickFilterToggle('all')}
          className={cn(
            'px-2.5 py-1 rounded-md font-semibold font-mono text-xs transition-all border shrink-0',
            selectedState === 'ALL' && selectedCriticality === 'ALL' && selectedRecency === 'ALL'
              ? 'bg-[#071A2D] dark:bg-blue-900/60 text-white border-primary shadow-xs'
              : 'bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-800 hover:border-primary/50'
          )}
        >
          {t('history.quickAll', { count: `${metrics.total}` })}
        </button>

        <button
          type="button"
          onClick={() => handleQuickFilterToggle('in_progress')}
          className={cn(
            'px-2.5 py-1 rounded-md font-semibold font-mono text-xs transition-all border flex items-center gap-1.5 shrink-0',
            selectedState === 'IN_PROGRESS'
              ? 'bg-amber-500/20 text-amber-700 dark:text-amber-300 border-amber-500 shadow-xs'
              : 'bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-800 hover:border-amber-400'
          )}
        >
          <span className="w-2 h-2 rounded-full bg-amber-500" />
          {t('history.quickInProgress', { count: metrics.in_progress })}
        </button>

        <button
          type="button"
          onClick={() => handleQuickFilterToggle('critical')}
          className={cn(
            'px-2.5 py-1 rounded-md font-semibold font-mono text-xs transition-all border flex items-center gap-1 shrink-0',
            selectedCriticality === 'CRITICAL'
              ? 'bg-rose-500/20 text-rose-700 dark:text-rose-300 border-rose-500 shadow-xs'
              : 'bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-800 hover:border-rose-400'
          )}
        >
          <Flame className="w-3 h-3 text-rose-500" />
          {t('history.quickCritical', { count: metrics.critical })}
        </button>

        <button
          type="button"
          onClick={() => handleQuickFilterToggle('changed')}
          className={cn(
            'px-2.5 py-1 rounded-md font-semibold font-mono text-xs transition-all border flex items-center gap-1 shrink-0',
            selectedRecency !== 'ALL' && selectedRecency !== 'NO_CHANGES'
              ? 'bg-blue-500/20 text-blue-700 dark:text-blue-300 border-blue-500 shadow-xs'
              : 'bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-800 hover:border-blue-400'
          )}
        >
          <Sparkles className="w-3 h-3 text-blue-500" />
          {t('history.quickChanged', { count: metrics.changed })}
        </button>

        <button
          type="button"
          onClick={() => handleQuickFilterToggle('completed')}
          className={cn(
            'px-2.5 py-1 rounded-md font-semibold font-mono text-xs transition-all border flex items-center gap-1 shrink-0',
            selectedState === 'COMPLETED'
              ? 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 border-emerald-500 shadow-xs'
              : 'bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-800 hover:border-emerald-400'
          )}
        >
          <CheckCircle2 className="w-3 h-3 text-emerald-500" />
          {t('history.quickCompleted', { count: metrics.completed })}
        </button>

        {hasActiveFilters && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleClearFilters}
            className="h-7 text-xs font-semibold text-rose-500 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/30 ml-auto shrink-0 gap-1 px-2"
          >
            <RotateCcw className="w-3 h-3" />
            {t('history.clearFilters')}
          </Button>
        )}
      </div>

      {/* Main Workspace Layout (Master-Detail) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* LEFT COLUMN: Compact Activity Directory */}
        <div
          className={cn(
            'transition-all duration-300',
            isDetailExpanded
              ? 'hidden'
              : 'lg:col-span-5 xl:col-span-4 space-y-2.5'
          )}
        >
          <Card className="border-slate-200 dark:border-[#214766]/60 bg-white dark:bg-[#071A2D]/80 shadow-xs">
            {/* Filter Bar Controls */}
            <div className="p-3 border-b border-slate-200 dark:border-slate-800 space-y-2 bg-slate-50/70 dark:bg-[#0A2238]/60 rounded-t-xl">
              <div className="flex items-center justify-between text-xs">
                <span className="font-bold text-slate-800 dark:text-slate-200 flex items-center gap-1.5">
                  <ListFilter className="w-3.5 h-3.5 text-[#FC4C02]" />
                  {t('history.activityDirectory')}
                </span>
                <span className="font-mono text-[11px] text-slate-500 dark:text-slate-400">
                  {directoryTotal} items
                </span>
              </div>

              {/* Characteristic Selectors Grid */}
              <div className="grid grid-cols-2 gap-2 text-[11px]">
                {/* Discipline */}
                <div>
                  <label className="text-[10px] uppercase font-bold text-slate-500 dark:text-slate-400 block mb-0.5">
                    {t('history.filterDiscipline')}
                  </label>
                  <select
                    value={selectedDiscipline}
                    onChange={(e) => handleFilterChange(setSelectedDiscipline, e.target.value)}
                    className="w-full text-xs py-1 px-2 rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200"
                  >
                    <option value="ALL">{t('history.allDisciplines')}</option>
                    <option value="CIVIL">CIVIL</option>
                    <option value="PIPING">PIPING</option>
                    <option value="ELECTRICAL">ELECTRICAL</option>
                    <option value="INSTRUMENTATION">INSTRUMENTATION</option>
                    <option value="STATIC_ROTATING_EQUIPMENT">STATIC / ROTATING</option>
                    <option value="HSE">HSE</option>
                  </select>
                </div>

                {/* Execution State */}
                <div>
                  <label className="text-[10px] uppercase font-bold text-slate-500 dark:text-slate-400 block mb-0.5">
                    {t('history.filterState')}
                  </label>
                  <select
                    value={selectedState}
                    onChange={(e) => handleFilterChange(setSelectedState, e.target.value)}
                    className="w-full text-xs py-1 px-2 rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200"
                  >
                    <option value="ALL">{t('history.allStates')}</option>
                    <option value="IN_PROGRESS">{t('history.stateInProgress')}</option>
                    <option value="COMPLETED">{t('history.stateCompleted')}</option>
                    <option value="NOT_STARTED">{t('history.stateNotStarted')}</option>
                    <option value="ON_HOLD">ON HOLD</option>
                    <option value="REOPEN_REQUESTED">REOPEN REQUESTED</option>
                    <option value="REOPENED">REOPENED</option>
                  </select>
                </div>

                {/* Criticality */}
                <div>
                  <label className="text-[10px] uppercase font-bold text-slate-500 dark:text-slate-400 block mb-0.5">
                    {t('history.filterCriticality')}
                  </label>
                  <select
                    value={selectedCriticality}
                    onChange={(e) => handleFilterChange(setSelectedCriticality, e.target.value)}
                    className="w-full text-xs py-1 px-2 rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200"
                  >
                    <option value="ALL">{t('history.allCriticality')}</option>
                    <option value="CRITICAL">⚡ {t('history.critCritical')}</option>
                    <option value="NON_CRITICAL">{t('history.critNonCritical')}</option>
                    <option value="UNKNOWN">{t('history.critUnknown')}</option>
                  </select>
                </div>

                {/* Total Float */}
                <div>
                  <label className="text-[10px] uppercase font-bold text-slate-500 dark:text-slate-400 block mb-0.5">
                    {t('history.filterFloat')}
                  </label>
                  <select
                    value={selectedFloat}
                    onChange={(e) => handleFilterChange(setSelectedFloat, e.target.value)}
                    className="w-full text-xs py-1 px-2 rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200"
                  >
                    <option value="ALL">{t('history.allFloats')}</option>
                    <option value="ZERO">{t('history.floatZero')}</option>
                    <option value="1_TO_5">{t('history.float1to5')}</option>
                    <option value="GT_5">{t('history.floatGt5')}</option>
                    <option value="UNKNOWN">{t('history.floatUnknown')}</option>
                  </select>
                </div>

                {/* Location */}
                <div>
                  <label className="text-[10px] uppercase font-bold text-slate-500 dark:text-slate-400 block mb-0.5">
                    {t('history.filterLocation')}
                  </label>
                  <select
                    value={selectedLocation}
                    onChange={(e) => handleFilterChange(setSelectedLocation, e.target.value)}
                    className="w-full text-xs py-1 px-2 rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 truncate"
                  >
                    <option value="ALL">{t('history.allLocations')}</option>
                    {distinctLocations.map((loc) => (
                      <option key={loc} value={loc}>
                        {loc}
                      </option>
                    ))}
                  </select>
                </div>

                {/* WBS Group */}
                <div>
                  <label className="text-[10px] uppercase font-bold text-slate-500 dark:text-slate-400 block mb-0.5">
                    {t('history.filterWbs')}
                  </label>
                  <select
                    value={selectedWbs}
                    onChange={(e) => handleFilterChange(setSelectedWbs, e.target.value)}
                    className="w-full text-xs py-1 px-2 rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 truncate"
                  >
                    <option value="ALL">{t('history.allWbs')}</option>
                    {distinctWbs.map((w) => (
                      <option key={w} value={w}>
                        WBS {w}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            {/* Compact Directory List */}
            <div className="divide-y divide-slate-100 dark:divide-slate-800/80 max-h-[640px] overflow-y-auto">
              {isLoadingDirectory ? (
                <div className="p-4 space-y-3">
                  <Skeleton className="h-16 w-full rounded-lg" />
                  <Skeleton className="h-16 w-full rounded-lg" />
                  <Skeleton className="h-16 w-full rounded-lg" />
                  <Skeleton className="h-16 w-full rounded-lg" />
                </div>
              ) : directoryError ? (
                <div className="p-4 text-center text-xs text-rose-500 space-y-2">
                  <p>{directoryError}</p>
                  <Button size="sm" variant="outline" onClick={loadDirectory} className="text-xs h-7">
                    Retry
                  </Button>
                </div>
              ) : activities.length === 0 ? (
                <div className="p-6 text-center text-xs text-slate-400 space-y-2">
                  <GitCommit className="w-8 h-8 mx-auto opacity-30" />
                  <p className="font-semibold">{t('history.noMatchingActivities')}</p>
                  <p className="text-[11px] text-slate-500">{t('history.noMatchingHint')}</p>
                  {hasActiveFilters && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={handleClearFilters}
                      className="text-xs h-7 mt-2"
                    >
                      {t('history.clearFilters')}
                    </Button>
                  )}
                </div>
              ) : (
                activities.map((act) => {
                  const isSelected = act.activity_id === selectedActivityId;
                  const isCompleted = act.execution_state === 'COMPLETED';
                  const isInProgress = act.execution_state === 'IN_PROGRESS';

                  return (
                    <button
                      key={act.activity_id}
                      type="button"
                      onClick={() => handleSelectActivity(act.activity_id)}
                      className={cn(
                        'w-full text-left p-3 transition-all flex flex-col gap-1.5 cursor-pointer',
                        isSelected
                          ? 'bg-[#FC4C02]/10 border-l-4 border-l-[#FC4C02] dark:bg-[#FC4C02]/15'
                          : 'hover:bg-slate-50/80 dark:hover:bg-[#0A2238]/60 border-l-4 border-l-transparent'
                      )}
                    >
                      {/* Top Row: Activity ID, State Badge, % Complete */}
                      <div className="flex items-center justify-between gap-1 text-xs">
                        <span
                          className={cn(
                            'font-mono font-bold text-xs truncate',
                            isSelected
                              ? 'text-[#FC4C02]'
                              : 'text-slate-900 dark:text-slate-100'
                          )}
                        >
                          {act.activity_id}
                        </span>

                        <div className="flex items-center gap-1.5 shrink-0">
                          <ExecutionStateBadge state={act.execution_state} size="sm" />

                          <span className="font-mono text-xs font-bold text-slate-800 dark:text-slate-200">
                            {act.actual_pct_complete !== null
                              ? `${Math.round(act.actual_pct_complete)}%`
                              : `${Math.round(act.baseline_pct_complete)}%`}
                          </span>
                        </div>
                      </div>

                      {/* Middle Row: Activity Name */}
                      <p className="text-xs text-slate-700 dark:text-slate-300 font-medium line-clamp-1 leading-snug">
                        {act.activity_name}
                      </p>

                      {/* Bottom Row: Discipline • Location | Float / Critical */}
                      <div className="flex items-center justify-between text-[10px] text-slate-500 dark:text-slate-400 font-mono gap-1">
                        <span className="truncate">
                          {act.discipline} · {act.location}
                        </span>

                        <div className="flex items-center gap-1.5 shrink-0">
                          {act.is_critical === true && (
                            <span className="text-rose-600 dark:text-rose-400 font-bold flex items-center gap-0.5">
                              <Flame className="w-2.5 h-2.5" />
                              CRIT
                            </span>
                          )}

                          <span>
                            Float:{' '}
                            {act.total_float !== null ? `${act.total_float}d` : 'Unk'}
                          </span>
                        </div>
                      </div>
                    </button>
                  );
                })
              )}
            </div>

            {/* Pagination Controls */}
            {totalDirectoryPages > 1 && (
              <div className="p-2.5 border-t border-slate-200 dark:border-slate-800 flex items-center justify-between text-xs bg-slate-50/50 dark:bg-slate-900/40 rounded-b-xl">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={directoryPage === 1}
                  onClick={() => setDirectoryPage((p) => Math.max(1, p - 1))}
                  className="h-7 text-xs px-2"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                </Button>

                <span className="font-mono text-[11px] text-slate-500">
                  {directoryPage} / {totalDirectoryPages}
                </span>

                <Button
                  variant="outline"
                  size="sm"
                  disabled={directoryPage === totalDirectoryPages}
                  onClick={() => setDirectoryPage((p) => Math.min(totalDirectoryPages, p + 1))}
                  className="h-7 text-xs px-2"
                >
                  <ChevronRight className="w-3.5 h-3.5" />
                </Button>
              </div>
            )}
          </Card>
        </div>

        {/* RIGHT COLUMN: Selected Activity Header & Lifecycle History Workspace */}
        <div
          className={cn(
            'space-y-4 transition-all duration-300',
            isDetailExpanded ? 'lg:col-span-12' : 'lg:col-span-7 xl:col-span-8'
          )}
        >
          {isLoadingHistory ? (
            <div className="space-y-4">
              <Skeleton className="h-28 rounded-xl" />
              <Skeleton className="h-64 rounded-xl" />
              <Skeleton className="h-48 rounded-xl" />
            </div>
          ) : historyError ? (
            <ErrorState
              message={historyError}
              onRetry={() => {
                if (selectedActivityId) loadActivityDetails(selectedActivityId);
              }}
            />
          ) : !selectedActivityId || !activityMetadata ? (
            <Card className="border-dashed border-2 p-12 text-center text-slate-400">
              <Search className="w-8 h-8 mx-auto mb-2 opacity-30" />
              <p className="font-medium text-sm">{t('history.emptyPrompt')}</p>
            </Card>
          ) : (
            <>
              {/* Compact Activity Header Summary (Recommendation 14) */}
              <Card className="border-slate-200 dark:border-blue-900/50 bg-white dark:bg-[#001E60]/80 shadow-xs">
                <CardContent className="p-4 sm:p-5 space-y-3">
                  {/* Title & Core Identifiers */}
                  <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-2">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2 flex-wrap text-xs">
                        <span className="bg-[#FC4C02] text-white font-mono font-bold px-2 py-0.5 rounded">
                          {activityMetadata.activity_id}
                        </span>

                        <span className="font-mono text-xs uppercase font-semibold px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-slate-700">
                          {activityMetadata.discipline}
                        </span>

                        {activityMetadata.wbs_code && (
                          <span className="font-mono text-xs text-slate-500 dark:text-slate-400">
                            WBS: {activityMetadata.wbs_code}
                          </span>
                        )}

                        {activityMetadata.asset_tag && (
                          <span className="font-mono text-xs text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/40 px-1.5 py-0.5 rounded border border-blue-200 dark:border-blue-900/50">
                            Tag: {activityMetadata.asset_tag}
                          </span>
                        )}

                        <ExecutionStateBadge state={activityMetadata.execution_state} size="sm" />
                      </div>

                      <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-slate-100">
                        {activityMetadata.activity_name}
                      </h2>
                    </div>

                    {/* Precedence Ripple Impact Quick-Action */}
                    <div className="shrink-0">
                      <Button
                        asChild
                        variant="outline"
                        size="sm"
                        className="text-xs h-8 gap-1.5 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800 hover:bg-blue-50 dark:hover:bg-blue-950/40"
                      >
                        <RouterLink to={`/impact?activity_id=${activityMetadata.activity_id}`}>
                          <span>Ripple Impact</span>
                          <ArrowUpRight className="w-3.5 h-3.5" />
                        </RouterLink>
                      </Button>
                    </div>
                  </div>

                  {/* Metadata Specs Strip */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 border-t border-slate-100 dark:border-slate-800/80 text-xs font-mono">
                    <div>
                      <span className="text-[10px] text-slate-400 uppercase block">
                        {t('history.location')}
                      </span>
                      <strong className="text-slate-700 dark:text-slate-200 truncate block">
                        {activityMetadata.location}
                      </strong>
                    </div>

                    <div>
                      <span className="text-[10px] text-slate-400 uppercase block">
                        {t('history.plannedWindow')}
                      </span>
                      <span className="text-slate-700 dark:text-slate-200 block truncate">
                        {activityMetadata.planned_start} → {activityMetadata.planned_finish}
                      </span>
                    </div>

                    <div>
                      <span className="text-[10px] text-slate-400 uppercase block">
                        Baseline % / Qty
                      </span>
                      <span className="text-slate-700 dark:text-slate-200 block">
                        {activityMetadata.baseline_pct_complete}% · {activityMetadata.planned_quantity || 'N/A'} {activityMetadata.uom || ''}
                      </span>
                    </div>

                    <div>
                      <span className="text-[10px] text-slate-400 uppercase block">
                        Float & Criticality
                      </span>
                      <span className="text-slate-700 dark:text-slate-200 block">
                        {activityMetadata.total_float !== null ? `${activityMetadata.total_float}d float` : 'Unk float'}
                        {activityMetadata.is_critical && (
                          <span className="text-rose-500 font-bold ml-1">· ⚡ Critical</span>
                        )}
                      </span>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Event Sub-Tabs */}
              <div className="flex items-center gap-1.5 border-b border-slate-200 dark:border-slate-800 text-xs">
                <button
                  type="button"
                  onClick={() => setTimelineTab('all')}
                  className={cn(
                    'px-3 py-2 font-semibold border-b-2 transition-colors flex items-center gap-1.5',
                    timelineTab === 'all'
                      ? 'border-[#FC4C02] text-[#FC4C02]'
                      : 'border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
                  )}
                >
                  <CalendarDays className="w-3.5 h-3.5" />
                  {t('history.tabAllEvents')} ({timeline.length})
                </button>

                <button
                  type="button"
                  onClick={() => setTimelineTab('claims')}
                  className={cn(
                    'px-3 py-2 font-semibold border-b-2 transition-colors flex items-center gap-1.5',
                    timelineTab === 'claims'
                      ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                      : 'border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
                  )}
                >
                  <FileText className="w-3.5 h-3.5" />
                  {t('history.tabClaims')} ({claimCount})
                </button>

                <button
                  type="button"
                  onClick={() => setTimelineTab('decisions')}
                  className={cn(
                    'px-3 py-2 font-semibold border-b-2 transition-colors flex items-center gap-1.5',
                    timelineTab === 'decisions'
                      ? 'border-emerald-500 text-emerald-600 dark:text-emerald-400'
                      : 'border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
                  )}
                >
                  <User className="w-3.5 h-3.5" />
                  {t('history.tabDecisions')} ({decisionCount})
                </button>

                <button
                  type="button"
                  onClick={() => setTimelineTab('actuals')}
                  className={cn(
                    'px-3 py-2 font-semibold border-b-2 transition-colors flex items-center gap-1.5',
                    timelineTab === 'actuals'
                      ? 'border-purple-500 text-purple-600 dark:text-purple-400'
                      : 'border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
                  )}
                >
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  {t('history.tabActuals')} ({actualCount})
                </button>

                <button
                  type="button"
                  onClick={() => setTimelineTab('audit')}
                  className={cn(
                    'px-3 py-2 font-semibold border-b-2 transition-colors flex items-center gap-1.5 ml-auto',
                    timelineTab === 'audit'
                      ? 'border-status-approved text-status-approved'
                      : 'border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
                  )}
                >
                  <ShieldCheck className="w-3.5 h-3.5" />
                  {t('history.tabAudit')} ({auditLogs.length})
                </button>
              </div>

              {/* Substantiated Vertical Lifecycle Timeline (when tab != audit) */}
              {timelineTab !== 'audit' && (
                <Card className="bg-white dark:bg-[#001E60]/80 border-slate-200 dark:border-blue-900/50 shadow-xs">
                  <CardContent className="pt-6">
                    {filteredTimeline.length === 0 ? (
                      <div className="text-center py-10 text-slate-400 dark:text-slate-500 text-xs space-y-2">
                        <GitCommit className="w-8 h-8 mx-auto opacity-30" />
                        <p className="font-semibold text-slate-600 dark:text-slate-300">
                          {timeline.length === 0
                            ? 'No lifecycle events recorded for this activity.'
                            : 'No events found matching this filter.'}
                        </p>
                        <p className="text-[11px] text-slate-400">
                          {timeline.length === 0
                            ? 'This activity exists in the schedule but has no field claims, decisions, or committed actuals yet.'
                            : 'Select "All Events" to view all lifecycle updates.'}
                        </p>
                      </div>
                    ) : (
                      <div className="relative pl-6 border-l-2 border-slate-200 dark:border-blue-900/50 space-y-6">
                        {filteredTimeline.map((item, idx) => {
                          const isEvent = item.type === 'execution_event';
                          const isDecision = item.type === 'planner_decision';
                          const isActual = item.type === 'approved_actual';

                          return (
                            <div key={idx} className="relative group">
                              <div
                                className={cn(
                                  'absolute -left-[31px] top-1.5 w-4 h-4 rounded-full border-4 border-white dark:border-[#001E60] shadow transition-transform group-hover:scale-125',
                                  isActual
                                    ? 'bg-purple-600'
                                    : isDecision
                                    ? item.action === 'APPROVE'
                                      ? 'bg-emerald-500'
                                      : item.action === 'REJECT'
                                      ? 'bg-rose-500'
                                      : 'bg-amber-500'
                                    : 'bg-blue-500'
                                )}
                              />

                              <div className="p-3.5 rounded-xl bg-slate-50 dark:bg-slate-950/60 border border-slate-200 dark:border-slate-800 hover:border-[#FC4C02]/40 transition-colors space-y-2 text-xs">
                                <div className="flex items-center justify-between flex-wrap gap-2">
                                  <div className="flex items-center gap-2 flex-wrap">
                                    <span className="inline-flex items-center gap-1.5 font-mono font-bold text-slate-900 dark:text-slate-200">
                                      {isEvent && (
                                        <>
                                          <FileText className="w-3.5 h-3.5 text-blue-500" />
                                          <span>Field Claim / Extraction ({item.event_id})</span>
                                        </>
                                      )}
                                      {isDecision && (
                                        <>
                                          <User className="w-3.5 h-3.5 text-emerald-500" />
                                          <span>Supervisor Decision ({item.decision_id ? item.decision_id.slice(0, 8) : 'DEC'})</span>
                                        </>
                                      )}
                                      {isActual && (
                                        <>
                                          <CheckCircle2 className="w-3.5 h-3.5 text-purple-500" />
                                          <span>Approved Actual Committed ({item.actual_id ? item.actual_id.slice(0, 8) : 'ACTL'})</span>
                                        </>
                                      )}
                                    </span>

                                    {isEvent && item.status && (
                                      <StatusBadge status={item.status} size="sm" />
                                    )}

                                    {isDecision && item.action && (
                                      <span
                                        className={cn(
                                          'px-2 py-0.5 rounded text-[10px] font-bold font-mono',
                                          item.action === 'APPROVE'
                                            ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300'
                                            : item.action === 'REJECT'
                                            ? 'bg-rose-100 text-rose-800 dark:bg-rose-950/40 dark:text-rose-300'
                                            : 'bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300'
                                        )}
                                      >
                                        ACTION: {item.action}
                                      </span>
                                    )}
                                  </div>

                                  <span className="text-[11px] font-mono text-slate-400 dark:text-slate-500 flex items-center gap-1">
                                    <Clock className="w-3 h-3" />
                                    {item.timestamp ? new Date(item.timestamp).toLocaleString() : 'Timestamp TBD'}
                                  </span>
                                </div>

                                {isEvent && item.raw_claim_text && (
                                  <p className="text-slate-700 dark:text-slate-200 font-medium leading-relaxed bg-slate-100 dark:bg-slate-900/60 rounded-lg p-2.5 border border-slate-200 dark:border-slate-800 italic">
                                    "{item.raw_claim_text}"
                                  </p>
                                )}

                                {isDecision && (
                                  <div className="p-2.5 bg-emerald-50/60 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-900/40 rounded-lg space-y-1">
                                    <div className="flex items-center justify-between text-[11px]">
                                      <span className="font-semibold text-emerald-900 dark:text-emerald-200">
                                        Supervisor Authority: {item.planner_id || 'Supervisor'}
                                      </span>
                                      <span className="font-mono font-bold text-slate-800 dark:text-slate-200">
                                        Approved: {item.approved_pct !== null ? `${item.approved_pct}%` : ''}{' '}
                                        {item.approved_qty ? `(${item.approved_qty} qty)` : ''}
                                      </span>
                                    </div>
                                    {item.justification && (
                                      <p className="text-slate-600 dark:text-slate-300 text-[11px] mt-0.5">
                                        <strong>Justification:</strong> {item.justification}
                                      </p>
                                    )}
                                  </div>
                                )}

                                {isActual && (
                                  <div className="p-2.5 bg-purple-50/60 dark:bg-purple-950/20 border border-purple-200 dark:border-purple-900/40 rounded-lg space-y-1 font-mono text-[11px]">
                                    <div className="flex justify-between items-center">
                                      <span className="text-slate-600 dark:text-slate-400">Actual Start:</span>
                                      <span className="font-bold text-slate-800 dark:text-slate-200">
                                        {item.actual_start || 'N/A'}
                                      </span>
                                    </div>
                                    <div className="flex justify-between items-center">
                                      <span className="text-slate-600 dark:text-slate-400">Actual Finish:</span>
                                      <span className="font-bold text-slate-800 dark:text-slate-200">
                                        {item.actual_finish || 'In Progress'}
                                      </span>
                                    </div>
                                    <div className="flex justify-between items-center pt-1 border-t border-purple-200/50 dark:border-purple-900/30">
                                      <span className="text-slate-600 dark:text-slate-400">Committed Actual %:</span>
                                      <span className="font-bold text-purple-700 dark:text-purple-300">
                                        {item.actual_pct_complete ?? 100}%
                                      </span>
                                    </div>
                                  </div>
                                )}

                                {/* Expandable Traceable Source Evidence */}
                                {isEvent && item.source_references && item.source_references.length > 0 && (
                                  <div className="pt-1.5">
                                    <button
                                      type="button"
                                      onClick={() => toggleEvidence(item.event_id || String(idx))}
                                      className="flex items-center gap-1 text-[11px] font-semibold text-blue-600 dark:text-blue-400 hover:text-blue-700 cursor-pointer"
                                    >
                                      <LinkIcon className="w-3.5 h-3.5" />
                                      <span>
                                        {item.source_references.length} Traceable Source Reference
                                        {item.source_references.length !== 1 ? 's' : ''}
                                      </span>
                                      {expandedEvidence[item.event_id || String(idx)] ? (
                                        <ChevronUp className="w-3.5 h-3.5" />
                                      ) : (
                                        <ChevronDown className="w-3.5 h-3.5" />
                                      )}
                                    </button>

                                    {expandedEvidence[item.event_id || String(idx)] && (
                                      <div className="mt-2 space-y-2 p-2.5 rounded-lg bg-blue-50/50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-900/40 text-[11px]">
                                        {item.source_references.map((ref, rIdx) => (
                                          <div
                                            key={rIdx}
                                            className="space-y-0.5 border-b border-blue-200/40 last:border-0 pb-1.5 last:pb-0"
                                          >
                                            <div className="flex items-center justify-between text-[10px] font-mono font-semibold text-slate-700 dark:text-slate-300">
                                              <span>{ref.file_name}</span>
                                              <span>
                                                {ref.sheet_name
                                                  ? `${ref.sheet_name} · ${ref.row_cell_ref}`
                                                  : ref.row_cell_ref}
                                              </span>
                                            </div>
                                            {ref.raw_snippet && (
                                              <p className="text-slate-600 dark:text-slate-400 italic font-mono text-[10px]">
                                                "{ref.raw_snippet}"
                                              </p>
                                            )}
                                          </div>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                )}

                                {/* Evidence Photo Thumbnail / Context */}
                                {isEvent && item.event_id && (
                                  <TimelinePhoto eventId={item.event_id} photoPath={item.photo_path} />
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </CardContent>
                </Card>
              )}

              {/* Cryptographic Audit Trail (visible on 'all' or 'audit' tab) */}
              {(timelineTab === 'all' || timelineTab === 'audit') && (
                <Card className="bg-white dark:bg-[#001E60]/80 border-slate-200 dark:border-blue-900/50 shadow-xs">
                  <CardHeader className="pb-3 border-b border-slate-200 dark:border-blue-900/50">
                    <CardTitle className="text-sm font-semibold flex items-center justify-between">
                      <span className="flex items-center gap-2 text-slate-900 dark:text-slate-100">
                        <ShieldCheck className="w-4 h-4 text-emerald-500" />
                        {t('history.auditTrailTitle')}
                      </span>
                      <span className="text-[10px] bg-status-approved/10 text-status-approved border border-status-approved/20 px-2 py-0.5 rounded-full font-mono font-semibold">
                        {t('history.hashChain')}
                      </span>
                    </CardTitle>
                    <CardDescription className="text-slate-500 dark:text-slate-400 text-xs">
                      {t('history.auditTrailDesc')}
                    </CardDescription>
                  </CardHeader>

                  <CardContent className="pt-4 space-y-3 font-mono text-[11px]">
                    {auditLogs.length === 0 ? (
                      <EmptyState
                        icon={Hash}
                        title={t('history.noAuditLogs')}
                        className="py-6"
                      />
                    ) : (
                      <>
                        {paginatedAuditLogs.map((log) => (
                          <div
                            key={log.log_id}
                            className="p-3 bg-card border border-border rounded-xl space-y-2 hover:border-status-approved/50 transition-colors shadow-xs"
                          >
                            <div className="flex items-center justify-between text-muted-foreground flex-wrap gap-1">
                              <span className="font-bold text-foreground flex items-center gap-1.5">
                                <Hash className="w-3 h-3 text-status-approved" />
                                {t('history.log')} {log.log_id} · {log.action}
                              </span>
                              <span className="text-muted-foreground text-[10px]">
                                {new Date(log.timestamp).toLocaleString()}
                              </span>
                            </div>

                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[10px]">
                              <div className="truncate p-2 bg-card-subtle rounded-lg border border-border">
                                <span className="text-muted-foreground block mb-0.5">
                                  {t('history.prevHash')}
                                </span>
                                <span className="text-muted-foreground">{log.previous_hash}</span>
                              </div>

                              <div className="truncate p-2 bg-card-subtle rounded-lg border border-border">
                                <span className="text-muted-foreground block mb-0.5">
                                  {t('history.currHash')}
                                </span>
                                <span className="text-primary font-bold">{log.current_hash}</span>
                              </div>
                            </div>
                          </div>
                        ))}

                        {auditLogs.length > TIMELINE_PAGE_SIZE && (
                          <div className="flex items-center justify-between border-t border-border pt-4 mt-4 text-xs text-muted-foreground">
                            <span>
                              {t('history.auditLogsCount', {
                                shown: `${(auditLogsPage - 1) * TIMELINE_PAGE_SIZE + 1}–${Math.min(
                                  auditLogsPage * TIMELINE_PAGE_SIZE,
                                  auditLogs.length
                                )}`,
                                total: auditLogs.length,
                              })}
                            </span>

                            <div className="flex items-center gap-2">
                              <Button
                                variant="outline"
                                size="sm"
                                disabled={auditLogsPage === 1}
                                onClick={() => setAuditLogsPage((p) => Math.max(1, p - 1))}
                                className="h-7 text-xs gap-1"
                              >
                                <ChevronLeft className="w-3.5 h-3.5" />
                                {t('common.previous')}
                              </Button>

                              <span className="font-mono px-2 font-medium text-foreground">
                                {auditLogsPage} / {totalAuditPages}
                              </span>

                              <Button
                                variant="outline"
                                size="sm"
                                disabled={auditLogsPage === totalAuditPages}
                                onClick={() => setAuditLogsPage((p) => Math.min(totalAuditPages, p + 1))}
                                className="h-7 text-xs gap-1"
                              >
                                {t('common.next')}
                                <ChevronRight className="w-3.5 h-3.5" />
                              </Button>
                            </div>
                          </div>
                        )}
                      </>
                    )}
                  </CardContent>
                </Card>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}