import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ShieldAlert,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  FileCheck,
  Eye,
  Clock,
  X,
  FileText,
  Lock,
  Unlock,
  AlertOctagon,
  Check,
  Ban,
} from 'lucide-react';
import {
  QualityGate,
  QualityGateType,
  QualityGateStatus,
  qualityGatesApi,
  UserRole,
} from '@/api';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

interface QualityGateModalProps {
  isOpen: boolean;
  onClose: () => void;
  activityId: string;
  activityName: string;
  gates: QualityGate[];
  userRole?: UserRole | string;
  onGateUpdated?: () => void;
}

export function QualityGateModal({
  isOpen,
  onClose,
  activityId,
  activityName,
  gates,
  userRole = 'SUPERVISOR',
  onGateUpdated,
}: QualityGateModalProps) {
  const { t } = useTranslation();
  const [waivingGateId, setWaivingGateId] = useState<string | null>(null);
  const [waiverJustification, setWaiverJustification] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  if (!isOpen) return null;

  const isSupervisor = userRole === 'SUPERVISOR';

  const handleCompleteGate = async (gate: QualityGate) => {
    setIsSubmitting(true);
    setActionError(null);
    try {
      await qualityGatesApi.completeGate(gate.id);
      if (onGateUpdated) onGateUpdated();
    } catch (err: any) {
      setActionError(err.message || 'Failed to complete Quality Gate');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleWaiveGate = async (gate: QualityGate) => {
    if (!waiverJustification.trim()) {
      setActionError('Waiver justification is mandatory.');
      return;
    }
    setIsSubmitting(true);
    setActionError(null);
    try {
      await qualityGatesApi.waiveGate(gate.id, waiverJustification.trim());
      setWaivingGateId(null);
      setWaiverJustification('');
      if (onGateUpdated) onGateUpdated();
    } catch (err: any) {
      setActionError(err.message || 'Failed to waive Quality Gate');
    } finally {
      setIsSubmitting(false);
    }
  };

  const getGateTypeBadge = (type: QualityGateType, required: boolean) => {
    switch (type) {
      case 'HOLD_POINT':
        return (
          <Badge
            variant="outline"
            className="text-[10px] font-bold uppercase tracking-wider bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300 border-rose-300 dark:border-rose-800 flex items-center gap-1"
          >
            <Lock className="w-3 h-3 text-rose-500" />
            Hold Point {required && <span className="text-[9px] text-rose-600 dark:text-rose-400 font-extrabold">(Req)</span>}
          </Badge>
        );
      case 'WITNESS_POINT':
        return (
          <Badge
            variant="outline"
            className="text-[10px] font-bold uppercase tracking-wider bg-blue-50 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300 border-blue-300 dark:border-blue-800 flex items-center gap-1"
          >
            <Eye className="w-3 h-3 text-blue-500" />
            Witness Point
          </Badge>
        );
      case 'ITP_CHECK':
        return (
          <Badge
            variant="outline"
            className="text-[10px] font-bold uppercase tracking-wider bg-teal-50 text-teal-700 dark:bg-teal-950/60 dark:text-teal-300 border-teal-300 dark:border-teal-800 flex items-center gap-1"
          >
            <FileCheck className="w-3 h-3 text-teal-500" />
            ITP Check
          </Badge>
        );
      case 'REVIEW_POINT':
        return (
          <Badge
            variant="outline"
            className="text-[10px] font-bold uppercase tracking-wider bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300 border-amber-300 dark:border-amber-800 flex items-center gap-1"
          >
            <FileText className="w-3 h-3 text-amber-500" />
            Review Point
          </Badge>
        );
    }
  };

  const getGateStatusBadge = (status: QualityGateStatus) => {
    switch (status) {
      case 'COMPLETED':
        return (
          <Badge
            variant="outline"
            className="text-[10px] font-bold uppercase tracking-wider bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-400 border-emerald-300 dark:border-emerald-800 flex items-center gap-1"
          >
            <CheckCircle2 className="w-3 h-3 text-emerald-500" />
            Completed
          </Badge>
        );
      case 'WAIVED':
        return (
          <Badge
            variant="outline"
            className="text-[10px] font-bold uppercase tracking-wider bg-purple-50 text-purple-700 dark:bg-purple-950/60 dark:text-purple-300 border-purple-300 dark:border-purple-800 flex items-center gap-1"
          >
            <Unlock className="w-3 h-3 text-purple-500" />
            Waived
          </Badge>
        );
      case 'READY':
        return (
          <Badge
            variant="outline"
            className="text-[10px] font-bold uppercase tracking-wider bg-blue-50 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300 border-blue-300 dark:border-blue-800 flex items-center gap-1"
          >
            <Clock className="w-3 h-3 text-blue-500" />
            Ready for Sign-off
          </Badge>
        );
      case 'BLOCKED':
        return (
          <Badge
            variant="outline"
            className="text-[10px] font-bold uppercase tracking-wider bg-rose-100 text-rose-800 dark:bg-rose-900/60 dark:text-rose-200 border-rose-400 dark:border-rose-700 flex items-center gap-1"
          >
            <AlertOctagon className="w-3 h-3 text-rose-600" />
            Blocked
          </Badge>
        );
      case 'PENDING':
      default:
        return (
          <Badge
            variant="outline"
            className="text-[10px] font-bold uppercase tracking-wider bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-400 border-amber-300 dark:border-amber-800 flex items-center gap-1"
          >
            <Clock className="w-3 h-3 text-amber-500" />
            Pending
          </Badge>
        );
    }
  };

  const completedCount = gates.filter((g) => g.status === 'COMPLETED' || g.status === 'WAIVED').length;
  const hasBlockingHoldPoint = gates.some((g) => g.gateType === 'HOLD_POINT' && g.required && (g.status === 'PENDING' || g.status === 'BLOCKED'));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs animate-in fade-in duration-200">
      <div className="bg-white dark:bg-[#071A2D] rounded-2xl border border-slate-300 dark:border-[#1E3A5F] shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="px-5 py-4 border-b border-slate-200 dark:border-[#1E3A5F] flex items-center justify-between bg-slate-50/70 dark:bg-[#0A2238]/60">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-purple-50 dark:bg-purple-950/60 text-purple-600 dark:text-purple-400 border border-purple-200 dark:border-purple-800">
              <ShieldAlert className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-base font-extrabold text-[#071A2D] dark:text-[#F5F7FA]">
                  {t('quality.modalTitle', { defaultValue: 'Quality Gates & ITP Hold Points' })}
                </h3>
                <span className="font-mono text-xs font-bold text-blue-600 dark:text-blue-400">
                  {activityId}
                </span>
              </div>
              <p className="text-xs text-muted-foreground truncate max-w-md">
                {activityName}
              </p>
            </div>
          </div>

          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            className="h-8 w-8 text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
          >
            <X className="w-4 h-4" />
          </Button>
        </div>

        {/* Progress Strip & Hold Point Banner */}
        <div className="p-4 border-b border-slate-200 dark:border-[#1E3A5F]/60 bg-white dark:bg-[#071A2D] space-y-2.5">
          <div className="flex items-center justify-between text-xs font-bold">
            <span className="text-slate-600 dark:text-slate-300">
              {t('quality.completionProgress', { defaultValue: 'Gate Clearance:' })}{' '}
              <span className="text-[#071A2D] dark:text-[#F5F7FA] font-mono">
                {completedCount} / {gates.length} Completed
              </span>
            </span>
            <span className="font-mono text-xs text-purple-600 dark:text-purple-400">
              {gates.length > 0 ? Math.round((completedCount / gates.length) * 100) : 0}%
            </span>
          </div>

          <div className="w-full bg-slate-200 dark:bg-[#1E3A5F] h-2 rounded-full overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full transition-all',
                completedCount === gates.length ? 'bg-emerald-500' : 'bg-purple-600'
              )}
              style={{ width: `${gates.length > 0 ? (completedCount / gates.length) * 100 : 0}%` }}
            />
          </div>

          {hasBlockingHoldPoint && (
            <div className="p-2.5 rounded-lg bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-900/60 flex items-start gap-2 text-xs text-rose-900 dark:text-rose-200">
              <AlertTriangle className="w-4 h-4 text-rose-600 shrink-0 mt-0.5" />
              <div>
                <strong>{t('quality.holdPointPendingTitle', { defaultValue: 'Required Hold Point Pending' })}:</strong>{' '}
                {t('quality.holdPointPendingDesc', {
                  defaultValue: 'This activity cannot be finalized (100% completion) until all required hold points are completed or waived by the Supervisor.',
                })}
              </div>
            </div>
          )}
        </div>

        {/* Gates List */}
        <div className="p-5 overflow-y-auto flex-1 space-y-3.5">
          {actionError && (
            <div className="p-3 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs font-semibold flex items-center gap-2">
              <XCircle className="w-4 h-4 shrink-0" />
              <span>{actionError}</span>
            </div>
          )}

          {gates.length === 0 ? (
            <div className="py-8 text-center text-muted-foreground text-xs">
              {t('quality.noGates', { defaultValue: 'No Quality Gates or ITP Hold Points attached to this activity.' })}
            </div>
          ) : (
            gates.map((gate) => {
              const isResolved = gate.status === 'COMPLETED' || gate.status === 'WAIVED';
              const isWaivingThis = waivingGateId === gate.id;

              return (
                <div
                  key={gate.id}
                  className={cn(
                    'p-4 rounded-xl border transition-all text-xs space-y-2.5',
                    gate.status === 'COMPLETED'
                      ? 'bg-emerald-50/40 dark:bg-emerald-950/20 border-emerald-200 dark:border-emerald-900/50'
                      : gate.status === 'WAIVED'
                      ? 'bg-purple-50/40 dark:bg-purple-950/20 border-purple-200 dark:border-purple-900/50'
                      : gate.status === 'BLOCKED'
                      ? 'bg-rose-50/40 dark:bg-rose-950/20 border-rose-300 dark:border-rose-900/60'
                      : 'bg-white dark:bg-[#0E2742] border-slate-200 dark:border-[#1E3A5F]'
                  )}
                >
                  <div className="flex items-start justify-between gap-2 flex-wrap">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono font-bold text-[11px] text-slate-500 dark:text-slate-400">
                          #{gate.sequence}
                        </span>
                        <h4 className="font-bold text-sm text-[#071A2D] dark:text-[#F5F7FA]">
                          {gate.name}
                        </h4>
                      </div>
                      <p className="text-xs text-muted-foreground leading-relaxed">
                        {gate.description}
                      </p>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0 flex-wrap">
                      {getGateTypeBadge(gate.gateType, gate.required)}
                      {getGateStatusBadge(gate.status)}
                    </div>
                  </div>

                  {/* Metadata info */}
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500 dark:text-slate-400 pt-1 border-t border-slate-200/60 dark:border-[#1E3A5F]/60">
                    {gate.dueDate && <span>Due: <strong>{gate.dueDate}</strong></span>}
                    {gate.ownerRole && <span>Responsible: <strong>{gate.ownerRole}</strong></span>}
                    {gate.evidenceRequired && (
                      <span className="text-blue-600 dark:text-blue-400 font-semibold flex items-center gap-1">
                        <FileCheck className="w-3 h-3" /> Evidence Required
                      </span>
                    )}
                    {gate.completedAt && (
                      <span className="text-emerald-600 dark:text-emerald-400 font-semibold">
                        Completed: {new Date(gate.completedAt).toLocaleDateString()}
                      </span>
                    )}
                    {gate.waiverJustification && (
                      <span className="text-purple-600 dark:text-purple-400 italic">
                        Waiver: "{gate.waiverJustification}"
                      </span>
                    )}
                  </div>

                  {/* Supervisor Action Controls */}
                  {isSupervisor && !isResolved && (
                    <div className="pt-2 border-t border-slate-200/60 dark:border-[#1E3A5F]/60 flex flex-col sm:flex-row sm:items-center justify-end gap-2">
                      {!isWaivingThis ? (
                        <>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setWaivingGateId(gate.id);
                              setWaiverJustification('');
                              setActionError(null);
                            }}
                            className="h-7 text-xs border-purple-300 dark:border-purple-800 text-purple-700 dark:text-purple-300 hover:bg-purple-50 dark:hover:bg-purple-950/40"
                          >
                            <Unlock className="w-3 h-3 mr-1" />
                            {t('quality.waiveGate', { defaultValue: 'Waive Gate' })}
                          </Button>
                          <Button
                            size="sm"
                            onClick={() => handleCompleteGate(gate)}
                            disabled={isSubmitting}
                            className="h-7 text-xs bg-emerald-600 hover:bg-emerald-700 text-white font-bold"
                          >
                            <Check className="w-3 h-3 mr-1" />
                            {t('quality.markComplete', { defaultValue: 'Mark Complete' })}
                          </Button>
                        </>
                      ) : (
                        <div className="w-full space-y-2 p-2.5 rounded-lg bg-purple-50/70 dark:bg-purple-950/40 border border-purple-300 dark:border-purple-800">
                          <Label className="text-[11px] font-bold text-purple-900 dark:text-purple-200">
                            {t('quality.waiverJustificationRequired', {
                              defaultValue: 'Waiver Justification & Technical Basis (Mandatory):',
                            })}
                          </Label>
                          <Textarea
                            value={waiverJustification}
                            onChange={(e) => setWaiverJustification(e.target.value)}
                            placeholder="e.g. Hold point waived due to approved QA/QC exception certificate ref QA-2026-089."
                            rows={2}
                            className="text-xs bg-white dark:bg-[#071A2D] border-purple-300 dark:border-purple-800"
                          />
                          <div className="flex items-center justify-end gap-2">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                setWaivingGateId(null);
                                setWaiverJustification('');
                              }}
                              className="h-7 text-xs"
                            >
                              {t('common.cancel', { defaultValue: 'Cancel' })}
                            </Button>
                            <Button
                              size="sm"
                              onClick={() => handleWaiveGate(gate)}
                              disabled={isSubmitting || !waiverJustification.trim()}
                              className="h-7 text-xs bg-purple-600 hover:bg-purple-700 text-white font-bold"
                            >
                              <Unlock className="w-3 h-3 mr-1" />
                              {t('quality.confirmWaiver', { defaultValue: 'Confirm Waiver' })}
                            </Button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-slate-200 dark:border-[#1E3A5F] bg-slate-50/70 dark:bg-[#0A2238]/60 flex items-center justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={onClose}
            className="text-xs font-semibold"
          >
            {t('common.close', { defaultValue: 'Close' })}
          </Button>
        </div>
      </div>
    </div>
  );
}
