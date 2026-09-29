/**
 * Deterministic Compound Impact Engine
 *
 * Evaluates downstream schedule impacts based strictly on:
 * 1. Authoritative Primavera schedule dependencies (FS/SS/FF/SF)
 * 2. Activity execution states and progress variances
 * 3. Quality Gate / Hold Point blocker states
 *
 * Decision-support only: No automatic claim rejection/approval, no date mutations.
 */

import {
  ScheduleActivity,
  ScheduleDependency,
  QualityGate,
  ExecutionState,
} from '@/api';

export type ImpactLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export interface DownstreamImpactedActivity {
  activityId: string;
  activityName: string;
  stageId?: string;
  stageName?: string;
  wbsCode?: string;
  discipline?: string;
  relationshipType: 'FS' | 'SS' | 'FF' | 'SF';
  lagDays?: number;
  plannedStart?: string | null;
  plannedFinish?: string | null;
  depth: number;
}

export interface CompoundImpact {
  activityId: string;
  activityName: string;
  projectId: string;
  scheduleId: string;
  stageId?: string | null;
  stageName?: string | null;
  discipline?: string | null;
  wbsCode?: string | null;
  impactLevel: ImpactLevel;
  directSuccessorCount: number;
  totalDownstreamCount: number;
  impactedActivityIds: string[];
  impactedStageIds: string[];
  impactedStageNames: string[];
  impactedWbsCodes: string[];
  downstreamActivities: DownstreamImpactedActivity[];
  primaryReason: string;
  riskFactors: string[];
  isBlockedOrAtRisk: boolean;
  sourceState: ExecutionState;
  calculatedAt: string;
}

/**
 * Deterministically compute compound downstream impacts for a single activity.
 */
export function calculateActivityImpact(
  activityId: string,
  activities: ScheduleActivity[],
  dependencies: ScheduleDependency[],
  qualityGates: QualityGate[] = [],
  projectId = '',
  scheduleId = ''
): CompoundImpact | null {
  const actMap = new Map<string, ScheduleActivity>();
  activities.forEach((a) => actMap.set(a.activity_id, a));

  const sourceAct = actMap.get(activityId);
  if (!sourceAct) return null;

  // Build forward dependency adjacency list
  const forwardMap = new Map<string, { succId: string; rel: 'FS' | 'SS' | 'FF' }[]>();
  for (const dep of dependencies) {
    const list = forwardMap.get(dep.predecessor_activity_id) || [];
    list.push({
      succId: dep.successor_activity_id,
      rel: (dep.relationship_type as 'FS' | 'SS' | 'FF') || 'FS',
    });
    forwardMap.set(dep.predecessor_activity_id, list);
  }

  // Traverse downstream DAG (bounded BFS with visited set to guard against cycles)
  const downstream: DownstreamImpactedActivity[] = [];
  const visited = new Set<string>();
  visited.add(activityId);

  const queue: { id: string; depth: number; rel: 'FS' | 'SS' | 'FF' }[] = [];
  const directSuccs = forwardMap.get(activityId) || [];
  for (const s of directSuccs) {
    if (!visited.has(s.succId)) {
      visited.add(s.succId);
      queue.push({ id: s.succId, depth: 1, rel: s.rel });
    }
  }

  const directSuccessorCount = queue.length;

  while (queue.length > 0) {
    const curr = queue.shift()!;
    const succAct = actMap.get(curr.id);
    if (succAct) {
      downstream.push({
        activityId: succAct.activity_id,
        activityName: succAct.activity_name,
        stageId: succAct.stage_id || undefined,
        stageName: succAct.stage_name || undefined,
        wbsCode: succAct.wbs_code || undefined,
        discipline: succAct.discipline,
        relationshipType: curr.rel,
        plannedStart: succAct.planned_start,
        plannedFinish: succAct.planned_finish,
        depth: curr.depth,
      });

      // Expand next level (up to depth 5 max)
      if (curr.depth < 5) {
        const nextSuccs = forwardMap.get(curr.id) || [];
        for (const ns of nextSuccs) {
          if (!visited.has(ns.succId)) {
            visited.add(ns.succId);
            queue.push({ id: ns.succId, depth: curr.depth + 1, rel: ns.rel });
          }
        }
      }
    }
  }

  const impactedActivityIds = downstream.map((d) => d.activityId);
  const impactedStageIds = Array.from(new Set(downstream.map((d) => d.stageId).filter(Boolean) as string[]));
  const impactedStageNames = Array.from(new Set(downstream.map((d) => d.stageName).filter(Boolean) as string[]));
  const impactedWbsCodes = Array.from(new Set(downstream.map((d) => d.wbsCode).filter(Boolean) as string[]));

  // Determine risk factors and deterministic reasons
  const riskFactors: string[] = [];
  let isBlockedOrAtRisk = false;

  // 1. Quality Gates check (Hold Points)
  const actGates = qualityGates.filter((g) => g.activityId === activityId);
  const blockingHoldPoint = actGates.find(
    (g) => g.gateType === 'HOLD_POINT' && g.required && (g.status === 'PENDING' || g.status === 'BLOCKED')
  );
  const blockedGate = actGates.find((g) => g.status === 'BLOCKED');

  if (blockingHoldPoint) {
    isBlockedOrAtRisk = true;
    riskFactors.push(
      blockingHoldPoint.status === 'BLOCKED'
        ? `Required Hold Point Blocked (${blockingHoldPoint.name})`
        : `Required Hold Point Pending (${blockingHoldPoint.name})`
    );
  } else if (blockedGate) {
    isBlockedOrAtRisk = true;
    riskFactors.push(`Quality Gate Blocked (${blockedGate.name})`);
  }

  // 2. Execution State check
  const state = sourceAct.execution_state || 'NOT_STARTED';
  if (state === 'ON_HOLD') {
    isBlockedOrAtRisk = true;
    riskFactors.push('Activity execution is on hold');
  } else if (state === 'REOPEN_REQUESTED') {
    isBlockedOrAtRisk = true;
    riskFactors.push('Reopen request pending Supervisor review');
  }

  // 3. Baseline Variance check
  const actualPct = sourceAct.actual_pct_complete ?? sourceAct.baseline_pct_complete ?? 0;
  const plannedPct = sourceAct.baseline_pct_complete ?? 0;
  const variance = actualPct - plannedPct;
  if (variance <= -15) {
    isBlockedOrAtRisk = true;
    riskFactors.push(`Activity is behind baseline progress by ${Math.abs(variance)}%`);
  }

  // 4. Critical path check
  if (sourceAct.is_critical && isBlockedOrAtRisk) {
    riskFactors.push('Activity is on the project critical path (0 total float)');
  }

  // If no downstream dependencies exist, return minimal impact
  if (downstream.length === 0) {
    return {
      activityId: sourceAct.activity_id,
      activityName: sourceAct.activity_name,
      projectId: projectId || sourceAct.schedule_id,
      scheduleId: scheduleId || sourceAct.schedule_id,
      stageId: sourceAct.stage_id,
      stageName: sourceAct.stage_name,
      discipline: sourceAct.discipline,
      wbsCode: sourceAct.wbs_code,
      impactLevel: 'LOW',
      directSuccessorCount: 0,
      totalDownstreamCount: 0,
      impactedActivityIds: [],
      impactedStageIds: [],
      impactedStageNames: [],
      impactedWbsCodes: [],
      downstreamActivities: [],
      primaryReason: 'No downstream schedule dependencies',
      riskFactors: riskFactors.length > 0 ? riskFactors : ['No downstream impact'],
      isBlockedOrAtRisk,
      sourceState: state,
      calculatedAt: new Date().toISOString(),
    };
  }

  // Deterministic impact level classification
  let impactLevel: ImpactLevel = 'LOW';
  if (isBlockedOrAtRisk) {
    if (blockingHoldPoint || blockedGate || sourceAct.is_critical || downstream.length >= 3 || impactedStageIds.length > 1) {
      impactLevel = (blockingHoldPoint && downstream.length >= 3) || sourceAct.is_critical ? 'HIGH' : 'MEDIUM';
      if (blockedGate && downstream.length >= 2) impactLevel = 'CRITICAL';
    } else {
      impactLevel = 'MEDIUM';
    }
  } else if (downstream.length >= 3 || impactedStageIds.length > 1) {
    impactLevel = 'MEDIUM';
  }

  const primaryReason =
    riskFactors.length > 0
      ? riskFactors[0]
      : `${downstream.length} downstream activities dependent on baseline schedule completion`;

  return {
    activityId: sourceAct.activity_id,
    activityName: sourceAct.activity_name,
    projectId: projectId || sourceAct.schedule_id,
    scheduleId: scheduleId || sourceAct.schedule_id,
    stageId: sourceAct.stage_id,
    stageName: sourceAct.stage_name,
    discipline: sourceAct.discipline,
    wbsCode: sourceAct.wbs_code,
    impactLevel,
    directSuccessorCount,
    totalDownstreamCount: downstream.length,
    impactedActivityIds,
    impactedStageIds,
    impactedStageNames,
    impactedWbsCodes,
    downstreamActivities: downstream,
    primaryReason,
    riskFactors,
    isBlockedOrAtRisk,
    sourceState: state,
    calculatedAt: new Date().toISOString(),
  };
}

/**
 * Deterministically compute compound impacts across all activities in a schedule.
 */
export function calculateScheduleImpacts(
  activities: ScheduleActivity[],
  dependencies: ScheduleDependency[],
  qualityGates: QualityGate[] = [],
  projectId = '',
  scheduleId = ''
): CompoundImpact[] {
  const results: CompoundImpact[] = [];
  for (const act of activities) {
    const impact = calculateActivityImpact(
      act.activity_id,
      activities,
      dependencies,
      qualityGates,
      projectId,
      scheduleId
    );
    if (impact && (impact.impactLevel !== 'LOW' || impact.totalDownstreamCount > 0)) {
      results.push(impact);
    }
  }
  return results;
}
