import React from 'react';
import { ExecutionState } from '../api';

interface ExecutionStateBadgeProps {
  state?: ExecutionState | string | null;
  size?: 'sm' | 'md' | 'lg';
  showDot?: boolean;
  className?: string;
}

const STATE_CONFIG: Record<
  string,
  { label: string; bg: string; text: string; border: string; dot: string }
> = {
  NOT_STARTED: {
    label: 'Not Started',
    bg: 'bg-slate-100 dark:bg-slate-800/60',
    text: 'text-slate-700 dark:text-slate-300',
    border: 'border-slate-200 dark:border-slate-700',
    dot: 'bg-slate-400',
  },
  IN_PROGRESS: {
    label: 'In Progress',
    bg: 'bg-blue-50 dark:bg-blue-950/50',
    text: 'text-blue-700 dark:text-blue-300',
    border: 'border-blue-200 dark:border-blue-800',
    dot: 'bg-blue-500 animate-pulse',
  },
  COMPLETED: {
    label: 'Completed',
    bg: 'bg-emerald-50 dark:bg-emerald-950/50',
    text: 'text-emerald-700 dark:text-emerald-300',
    border: 'border-emerald-200 dark:border-emerald-800',
    dot: 'bg-emerald-500',
  },
  ON_HOLD: {
    label: 'On Hold',
    bg: 'bg-amber-50 dark:bg-amber-950/50',
    text: 'text-amber-700 dark:text-amber-300',
    border: 'border-amber-200 dark:border-amber-800',
    dot: 'bg-amber-500',
  },
  REOPEN_REQUESTED: {
    label: 'Reopen Requested',
    bg: 'bg-purple-50 dark:bg-purple-950/50',
    text: 'text-purple-700 dark:text-purple-300',
    border: 'border-purple-200 dark:border-purple-800',
    dot: 'bg-purple-500 animate-pulse',
  },
  REOPENED: {
    label: 'Reopened',
    bg: 'bg-teal-50 dark:bg-teal-950/50',
    text: 'text-teal-700 dark:text-teal-300',
    border: 'border-teal-200 dark:border-teal-800',
    dot: 'bg-teal-500',
  },
};

export const ExecutionStateBadge: React.FC<ExecutionStateBadgeProps> = ({
  state,
  size = 'md',
  showDot = true,
  className = '',
}) => {
  const normState = (state || 'NOT_STARTED').toUpperCase();
  const config = STATE_CONFIG[normState] || STATE_CONFIG.NOT_STARTED;

  const sizeClasses = {
    sm: 'text-[10px] px-1.5 py-0.5 font-medium',
    md: 'text-xs px-2 py-0.5 font-medium',
    lg: 'text-sm px-2.5 py-1 font-semibold',
  }[size];

  const dotSizes = {
    sm: 'w-1.5 h-1.5',
    md: 'w-2 h-2',
    lg: 'w-2.5 h-2.5',
  }[size];

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border ${config.bg} ${config.text} ${config.border} ${sizeClasses} ${className}`}
    >
      {showDot && <span className={`rounded-full shrink-0 ${config.dot} ${dotSizes}`} />}
      <span>{config.label}</span>
    </span>
  );
};
