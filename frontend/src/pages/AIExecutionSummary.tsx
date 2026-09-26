import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  FileSpreadsheet,
  Calendar,
  Filter,
  RefreshCw,
  AlertCircle,
  Sparkles,
  TrendingUp,
  ShieldCheck,
  Clock,
  CheckCircle2,
  FileText,
} from 'lucide-react';
import { reportsApi, ExecutionReportResponse, Discipline } from '../api';
import { useProject } from '@/context/ProjectContext';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { ErrorState } from '../components/ui/error-state';
import { EmptyState } from '../components/ui/empty-state';
import { Skeleton } from '../components/ui/skeleton';

const DISCIPLINES: { label: string; value: Discipline | '' }[] = [
  { label: 'All Disciplines', value: '' },
  { label: 'Civil', value: 'CIVIL' },
  { label: 'Piping', value: 'PIPING' },
  { label: 'Electrical', value: 'ELECTRICAL' },
  { label: 'Instrumentation', value: 'INSTRUMENTATION' },
  { label: 'Equipment', value: 'STATIC_ROTATING_EQUIPMENT' },
  { label: 'HSE', value: 'HSE' },
];

export default function AIExecutionSummary() {
  const { t, i18n } = useTranslation();
  const { currentProject, currentScheduleVersion } = useProject();
  const displayLang = (i18n.language || 'en').slice(0, 2);

  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 7);
    return d.toISOString().split('T')[0];
  });
  const [endDate, setEndDate] = useState(() => new Date().toISOString().split('T')[0]);
  const [selectedDiscipline, setSelectedDiscipline] = useState<Discipline | ''>('');

  const [summaryData, setSummaryData] = useState<ExecutionReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSummary = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await reportsApi.getExecutionSummary({
        schedule_id: currentScheduleVersion.id,
        start: startDate,
        end: endDate,
        discipline: selectedDiscipline || undefined,
        language: displayLang,
      });
      setSummaryData(res);
    } catch (err: any) {
      setError(err?.message || 'Failed to fetch AI execution summary report');
      setSummaryData(null);
    } finally {
      setLoading(false);
    }
  }, [currentProject.id, currentScheduleVersion.id, startDate, endDate, selectedDiscipline, displayLang]);

  useEffect(() => {
    fetchSummary();
  }, [fetchSummary]);

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-300 dark:border-[#214766]/60 pb-4">
        <div>
          <div className="flex items-center gap-2 text-xs font-mono text-primary font-bold">
            <span>{t('nav.reports') || 'REPORTS'}</span>
            <span>·</span>
            <span>FEATURE 35</span>
          </div>
          <h1 className="text-2xl font-extrabold text-[#071A2D] dark:text-[#F5F7FA] tracking-tight flex items-center gap-2 mt-0.5">
            <Sparkles className="w-6 h-6 text-primary" />
            AI Execution Summary
          </h1>
          <p className="text-[#334155] dark:text-[#CBD5E1] text-xs font-semibold mt-1">
            Supervisor-level periodic progress synthesis, bottleneck analysis, and multi-discipline actuals audit.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={fetchSummary}
            disabled={loading}
            className="text-xs h-9 gap-1.5 border-slate-300 dark:border-[#214766] bg-white dark:bg-[#0A2238] text-[#071A2D] dark:text-[#F5F7FA] hover:dark:bg-[#0D2942] font-bold"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh Summary
          </Button>
        </div>
      </div>

      {/* Filter Bar */}
      <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl">
        <CardContent className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex flex-wrap items-center gap-3 text-xs">
              <div className="flex items-center gap-2">
                <Calendar className="w-4 h-4 text-slate-400 dark:text-[#8FA6BA]" />
                <span className="font-bold text-[#071A2D] dark:text-[#F5F7FA]">Date Range:</span>
              </div>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="px-2.5 py-1.5 rounded-xl border border-slate-300 dark:border-[#214766] bg-white dark:bg-[#0B2742] text-[#071A2D] dark:text-[#F5F7FA] font-mono text-xs focus:ring-1 focus:ring-primary"
              />
              <span className="text-[#475569] dark:text-[#CBD5E1] font-semibold">to</span>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="px-2.5 py-1.5 rounded-xl border border-slate-300 dark:border-[#214766] bg-white dark:bg-[#0B2742] text-[#071A2D] dark:text-[#F5F7FA] font-mono text-xs focus:ring-1 focus:ring-primary"
              />

              <div className="h-4 w-px bg-slate-300 dark:bg-[#214766] mx-1" />

              <div className="flex items-center gap-2">
                <Filter className="w-4 h-4 text-slate-400 dark:text-[#8FA6BA]" />
                <span className="font-bold text-[#071A2D] dark:text-[#F5F7FA]">Discipline:</span>
              </div>
              <select
                value={selectedDiscipline}
                onChange={(e) => setSelectedDiscipline(e.target.value as Discipline | '')}
                className="px-2.5 py-1.5 rounded-xl border border-slate-300 dark:border-[#214766] bg-white dark:bg-[#0B2742] text-[#071A2D] dark:text-[#F5F7FA] text-xs font-semibold focus:ring-1 focus:ring-primary"
              >
                {DISCIPLINES.map((d) => (
                  <option key={d.value} value={d.value}>
                    {d.label}
                  </option>
                ))}
              </select>
            </div>

            {summaryData?.generated_at && (
              <div className="text-[11px] text-[#475569] dark:text-[#CBD5E1] font-mono font-bold">
                Generated: {new Date(summaryData.generated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Main Body */}
      {loading ? (
        <div className="space-y-4">
          <Skeleton className="h-40 w-full rounded-2xl" />
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Skeleton className="h-28 rounded-xl" />
            <Skeleton className="h-28 rounded-xl" />
            <Skeleton className="h-28 rounded-xl" />
          </div>
        </div>
      ) : error ? (
        <ErrorState
          title="Summary Report Unavailable"
          message={error}
          onRetry={fetchSummary}
          retryText="Retry Report Generation"
        />
      ) : !summaryData || !summaryData.summary_text ? (
        <EmptyState
          icon={FileText}
          title="No Summary Available"
          description="The AI execution summary service returned no synthesis records for the selected period."
        />
      ) : (
        <div className="space-y-6">
          {/* Executive Summary Card */}
          <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl">
            <CardHeader className="pb-3 border-b border-slate-300 dark:border-[#214766]/60 bg-primary/5 rounded-t-2xl">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm font-extrabold flex items-center gap-2 text-[#071A2D] dark:text-[#F5F7FA]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  Executive Execution Synthesis
                </CardTitle>
                <div className="flex items-center gap-2 text-xs font-mono text-[#475569] dark:text-[#CBD5E1] font-bold">
                  <span>
                    {summaryData.reporting_period.start_date} &rarr; {summaryData.reporting_period.end_date}
                  </span>
                  {summaryData.discipline && (
                    <span className="px-2 py-0.5 rounded-md bg-primary/10 text-primary font-bold">
                      {summaryData.discipline}
                    </span>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent className="pt-5 space-y-4">
              <p className="text-sm text-[#071A2D] dark:text-[#F8FAFC] leading-relaxed font-medium">
                {summaryData.summary_text}
              </p>

              {summaryData.key_highlights && summaryData.key_highlights.length > 0 && (
                <div className="mt-4 pt-3 border-t border-slate-200 dark:border-[#214766]/60 space-y-2">
                  <h4 className="text-xs font-extrabold uppercase tracking-wider text-[#334155] dark:text-[#CBD5E1] flex items-center gap-1.5">
                    <CheckCircle2 className="w-3.5 h-3.5 text-status-approved" />
                    Key Milestones &amp; Highlights
                  </h4>
                  <ul className="space-y-1.5 text-xs text-[#1E293B] dark:text-[#E2E8F0] font-medium">
                    {summaryData.key_highlights.map((h, i) => (
                      <li key={i} className="flex items-start gap-2">
                        <span className="w-1.5 h-1.5 rounded-full bg-primary mt-1.5 shrink-0" />
                        <span>{h}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Supporting Metrics (Only rendered if returned by backend) */}
          {summaryData.metrics && (
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              {summaryData.metrics.total_claims_processed != null && (
                <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#0A2238] rounded-xl shadow-xs">
                  <CardContent className="p-4 flex items-center gap-3">
                    <div className="p-2.5 rounded-xl bg-blue-50 dark:bg-blue-950/60 text-blue-600 dark:text-blue-400">
                      <FileSpreadsheet className="w-5 h-5" />
                    </div>
                    <div>
                      <span className="text-[11px] font-bold text-[#475569] dark:text-[#CBD5E1] block">Claims Processed</span>
                      <span className="text-xl font-bold font-mono text-[#071A2D] dark:text-[#F5F7FA]">
                        {summaryData.metrics.total_claims_processed}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              )}

              {summaryData.metrics.approval_rate_pct != null && (
                <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#0A2238] rounded-xl shadow-xs">
                  <CardContent className="p-4 flex items-center gap-3">
                    <div className="p-2.5 rounded-xl bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400">
                      <TrendingUp className="w-5 h-5" />
                    </div>
                    <div>
                      <span className="text-[11px] font-bold text-[#475569] dark:text-[#CBD5E1] block">Approval Rate</span>
                      <span className="text-xl font-bold font-mono text-[#071A2D] dark:text-[#F5F7FA]">
                        {summaryData.metrics.approval_rate_pct}%
                      </span>
                    </div>
                  </CardContent>
                </Card>
              )}

              {summaryData.metrics.open_conflicts_count != null && (
                <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#0A2238] rounded-xl shadow-xs">
                  <CardContent className="p-4 flex items-center gap-3">
                    <div className="p-2.5 rounded-xl bg-amber-50 dark:bg-amber-950/60 text-amber-600 dark:text-amber-400">
                      <AlertCircle className="w-5 h-5" />
                    </div>
                    <div>
                      <span className="text-[11px] font-bold text-[#475569] dark:text-[#CBD5E1] block">Open Conflicts</span>
                      <span className="text-xl font-bold font-mono text-[#071A2D] dark:text-[#F5F7FA]">
                        {summaryData.metrics.open_conflicts_count}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              )}

              {summaryData.metrics.high_priority_escalations != null && (
                <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#0A2238] rounded-xl shadow-xs">
                  <CardContent className="p-4 flex items-center gap-3">
                    <div className="p-2.5 rounded-xl bg-rose-50 dark:bg-rose-950/60 text-rose-600 dark:text-rose-400">
                      <ShieldCheck className="w-5 h-5" />
                    </div>
                    <div>
                      <span className="text-[11px] font-bold text-[#475569] dark:text-[#CBD5E1] block">Escalations</span>
                      <span className="text-xl font-bold font-mono text-[#071A2D] dark:text-[#F5F7FA]">
                        {summaryData.metrics.high_priority_escalations}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              )}
            </div>
          )}

          {/* Top Delay Drivers Breakdown */}
          {summaryData.metrics?.top_delay_drivers && summaryData.metrics.top_delay_drivers.length > 0 && (
            <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl">
              <CardHeader className="pb-3 border-b border-slate-300 dark:border-[#214766]/60">
                <CardTitle className="text-xs font-extrabold uppercase tracking-wider text-[#334155] dark:text-[#CBD5E1] flex items-center gap-2">
                  <Clock className="w-4 h-4 text-status-warning" />
                  Top Delay Drivers Reported
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {summaryData.metrics.top_delay_drivers.map((driver, idx) => (
                    <div
                      key={idx}
                      className="p-3 rounded-xl bg-slate-50 dark:bg-[#0D2942] border border-slate-200 dark:border-[#214766] flex items-center justify-between text-xs"
                    >
                      <span className="font-bold text-[#071A2D] dark:text-[#F5F7FA]">{driver.reason}</span>
                      <span className="font-mono font-bold px-2 py-0.5 rounded bg-slate-200 dark:bg-[#152E48] text-[#334155] dark:text-[#CBD5E1]">
                        {driver.count} occurrences
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
