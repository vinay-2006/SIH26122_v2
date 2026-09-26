import React, { useEffect, useState, useCallback } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  claimsApi,
  decisionsApi,
  digestApi,
  schedulesApi,
  wbsApi,
  ExecutionEvent,
  CandidateMatch,
  ValidationIssue,
  ConflictRecord,
  DecisionAction,
  ScheduleActivity,
} from '@/api';
import {
  FileText,
  ShieldAlert,
  CheckCircle2,
  AlertTriangle,
  Sparkles,
  Check,
  ListOrdered,
  ChevronDown,
  ChevronUp,
  ArrowRight,
  Flame,
  Layers,
  ShieldCheck,
  Network,
  HelpCircle,
  Building2,
  Lock,
} from 'lucide-react';
import { useProject } from '@/context/ProjectContext';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { ConfidenceBar } from '@/components/ConfidenceBar';
import { StatusBadge } from '@/components/StatusBadge';
import { ProvenanceBadge } from '@/components/ProvenanceBadge';
import { SourceReferenceCard } from '@/components/SourceReferenceCard';
import { FieldProvenanceBadge } from '@/components/FieldProvenanceBadge';
import { AskWhyPanel } from '@/components/AskWhyPanel';
import { WBSSplitEditor } from '@/components/WBSSplitEditor';
import { EvidencePanel } from '@/components/EvidencePanel';
import { KnowledgeGraph } from '@/components/KnowledgeGraph';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

export default function ReviewWorkspace() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const { currentProject, currentScheduleVersion } = useProject();
  const eventIdParam = searchParams.get('event_id');

  const [event, setEvent] = useState<ExecutionEvent | null>(null);
  const [candidates, setCandidates] = useState<CandidateMatch[]>([]);
  const [conflicts, setConflicts] = useState<ConflictRecord[]>([]);
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [activities, setActivities] = useState<ScheduleActivity[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  // Quick Queue State
  const [queueClaims, setQueueClaims] = useState<ExecutionEvent[]>([]);
  const [queueFilter, setQueueFilter] = useState<'ALL' | 'UNMATCHED' | 'HIGH_PRIORITY'>('ALL');
  const [isLoadingQueue, setIsLoadingQueue] = useState<boolean>(true);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [isQueueOpen, setIsQueueOpen] = useState<boolean>(true);

  const filteredQueueClaims = React.useMemo(() => {
    if (queueFilter === 'UNMATCHED') {
      return queueClaims.filter((c) => c.status === 'UNMATCHED' || !c.matched_activity_id);
    }
    if (queueFilter === 'HIGH_PRIORITY') {
      return queueClaims.filter((c) => (c.priority_score && c.priority_score > 0.7) || c.is_escalated);
    }
    return queueClaims;
  }, [queueClaims, queueFilter]);

  // Feature 30: Matching Mode State ('direct' vs 'split')
  const [matchMode, setMatchMode] = useState<'direct' | 'split'>('direct');
  const [hasSplits, setHasSplits] = useState<boolean>(false);

  // Feature 31: Bottom Inspection Tab ('evidence' vs 'graph')
  const [bottomTab, setBottomTab] = useState<'evidence' | 'graph'>('evidence');

  // Feature 34: Ask Why Drawer / Panel State
  const [isAskWhyOpen, setIsAskWhyOpen] = useState<boolean>(false);

  // Form State
  const [selectedActivityId, setSelectedActivityId] = useState<string>('');
  const [action, setAction] = useState<DecisionAction>('APPROVE');
  const [approvedPct, setApprovedPct] = useState<number | ''>('');
  const [approvedQty, setApprovedQty] = useState<number | ''>('');
  const [justification, setJustification] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [decisionSuccess, setDecisionSuccess] = useState<boolean>(false);

  const loadQueue = useCallback(async () => {
    setIsLoadingQueue(true);
    setQueueError(null);
    try {
      try {
        const queue = await claimsApi.getReviewQueue('priority');
        setQueueClaims(queue);
      } catch {
        // Fallback to digestApi if review-queue endpoint is unavailable
        const all = await digestApi.getAll();
        const reviewable = all.filter(
          (c) => c.status === 'REVIEW_REQUIRED' || c.status === 'VALIDATED' || c.status === 'HOLD'
        );
        setQueueClaims(reviewable);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to load queue';
      setQueueError(msg);
    } finally {
      setIsLoadingQueue(false);
    }
  }, [currentProject.id, currentScheduleVersion.id]);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  const loadData = useCallback(async (id: string) => {
    setIsLoading(true);
    setSubmitError(null);
    setDecisionSuccess(false);
    try {
      const [ev, cands, confs, valIssues, actList, splitRows] = await Promise.all([
        claimsApi.getEvent(id),
        claimsApi.getCandidates(id),
        claimsApi.getConflicts(id),
        claimsApi.getValidation(id),
        schedulesApi.getActivities(currentScheduleVersion.id).catch(() => []),
        wbsApi.getSplits(id).catch(() => []),
      ]);
      setEvent(ev);
      setCandidates(cands);
      setConflicts(confs);
      setIssues(valIssues);
      setActivities(actList);

      // Feature 30: a decomposed claim has no single matched activity -- it has split rows.
      const splitActive = !ev.matched_activity_id && splitRows.length > 0;
      setHasSplits(splitActive);
      const defaultActivity =
        (splitActive ? splitRows[0]?.activity_id : undefined) || cands[0]?.activity_id || ev.matched_activity_id || '';
      setSelectedActivityId(defaultActivity);
      setApprovedPct(ev.claimed_pct ?? 100);
      setApprovedQty(ev.claimed_quantity ?? '');
      setMatchMode(splitActive ? 'split' : 'direct');
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to load claim details';
      setSubmitError(t('review.errLoadFailed', { message: msg }));
    } finally {
      setIsLoading(false);
    }
  }, [currentScheduleVersion.id, t]);

  useEffect(() => {
    if (eventIdParam) {
      loadData(eventIdParam);
    } else {
      setIsLoading(false);
    }
  }, [eventIdParam, loadData, currentProject.id, currentScheduleVersion.id]);

  const handleSelectClaim = (id: string) => {
    setSearchParams({ event_id: id });
  };

  const handleSubmitDecision = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);

    if (!justification.trim()) {
      setSubmitError(t('review.errJustificationRequired'));
      return;
    }
    if (!selectedActivityId) {
      setSubmitError(t('review.errActivityRequired'));
      return;
    }

    setIsSubmitting(true);
    try {
      await decisionsApi.submit({
        event_id: eventIdParam!,
        selected_activity_id: selectedActivityId,
        action,
        approved_pct: approvedPct !== '' ? Number(approvedPct) : null,
        approved_qty: approvedQty !== '' ? Number(approvedQty) : null,
        justification: justification.trim(),
      });
      setDecisionSuccess(true);
      loadQueue();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t('review.errDecisionFailed');
      setSubmitError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const nextPendingClaim = queueClaims.find((c) => c.event_id !== eventIdParam);

  // If no claim selected via query param, display Quick Queue selector
  if (!eventIdParam) {
    return (
      <div className="space-y-6 max-w-4xl mx-auto py-4 animate-in fade-in duration-200">
        <div className="flex items-center justify-between border-b border-border pb-4">
          <div>
            <h1 className="text-2xl font-extrabold text-[#071A2D] dark:text-[#F5F7FA] tracking-tight flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-primary/10 border border-primary/20">
                <ListOrdered className="w-5 h-5 text-primary" />
              </div>
              {t('review.quickQueueTitle')}
            </h1>
            <p className="text-[#334155] dark:text-[#CBD5E1] text-xs font-semibold mt-1">
              {t('review.selectClaimDesc')}
            </p>
          </div>
          <Button onClick={() => navigate('/digest')} variant="outline" size="sm" className="text-xs h-9">
            {t('review.goToDigest')}
          </Button>
        </div>

        {isLoadingQueue ? (
          <div className="space-y-3">
            <Skeleton className="h-20 w-full rounded-xl" />
            <Skeleton className="h-20 w-full rounded-xl" />
            <Skeleton className="h-20 w-full rounded-xl" />
          </div>
        ) : queueError ? (
          <ErrorState message={queueError} onRetry={loadQueue} retryText={t('common.retry')} />
        ) : queueClaims.length === 0 ? (
          <EmptyState
            icon={FileText}
            title={t('review.quickQueueEmpty')}
            description={t('review.queueAllReviewed')}
            action={
              <Button onClick={() => navigate('/digest')} size="sm">
                {t('review.goToDigest')}
              </Button>
            }
            className="py-12"
          />
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider px-1">
                {t('review.quickQueueCount', { count: filteredQueueClaims.length })}
              </div>
              <div className="flex items-center gap-1.5">
                {(['ALL', 'HIGH_PRIORITY', 'UNMATCHED'] as const).map((filter) => (
                  <button
                    key={filter}
                    type="button"
                    onClick={() => setQueueFilter(filter)}
                    className={cn(
                      'px-2.5 py-1 text-[11px] font-bold rounded-lg transition-all cursor-pointer border',
                      queueFilter === filter
                        ? 'bg-primary text-white border-primary shadow-2xs'
                        : 'bg-card border-border text-muted-foreground hover:text-foreground'
                    )}
                  >
                    {filter === 'ALL'
                      ? 'All Pending'
                      : filter === 'HIGH_PRIORITY'
                      ? 'High Priority 🔥'
                      : 'Unmatched / New Scope ⚠️'}
                  </button>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {filteredQueueClaims.map((claim) => (
                <div
                  key={claim.event_id}
                  onClick={() => handleSelectClaim(claim.event_id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      handleSelectClaim(claim.event_id);
                    }
                  }}
                  tabIndex={0}
                  role="button"
                  aria-label={`Review claim ${claim.event_id}`}
                  className="p-4 rounded-xl bg-card border border-border hover:border-primary/60 hover:shadow-sm transition-all cursor-pointer space-y-2 text-xs group focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono font-bold text-foreground text-sm group-hover:text-primary transition-colors">
                        {claim.event_id}
                      </span>
                      <StatusBadge status={claim.status} size="sm" />
                      {claim.priority_score != null && (
                        <span className="inline-flex items-center gap-1 text-[10px] font-mono font-bold px-1.5 py-0.5 rounded-full bg-red-100 text-red-700 dark:bg-red-950/70 dark:text-red-300 border border-red-200 dark:border-red-900/60">
                          <Flame className="w-2.5 h-2.5 text-red-500" />
                          P{claim.priority_rank ?? 1} · {Math.round(claim.priority_score)} pts
                        </span>
                      )}
                      {claim.is_escalated && (
                        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 dark:bg-amber-950/70 dark:text-amber-300 border border-amber-200 dark:border-amber-900/60">
                          Escalated
                        </span>
                      )}
                    </div>
                    <span className="text-[11px] font-mono text-muted-foreground">{claim.event_date}</span>
                  </div>

                  <p className="text-foreground/90 font-medium line-clamp-2 italic">
                    "{claim.raw_claim_text}"
                  </p>

                  <div className="flex items-center justify-between text-muted-foreground text-[11px] pt-1 border-t border-border/50">
                    <span className="font-mono uppercase font-semibold">{claim.discipline || t('review.unassigned')}</span>
                    <ProvenanceBadge channel={claim.input_channel} size="sm" />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-4 max-w-6xl mx-auto py-8">
        <Skeleton className="h-10 w-64 rounded-xl" />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Skeleton className="h-96 rounded-xl lg:col-span-2" />
          <Skeleton className="h-96 rounded-xl" />
        </div>
      </div>
    );
  }

  if (!event) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title={t('review.claimNotFound')}
        action={
          <Button onClick={() => navigate('/digest')} size="sm">
            {t('review.returnToDigest')}
          </Button>
        }
        className="max-w-xl mx-auto my-12"
      />
    );
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
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
            <span>·</span>
            <span>{t('review.claimIdLabel')}: {event.event_id}</span>
            <span>·</span>
            <span>{t('review.dateLabel')}: {event.event_date}</span>
            <span>·</span>
            <ProvenanceBadge channel={event.input_channel} size="sm" />
            {event.priority_score != null && (
              <>
                <span>·</span>
                <span className="inline-flex items-center gap-1 text-[11px] font-mono font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-700 dark:bg-red-950/70 dark:text-red-300 border border-red-200 dark:border-red-900/60">
                  <Flame className="w-3 h-3 text-red-500" />
                  Priority Score: {Math.round(event.priority_score)}{event.priority_rank ? ` (Rank #${event.priority_rank})` : ''}
                </span>
              </>
            )}
            {event.is_escalated && (
              <>
                <span>·</span>
                <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-amber-100 text-amber-800 dark:bg-amber-950/70 dark:text-amber-300 border border-amber-200 dark:border-amber-900/60">
                  Escalated Priority
                </span>
              </>
            )}
          </div>
          <h1 className="text-2xl font-bold text-foreground tracking-tight mt-0.5">
            {t('review.title')}
          </h1>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setIsQueueOpen((prev) => !prev)}
            className="text-xs h-9 gap-1.5"
            aria-expanded={isQueueOpen}
          >
            <ListOrdered className="w-3.5 h-3.5 text-primary" />
            {t('review.quickQueueTitle')} ({queueClaims.length})
            {isQueueOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={() => navigate('/digest')}
            className="text-xs h-9"
          >
            {t('review.backToDigest')}
          </Button>
        </div>
      </div>

      {/* Quick Queue Strip / Drawer */}
      {isQueueOpen && (
        <Card className="bg-card border-border shadow-xs">
          <CardHeader className="py-2 px-4 border-b border-border flex flex-row items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2">
              <ListOrdered className="w-4 h-4 text-primary" />
              <CardTitle className="text-xs font-bold text-foreground">
                {t('review.quickQueueTitle')}
              </CardTitle>
              <span className="text-[10px] bg-primary/10 text-primary border border-primary/20 px-2 py-0.5 rounded-full font-mono font-semibold">
                {filteredQueueClaims.length}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              {(['ALL', 'HIGH_PRIORITY', 'UNMATCHED'] as const).map((filter) => (
                <button
                  key={filter}
                  type="button"
                  onClick={() => setQueueFilter(filter)}
                  className={cn(
                    'px-2 py-0.5 text-[10px] font-bold rounded-md transition-all cursor-pointer border',
                    queueFilter === filter
                      ? 'bg-primary text-white border-primary shadow-2xs'
                      : 'bg-card border-border text-muted-foreground hover:text-foreground'
                  )}
                >
                  {filter === 'ALL' ? 'All' : filter === 'HIGH_PRIORITY' ? 'High Priority' : 'Unmatched'}
                </button>
              ))}
            </div>
          </CardHeader>
          <CardContent className="p-3">
            {isLoadingQueue ? (
              <div className="flex gap-3 overflow-x-auto pb-1">
                <Skeleton className="h-16 w-52 shrink-0 rounded-lg" />
                <Skeleton className="h-16 w-52 shrink-0 rounded-lg" />
                <Skeleton className="h-16 w-52 shrink-0 rounded-lg" />
              </div>
            ) : queueError ? (
              <ErrorState message={queueError} onRetry={loadQueue} retryText={t('common.retry')} />
            ) : filteredQueueClaims.length === 0 ? (
              <div className="text-xs text-muted-foreground text-center py-2">
                No claims match the selected filter.
              </div>
            ) : (
              <div className="flex gap-3 overflow-x-auto pb-2 pt-0.5">
                {filteredQueueClaims.map((claim) => {
                  const isSelected = claim.event_id === eventIdParam;
                  return (
                    <button
                      key={claim.event_id}
                      type="button"
                      onClick={() => handleSelectClaim(claim.event_id)}
                      aria-current={isSelected ? 'true' : undefined}
                      className={cn(
                        'shrink-0 text-left p-2.5 rounded-xl border transition-all text-xs w-60 space-y-1.5 cursor-pointer shadow-xs focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary',
                        isSelected
                          ? 'bg-primary/10 border-primary ring-1 ring-primary text-foreground'
                          : 'bg-card border-border hover:border-primary/50 text-foreground'
                      )}
                    >
                      <div className="flex items-center justify-between gap-1 flex-wrap">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className={cn('font-mono font-bold text-xs', isSelected ? 'text-primary' : 'text-foreground')}>
                            {claim.event_id}
                          </span>
                          <StatusBadge status={claim.status} size="sm" />
                          {claim.priority_score != null && (
                            <span className="inline-flex items-center gap-0.5 text-[9px] font-mono font-bold px-1.5 py-0.2 rounded-full bg-red-100 text-red-700 dark:bg-red-950/70 dark:text-red-300">
                              <Flame className="w-2.5 h-2.5 text-red-500" />
                              P{claim.priority_rank ?? 1}
                            </span>
                          )}
                          {claim.is_escalated && (
                            <span className="text-[9px] font-bold px-1 py-0.2 rounded bg-amber-100 text-amber-800 dark:bg-amber-950/70 dark:text-amber-300">
                              Escalated
                            </span>
                          )}
                        </div>
                      </div>
                      <p className="text-[11px] text-muted-foreground line-clamp-1 italic">
                        "{claim.raw_claim_text}"
                      </p>
                      <div className="flex items-center justify-between text-[10px] text-muted-foreground font-mono">
                        <span>{claim.discipline || t('review.unassigned')}</span>
                        <span>{claim.event_date}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* 4-Step Pipeline Flow Bar */}
      <div className="grid grid-cols-4 gap-2 text-center text-xs">
        <div className="p-3 rounded-xl bg-card border border-border text-foreground font-semibold flex items-center justify-center gap-1.5 shadow-xs">
          <FileText className="w-4 h-4 text-[#1565C0] dark:text-blue-400" /> 1. {t('review.step1')}
        </div>
        <div className="p-3 rounded-xl bg-teal-50/60 dark:bg-[#0A2340] border border-teal-200 dark:border-[#1E3A5F] text-[#061526] dark:text-[#F5F7FA] font-semibold flex items-center justify-center gap-1.5 shadow-xs">
          <Sparkles className="w-4 h-4 text-[#14B8A6] dark:text-[#22D3EE]" /> 2. {t('review.step2')}
        </div>
        <div className="p-3 rounded-xl bg-amber-50/60 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900/60 text-amber-800 dark:text-amber-300 font-semibold flex items-center justify-center gap-1.5 shadow-xs">
          <ShieldAlert className="w-4 h-4 text-amber-600" /> 3. {t('review.step3')}
        </div>
        <div className="p-3 rounded-xl bg-gradient-to-r from-[#FF7A18] to-[#FF941F] text-white font-bold flex items-center justify-center gap-1.5 shadow-xs">
          <CheckCircle2 className="w-4 h-4" /> 4. {t('review.step4')}
        </div>
      </div>

      {decisionSuccess && (
        <div className="p-6 rounded-2xl bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-900/60 text-foreground space-y-3 animate-in fade-in duration-200">
          <div className="flex items-center gap-3">
            <CheckCircle2 className="w-6 h-6 text-emerald-600 dark:text-emerald-400 shrink-0" />
            <div>
              <h3 className="font-bold text-base text-emerald-700 dark:text-emerald-300">{t('review.decisionCommitted')}</h3>
              <p className="text-xs text-muted-foreground">
                {t('review.decisionLoggedTo', { eventId: event.event_id, action })}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 pt-2 flex-wrap">
            {nextPendingClaim && (
              <Button
                size="sm"
                onClick={() => handleSelectClaim(nextPendingClaim.event_id)}
                className="bg-gradient-to-r from-[#FF7A18] to-[#FF941F] hover:from-[#E06810] hover:to-[#FF7A18] text-white text-xs gap-1.5 shadow-sm shadow-orange-500/25 font-semibold"
              >
                {t('review.reviewNextClaim')} ({nextPendingClaim.event_id})
                <ArrowRight className="w-3.5 h-3.5" />
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => navigate('/digest')}
              className="text-xs"
            >
              {t('review.returnToDigest')}
            </Button>
          </div>
        </div>
      )}

      {/* Main 3-Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* LEFT COLUMN: Field Claim Provenance */}
        <div className="lg:col-span-4 space-y-6">
          <Card>
            <CardHeader className="pb-3 border-b border-border">
              <CardTitle className="text-sm font-semibold flex items-center gap-2 text-foreground">
                <FileText className="w-4 h-4 text-accent" />
                {t('review.provenanceTitle')}
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-4 space-y-4 text-xs">
              <div className="p-3 bg-card-subtle rounded-xl border border-border font-medium text-foreground leading-relaxed">
                "{event.raw_claim_text}"
              </div>

              <div className="grid grid-cols-2 gap-3 text-muted-foreground">
                <div>
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="text-[10px] uppercase font-bold text-muted-foreground block">{t('review.discipline')}</span>
                    <FieldProvenanceBadge provenance={event.field_provenance?.discipline} />
                  </div>
                  <span className="font-mono text-foreground font-bold">{event.discipline || t('review.unassigned')}</span>
                </div>
                <div>
                  <span className="text-[10px] uppercase font-bold text-muted-foreground block mb-1">{t('review.channel')}</span>
                  <ProvenanceBadge channel={event.input_channel} size="sm" />
                </div>
                <div>
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="text-[10px] uppercase font-bold text-muted-foreground block">{t('review.claimMode')}</span>
                    <FieldProvenanceBadge provenance={event.field_provenance?.claim_mode} />
                  </div>
                  <span className="font-mono text-foreground">{event.claim_mode}</span>
                </div>
                <div>
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="text-[10px] uppercase font-bold text-muted-foreground block">{t('review.eventType')}</span>
                    <FieldProvenanceBadge provenance={event.field_provenance?.event_type} />
                  </div>
                  <span className="font-mono text-foreground">{event.event_type}</span>
                </div>
                <div>
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="text-[10px] uppercase font-bold text-muted-foreground block">{t('review.location')}</span>
                    <FieldProvenanceBadge provenance={event.field_provenance?.location} />
                  </div>
                  <span className="text-foreground">{event.location || t('review.notAvailable')}</span>
                </div>
                <div>
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="text-[10px] uppercase font-bold text-muted-foreground block">{t('review.assetTag')}</span>
                    <FieldProvenanceBadge provenance={event.field_provenance?.asset_tag} />
                  </div>
                  <span className="font-mono text-foreground">{event.asset_tag || t('review.notAvailable')}</span>
                </div>
                <div>
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="text-[10px] uppercase font-bold text-muted-foreground block">Claimed progress</span>
                    <FieldProvenanceBadge
                      provenance={event.field_provenance?.claimed_pct ?? event.field_provenance?.claimed_quantity}
                    />
                  </div>
                  <span className="font-mono text-foreground">
                    {event.claimed_pct != null
                      ? `${event.claimed_pct}%`
                      : event.claimed_quantity != null
                        ? `${event.claimed_quantity} ${event.claimed_uom ?? ''}`
                        : t('review.notAvailable')}
                  </span>
                </div>
                <div>
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="text-[10px] uppercase font-bold text-muted-foreground block">Matched activity</span>
                    <FieldProvenanceBadge provenance={event.field_provenance?.activity_id} />
                  </div>
                  <span className="font-mono text-foreground">{event.matched_activity_id || t('review.notAvailable')}</span>
                </div>
              </div>

              {/* Source Evidence Context */}
              <SourceReferenceCard event={event} />

              {/* Feature 32: Smart Priority Reasoning Callout */}
              {event.priority_reasons && event.priority_reasons.length > 0 && (
                <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-foreground space-y-1.5">
                  <div className="flex items-center gap-1.5 text-xs font-bold text-red-600 dark:text-red-400">
                    <Flame className="w-4 h-4 text-red-500" />
                    <span>Priority Ranking Factors</span>
                  </div>
                  <ul className="list-disc list-inside text-[11px] text-muted-foreground space-y-0.5">
                    {event.priority_reasons.map((reason, idx) => (
                      <li key={idx}>{reason}</li>
                    ))}
                  </ul>
                </div>
              )}

              {event.delay_reason && (
                <div className="p-3 rounded-xl bg-status-warning/10 border border-status-warning/30 text-foreground space-y-1">
                  <span className="font-bold text-[10px] uppercase block text-status-warning">{t('review.statedDelayReason')}</span>
                  <p className="text-xs">{event.delay_reason}</p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Validation & Conflict Checks */}
          <Card>
            <CardHeader className="pb-3 border-b border-border">
              <CardTitle className="text-sm font-semibold flex items-center gap-2 text-foreground">
                <ShieldAlert className="w-4 h-4 text-status-warning" />
                {t('review.validationTitle')}
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-4 space-y-3 text-xs">
              {issues.length === 0 && conflicts.length === 0 ? (
                <div className="p-3 bg-status-approved/10 border border-status-approved/30 text-status-approved rounded-xl flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-status-approved" />
                  <span className="font-semibold">{t('review.noWarnings')}</span>
                </div>
              ) : (
                <>
                  {issues.map((iss) => (
                    <div key={iss.issue_id} className="p-3 bg-status-warning/10 border border-status-warning/30 text-foreground rounded-xl space-y-1">
                      <div className="font-bold text-[10px] uppercase flex items-center gap-1.5 text-status-warning">
                        <AlertTriangle className="w-3.5 h-3.5" />
                        {iss.rule_code || 'RULE_WARNING'}
                      </div>
                      <p className="text-xs">{iss.description}</p>
                    </div>
                  ))}

                  {conflicts.map((cnf) => (
                    <div key={cnf.conflict_id} className="p-3 bg-status-error/10 border border-status-error/30 text-foreground rounded-xl space-y-1">
                      <div className="font-bold text-[10px] uppercase flex items-center gap-1.5 text-status-error">
                        <ShieldAlert className="w-3.5 h-3.5" />
                        {t('review.duplicateClaim', { variance: cnf.variance_pct })}
                      </div>
                      <p className="text-xs">
                        {t('review.conflictingEvent', { eventId: cnf.event_id_b, valueB: cnf.value_b, valueA: cnf.value_a })}
                      </p>
                    </div>
                  ))}
                </>
              )}
            </CardContent>
          </Card>
        </div>

        {/* MIDDLE COLUMN: Match Selection & WBS Granularity Bridge */}
        <div className="lg:col-span-4 space-y-4">
          {/* Mode Switcher (only claims decomposed by the WBS bridge have a split view) */}
          {hasSplits && (
          <div className="flex items-center bg-card-subtle p-1 rounded-xl border border-border">
            <button
              type="button"
              onClick={() => setMatchMode('direct')}
              className={cn(
                'flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-semibold rounded-lg transition-all',
                matchMode === 'direct'
                  ? 'bg-gradient-to-r from-[#FF7A18] to-[#FF941F] text-white shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              <Sparkles className="w-3.5 h-3.5" />
              Direct Match
            </button>
            <button
              type="button"
              onClick={() => setMatchMode('split')}
              className={cn(
                'flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-semibold rounded-lg transition-all',
                matchMode === 'split'
                  ? 'bg-gradient-to-r from-[#FF7A18] to-[#FF941F] text-white shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              <Layers className="w-3.5 h-3.5" />
              WBS Split Allocation
            </button>
          </div>
          )}

          {matchMode === 'split' ? (
            <WBSSplitEditor
              claim={event}
              activities={activities}
              onSuccess={() => {
                // Split edited + claim re-checked: refresh the claim (priority/validation) and the queue.
                if (eventIdParam) loadData(eventIdParam);
                loadQueue();
              }}
              onCancel={() => setMatchMode('direct')}
            />
          ) : (
            <div className="space-y-4">
              <Card>
                <CardHeader className="pb-3 border-b border-border">
                  <CardTitle className="text-sm font-semibold flex items-center justify-between text-foreground">
                    <span className="flex items-center gap-2">
                      <Sparkles className="w-4 h-4 text-primary" />
                      {t('review.topMatchesTitle')}
                    </span>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setIsAskWhyOpen((prev) => !prev)}
                        className={cn(
                          'inline-flex items-center gap-1 text-[10px] font-bold px-2.5 py-0.5 rounded-full border transition-all cursor-pointer',
                          isAskWhyOpen
                            ? 'bg-[#1565C0] text-white border-[#1565C0] shadow-xs'
                            : 'bg-blue-50 dark:bg-blue-950/60 text-[#003087] dark:text-blue-300 border-blue-200 dark:border-blue-900/60 hover:bg-blue-100 dark:hover:bg-blue-900/80'
                        )}
                        title="Explain graph reasoning for this match"
                      >
                        <HelpCircle className="w-3 h-3" />
                        Ask Why
                      </button>
                      <span className="text-[10px] bg-primary/10 text-primary border border-primary/30 px-2 py-0.5 rounded-full font-mono font-bold">
                        {t('review.faissMatching')}
                      </span>
                    </div>
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-4 space-y-3">
                {/* Feature 2: Supervisor Planner-Review Function for Unmatched / New Activities */}
                {(!event.matched_activity_id || event.status === 'UNMATCHED' || candidates.length === 0) && (
                  <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/40 text-foreground space-y-3">
                    <div className="flex items-start gap-2.5">
                      <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
                      <div>
                        <span className="font-bold text-xs text-amber-800 dark:text-amber-300 block">
                          Unmatched / New Scope — Supervisor Planner-Review Action Required
                        </span>
                        <span className="text-[11px] text-muted-foreground leading-relaxed block mt-0.5">
                          This field claim could not be auto-matched to a single baseline activity with high confidence. As Supervisor, perform the planner-review function: bind an existing activity from the schedule master below, or allocate via WBS split.
                        </span>
                      </div>
                    </div>

                    {/* Searchable / Selectable Activity Master dropdown */}
                    <div className="space-y-1.5 pt-1 border-t border-amber-500/20">
                      <Label className="text-[11px] font-bold text-foreground block">
                        Bind to Schedule Activity Master:
                      </Label>
                      <select
                        value={selectedActivityId}
                        onChange={(e) => setSelectedActivityId(e.target.value)}
                        className="w-full h-9 px-3 rounded-lg border border-slate-300 dark:border-[#214766] bg-white dark:bg-[#0B2742] text-xs font-mono font-bold text-foreground focus:ring-1 focus:ring-primary"
                      >
                        <option value="">-- Select Schedule Activity to Bind --</option>
                        {activities.map((act) => (
                          <option key={act.activity_id} value={act.activity_id}>
                            {act.activity_id} — {act.activity_name} ({act.discipline} | WBS: {act.wbs_code || 'N/A'})
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="flex items-center gap-2 pt-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => setMatchMode('split')}
                        className="text-xs h-7 gap-1 font-semibold border-amber-400 dark:border-amber-700 text-amber-800 dark:text-amber-300 hover:bg-amber-100 dark:hover:bg-amber-950/40"
                      >
                        <Layers className="w-3 h-3" />
                        Decompose via WBS Split
                      </Button>
                    </div>
                  </div>
                )}

                {candidates.length > 0 && (
                  <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider px-1">
                    Machine Match Candidates ({candidates.length})
                  </div>
                )}

                {candidates.map((cand) => {
                  const isSelected = selectedActivityId === cand.activity_id;

                  return (
                    <div
                      key={cand.candidate_id}
                      onClick={() => setSelectedActivityId(cand.activity_id)}
                      className={cn(
                        'p-4 rounded-xl border transition-all cursor-pointer relative space-y-2.5 shadow-xs',
                        isSelected
                          ? 'bg-primary/5 border-primary shadow-sm ring-1 ring-primary'
                          : 'bg-card border-border hover:border-primary/60'
                      )}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-bold bg-muted text-muted-foreground px-2 py-0.5 rounded-md font-mono">
                            #{cand.rank_order}
                          </span>
                          <span className="font-mono font-bold text-sm text-foreground">{cand.activity_id}</span>
                          {cand.match_tier && (
                            <span className="text-[10px] font-bold bg-primary/10 text-primary border border-primary/30 px-1.5 py-0.5 rounded font-mono uppercase">
                              {cand.match_tier}
                            </span>
                          )}
                        </div>

                        <div className="flex items-center gap-1.5">
                          {isSelected && <Check className="w-4 h-4 text-primary font-bold" />}
                        </div>
                      </div>

                      {/* Integrated ConfidenceBar */}
                      <ConfidenceBar score={cand.composite_confidence} />

                      <div className="grid grid-cols-4 gap-1 text-[10px] font-mono text-muted-foreground pt-2 border-t border-border/60">
                        <div>{t('review.semShort')}: <span className="font-bold text-foreground">{Math.round((cand.semantic_score || 0) * 100)}%</span></div>
                        <div>{t('review.fuzShort')}: <span className="font-bold text-foreground">{Math.round((cand.fuzzy_score || 0) * 100)}%</span></div>
                        <div>{t('review.locShort')}: <span className="font-bold text-foreground">{Math.round((cand.location_score || 0) * 100)}%</span></div>
                        <div>{t('review.disShort')}: <span className="font-bold text-foreground">{Math.round((cand.discipline_score || 0) * 100)}%</span></div>
                      </div>

                      {cand.supporting_signals && (
                        <p className="text-[11px] text-status-approved leading-tight">
                          ✓ {cand.supporting_signals}
                        </p>
                      )}
                    </div>
                  );
                })}
              </CardContent>
            </Card>

            {isAskWhyOpen && (
              <AskWhyPanel
                eventId={event.event_id}
                selectedActivityId={selectedActivityId}
                onClose={() => setIsAskWhyOpen(false)}
              />
            )}
          </div>
        )}
      </div>

        {/* RIGHT COLUMN: Human Supervisor Action Form */}
        <div className="lg:col-span-4 space-y-6">
          <Card className="border-2 border-primary/60 shadow-md">
            <CardHeader className="pb-3 border-b border-border">
              <CardTitle className="text-sm font-bold flex items-center gap-2 text-foreground">
                <CheckCircle2 className="w-4 h-4 text-primary" />
                {t('review.decisionTitle')}
              </CardTitle>
              <CardDescription className="text-muted-foreground text-xs">
                {t('review.decisionSubtitle')}
              </CardDescription>
            </CardHeader>

            <CardContent className="pt-4 space-y-4">
              {submitError && (
                <ErrorState
                  message={submitError}
                  onRetry={() => setSubmitError(null)}
                  retryText="Dismiss"
                />
              )}

              <form onSubmit={handleSubmitDecision} className="space-y-4 text-xs">
                <div className="space-y-1.5">
                  <Label className="text-xs text-foreground font-bold">{t('review.selectedActivity')}</Label>
                  <Input
                    value={selectedActivityId}
                    onChange={(e) => setSelectedActivityId(e.target.value)}
                    className="font-mono text-primary font-bold text-sm h-9 rounded-lg"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label className="text-xs text-foreground font-bold">{t('review.actionType')}</Label>
                  <div className="grid grid-cols-2 gap-2">
                    {(['APPROVE', 'EDIT', 'HOLD', 'REJECT'] as DecisionAction[]).map((act) => (
                      <button
                        key={act}
                        type="button"
                        onClick={() => setAction(act)}
                        className={cn(
                          'p-2.5 rounded-lg border text-xs font-bold transition-all text-center uppercase tracking-wider cursor-pointer',
                          action === act
                            ? act === 'APPROVE'
                              ? 'bg-emerald-600 border-emerald-600 text-white shadow-xs'
                              : act === 'EDIT'
                              ? 'bg-[#1565C0] border-[#1565C0] text-white shadow-xs'
                              : act === 'HOLD'
                              ? 'bg-amber-600 border-amber-600 text-white shadow-xs'
                              : 'bg-rose-600 border-rose-600 text-white shadow-xs'
                            : 'bg-card border-border text-foreground hover:bg-card-subtle'
                        )}
                      >
                        {act === 'APPROVE'
                          ? t('review.actionApprove')
                          : act === 'EDIT'
                          ? t('review.actionEdit')
                          : act === 'HOLD'
                          ? t('review.actionHold')
                          : t('review.actionReject')}
                      </button>
                    ))}
                  </div>
                </div>

                {action === 'EDIT' && (
                  <div className="grid grid-cols-2 gap-3 p-3 bg-card-subtle rounded-xl border border-border">
                    <div className="space-y-1">
                      <Label className="text-[11px] text-muted-foreground">{t('review.approvedPct')}</Label>
                      <Input
                        type="number"
                        value={approvedPct}
                        onChange={(e) => setApprovedPct(e.target.value !== '' ? Number(e.target.value) : '')}
                        className="font-mono h-8"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-[11px] text-muted-foreground">{t('review.approvedQty')}</Label>
                      <Input
                        type="number"
                        value={approvedQty}
                        onChange={(e) => setApprovedQty(e.target.value !== '' ? Number(e.target.value) : '')}
                        className="font-mono h-8"
                      />
                    </div>
                  </div>
                )}

                <div className="space-y-1.5">
                  <Label className="text-xs text-foreground font-bold flex items-center justify-between">
                    <span>{t('review.justification')}</span>
                    <span className="text-[10px] text-destructive uppercase font-bold">{t('review.mandatory')}</span>
                  </Label>
                  <Textarea
                    rows={4}
                    placeholder={t('review.justificationPlaceholder')}
                    value={justification}
                    onChange={(e) => setJustification(e.target.value)}
                    className="text-xs"
                  />
                </div>

                {currentScheduleVersion.isImmutable && (
                  <div className="p-3 bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-800 rounded-xl flex items-center gap-2 text-xs text-amber-800 dark:text-amber-200">
                    <Lock className="w-4 h-4 shrink-0 text-amber-600" />
                    <span>Schedule Version <strong>{currentScheduleVersion.versionNumber}</strong> is archived (read-only). Review decisions cannot be committed.</span>
                  </div>
                )}

                <Button
                  type="submit"
                  disabled={isSubmitting || decisionSuccess || currentScheduleVersion.isImmutable}
                  isLoading={isSubmitting}
                  className="w-full font-bold h-10 shadow-xs"
                >
                  {currentScheduleVersion.isImmutable
                    ? 'Schedule Version is Read-Only'
                    : isSubmitting
                    ? t('review.recordingDecision')
                    : t('review.commitDecision')}
                </Button>
              </form>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Feature 31: Multi-Source Evidence Fusion & Construction Knowledge Graph Section */}
      <div className="space-y-4 pt-4 border-t border-border">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-2 bg-card p-1 rounded-xl border border-border">
            <button
              type="button"
              onClick={() => setBottomTab('evidence')}
              className={cn(
                'flex items-center gap-2 px-3.5 py-2 text-xs font-bold rounded-lg border transition-all',
                bottomTab === 'evidence'
                  ? 'bg-blue-50 dark:bg-blue-950/60 border-blue-200 dark:border-blue-900 text-blue-700 dark:text-blue-300 shadow-xs'
                  : 'bg-transparent border-transparent text-muted-foreground hover:text-foreground'
              )}
            >
              <ShieldCheck className="w-4 h-4 text-blue-500" />
              Multi-Source Evidence Fusion
            </button>

            <button
              type="button"
              onClick={() => setBottomTab('graph')}
              className={cn(
                'flex items-center gap-2 px-3.5 py-2 text-xs font-bold rounded-lg border transition-all cursor-pointer',
                bottomTab === 'graph'
                  ? 'bg-blue-50 dark:bg-blue-950/60 border-blue-200 dark:border-blue-900 text-[#003087] dark:text-blue-300 shadow-xs'
                  : 'bg-transparent border-transparent text-muted-foreground hover:text-foreground'
              )}
            >
              <Network className="w-4 h-4 text-[#1565C0]" />
              Construction Knowledge Graph
            </button>
          </div>
        </div>

        <ErrorBoundary
          label={bottomTab === 'evidence' ? 'Evidence panel' : 'Knowledge graph'}
          resetKey={`${event.event_id}:${bottomTab}`}
        >
          {bottomTab === 'evidence' ? (
            <EvidencePanel eventId={event.event_id} />
          ) : (
            <KnowledgeGraph eventId={event.event_id} />
          )}
        </ErrorBoundary>
      </div>
    </div>
  );
}
