import React, { useEffect, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  dashboardApi,
  decisionsApi,
  digestApi,
  executionSummaryApi,
  impactApi,
  CompoundImpact,
  ExecutionSummaryResponse,
  ScheduleActivity,
  PlannerDecision,
  DisciplineForecastData,
  DisciplineForecastItem,
  ExecutionEvent,
} from '@/api';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  TrendingDown,
  TrendingUp,
  Filter,
  CheckCircle2,
  Layers,
  Clock,
  RefreshCw,
  ArrowUpRight,
  ShieldCheck,
  Download,
  BrainCircuit,
  FileSpreadsheet,
  XCircle,
  BarChart3,
  Loader2,
  Sparkles,
  Globe2,
  Server,
  Building2,
  Calendar,
  Lock,
  Upload,
  ArrowRight,
  FolderTree,
  GitFork,
} from 'lucide-react';
import { P6SyncStagingModal } from '@/components/P6SyncStagingModal';
import { ScheduleImportModal } from '@/components/ScheduleImportModal';
import { CompoundImpactModal } from '@/components/CompoundImpactModal';
import { useProject } from '@/context/ProjectContext';
import { useAuth } from '@/auth/AuthProvider';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from '@/components/ui/card';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  PieChart,
  Pie,
} from 'recharts';
import { cn } from '@/lib/utils';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorState } from '@/components/ui/error-state';

const CHART_COLORS = {
  indigo: '#14B8A6',
  blue: '#22D3EE',
  amber: '#f59e0b',
  violet: '#818cf8',
  emerald: '#10b981',
  orange: '#FF7A18',
  navy: '#0A2340',
};

const DISCIPLINE_COLORS: Record<string, string> = {
  CIVIL: CHART_COLORS.orange,
  PIPING: CHART_COLORS.blue,
  ELECTRICAL: CHART_COLORS.amber,
  INSTRUMENTATION: CHART_COLORS.violet,
  HSE: CHART_COLORS.emerald,
  STATIC_ROTATING_EQUIPMENT: CHART_COLORS.navy,
  UNASSIGNED: '#94a3b8',
};

export default function Dashboard() {
  const { t } = useTranslation();

  const [summary, setSummary] = useState<{
    total_claims: number;
    pending_review: number;
    actuals: number;
    conflicts: number;
    discipline_breakdown: {
      discipline: string;
      name: string;
      count: number;
      value: number;
    }[];
    claims_trend_pct: number | null;
  } | null>(null);

  const [delayReasons, setDelayReasons] = useState<
    { reason: string; count: number }[]
  >([]);

  const [institutionalMemory, setInstitutionalMemory] = useState<
    { topic: string; resolution: string; count: number }[]
  >([]);

  const [forecastData, setForecastData] =
    useState<DisciplineForecastData | null>(null);

  const [selectedDiscipline, setSelectedDiscipline] =
    useState<string>('CIVIL');

  const [isForecastLoading, setIsForecastLoading] =
    useState<boolean>(false);

  const [silentActivities, setSilentActivities] = useState<
    ScheduleActivity[]
  >([]);

  const [recentDecisions, setRecentDecisions] = useState<
    PlannerDecision[]
  >([]);

  const [claims, setClaims] = useState<ExecutionEvent[]>([]);

  const [scheduleImpacts, setScheduleImpacts] = useState<CompoundImpact[]>([]);
  const [selectedImpact, setSelectedImpact] = useState<CompoundImpact | null>(null);
  const [isImpactModalOpen, setIsImpactModalOpen] = useState<boolean>(false);

  const [isLoading, setIsLoading] = useState<boolean>(true);

  const [error, setError] = useState<string | null>(null);

  const [isExporting, setIsExporting] = useState<boolean>(false);

  const [exportError, setExportError] = useState<string | null>(null);

  const { currentProject, currentScheduleVersion, setSelectedStageId } = useProject();
  const navigate = useNavigate();
  const { user } = useAuth();
  const isSupervisor = user?.role === 'SUPERVISOR';
  const [isP6StagingOpen, setIsP6StagingOpen] = useState<boolean>(false);
  const [isScheduleImportOpen, setIsScheduleImportOpen] = useState<boolean>(false);

  // Phase 7: AI Execution Summary & Dynamic Translation
  const [execSummary, setExecSummary] =
    useState<ExecutionSummaryResponse | null>(null);

  const [summaryPeriod, setSummaryPeriod] = useState<
    'last_7_days' | 'this_month' | 'custom'
  >('last_7_days');

  const [summaryStartDate, setSummaryStartDate] =
    useState<string>('');

  const [summaryEndDate, setSummaryEndDate] =
    useState<string>('');

  const [summaryDiscipline, setSummaryDiscipline] =
    useState<string>('ALL');

  const [summaryLanguage, setSummaryLanguage] =
    useState<'en' | 'hi' | 'te'>('en');

  const [isSummaryLoading, setIsSummaryLoading] =
    useState<boolean>(false);

  const handleDisciplineChange = async (discipline: string) => {
    setSelectedDiscipline(discipline);
    setIsForecastLoading(true);

    try {
      const data = await dashboardApi.getForecast(discipline);
      setForecastData(data);
    } catch (err) {
      console.error(
        'Failed to load forecast for discipline:',
        discipline,
        err
      );
    } finally {
      setIsForecastLoading(false);
    }
  };

  const loadExecutionSummary = async (
    period = summaryPeriod,
    discipline = summaryDiscipline,
    language = summaryLanguage,
    startDate = summaryStartDate,
    endDate = summaryEndDate
  ) => {
    setIsSummaryLoading(true);

    try {
      const data = await executionSummaryApi.getSummary({
        period,
        discipline,
        language,
        start_date:
          period === 'custom' ? startDate : undefined,
        end_date:
          period === 'custom' ? endDate : undefined,
      });

      setExecSummary(data);
    } catch (e) {
      console.error(
        'Failed to load execution summary:',
        e
      );
    } finally {
      setIsSummaryLoading(false);
    }
  };

  const loadDashboardData = async () => {
    setIsLoading(true);
    setError(null);

    try {
      const [
        sum,
        reasons,
        memory,
        fc,
        silent,
        decisions,
        allClaims,
        impacts,
      ] = await Promise.all([
        dashboardApi
          .getSummary()
          .catch(() => ({
            total_claims: 0,
            pending_review: 0,
            actuals: 0,
            conflicts: 0,
            discipline_breakdown: [],
            claims_trend_pct: null,
          })),

        dashboardApi.getDelayReasons(),

        dashboardApi.getInstitutionalMemory(),

        dashboardApi.getForecast(selectedDiscipline),

        dashboardApi.getSilentActivities(),

        decisionsApi.getRecent(),

        digestApi.getAll(),

        impactApi.getScheduleImpacts(currentProject.id, currentScheduleVersion.id).catch(() => []),
      ]);

      setSummary(sum);
      setDelayReasons(reasons);
      setInstitutionalMemory(memory);
      setForecastData(fc);
      setSilentActivities(silent);
      setRecentDecisions(decisions);
      setClaims(allClaims || []);
      setScheduleImpacts(impacts || []);
    } catch (e: unknown) {
      const msg =
        e instanceof Error
          ? e.message
          : 'Failed to load dashboard data';

      setError(msg);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadDashboardData();
    loadExecutionSummary();
  }, [currentProject.id, currentScheduleVersion.id]);

  const handleExportCsv = async () => {
    setIsExporting(true);
    setExportError(null);

    try {
      const blob = await dashboardApi.exportCsv();

      const url = window.URL.createObjectURL(blob);

      const a = document.createElement('a');
      a.href = url;
      a.download = 'approved_actuals.csv';

      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);

      window.URL.revokeObjectURL(url);
    } catch (e: unknown) {
      const msg =
        e instanceof Error
          ? e.message
          : 'Failed to export CSV';

      setExportError(msg);
    } finally {
      setIsExporting(false);
    }
  };

  const disciplineData = useMemo(() => {
    if (claims && claims.length > 0) {
      const counts: Record<string, number> = {};

      claims.forEach((c) => {
        const d = c.discipline || 'UNASSIGNED';
        counts[d] = (counts[d] || 0) + 1;
      });

      return Object.entries(counts).map(
        ([name, value]) => ({
          name,
          value,
          fill:
            DISCIPLINE_COLORS[name] ||
            CHART_COLORS.indigo,
        })
      );
    }

    if (summary?.discipline_breakdown?.length) {
      const disciplinePalette = [
        CHART_COLORS.orange,
        CHART_COLORS.blue,
        CHART_COLORS.amber,
        CHART_COLORS.violet,
        CHART_COLORS.emerald,
      ];

      return summary.discipline_breakdown.map(
        (item: any, idx: number) => ({
          name: item.name || item.discipline,
          value: item.value ?? item.count,
          fill:
            disciplinePalette[
              idx % disciplinePalette.length
            ],
        })
      );
    }

    return [
      {
        name: 'CIVIL',
        value: 0,
        fill: CHART_COLORS.orange,
      },
    ];
  }, [claims, summary]);

  const kpiCards = useMemo(() => {
    const totalClaimsCount =
      claims.length ||
      summary?.total_claims ||
      0;

    const pendingReviewCount = claims.length
      ? claims.filter(
          (c) =>
            c.status === 'REVIEW_REQUIRED' ||
            c.status === 'VALIDATED'
        ).length
      : summary?.pending_review || 0;

    const actualsCommittedCount = claims.length
      ? claims.filter(
          (c) =>
            c.status === 'APPROVED' ||
            c.status === 'EDITED'
        ).length
      : summary?.actuals || 0;

    const openConflictsCount =
      summary?.conflicts != null
        ? summary.conflicts
        : t('review.notAvailable');

    return [
      {
        label: t('dashboard.kpiTotalClaims', {
          defaultValue: t(
            'dashboard.totalClaims',
            {
              defaultValue:
                'Total Claims Ingested',
            }
          ),
        }),
        value: isLoading
          ? '...'
          : String(totalClaimsCount),
        delta: isLoading
          ? ''
          : summary?.claims_trend_pct == null
          ? t('dashboard.kpiTotalClaimsDeltaUnavailable', {
              defaultValue: 'No previous-period data',
            })
          : t('dashboard.kpiTotalClaimsDelta', {
              pct: summary.claims_trend_pct > 0 ? `+${summary.claims_trend_pct}` : String(summary.claims_trend_pct),
              defaultValue: `${summary.claims_trend_pct > 0 ? '+' : ''}${summary.claims_trend_pct}% vs last week`,
            }),
        deltaPositive: summary?.claims_trend_pct == null ? null : summary.claims_trend_pct > 0,
        icon: FileSpreadsheet,
        accent:
          'text-[#14B8A6] dark:text-[#22D3EE]',
        bg:
          'bg-teal-50 dark:bg-[#0A2340]',
        border:
          'border-teal-200 dark:border-[#1E3A5F]',
      },

      {
        label: t('dashboard.kpiPendingReview', {
          defaultValue: t(
            'dashboard.pendingReview',
            {
              defaultValue:
                'Pending Supervisor Review',
            }
          ),
        }),
        value: isLoading
          ? '...'
          : String(pendingReviewCount),
        delta: t(
          'dashboard.kpiPendingReviewDelta',
          {
            defaultValue:
              'Action required',
          }
        ),
        deltaPositive: null,
        icon: Clock,
        accent:
          'text-amber-600 dark:text-amber-400',
        bg:
          'bg-amber-50 dark:bg-amber-950/60',
        border:
          'border-amber-200 dark:border-amber-900/60',
      },

      {
        label: t(
          'dashboard.kpiActualsCommitted',
          {
            defaultValue: t(
              'dashboard.approvedActuals',
              {
                defaultValue:
                  'Approved Actuals',
              }
            ),
          }
        ),
        value: isLoading
          ? '...'
          : String(actualsCommittedCount),
        delta: t(
          'dashboard.kpiActualsCommittedDelta',
          {
            defaultValue:
              'P6 Sync ready',
          }
        ),
        deltaPositive: true,
        icon: CheckCircle2,
        accent:
          'text-emerald-600 dark:text-emerald-400',
        bg:
          'bg-emerald-50 dark:bg-emerald-950/60',
        border:
          'border-emerald-200 dark:border-emerald-900/60',
      },

      {
        label: t(
          'dashboard.kpiOpenConflicts',
          {
            defaultValue: t(
              'dashboard.activeConflicts',
              {
                defaultValue:
                  'Active Conflicts',
              }
            ),
          }
        ),
        value: isLoading
          ? '...'
          : String(openConflictsCount),
        delta: t(
          'dashboard.kpiOpenConflictsDelta',
          {
            defaultValue:
              'Flagged for review',
          }
        ),
        deltaPositive: false,
        icon: XCircle,
        accent:
          'text-rose-600 dark:text-rose-400',
        bg:
          'bg-rose-50 dark:bg-rose-950/60',
        border:
          'border-rose-200 dark:border-rose-900/60',
      },
    ];
  }, [claims, summary, isLoading, t]);

  return (
    <div className="space-y-6">
      {/* Top Header Toolbar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-300 dark:border-[#214766]/60 pb-4">
        <div>
          <h1 className="text-2xl font-extrabold text-[#071A2D] dark:text-[#F5F7FA] tracking-tight flex items-center gap-2">
            <div className="p-1.5 rounded-lg bg-[#FF7A18]/10 border border-[#FF7A18]/20">
              <BarChart3 className="w-5 h-5 text-[#FF7A18]" />
            </div>

            {t('dashboard.title')}
          </h1>

          <p className="text-[#334155] dark:text-[#CBD5E1] text-xs font-semibold mt-1">
            {t('dashboard.subtitle')}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            onClick={() => {
              loadDashboardData();
              loadExecutionSummary();
            }}
            variant="outline"
            disabled={isLoading}
            className="border-slate-300 dark:border-[#1E3A5F] text-foreground hover:bg-secondary h-9 text-xs gap-1.5"
          >
            <RefreshCw
              className={cn(
                'w-3.5 h-3.5',
                isLoading && 'animate-spin'
              )}
            />

            {t('dashboard.refresh')}
          </Button>

          <Button
            onClick={() => setIsP6StagingOpen(true)}
            variant="outline"
            className="border-blue-300 dark:border-blue-800 text-blue-700 dark:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-950/40 h-9 text-xs font-bold gap-1.5 shadow-2xs"
          >
            <Server className="w-3.5 h-3.5" />
            P6 / PMIS Sync Staging
          </Button>

          <Button
            onClick={handleExportCsv}
            disabled={isExporting}
            className="bg-gradient-to-r from-[#FF7A18] to-[#FF941F] hover:from-[#E06810] hover:to-[#FF7A18] text-white font-semibold text-xs h-9 shadow-md shadow-orange-500/25 gap-1.5"
          >
            {isExporting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Download className="w-4 h-4" />
            )}

            {t('dashboard.exportCsv')}
          </Button>
        </div>
      </div>

      {/* Error Banners */}
      {error && (
        <ErrorState
          message={error}
          onRetry={loadDashboardData}
          retryText={t('common.retry')}
        />
      )}

      {exportError && (
        <ErrorState
          message={exportError}
          onRetry={handleExportCsv}
          retryText={t('common.retry')}
        />
      )}

      {/* V7 Project-Centric Context & Stage Milestone Structure Card */}
      <Card className="border-slate-300 dark:border-[#1E3A5F] bg-white/98 dark:bg-[#071B2D]/95 shadow-md rounded-2xl overflow-hidden">
        <div className="bg-gradient-to-r from-[#002266] via-[#003388] to-[#001D5E] dark:from-[#061526] dark:via-[#071B2D] dark:to-[#0A2238] p-5 text-white border-b border-slate-200 dark:border-[#1E3A5F]/60">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-xs font-mono font-bold text-orange-400">
                <Building2 className="w-4 h-4 text-[#FF7A18]" />
                <span>ENTERPRISE PROJECT CONTEXT</span>
                <span>·</span>
                <span className="bg-white/10 px-2 py-0.5 rounded text-white">{currentProject.id}</span>
                <span>·</span>
                <span className="text-white/80">{currentProject.region}</span>
              </div>
              <h2 className="text-xl font-black tracking-tight text-white flex items-center gap-2">
                {currentProject.name}
              </h2>
              <div className="flex flex-wrap items-center gap-3 text-xs text-white/80 pt-1">
                <span>Client: <strong className="text-white">{currentProject.client}</strong></span>
                <span>•</span>
                <span>Budget: <strong className="text-white font-mono">{currentProject.totalBudget}</strong></span>
                <span>•</span>
                <span>Active Stage: <strong className="text-amber-300">{currentProject.activeStage}</strong> ({currentProject.totalStages} Stages Total)</span>
              </div>
            </div>

            {/* Schedule Version Pill & Import Trigger */}
            <div className="flex flex-col sm:flex-row items-start sm:items-center gap-2.5 bg-black/20 dark:bg-[#04101E]/60 p-3 rounded-xl border border-white/10 shrink-0">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-blue-500/20 border border-blue-400/40 text-blue-300">
                  <Calendar className="w-4 h-4" />
                </div>
                <div className="flex flex-col">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-bold text-white font-mono">{currentScheduleVersion.versionNumber}</span>
                    <span className="text-[10px] font-mono font-bold px-1.5 py-0.2 rounded bg-emerald-500/20 border border-emerald-400/40 text-emerald-300">
                      {currentScheduleVersion.status}
                    </span>
                  </div>
                  <span className="text-[10px] text-white/70 truncate max-w-[200px]">
                    {currentScheduleVersion.name}
                  </span>
                </div>
              </div>

              {isSupervisor && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setIsScheduleImportOpen(true)}
                  className="h-8 text-[11px] font-bold border-white/20 hover:bg-white/10 text-white gap-1.5 shrink-0"
                >
                  <Upload className="w-3.5 h-3.5 text-[#FF7A18]" />
                  <span>Import Version</span>
                </Button>
              )}
            </div>
          </div>

          {/* Progress & Variance Bar */}
          <div className="mt-5 pt-4 border-t border-white/10 grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="space-y-1.5 md:col-span-2">
              <div className="flex items-center justify-between text-xs font-semibold">
                <span>Overall Physical Progress</span>
                <span className="font-mono font-bold text-amber-300">
                  {currentProject.overallActual}% Actual / {currentProject.overallPlanned}% Planned
                </span>
              </div>
              <div className="h-3 w-full rounded-full bg-white/20 overflow-hidden relative">
                <div
                  className="h-full bg-gradient-to-r from-blue-400 to-blue-500 rounded-full transition-all absolute top-0 left-0 opacity-40"
                  style={{ width: `${currentProject.overallPlanned}%` }}
                  title={`Planned: ${currentProject.overallPlanned}%`}
                />
                <div
                  className="h-full bg-gradient-to-r from-[#FF7A18] to-[#FF941F] rounded-full transition-all absolute top-0 left-0 shadow-sm"
                  style={{ width: `${currentProject.overallActual}%` }}
                  title={`Actual: ${currentProject.overallActual}%`}
                />
              </div>
              <div className="flex items-center justify-between text-[10px] text-white/70 font-mono">
                <span>0%</span>
                <span className={cn('font-bold', currentProject.variance < 0 ? 'text-rose-300' : 'text-emerald-300')}>
                  Variance: {currentProject.variance > 0 ? `+${currentProject.variance}` : currentProject.variance}%
                </span>
                <span>100% Complete</span>
              </div>
            </div>

            <div className="bg-white/10 dark:bg-white/5 p-2.5 rounded-xl border border-white/10 flex items-center justify-between text-xs">
              <div>
                <span className="text-[10px] text-white/70 block uppercase font-mono">Critical Activities</span>
                <span className="text-base font-bold font-mono text-rose-300">{currentProject.criticalActivities} Path Items</span>
              </div>
              <AlertTriangle className="w-5 h-5 text-rose-300 opacity-80" />
            </div>

            <div className="bg-white/10 dark:bg-white/5 p-2.5 rounded-xl border border-white/10 flex items-center justify-between text-xs">
              <div>
                <span className="text-[10px] text-white/70 block uppercase font-mono">Quality Holds</span>
                <span className="text-base font-bold font-mono text-amber-300">{currentProject.qualityHolds} ITP Gates</span>
              </div>
              <ShieldCheck className="w-5 h-5 text-amber-300 opacity-80" />
            </div>
          </div>
        </div>

        {/* Project Stage / Milestone Breakdown */}
        <CardContent className="p-5 space-y-3 bg-slate-50/50 dark:bg-[#0A2238]/40">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-foreground flex items-center gap-1.5 uppercase tracking-wider font-mono">
              <Layers className="w-4 h-4 text-primary" />
              Project Stage & Milestone Hierarchy
            </span>
            <Link to="/wbs" className="text-xs text-primary font-bold hover:underline flex items-center gap-1">
              <span>Explore Full WBS</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            {currentProject.stages.map((stage) => {
              const isCompleted = stage.status === 'COMPLETED';
              const isActive = stage.status === 'ACTIVE';
              return (
                <Link
                  key={stage.id}
                  to={`/wbs?stage=${stage.id}`}
                  onClick={() => setSelectedStageId(stage.id)}
                  className={cn(
                    'p-3.5 rounded-xl border transition-all text-xs flex flex-col justify-between space-y-2 group cursor-pointer shadow-2xs',
                    isActive
                      ? 'bg-orange-50/70 dark:bg-orange-950/30 border-[#FF7A18] shadow-orange-500/10'
                      : isCompleted
                      ? 'bg-emerald-50/50 dark:bg-emerald-950/20 border-emerald-300 dark:border-emerald-900/60'
                      : 'bg-white dark:bg-[#071B2D] border-slate-200 dark:border-[#1E3A5F] hover:border-slate-400'
                  )}
                >
                  <div className="flex items-start justify-between gap-1.5">
                    <span className="font-bold text-[#071A2D] dark:text-[#F5F7FA] line-clamp-1 group-hover:text-[#FF7A18] transition-colors">
                      {stage.name}
                    </span>
                    <span
                      className={cn(
                        'text-[9px] font-mono font-bold px-1.5 py-0.2 rounded shrink-0',
                        isCompleted
                          ? 'bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200'
                          : isActive
                          ? 'bg-orange-100 dark:bg-orange-900 text-orange-800 dark:text-orange-200'
                          : 'bg-slate-100 dark:bg-slate-800 text-muted-foreground'
                      )}
                    >
                      {stage.status}
                    </span>
                  </div>

                  <div className="space-y-1">
                    <div className="flex items-center justify-between text-[11px] font-mono">
                      <span className="text-muted-foreground">Progress:</span>
                      <span className="font-bold text-foreground">
                        {stage.actualPct}% <span className="text-muted-foreground font-normal">/ {stage.plannedPct}%</span>
                      </span>
                    </div>
                    <div className="h-1.5 w-full bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
                      <div
                        className={cn(
                          'h-full rounded-full',
                          isCompleted ? 'bg-emerald-500' : isActive ? 'bg-[#FF7A18]' : 'bg-blue-500'
                        )}
                        style={{ width: `${stage.actualPct}%` }}
                      />
                    </div>
                  </div>

                  <div className="flex items-center justify-between text-[10px] text-muted-foreground pt-1 border-t border-slate-200/60 dark:border-[#1E3A5F]/60">
                    <span className="font-mono">{stage.activitiesCount} Activities</span>
                    <span className="text-primary font-bold group-hover:translate-x-0.5 transition-transform">
                      View WBS →
                    </span>
                  </div>
                </Link>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Embedded Schedule Ingestion Modal */}
      <ScheduleImportModal
        isOpen={isScheduleImportOpen}
        onClose={() => setIsScheduleImportOpen(false)}
      />

      {/* Phase 7: AI Execution Summary & Dynamic Translation Panel */}
      <Card className="bg-gradient-to-br from-white to-blue-50/40 dark:from-[#001E60]/90 dark:to-[#001440] border-blue-200 dark:border-blue-900/60 shadow-sm">
        <CardHeader className="pb-3 border-b border-blue-100 dark:border-blue-900/40">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-lg bg-blue-600 text-white shadow-sm shadow-blue-500/30">
                <Sparkles className="w-4 h-4" />
              </div>

              <div>
                <CardTitle className="text-sm font-bold text-slate-900 dark:text-slate-100 flex items-center gap-2">
                  AI Execution Summary

                  {execSummary?.generated_by ===
                    'llm' && (
                    <span className="text-[10px] font-semibold uppercase tracking-wider bg-blue-100 dark:bg-blue-900/60 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800 px-2 py-0.5 rounded-full">
                      LLM Synthesized
                    </span>
                  )}

                  {execSummary?.generated_by ===
                    'deterministic_fallback' && (
                    <span className="text-[10px] font-semibold uppercase tracking-wider bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-slate-700 px-2 py-0.5 rounded-full">
                      Deterministic Verified
                    </span>
                  )}

                  {execSummary?.cached && (
                    <span className="text-[10px] font-semibold uppercase tracking-wider bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 px-2 py-0.5 rounded-full">
                      Cache HIT
                    </span>
                  )}
                </CardTitle>

                <CardDescription className="text-xs text-slate-500 dark:text-slate-400">
                  Supervisor intelligence compiled from verified site logs, actuals, conflicts, and delay records
                </CardDescription>
              </div>
            </div>

            {/* Filter Toolbar */}
            <div className="flex flex-wrap items-center gap-2 text-xs">
              {/* Period Selector */}
              <div className="flex items-center gap-1 bg-white dark:bg-slate-900/80 border border-slate-200 dark:border-blue-900/60 rounded-lg p-0.5">
                <button
                  type="button"
                  onClick={() => {
                    setSummaryPeriod('last_7_days');
                    loadExecutionSummary(
                      'last_7_days',
                      summaryDiscipline,
                      summaryLanguage
                    );
                  }}
                  className={cn(
                    'px-2.5 py-1 rounded-md font-medium transition-colors',
                    summaryPeriod === 'last_7_days'
                      ? 'bg-blue-600 text-white shadow-xs'
                      : 'text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white'
                  )}
                >
                  Last 7 Days
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setSummaryPeriod('this_month');
                    loadExecutionSummary(
                      'this_month',
                      summaryDiscipline,
                      summaryLanguage
                    );
                  }}
                  className={cn(
                    'px-2.5 py-1 rounded-md font-medium transition-colors',
                    summaryPeriod === 'this_month'
                      ? 'bg-blue-600 text-white shadow-xs'
                      : 'text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white'
                  )}
                >
                  This Month
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setSummaryPeriod('custom');
                  }}
                  className={cn(
                    'px-2.5 py-1 rounded-md font-medium transition-colors',
                    summaryPeriod === 'custom'
                      ? 'bg-blue-600 text-white shadow-xs'
                      : 'text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white'
                  )}
                >
                  Custom
                </button>
              </div>

              {/* Discipline Dropdown */}
              <select
                value={summaryDiscipline}
                onChange={(e) => {
                  const val = e.target.value;

                  setSummaryDiscipline(val);

                  loadExecutionSummary(
                    summaryPeriod,
                    val,
                    summaryLanguage,
                    summaryStartDate,
                    summaryEndDate
                  );
                }}
                className="bg-white dark:bg-slate-900/80 border border-slate-200 dark:border-blue-900/60 text-slate-700 dark:text-slate-200 rounded-lg px-2 py-1 font-medium focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="ALL">
                  All Disciplines
                </option>
                <option value="CIVIL">Civil</option>
                <option value="PIPING">Piping</option>
                <option value="STATIC_ROTATING_EQUIPMENT">
                  Mechanical / Equipment
                </option>
                <option value="ELECTRICAL">
                  Electrical
                </option>
                <option value="INSTRUMENTATION">
                  Instrumentation
                </option>
                <option value="HSE">HSE</option>
              </select>

              {/* Language Dropdown */}
              <div className="flex items-center gap-1 bg-white dark:bg-slate-900/80 border border-slate-200 dark:border-blue-900/60 rounded-lg px-2 py-1 text-slate-700 dark:text-slate-200">
                <Globe2 className="w-3.5 h-3.5 text-blue-500" />

                <select
                  value={summaryLanguage}
                  onChange={(e) => {
                    const lang =
                      e.target.value as
                        | 'en'
                        | 'hi'
                        | 'te';

                    setSummaryLanguage(lang);

                    loadExecutionSummary(
                      summaryPeriod,
                      summaryDiscipline,
                      lang,
                      summaryStartDate,
                      summaryEndDate
                    );
                  }}
                  className="bg-transparent text-slate-700 dark:text-slate-200 font-medium focus:outline-none"
                >
                  <option value="en">
                    English (Canonical)
                  </option>
                  <option value="hi">
                    हिन्दी (Hindi)
                  </option>
                  <option value="te">
                    తెలుగు (Telugu)
                  </option>
                </select>
              </div>
            </div>
          </div>

          {/* Custom Date Range Picker */}
          {summaryPeriod === 'custom' && (
            <div className="flex items-center gap-2 pt-2 text-xs">
              <span className="text-slate-500 dark:text-slate-400">
                From:
              </span>

              <input
                type="date"
                value={summaryStartDate}
                onChange={(e) =>
                  setSummaryStartDate(e.target.value)
                }
                className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-blue-900/60 rounded px-2 py-1 text-slate-700 dark:text-slate-200"
              />

              <span className="text-slate-500 dark:text-slate-400">
                To:
              </span>

              <input
                type="date"
                value={summaryEndDate}
                onChange={(e) =>
                  setSummaryEndDate(e.target.value)
                }
                className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-blue-900/60 rounded px-2 py-1 text-slate-700 dark:text-slate-200"
              />

              <Button
                size="sm"
                onClick={() =>
                  loadExecutionSummary(
                    'custom',
                    summaryDiscipline,
                    summaryLanguage,
                    summaryStartDate,
                    summaryEndDate
                  )
                }
                className="h-7 text-xs bg-blue-600 hover:bg-blue-700 text-white"
              >
                Apply Range
              </Button>
            </div>
          )}
        </CardHeader>

        <CardContent className="pt-3 pb-4">
          {isSummaryLoading ? (
            <div className="py-8 flex flex-col items-center justify-center gap-2 text-slate-400 text-xs">
              <RefreshCw className="w-5 h-5 animate-spin text-blue-500" />

              <span>
                Generating verified execution summary...
              </span>
            </div>
          ) : execSummary ? (
            <div className="space-y-3">
              <div className="bg-white/80 dark:bg-slate-900/50 border border-slate-200/80 dark:border-blue-900/40 rounded-xl p-4 text-xs leading-relaxed text-slate-800 dark:text-slate-200 whitespace-pre-line font-normal shadow-xs">
                {execSummary.summary}
              </div>

              {/* Verified Metrics Strip */}
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 pt-1">
                <div className="p-2 rounded-lg bg-blue-50/80 dark:bg-blue-950/40 border border-blue-100 dark:border-blue-900/40 text-[11px]">
                  <span className="text-slate-500 dark:text-slate-400 block font-medium">
                    Scope Activities
                  </span>

                  <span className="font-bold text-blue-700 dark:text-blue-300 font-mono text-sm">
                    {execSummary.aggregate.activities.total}
                  </span>

                  <span className="text-[10px] text-slate-400 block">
                    {execSummary.aggregate.activities.completed}{' '}
                    Done ·{' '}
                    {
                      execSummary.aggregate.activities
                        .in_progress
                    }{' '}
                    Active
                  </span>
                </div>

                <div className="p-2 rounded-lg bg-emerald-50/80 dark:bg-emerald-950/40 border border-emerald-100 dark:border-emerald-900/40 text-[11px]">
                  <span className="text-slate-500 dark:text-slate-400 block font-medium">
                    Progress Claims
                  </span>

                  <span className="font-bold text-emerald-700 dark:text-emerald-300 font-mono text-sm">
                    {
                      execSummary.aggregate.claims
                        .total_claims
                    }
                  </span>

                  <span className="text-[10px] text-slate-400 block">
                    {
                      execSummary.aggregate
                        .approved_progress
                        .total_approved
                    }{' '}
                    Approved (
                    {
                      execSummary.aggregate
                        .approved_progress
                        .avg_approved_pct
                    }
                    %)
                  </span>
                </div>

                <div className="p-2 rounded-lg bg-amber-50/80 dark:bg-amber-950/40 border border-amber-100 dark:border-amber-900/40 text-[11px]">
                  <span className="text-slate-500 dark:text-slate-400 block font-medium">
                    Conflicts
                  </span>

                  <span className="font-bold text-amber-700 dark:text-amber-300 font-mono text-sm">
                    {
                      execSummary.aggregate.conflicts
                        .total_conflicts
                    }
                  </span>

                  <span className="text-[10px] text-slate-400 block">
                    {
                      execSummary.aggregate.conflicts
                        .by_status?.OPEN || 0
                    }{' '}
                    Open
                  </span>
                </div>

                <div className="p-2 rounded-lg bg-rose-50/80 dark:bg-rose-950/40 border border-rose-100 dark:border-rose-900/40 text-[11px]">
                  <span className="text-slate-500 dark:text-slate-400 block font-medium">
                    Delay Events
                  </span>

                  <span className="font-bold text-rose-700 dark:text-rose-300 font-mono text-sm">
                    {
                      execSummary.aggregate.delays
                        .total_delay_events
                    }
                  </span>

                  <span className="text-[10px] text-slate-400 block">
                    {
                      Object.keys(
                        execSummary.aggregate.delays
                          .reasons || {}
                      ).length
                    }{' '}
                    Factors Reported
                  </span>
                </div>

                <div className="p-2 rounded-lg bg-violet-50/80 dark:bg-violet-950/40 border border-violet-100 dark:border-violet-900/40 text-[11px] col-span-2 sm:col-span-1">
                  <span className="text-slate-500 dark:text-slate-400 block font-medium">
                    Historical Ratio
                  </span>

                  <span className="font-bold text-violet-700 dark:text-violet-300 font-mono text-sm">
                    {execSummary.aggregate.forecast
                      .historical_ratio
                      ? `${execSummary.aggregate.forecast.historical_ratio}×`
                      : 'N/A'}
                  </span>

                  <span className="text-[10px] text-slate-400 block">
                    {execSummary.aggregate.forecast
                      .historical_ratio
                      ? 'Discipline Multiplier'
                      : 'Select Discipline'}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <div className="py-6 text-center text-slate-400 text-xs">
              Click refresh to generate the project execution summary.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Silent Activities Alert Banner */}
      {silentActivities.length > 0 && (
        <div className="p-4 rounded-xl bg-amber-50 dark:bg-amber-500/10 border border-amber-400/40 dark:border-amber-500/30 text-amber-800 dark:text-amber-300 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5 text-amber-500" />

          <div className="flex-1">
            <h4 className="font-bold text-xs uppercase tracking-wider text-amber-700 dark:text-amber-400">
              {t('dashboard.silentAlertTitle', {
                count: silentActivities.length,
              })}
            </h4>

            <p className="text-xs mt-0.5 text-amber-700 dark:text-amber-200/90">
              {t('dashboard.silentAlertDesc')}
            </p>

            <div className="flex flex-wrap gap-2 mt-2">
              {silentActivities.map((act) => (
                <span
                  key={act.activity_id}
                  className="bg-amber-100 dark:bg-amber-950/80 border border-amber-400/40 dark:border-amber-500/40 text-amber-800 dark:text-amber-300 text-[11px] px-2.5 py-1 rounded-md font-mono"
                >
                  {act.activity_id}: {act.activity_name}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* KPI Cards Bar */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {kpiCards.map((kpi) => {
          const Icon = kpi.icon;

          return (
            <Card
              key={kpi.label}
              className={cn(
                'border',
                kpi.border,
                'bg-card shadow-xs hover:shadow-sm transition-shadow'
              )}
            >
              <CardContent className="p-4">
                <div className="flex items-start justify-between">
                  <div
                    className={cn(
                      'p-2 rounded-lg',
                      kpi.bg
                    )}
                  >
                    <Icon
                      className={cn(
                        'w-4 h-4',
                        kpi.accent
                      )}
                    />
                  </div>
                </div>

                {isLoading ? (
                  <Skeleton className="h-8 w-16 mt-3" />
                ) : (
                  <div
                    className={cn(
                      'text-3xl font-bold mt-3 font-mono',
                      kpi.accent
                    )}
                  >
                    {kpi.value}
                  </div>
                )}

                <div className="text-xs text-muted-foreground font-semibold uppercase tracking-wide mt-1">
                  {kpi.label}
                </div>

                {kpi.delta && (
                  <div
                    className={cn(
                      'text-[11px] mt-1.5 flex items-center gap-1 font-medium',
                      kpi.deltaPositive === true
                        ? 'text-emerald-600 dark:text-emerald-400'
                        : kpi.deltaPositive === false
                        ? 'text-rose-500 dark:text-rose-400'
                        : 'text-muted-foreground'
                    )}
                  >
                    {kpi.deltaPositive === true && (
                      <ArrowUpRight className="w-3.5 h-3.5" />
                    )}

                    {kpi.deltaPositive === false && (
                      <TrendingDown className="w-3.5 h-3.5" />
                    )}

                    {kpi.delta}
                  </div>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Grid: Delay Reasons Chart & Discipline Breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Delay Reasons Bar Chart */}
        <Card className="bg-card border-border text-foreground lg:col-span-7 shadow-xs">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center justify-between">
              <span className="flex items-center gap-2">
                <TrendingDown className="w-4 h-4 text-rose-500" />
                {t('dashboard.delayReasonsTitle')}
              </span>

              <span className="text-[10px] bg-secondary text-secondary-foreground px-2 py-0.5 rounded-full font-mono">
                {t('dashboard.paretoAnalysis')}
              </span>
            </CardTitle>

            <CardDescription className="text-muted-foreground text-xs">
              {t('dashboard.delayReasonsDesc')}
            </CardDescription>
          </CardHeader>

          <CardContent className="pt-4 h-64">
            {isLoading ? (
              <div className="space-y-3 pt-4">
                <Skeleton className="h-6 w-full" />
                <Skeleton className="h-6 w-4/5" />
                <Skeleton className="h-6 w-3/5" />
                <Skeleton className="h-6 w-2/5" />
              </div>
            ) : delayReasons.length === 0 ? (
              <div className="text-center py-16 text-muted-foreground text-xs">
                {t('dashboard.delayReasonsDesc')}
              </div>
            ) : (
              <ResponsiveContainer
                width="100%"
                height="100%"
              >
                <BarChart
                  data={delayReasons}
                  layout="vertical"
                  margin={{
                    top: 5,
                    right: 30,
                    left: 100,
                    bottom: 5,
                  }}
                >
                  <XAxis
                    type="number"
                    stroke="#94a3b8"
                    fontSize={11}
                  />

                  <YAxis
                    dataKey="reason"
                    type="category"
                    stroke="#94a3b8"
                    fontSize={10}
                    tickLine={false}
                    width={150}
                  />

                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#001E60',
                      borderColor: '#1e3a8a',
                      borderRadius: '8px',
                      fontSize: '11px',
                      color: '#f8fafc',
                    }}
                  />

                  <Bar
                    dataKey="count"
                    fill={CHART_COLORS.orange}
                    radius={[0, 4, 4, 0]}
                    barSize={16}
                  />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        {/* Discipline Pie */}
        <Card className="bg-card border-border text-foreground lg:col-span-5 shadow-xs">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Layers className="w-4 h-4 text-[#1565C0] dark:text-blue-400" />
              {t('dashboard.disciplineVolumeTitle')}
            </CardTitle>

            <CardDescription className="text-muted-foreground text-xs">
              {t('dashboard.disciplineVolumeDesc')}
            </CardDescription>
          </CardHeader>

          <CardContent className="pt-2 flex items-center justify-center h-64">
            {isLoading ? (
              <div className="flex flex-col items-center justify-center gap-2">
                <Skeleton className="h-36 w-36 rounded-full" />
                <Skeleton className="h-4 w-24" />
              </div>
            ) : disciplineData.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground text-xs">
                {t('dashboard.disciplineVolumeDesc')}
              </div>
            ) : (
              <ResponsiveContainer
                width="100%"
                height="100%"
              >
                <PieChart>
                  <Pie
                    data={disciplineData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    outerRadius={75}
                    innerRadius={40}
                    paddingAngle={4}
                    label={({ name, percent }) =>
                      `${name} ${(
                        (percent || 0) * 100
                      ).toFixed(0)}%`
                    }
                    fontSize={10}
                  >
                    {disciplineData.map(
                      (entry, index) => (
                        <Cell
                          key={`cell-${index}`}
                          fill={entry.fill}
                        />
                      )
                    )}
                  </Pie>

                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#001E60',
                      borderColor: '#1e3a8a',
                      borderRadius: '8px',
                      fontSize: '11px',
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Grid: Institutional Memory & Schedule Forecast */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Institutional Memory */}
        <Card className="bg-card border-border text-foreground lg:col-span-6 shadow-xs">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-blue-50 dark:bg-blue-950/60 border border-blue-200 dark:border-blue-900/60">
                <BrainCircuit className="w-3.5 h-3.5 text-[#1565C0] dark:text-blue-400" />
              </div>

              <span className="text-[#003087] dark:text-blue-300">
                {t('dashboard.institutionalMemoryTitle')}
              </span>
            </CardTitle>

            <CardDescription className="text-muted-foreground text-xs">
              {t('dashboard.institutionalMemoryDesc')}
            </CardDescription>
          </CardHeader>

          <CardContent className="pt-4 space-y-3">
            {isLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-16 w-full rounded-xl" />
                <Skeleton className="h-16 w-full rounded-xl" />
              </div>
            ) : institutionalMemory.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground text-xs">
                {t('dashboard.noInstitutionalMemory')}
              </div>
            ) : (
              institutionalMemory.map((mem, idx) => (
                <div
                  key={idx}
                  className="p-3 bg-card-subtle border border-border rounded-xl space-y-1 text-xs hover:border-[#1565C0]/40 transition-colors"
                >
                  <div className="flex items-center justify-between text-foreground font-bold">
                    <span>{mem.topic}</span>

                    <span className="text-[10px] bg-blue-100 dark:bg-blue-950/80 text-[#1565C0] dark:text-blue-300 border border-blue-200 dark:border-blue-900/60 px-2 py-0.5 rounded-full font-mono">
                      {mem.count}×
                    </span>
                  </div>

                  <p className="text-muted-foreground leading-relaxed text-[11px]">
                    {mem.resolution}
                  </p>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        {/* Schedule Forecast */}
        <Card className="bg-white dark:bg-[#001E60]/80 border-slate-200 dark:border-blue-900/50 text-slate-900 dark:text-slate-100 lg:col-span-6 shadow-sm flex flex-col">
          <CardHeader className="pb-3 border-b border-slate-100 dark:border-blue-900/40">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div>
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <div className="p-1.5 rounded-lg bg-blue-100 dark:bg-blue-500/10 border border-blue-200 dark:border-blue-500/20">
                    <Clock className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
                  </div>

                  <span>
                    {t('dashboard.forecastTitle')}
                  </span>

                  <span className="text-[10px] bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 px-2 py-0.5 rounded-full font-mono">
                    {t('dashboard.ratioForecast')}
                  </span>
                </CardTitle>

                <CardDescription className="text-slate-500 dark:text-slate-400 text-xs mt-1">
                  {t('dashboard.forecastDesc')}
                </CardDescription>
              </div>

              {forecastData?.historical_ratio !=
                null && (
                <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-700 dark:text-amber-400 text-xs font-mono font-medium self-start sm:self-auto">
                  <TrendingUp className="w-3.5 h-3.5" />

                  <span>
                    {forecastData.historical_ratio.toFixed(
                      2
                    )}
                    × Multiplier
                  </span>
                </div>
              )}
            </div>

            {/* Discipline Selector */}
            <div className="flex items-center gap-1.5 mt-3 pt-2 border-t border-slate-100 dark:border-slate-800/60 overflow-x-auto pb-1">
              <span className="text-[11px] text-slate-500 dark:text-slate-400 font-medium mr-1 flex items-center gap-1 shrink-0">
                <Filter className="w-3 h-3" />
                Discipline:
              </span>

              {[
                'CIVIL',
                'PIPING',
                'ELECTRICAL',
                'INSTRUMENTATION',
                'STATIC_ROTATING_EQUIPMENT',
              ].map((disc) => {
                const isActive =
                  selectedDiscipline === disc;

                return (
                  <button
                    key={disc}
                    type="button"
                    onClick={() =>
                      handleDisciplineChange(disc)
                    }
                    className={cn(
                      'px-2.5 py-0.5 text-[11px] rounded-md font-medium transition-all shrink-0',
                      isActive
                        ? 'bg-blue-600 text-white shadow-sm font-semibold'
                        : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700'
                    )}
                  >
                    {disc ===
                    'STATIC_ROTATING_EQUIPMENT'
                      ? 'MECHANICAL / EQUIPMENT'
                      : disc}
                  </button>
                );
              })}
            </div>
          </CardHeader>

          <CardContent className="pt-3 space-y-2.5 text-xs flex-1 max-h-[360px] overflow-y-auto">
            {isForecastLoading ? (
              <div className="flex flex-col items-center justify-center py-10 text-slate-400 gap-2">
                <RefreshCw className="w-5 h-5 animate-spin text-blue-500" />

                <span className="text-xs">
                  Computing historical ratio forecast...
                </span>
              </div>
            ) : !forecastData ||
              forecastData.activities.length === 0 ? (
              <div className="text-center py-10 text-slate-400 dark:text-slate-500 text-xs flex flex-col items-center gap-1">
                <AlertTriangle className="w-6 h-6 text-slate-400 mb-1 opacity-60" />

                <span className="font-medium">
                  No forecast activities for{' '}
                  {selectedDiscipline}
                </span>

                <span className="text-[11px]">
                  No matching schedule activities or
                  baseline durations found.
                </span>
              </div>
            ) : (
              forecastData.activities.map(
                (fc, idx) => {
                  const maxDuration = Math.max(
                    fc.planned_duration || 0,
                    fc.forecast_duration || 0,
                    1
                  );

                  const plannedPct = Math.round(
                    ((fc.planned_duration || 0) /
                      maxDuration) *
                      100
                  );

                  const forecastPct = Math.round(
                    ((fc.forecast_duration || 0) /
                      maxDuration) *
                      100
                  );

                  const hasSlip =
                    (fc.slippage_days || 0) > 0;

                  return (
                    <div
                      key={idx}
                      className="p-3 bg-slate-50 dark:bg-slate-950/60 border border-slate-200 dark:border-slate-800 rounded-xl hover:border-blue-400/40 dark:hover:border-blue-700/50 transition-colors"
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="font-medium text-slate-800 dark:text-slate-200 truncate max-w-[70%] font-mono text-[12px]">
                          {fc.activity_id}
                        </div>

                        <div className="text-right font-mono">
                          {hasSlip ? (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-bold bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20">
                              +{fc.slippage_days}d slip
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
                              On Schedule
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Dual Duration Comparison Bars */}
                      <div className="space-y-1.5 text-[11px]">
                        <div className="flex items-center gap-2">
                          <span className="w-14 text-slate-500 dark:text-slate-400 text-[10px]">
                            Planned
                          </span>

                          <div className="flex-1 bg-slate-200 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
                            <div
                              className="bg-blue-500 h-full rounded-full transition-all duration-300"
                              style={{
                                width: `${plannedPct}%`,
                              }}
                            />
                          </div>

                          <span className="w-14 text-right font-mono text-slate-700 dark:text-slate-300">
                            {fc.planned_duration !=
                            null
                              ? `${fc.planned_duration}d`
                              : 'N/A'}
                          </span>
                        </div>

                        <div className="flex items-center gap-2">
                          <span className="w-14 text-slate-500 dark:text-slate-400 text-[10px]">
                            Forecast
                          </span>

                          <div className="flex-1 bg-slate-200 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
                            <div
                              className={cn(
                                'h-full rounded-full transition-all duration-300',
                                hasSlip
                                  ? 'bg-amber-500'
                                  : 'bg-emerald-500'
                              )}
                              style={{
                                width: `${forecastPct}%`,
                              }}
                            />
                          </div>

                          <span
                            className={cn(
                              'w-14 text-right font-mono font-medium',
                              hasSlip
                                ? 'text-amber-600 dark:text-amber-400'
                                : 'text-emerald-600 dark:text-emerald-400'
                            )}
                          >
                            {fc.forecast_duration !=
                            null
                              ? `${fc.forecast_duration}d`
                              : 'N/A'}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                }
              )
            )}
          </CardContent>
        </Card>
      </div>

      {/* Downstream Schedule Impact Watch (Decision-Support) */}
      <Card className="bg-card border-border text-foreground shadow-xs">
        <CardHeader className="pb-3 border-b border-border">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div>
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-amber-100 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 text-amber-600 dark:text-amber-400">
                  <GitFork className="w-4 h-4" />
                </div>
                <span>Downstream Schedule Impact Watch</span>
                {scheduleImpacts.length > 0 ? (
                  <span className="text-[10px] bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-300 border border-amber-200 dark:border-amber-900/60 px-2 py-0.5 rounded-full font-mono font-bold">
                    {scheduleImpacts.length} {scheduleImpacts.length === 1 ? 'Activity at Risk' : 'Activities at Risk'}
                  </span>
                ) : (
                  <span className="text-[10px] bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-900/60 px-2 py-0.5 rounded-full font-mono font-bold">
                    Clean Baseline
                  </span>
                )}
              </CardTitle>
              <CardDescription className="text-muted-foreground text-xs mt-1">
                Deterministic Primavera schedule dependency analysis for delayed, on-hold, or quality-gated activities.
              </CardDescription>
            </div>
            <div className="text-[11px] text-muted-foreground font-mono self-start sm:self-auto">
              Schedule: <span className="font-bold text-foreground">{currentScheduleVersion.id}</span>
            </div>
          </div>
        </CardHeader>

        <CardContent className="pt-3">
          {isLoading ? (
            <div className="space-y-3 py-3">
              <Skeleton className="h-16 w-full rounded-lg" />
              <Skeleton className="h-16 w-full rounded-lg" />
            </div>
          ) : scheduleImpacts.length === 0 ? (
            <div className="py-6 px-4 rounded-lg border border-dashed border-border bg-card/40 flex items-center justify-center gap-3 text-xs text-muted-foreground">
              <CheckCircle2 className="w-5 h-5 text-emerald-500 shrink-0" />
              <span>
                No active downstream schedule risks detected in <strong>{currentScheduleVersion.name}</strong>. All quality hold points and successor dependencies are on track.
              </span>
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              {scheduleImpacts.map((imp) => {
                const getBadge = (lvl: string) => {
                  switch (lvl) {
                    case 'CRITICAL':
                      return 'bg-red-100 text-red-800 dark:bg-red-950/80 dark:text-red-300 border-red-300 dark:border-red-800';
                    case 'HIGH':
                      return 'bg-amber-100 text-amber-800 dark:bg-amber-950/80 dark:text-amber-300 border-amber-300 dark:border-amber-800';
                    case 'MEDIUM':
                      return 'bg-blue-100 text-blue-800 dark:bg-blue-950/80 dark:text-blue-300 border-blue-300 dark:border-blue-800';
                    default:
                      return 'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-300 border-slate-300 dark:border-slate-700';
                  }
                };

                return (
                  <div
                    key={imp.activityId}
                    className="p-3.5 rounded-lg border border-border bg-card/60 hover:bg-secondary/30 transition-all flex flex-col justify-between gap-2.5"
                  >
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between gap-2 flex-wrap">
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-bold text-xs text-primary px-2 py-0.5 rounded bg-primary/10 border border-primary/20">
                            {imp.activityId}
                          </span>
                          <span className="text-xs font-bold text-foreground line-clamp-1" title={imp.activityName}>
                            {imp.activityName}
                          </span>
                        </div>
                        <span className={cn('text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase', getBadge(imp.impactLevel))}>
                          {imp.impactLevel} IMPACT
                        </span>
                      </div>

                      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                        <span>Discipline: <strong className="text-foreground">{imp.discipline || 'UNASSIGNED'}</strong></span>
                        <span>•</span>
                        <span>Stage: <strong className="text-foreground">{imp.stageId || 'N/A'}</strong></span>
                        <span>•</span>
                        <span>WBS: <strong className="font-mono text-foreground">{imp.wbsCode || 'N/A'}</strong></span>
                      </div>

                      {/* Primary Trigger / Reason */}
                      <div className="text-xs text-amber-700 dark:text-amber-300 bg-amber-500/10 border border-amber-500/20 p-2 rounded-md font-medium flex items-start gap-1.5">
                        <AlertTriangle className="w-3.5 h-3.5 text-amber-500 shrink-0 mt-0.5" />
                        <span className="line-clamp-2">{imp.primaryReason || 'Downstream dependency constraint detected'}</span>
                      </div>

                      {/* Impact Scope Stats */}
                      <div className="text-[11px] text-muted-foreground flex items-center justify-between pt-1">
                        <span>Direct Successors: <strong className="text-foreground font-mono">{imp.directSuccessorCount}</strong></span>
                        <span>Total Downstream: <strong className="text-primary font-mono">{imp.totalDownstreamCount}</strong> across <strong className="text-foreground font-mono">{imp.impactedStageIds.length}</strong> {imp.impactedStageIds.length === 1 ? 'stage' : 'stages'}</span>
                      </div>
                    </div>

                    <div className="flex items-center justify-end gap-2 pt-2 border-t border-border/60">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setSelectedImpact(imp);
                          setIsImpactModalOpen(true);
                        }}
                        className="h-7 text-[11px] gap-1 cursor-pointer"
                      >
                        <GitFork className="w-3 h-3 text-primary" />
                        Inspect Impact Chain
                      </Button>
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => {
                          if (imp.stageId) {
                            setSelectedStageId(imp.stageId);
                          }
                          navigate('/wbs-explorer');
                        }}
                        className="h-7 text-[11px] gap-1 cursor-pointer"
                      >
                        <FolderTree className="w-3 h-3" />
                        WBS Explorer
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Recent Supervisor Decisions */}
      <Card className="bg-card border-border text-foreground shadow-xs">
        <CardHeader className="pb-2 border-b border-border">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-emerald-500 dark:text-emerald-400" />

            {t('dashboard.recentDecisionsTitle')}
          </CardTitle>

          <CardDescription className="text-muted-foreground text-xs">
            {t('dashboard.recentDecisionsDesc')}
          </CardDescription>
        </CardHeader>

        <CardContent className="pt-2">
          <div className="divide-y divide-border/60 text-xs">
            {isLoading ? (
              <div className="space-y-3 py-3">
                <Skeleton className="h-12 w-full rounded-lg" />
                <Skeleton className="h-12 w-full rounded-lg" />
              </div>
            ) : recentDecisions.length === 0 ? (
              <div className="py-8 text-center text-muted-foreground">
                {t('dashboard.noRecentDecisions')}
              </div>
            ) : (
              recentDecisions.map((dec) => (
                <div
                  key={dec.decision_id}
                  className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2 group hover:bg-secondary/40 px-2 rounded-lg transition-colors"
                >
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono font-bold text-[#003087] dark:text-blue-200 bg-blue-100 dark:bg-blue-900/80 px-2 py-0.5 rounded-md text-[11px]">
                        {dec.selected_activity_id}
                      </span>

                      <span
                        className={cn(
                          'text-[10px] font-bold px-2 py-0.5 rounded-full uppercase',
                          dec.action === 'APPROVE'
                            ? 'bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400'
                            : dec.action === 'EDIT' ||
                              dec.action === 'HOLD'
                            ? 'bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400'
                            : 'bg-rose-100 dark:bg-rose-500/20 text-rose-700 dark:text-rose-400'
                        )}
                      >
                        {dec.action}
                      </span>

                      <span className="text-muted-foreground font-mono">
                        {t('dashboard.eventLabel')}:{' '}
                        {dec.event_id}
                      </span>
                    </div>

                    <p className="text-foreground/90 text-xs">
                      "{dec.justification}"
                    </p>
                  </div>

                  <div className="text-muted-foreground font-mono text-[11px] shrink-0">
                    {t('dashboard.plannerLabel')}:{' '}
                    {dec.planner_id}
                  </div>
                </div>
              ))
            )}
          </div>
        </CardContent>
      </Card>

      {/* Feature 26: P6 / PMIS Sync Staging Buffer Modal */}
      <P6SyncStagingModal
        isOpen={isP6StagingOpen}
        onClose={() => setIsP6StagingOpen(false)}
      />

      {/* Schedule Import Modal */}
      {isSupervisor && (
        <ScheduleImportModal
          isOpen={isScheduleImportOpen}
          onClose={() => setIsScheduleImportOpen(false)}
        />
      )}

      {/* Compound Impact Inspection Modal */}
      <CompoundImpactModal
        isOpen={isImpactModalOpen}
        onClose={() => setIsImpactModalOpen(false)}
        impact={selectedImpact}
      />
    </div>
  );
}