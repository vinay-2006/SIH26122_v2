import React, { useState } from 'react';
import { useAuth } from '@/auth/AuthProvider';
import { reopenApi, ReopenRequest } from '../api';
import { ExecutionStateBadge } from './ExecutionStateBadge';
import { CheckCircle2, XCircle, ShieldAlert, Lock, AlertCircle, X } from 'lucide-react';

interface ReopenReviewModalProps {
  request: ReopenRequest;
  isOpen: boolean;
  onClose: () => void;
  onReviewed: (updatedRequest: ReopenRequest) => void;
}

export const ReopenReviewModal: React.FC<ReopenReviewModalProps> = ({
  request,
  isOpen,
  onClose,
  onReviewed,
}) => {
  const { user } = useAuth();
  const [supervisorNotes, setSupervisorNotes] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleDecision = async (decision: 'APPROVED' | 'REJECTED') => {
    try {
      setIsSubmitting(true);
      setError(null);
      const updated = await reopenApi.reviewRequest({
        reopen_id: request.reopen_id || request.request_id || '',
        decision,
        supervisor_notes: supervisorNotes.trim() || undefined,
        reviewer_name: user?.full_name || 'Supervisor',
      });
      onReviewed(updated);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to submit review decision');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/60 backdrop-blur-sm animate-fade-in overflow-y-auto">
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh] my-auto">
        {/* Header - Fixed */}
        <div className="p-4 sm:p-5 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-purple-500/5 shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-purple-100 dark:bg-purple-950/60 text-purple-600 dark:text-purple-400 border border-purple-200 dark:border-purple-800/80 shrink-0">
              <ShieldAlert className="w-6 h-6" />
            </div>
            <div>
              <h3 className="font-semibold text-slate-900 dark:text-slate-100 text-base sm:text-lg leading-tight">
                Review Activity Reopen Request
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Supervisor Approval & Actual-Finish Protection Gate
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

        {/* Content Body - Scrollable */}
        <div className="p-4 sm:p-6 overflow-y-auto space-y-4 sm:space-y-5 flex-1 min-h-0 text-sm">
          {error && (
            <div className="p-3 bg-red-50 dark:bg-red-950/50 border border-red-200 dark:border-red-900/60 rounded-xl text-xs text-red-700 dark:text-red-300 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          {/* Activity & Request Summary */}
          <div className="p-4 rounded-xl bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/60 space-y-3">
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <span className="font-mono text-xs font-bold text-slate-700 dark:text-slate-300 bg-white dark:bg-slate-900 px-2 py-0.5 rounded border border-slate-200 dark:border-slate-700">
                {request.activity_id}
              </span>
              <ExecutionStateBadge state="REOPEN_REQUESTED" size="sm" />
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">
                Reopen Classification
              </div>
              <div className="font-medium text-slate-900 dark:text-slate-100 text-sm">
                {request.reason}
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 sm:gap-3 text-xs pt-1 border-t border-slate-200/60 dark:border-slate-700/60">
              <div>
                <span className="text-slate-400">Requested By:</span>{' '}
                <strong className="text-slate-700 dark:text-slate-200">{request.requested_by_name} ({request.requested_by_role})</strong>
              </div>
              <div>
                <span className="text-slate-400">Date:</span>{' '}
                <strong className="text-slate-700 dark:text-slate-200">{new Date(request.created_at || request.requested_at || Date.now()).toLocaleDateString()}</strong>
              </div>
            </div>
          </div>

          {/* Engineer's Justification */}
          <div className="p-3.5 rounded-xl bg-amber-500/5 border border-amber-200/80 dark:border-amber-900/40 space-y-1">
            <div className="text-xs font-semibold text-amber-800 dark:text-amber-300">
              Engineer Justification & Technical Basis:
            </div>
            <p className="text-xs text-slate-700 dark:text-slate-300 italic whitespace-pre-wrap">
              "{request.justification}"
            </p>
          </div>

          {/* Locked Actuals Notice */}
          {request.locked_actuals_summary && (
            <div className="p-3 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 space-y-1">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-700 dark:text-slate-300">
                <Lock className="w-3.5 h-3.5" />
                <span>Current Locked Actuals</span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-[11px] text-slate-600 dark:text-slate-400">
                <div>Start: <strong className="font-mono text-slate-800 dark:text-slate-200">{request.locked_actuals_summary.actual_start || 'N/A'}</strong></div>
                <div>Finish: <strong className="font-mono text-slate-800 dark:text-slate-200">{request.locked_actuals_summary.actual_finish || 'N/A'}</strong></div>
                <div>Pct: <strong className="font-mono text-emerald-600 dark:text-emerald-400">{request.locked_actuals_summary.actual_pct}%</strong></div>
              </div>
            </div>
          )}

          {/* Supervisor Notes */}
          <div className="space-y-1.5">
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">
              Supervisor Review Notes / Approval Conditions (Optional)
            </label>
            <textarea
              rows={2}
              value={supervisorNotes}
              onChange={(e) => setSupervisorNotes(e.target.value)}
              placeholder="Add review remarks, conditions for rework, or audit justification..."
              className="w-full text-xs rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-950 p-3 text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-purple-500 resize-none"
            />
          </div>
        </div>

        {/* Footer with Supervisor Action Buttons - Fixed / Pinned */}
        <div className="p-4 sm:px-6 border-t border-slate-100 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-900/90 flex flex-col-reverse sm:flex-row sm:items-center justify-between gap-3 shrink-0">
          <button
            type="button"
            onClick={onClose}
            className="w-full sm:w-auto px-4 py-2 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-200/60 dark:hover:bg-slate-800 rounded-xl transition-colors text-center"
          >
            Cancel
          </button>
          <div className="flex flex-col sm:flex-row items-center gap-2 w-full sm:w-auto">
            <button
              type="button"
              disabled={isSubmitting}
              onClick={() => handleDecision('REJECTED')}
              className="w-full sm:w-auto px-4 py-2 text-xs font-medium text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/60 hover:bg-red-100 dark:hover:bg-red-900/40 border border-red-200 dark:border-red-800 rounded-xl transition-colors flex items-center justify-center gap-1.5"
            >
              <XCircle className="w-4 h-4" />
              Reject & Keep Completed
            </button>
            <button
              type="button"
              disabled={isSubmitting}
              onClick={() => handleDecision('APPROVED')}
              className="w-full sm:w-auto px-4 py-2 text-xs font-medium text-white bg-purple-600 hover:bg-purple-700 disabled:opacity-50 rounded-xl shadow transition-colors flex items-center justify-center gap-1.5"
            >
              <CheckCircle2 className="w-4 h-4" />
              Approve & Reopen Activity
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
