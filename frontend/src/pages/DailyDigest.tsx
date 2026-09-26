import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { digestApi, ExecutionEvent, DISCIPLINES } from '@/api';
import {
  Calendar as CalendarIcon,
  ChevronLeft,
  ChevronRight,
  Clock,
  ArrowRight,
  Sparkles,
  CheckSquare,
  ClipboardList,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/StatusBadge';
import { ProvenanceBadge } from '@/components/ProvenanceBadge';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { useProject } from '@/context/ProjectContext';
import { Building2 } from 'lucide-react';

export default function DailyDigest() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { currentProject, currentScheduleVersion } = useProject();

  const [selectedDate, setSelectedDate] = useState<string>(new Date().toISOString().split('T')[0]);
  const [currentMonth, setCurrentMonth] = useState<Date>(new Date());
  const [showCalendar, setShowCalendar] = useState<boolean>(false);
  const [hasResolvedInitialDate, setHasResolvedInitialDate] = useState<boolean>(false);

  const [events, setEvents] = useState<ExecutionEvent[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [isBulking, setIsBulking] = useState<boolean>(false);
  const [bulkResult, setBulkResult] = useState<{ approved: string[]; failed: string[] } | null>(null);

  const loadDigest = async (dateStr: string) => {
    setIsLoading(true);
    setError(null);
    setBulkResult(null);
    try {
      const data = await digestApi.getByDate(dateStr);
      setEvents(data);
    } catch (err: any) {
      setError(err.message || t('digest.failedToLoad'));
    } finally {
      setIsLoading(false);
    }
  };

  // On first load, jump to the event_date of the MOST RECENTLY CREATED
  // claim, instead of blindly defaulting to today's real calendar date.
  // Two things this deliberately does NOT use, because both fail here:
  //   - max(event_date): a claim's event_date is whatever date its source
  //     report/text reported, not its upload date. Since untyped/undated
  //     submissions fall back to today's real date, "today" ends up WITH
  //     the max event_date too (it's not empty) even when it's full of
  //     stale test claims and the real newly-ingested ones are dated
  //     elsewhere (e.g. this project's sample data all sits in Aug 2026).
  //   - most claims / most actionable claims per date: today can easily
  //     have MORE accumulated claims than the date someone just uploaded
  //     to, for the same reason.
  // Sorting by created_at (when the claim actually entered the system)
  // instead directly answers "where did what I just ingested land" --
  // exactly what "newly extracted claims aren't visible in the digest"
  // means. Runs once; the Today/prev/next/calendar controls below still
  // navigate normally afterward, including to the real today if wanted.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const all = await digestApi.getAll();
        if (!cancelled && all.length > 0) {
          const mostRecentlyCreated = all.reduce((latest, e) =>
            e.created_at > latest.created_at ? e : latest
          );
          const latestDate = mostRecentlyCreated.event_date;
          setSelectedDate(latestDate);
          setCurrentMonth(new Date(latestDate));
          setHasResolvedInitialDate(true);
          loadDigest(latestDate);
          return;
        }
      } catch {
        // Fall through -- load whatever selectedDate already is
      }
      if (!cancelled) {
        setHasResolvedInitialDate(true);
        loadDigest(selectedDate);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [currentProject.id]);

  useEffect(() => {
    if (!hasResolvedInitialDate) return;
    loadDigest(selectedDate);
  }, [selectedDate, hasResolvedInitialDate]);

  const handleDateChange = (newDateStr: string) => {
    setSelectedDate(newDateStr);
    setShowCalendar(false);
  };

  const handlePrevDay = () => {
    const d = new Date(selectedDate);
    d.setDate(d.getDate() - 1);
    setSelectedDate(d.toISOString().split('T')[0]);
  };

  const handleNextDay = () => {
    const d = new Date(selectedDate);
    d.setDate(d.getDate() + 1);
    setSelectedDate(d.toISOString().split('T')[0]);
  };

  const handleToday = () => {
    const today = new Date().toISOString().split('T')[0];
    setSelectedDate(today);
    setCurrentMonth(new Date());
  };

  const handleBulkApprove = async () => {
    const reviewableIds = events
      .filter((e) => e.status === 'REVIEW_REQUIRED' || e.status === 'VALIDATED')
      .map((e) => e.event_id);

    if (reviewableIds.length === 0) return;

    setIsBulking(true);
    try {
      const res = await digestApi.bulkApprove(reviewableIds);
      setBulkResult(res);
      loadDigest(selectedDate);
    } catch (err: any) {
      setError(t('digest.bulkApproveFailed', { message: err.message }));
    } finally {
      setIsBulking(false);
    }
  };

  const totalClaims = events.length;
  const reviewRequired = events.filter((e) => e.status === 'REVIEW_REQUIRED').length;
  const validated = events.filter((e) => e.status === 'VALIDATED').length;
  const unmatched = events.filter((e) => e.status === 'UNMATCHED' || !e.matched_activity_id).length;
  const approved = events.filter((e) => e.status === 'APPROVED').length;
  const hold = events.filter((e) => e.status === 'HOLD').length;

  const renderCalendar = () => {
    const year = currentMonth.getFullYear();
    const month = currentMonth.getMonth();

    const firstDayOfMonth = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    const days = [];
    for (let i = 0; i < firstDayOfMonth; i++) {
      days.push(<div key={`empty-${i}`} className="h-8 w-8" />);
    }

    for (let d = 1; d <= daysInMonth; d++) {
      const dateString = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      const isSelected = dateString === selectedDate;
      const isTodayStr = dateString === new Date().toISOString().split('T')[0];

      days.push(
        <button
          key={d}
          type="button"
          onClick={() => handleDateChange(dateString)}
          className={cn(
            'h-8 w-8 rounded-lg text-xs font-semibold flex items-center justify-center transition-all',
            isSelected
              ? 'bg-gradient-to-r from-[#FF7A18] to-[#FF941F] text-white shadow-xs font-bold scale-105'
              : isTodayStr
              ? 'border border-[#FF7A18] text-[#FF7A18] hover:bg-card-subtle'
              : 'text-foreground hover:bg-card-subtle'
          )}
        >
          {d}
        </button>
      );
    }

    const monthNames = t('digest.months', { returnObjects: true }) as unknown as string[];

    return (
      <div className="p-4 bg-card border border-border rounded-xl shadow-xl w-72 space-y-3 z-50 animate-in fade-in zoom-in-95 duration-150">
        <div className="flex items-center justify-between border-b border-border pb-2">
          <span className="text-xs font-bold text-foreground">
            {monthNames[month]} {year}
          </span>
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6 text-muted-foreground hover:text-foreground"
              onClick={() => setCurrentMonth(new Date(year, month - 1, 1))}
            >
              <ChevronLeft className="w-4 h-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6 text-muted-foreground hover:text-foreground"
              onClick={() => setCurrentMonth(new Date(year, month + 1, 1))}
            >
              <ChevronRight className="w-4 h-4" />
            </Button>
          </div>
        </div>
        <div className="grid grid-cols-7 gap-1 text-center text-[10px] font-bold text-muted-foreground uppercase">
          <span>{t('digest.sunday')}</span><span>{t('digest.monday')}</span><span>{t('digest.tuesday')}</span><span>{t('digest.wednesday')}</span><span>{t('digest.thursday')}</span><span>{t('digest.friday')}</span><span>{t('digest.saturday')}</span>
        </div>
        <div className="grid grid-cols-7 gap-1">{days}</div>
      </div>
    );
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Top Header Toolbar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
        <div>
          <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground pb-1">
            <span className="flex items-center gap-1 font-bold text-primary">
              <Building2 className="w-3.5 h-3.5 text-[#FF7A18]" />
              {currentProject.name} ({currentProject.code})
            </span>
            <span>·</span>
            <span className="text-[11px] px-2 py-0.2 rounded bg-slate-100 dark:bg-[#0B2742] text-muted-foreground border border-slate-300 dark:border-[#214766]">
              {currentScheduleVersion.versionNumber}
            </span>
          </div>
          <h1 className="text-2xl font-extrabold text-[#071A2D] dark:text-[#F5F7FA] tracking-tight flex items-center gap-2">
            <ClipboardList className="w-6 h-6 text-primary" />
            {t('digest.title')}
          </h1>
          <p className="text-[#334155] dark:text-[#CBD5E1] text-xs font-semibold mt-1">
            {t('digest.subtitle')}
          </p>
        </div>

        {/* Date Selector */}
        <div className="flex items-center gap-2 relative">
          <Button
            variant="outline"
            size="sm"
            onClick={handlePrevDay}
            className="h-9"
          >
            <ChevronLeft className="w-4 h-4" />
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowCalendar(!showCalendar)}
            className="font-mono text-xs h-9 px-3 gap-2 shadow-xs"
          >
            <CalendarIcon className="w-4 h-4 text-primary" />
            <span>{selectedDate}</span>
          </Button>

          {showCalendar && (
            <div className="absolute right-0 top-11">
              {renderCalendar()}
            </div>
          )}

          <Button
            variant="outline"
            size="sm"
            onClick={handleNextDay}
            className="h-9"
          >
            <ChevronRight className="w-4 h-4" />
          </Button>

          <Button
            variant="ghost"
            size="sm"
            onClick={handleToday}
            className="text-xs text-primary hover:text-primary hover:bg-primary/10 h-9 font-semibold"
          >
            {t('digest.today')}
          </Button>
        </div>
      </div>

      {/* Summary KPI Bar — Multi-Level Surface Elevation */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="p-4 rounded-xl bg-card border border-border shadow-xs backdrop-blur-xs">
          <span className="text-[11px] font-bold text-muted-foreground uppercase">{t('digest.totalClaims')}</span>
          <div className="text-2xl font-bold text-foreground mt-1 font-mono">{totalClaims}</div>
        </div>

        <div className="p-4 rounded-xl bg-amber-50/70 dark:bg-[#0A2340]/80 border border-amber-200/80 dark:border-amber-900/50 shadow-xs backdrop-blur-xs">
          <span className="text-[11px] font-bold text-amber-800 dark:text-amber-300 uppercase">{t('digest.reviewRequired')}</span>
          <div className="text-2xl font-bold text-amber-900 dark:text-amber-200 mt-1 font-mono">{reviewRequired}</div>
        </div>

        <div className="p-4 rounded-xl bg-teal-50/70 dark:bg-[#0A2340]/80 border border-teal-200/80 dark:border-[#1E3A5F] shadow-xs backdrop-blur-xs">
          <span className="text-[11px] font-bold text-teal-800 dark:text-[#22D3EE] uppercase font-mono">{t('digest.validated')}</span>
          <div className="text-2xl font-bold text-teal-900 dark:text-[#22D3EE] mt-1 font-mono">{validated}</div>
        </div>

        <div className="p-4 rounded-xl bg-orange-50/70 dark:bg-[#0A2340]/80 border border-orange-200/80 dark:border-orange-900/50 shadow-xs backdrop-blur-xs">
          <span className="text-[11px] font-bold text-orange-800 dark:text-orange-300 uppercase font-mono">Unmatched Scope</span>
          <div className="text-2xl font-bold text-orange-900 dark:text-orange-200 mt-1 font-mono">{unmatched}</div>
        </div>

        <div className="p-4 rounded-xl bg-emerald-50/70 dark:bg-[#0A2340]/80 border border-emerald-200/80 dark:border-emerald-900/50 shadow-xs backdrop-blur-xs">
          <span className="text-[11px] font-bold text-emerald-800 dark:text-emerald-300 uppercase font-mono">{t('digest.approved')}</span>
          <div className="text-2xl font-bold text-emerald-900 dark:text-emerald-200 mt-1 font-mono">{approved}</div>
        </div>

        <div className="p-4 rounded-xl bg-slate-50/80 dark:bg-[#0A2340]/80 border border-slate-200 dark:border-[#1E3A5F] shadow-xs backdrop-blur-xs">
          <span className="text-[11px] font-bold text-muted-foreground uppercase">{t('digest.onHold')}</span>
          <div className="text-2xl font-bold text-muted-foreground mt-1 font-mono">{hold}</div>
        </div>
      </div>

      {/* Bulk Action Toolbar */}
      <div className="p-4 rounded-xl bg-card border border-border flex flex-col sm:flex-row items-center justify-between gap-3 shadow-xs">
        <div className="text-xs text-foreground flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-primary shrink-0" />
          <span>
            {t('digest.claimsReadyForSignoff', { count: reviewRequired + validated, date: selectedDate })}
          </span>
        </div>

        <Button
          onClick={handleBulkApprove}
          disabled={isBulking || (reviewRequired + validated === 0)}
          isLoading={isBulking}
          className="text-xs font-semibold h-9 px-4 shadow-xs w-full sm:w-auto rounded-lg"
        >
          {isBulking ? (
            <span>{t('digest.processingBulkApproval')}</span>
          ) : (
            <>
              <CheckSquare className="w-4 h-4 mr-1.5" />
              {t('digest.bulkApprove')}
            </>
          )}
        </Button>
      </div>

      {bulkResult && (
        <div className="p-3.5 rounded-xl bg-status-approved/10 border border-status-approved/30 text-status-approved text-xs flex items-center justify-between">
          <span>{t('digest.bulkApprovalComplete', { count: bulkResult.approved.length })}</span>
          <Button variant="ghost" size="sm" onClick={() => setBulkResult(null)} className="h-6 text-xs">{t('common.dismiss')}</Button>
        </div>
      )}

      {error && (
        <ErrorState
          message={error}
          onRetry={() => loadDigest(selectedDate)}
        />
      )}

      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-28 rounded-xl" />
          ))}
        </div>
      ) : events.length === 0 ? (
        <EmptyState
          icon={Clock}
          title={t('digest.noClaimsForDate', { date: selectedDate })}
          description={t('digest.noClaimsDesc')}
        />
      ) : (
        <div className="space-y-6">
          {[...DISCIPLINES, 'UNASSIGNED' as const].map((disc) => {
            const discEvents = events.filter((e) =>
              disc === 'UNASSIGNED' ? !e.discipline || (e.discipline as string) === 'UNASSIGNED' : e.discipline === disc
            );
            if (discEvents.length === 0) return null;

            return (
              <div key={disc} className="space-y-3">
                <div className="flex items-center gap-2 border-b border-border/70 pb-2">
                  <span className="text-xs font-bold uppercase tracking-wider text-primary font-mono">
                    {t(`disciplines.${disc}` as any, { defaultValue: disc.split('_').join(' ') })}
                  </span>
                  <span className="text-[10px] bg-secondary text-secondary-foreground px-2 py-0.5 rounded-full font-mono font-semibold">
                    {t('digest.claimsCount', { count: discEvents.length })}
                  </span>
                </div>

                <div className="grid grid-cols-1 gap-3">
                  {discEvents.map((ev) => (
                    <div
                      key={ev.event_id}
                      onClick={() => navigate(`/review?event_id=${ev.event_id}`)}
                      className={cn(
                        'p-4 rounded-xl bg-card border transition-all cursor-pointer hover:border-[#FF7A18] shadow-xs group relative',
                        ev.status === 'REVIEW_REQUIRED'
                          ? 'border-amber-300 dark:border-amber-900/60'
                          : 'border-border'
                      )}
                    >
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-2">
                        <div className="flex items-center gap-2 flex-wrap">
                          <StatusBadge status={ev.status} size="sm" />
                          <ProvenanceBadge channel={ev.input_channel} size="sm" />
                          <span className="text-[11px] font-mono text-muted-foreground">{t('digest.idLabel')}: {ev.event_id}</span>
                        </div>

                        <div className="text-xs text-muted-foreground font-mono flex items-center gap-1">
                          <Clock className="w-3.5 h-3.5 text-muted-foreground" />
                          {ev.event_date}
                        </div>
                      </div>

                      <p className="text-sm font-semibold text-foreground leading-relaxed mb-3 group-hover:text-primary transition-colors">
                        "{ev.raw_claim_text}"
                      </p>

                      <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-border/50 text-xs">
                        <div className="flex items-center gap-4 text-muted-foreground">
                          <div>
                            {t('digest.matchedActivity')}:{' '}
                            <span className="font-mono text-foreground font-bold">
                              {ev.matched_activity_id || t('digest.awaitingMatch')}
                            </span>
                          </div>
                          {ev.claimed_pct !== null && (
                            <div>
                              {t('digest.progress')}:{' '}
                              <span className="font-mono text-primary font-bold">{ev.claimed_pct}%</span>
                            </div>
                          )}
                        </div>

                        <div className="text-primary font-semibold flex items-center gap-1 group-hover:translate-x-1 transition-transform">
                          <span>{t('digest.inspectClaim')}</span>
                          <ArrowRight className="w-3.5 h-3.5" />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
