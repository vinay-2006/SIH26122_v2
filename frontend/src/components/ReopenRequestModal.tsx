import React, { useState } from 'react';
import { useAuth } from '@/auth/AuthProvider';
import { reopenApi, ScheduleActivity, ReopenRequest } from '../api';
import { ExecutionStateBadge } from './ExecutionStateBadge';
import { AlertTriangle, Lock, ShieldAlert, CheckCircle, X } from 'lucide-react';

interface ReopenRequestModalProps {
  activity: ScheduleActivity;
  eventId?: string;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (request: ReopenRequest) => void;
}

const REOPEN_REASONS = [
  'Scope Modification / Additional Work Required',
  'Rework / QC Rectification Work',
  'Testing & Commissioning Verification Extension',
  'Inadvertent Early Closure / Punchlist Work',
  'Client / PMIS Variance Reconciliation',
];

export const ReopenRequestModal: React.FC<ReopenRequestModalProps> = ({
  activity,
  eventId,
  isOpen,
  onClose,
  onSuccess,
}) => {
  const { user } = useAuth();
  const [reason, setReason] = useState(REOPEN_REASONS[0]);
  const [justification, setJustification] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!justification.trim()) {
      setError('Please provide a formal justification for reopening this completed activity.');
      return;
    }

    try {
      setIsSubmitting(true);
      setError(null);
      const req = await reopenApi.createRequest({
        activity_id: activity.activity_id,
        reason,
        justification: justification.trim(),
        requested_by_role: user?.role || 'SITE_ENGINEER',
        requested_by_name: user?.full_name || 'Site Engineer',
        event_id: eventId,
      });
      onSuccess(req);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to submit reopen request');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/60 backdrop-blur-sm animate-fade-in overflow-y-auto">
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh] my-auto">
        {/* Header - Fixed */}
        <div className="p-4 sm:p-5 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-amber-500/5 shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-amber-100 dark:bg-amber-950/60 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-800/80 shrink-0">
              <ShieldAlert className="w-6 h-6" />
            </div>
            <div>
              <h3 className="font-semibold text-slate-900 dark:text-slate-100 text-base sm:text-lg leading-tight">
                Request Activity Reopen
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Completed Activity Protection & Exception Flow
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors shrink-0"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form Body - Scrollable */}
        <form onSubmit={handleSubmit} className="flex flex-col flex-1 min-h-0 overflow-hidden">
          <div className="p-4 sm:p-6 overflow-y-auto space-y-4 sm:space-y-5 flex-1 min-h-0 text-sm">
            {error && (
              <div className="p-3 bg-red-50 dark:bg-red-950/50 border border-red-200 dark:border-red-900/60 rounded-xl text-xs text-red-700 dark:text-red-300 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            )}

            {/* Activity Target Card */}
            <div className="p-4 rounded-xl bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/60 space-y-2.5">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="font-mono text-xs font-bold text-slate-600 dark:text-slate-300 bg-white dark:bg-slate-900 px-2 py-0.5 rounded border border-slate-200 dark:border-slate-700">
                  {activity.activity_id}
                </span>
                <ExecutionStateBadge state={activity.execution_state || 'COMPLETED'} size="sm" />
              </div>
              <h4 className="font-medium text-slate-900 dark:text-slate-100 text-sm">
                {activity.activity_name}
              </h4>
              <div className="text-xs text-slate-500 dark:text-slate-400 flex flex-wrap gap-x-4 gap-y-1">
                <span>Discipline: <strong className="text-slate-700 dark:text-slate-200">{activity.discipline}</strong></span>
                <span>WBS: <strong className="text-slate-700 dark:text-slate-200">{activity.wbs_code || 'Root'}</strong></span>
                <span>Location: <strong className="text-slate-700 dark:text-slate-200">{activity.location || 'Site'}</strong></span>
              </div>
            </div>

            {/* Locked Actuals Notice */}
            <div className="p-3.5 rounded-xl bg-purple-500/5 border border-purple-200 dark:border-purple-900/40 space-y-1.5">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-purple-800 dark:text-purple-300">
                <Lock className="w-3.5 h-3.5" />
                <span>Authoritative Actuals Currently Locked</span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-[11px] text-slate-600 dark:text-slate-400">
                <div className="p-2 rounded bg-white dark:bg-slate-900 border border-purple-100 dark:border-purple-900/30">
                  <div className="text-slate-400 text-[10px]">Actual Start</div>
                  <div className="font-mono font-medium text-slate-800 dark:text-slate-200">{activity.actual_start || activity.planned_start || 'N/A'}</div>
                </div>
                <div className="p-2 rounded bg-white dark:bg-slate-900 border border-purple-100 dark:border-purple-900/30">
                  <div className="text-slate-400 text-[10px]">Actual Finish</div>
                  <div className="font-mono font-medium text-slate-800 dark:text-slate-200">{activity.actual_finish || activity.planned_finish || 'N/A'}</div>
                </div>
                <div className="p-2 rounded bg-white dark:bg-slate-900 border border-purple-100 dark:border-purple-900/30">
                  <div className="text-slate-400 text-[10px]">Completion Pct</div>
                  <div className="font-mono font-bold text-emerald-600 dark:text-emerald-400">{activity.actual_pct_complete ?? activity.baseline_pct_complete ?? 100}%</div>
                </div>
              </div>
              <p className="text-[11px] text-slate-500 dark:text-slate-400 leading-relaxed pt-1">
                To prevent inadvertent schedule corruption, claims targeting completed activities are isolated. Once submitted, this request transitions state to <strong className="text-purple-700 dark:text-purple-300">REOPEN_REQUESTED</strong> for Supervisor approval.
              </p>
            </div>

            {/* Reason Selection */}
            <div className="space-y-1.5">
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                Reopen Classification Reason <span className="text-red-500">*</span>
              </label>
              <select
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="w-full text-xs rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-950 px-3 py-2 text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-amber-500"
              >
                {REOPEN_REASONS.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>

            {/* Justification Input */}
            <div className="space-y-1.5">
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
                Detailed Justification & Technical Basis <span className="text-red-500">*</span>
              </label>
              <textarea
                rows={3}
                value={justification}
                onChange={(e) => setJustification(e.target.value)}
                placeholder="State the engineering rationale, punchlist items, or field findings requiring this activity to be reopened..."
                className="w-full text-xs rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-950 p-3 text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-amber-500 resize-none"
              />
            </div>
          </div>

          {/* Action Footer - Fixed / Pinned */}
          <div className="p-4 sm:px-6 border-t border-slate-100 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-900/90 flex items-center justify-end gap-3 shrink-0">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-200/60 dark:hover:bg-slate-800 rounded-xl transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !justification.trim()}
              className="px-4 py-2 text-xs font-medium text-white bg-amber-600 hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-xl shadow transition-colors flex items-center gap-1.5"
            >
              {isSubmitting ? 'Submitting...' : 'Submit Reopen Request'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
