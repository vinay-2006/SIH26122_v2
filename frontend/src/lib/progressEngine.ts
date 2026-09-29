import type {
  ScheduleActivity,
  ExecutionState,
  Discipline,
} from '@/api';
import type { ProjectStage } from '@/context/ProjectContext';

export interface ActivityProgressSummary {
  activity_id: string;
  activity_name: string;
  project_id: string;
  schedule_version_id: string;
  stage_id: string;
  stage_name: string;
  wbs_code: string;
  discipline: Discipline;
  weight: number;
  planned_start: string;
  planned_finish: string;
  actual_start: string | null;
  actual_finish: string | null;
  planned_progress: number;
  actual_progress: number;
  variance: number;
  execution_state: ExecutionState;
  is_critical: boolean;
}

export interface WBSProgressSummary {
  wbs_code: string;
  stage_id?: string;
  stage_name?: string;
  schedule_version_id: string;
  project_id: string;
  total_weight: number;
  planned_progress: number;
  actual_progress: number;
  variance: number;
  activities_count: number;
  completed_count: number;
  in_progress_count: number;
  not_started_count: number;
  on_hold_count: number;
}

export interface StageProgressSummary {
  stage_id: string;
  stage_name: string;
  stage_number: number;
  discipline: string;
  wbs_prefix: string;
  total_weight: number;
  planned_progress: number;
  actual_progress: number;
  variance: number;
  status: 'COMPLETED' | 'ACTIVE' | 'IN_PROGRESS' | 'NOT_STARTED';
  activities_count: number;
  completed_count: number;
  is_completed: boolean;
}

export interface WorkPackageProgressSummary {
  work_package_id: string;
  work_package_code: string;
  name: string;
  description?: string;
  contractor_id: string;
  contractor_name?: string;
  project_id: string;
  schedule_version_id: string;
  discipline: string;
  stage_id?: string;
  stage_name?: string;
  wbs_id?: string;
  status: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED' | 'ON_HOLD';
  planned_start?: string;
  planned_finish?: string;
  activities_count: number;
  completed_count: number;
  in_progress_count: number;
  not_started_count: number;
  on_hold_count: number;
  total_weight: number;
  planned_progress: number;
  actual_progress: number;
  variance: number;
  activity_ids: string[];
}

export interface ContractorProgressSummary {
  contractor_id: string;
  contractor_code: string;
  name: string;
  description?: string;
  project_id: string;
  status: 'ACTIVE' | 'INACTIVE' | 'ON_HOLD';
  active: boolean;
  contact_name?: string;
  contact_role?: string;
  contact_email?: string;
  contact_phone?: string;
  work_packages_count: number;
  activities_count: number;
  completed_count: number;
  in_progress_count: number;
  total_weight: number;
  planned_progress: number;
  actual_progress: number;
  variance: number;
  work_packages: WorkPackageProgressSummary[];
}

export interface ProjectProgressSummary {
  project_id: string;
  schedule_version_id: string;
  total_weight: number;
  overall_planned: number;
  overall_actual: number;
  variance: number;
  total_activities: number;
  completed_activities: number;
  in_progress_activities: number;
  on_hold_activities: number;
  not_started_activities: number;
  reopened_activities: number;
  critical_activities: number;
  stages: StageProgressSummary[];
}

/**
 * Derives authoritative planned & actual progress for a single activity.
 *
 * Rules:
 * - NOT_STARTED: actual progress = 0%
 * - IN_PROGRESS: actual progress = approved actual percentage
 * - ON_HOLD: actual progress = approved actual percentage
 * - COMPLETED: actual progress = 100%
 * - REOPEN_REQUESTED: retains previous authoritative approved progress
 * - REOPENED: retains previous authoritative progress until a new approved claim arrives
 */
export function deriveActivityProgress(act: ScheduleActivity): {
  plannedProgress: number;
  actualProgress: number;
  variance: number;
  effectiveWeight: number;
} {
  const plannedProgress = Math.min(100, Math.max(0, act.baseline_pct_complete ?? 0));
  const state: ExecutionState = act.execution_state || 'NOT_STARTED';

  let actualProgress = 0;
  if (state === 'COMPLETED') {
    actualProgress = 100;
  } else if (state === 'NOT_STARTED') {
    actualProgress = 0;
  } else if (act.actual_pct_complete !== undefined && act.actual_pct_complete !== null) {
    actualProgress = Math.min(100, Math.max(0, act.actual_pct_complete));
  } else if (state === 'IN_PROGRESS' || state === 'ON_HOLD') {
    actualProgress = Math.min(100, Math.max(0, act.baseline_pct_complete ?? 0));
  } else if (state === 'REOPENED' || state === 'REOPEN_REQUESTED') {
    actualProgress = act.actual_pct_complete !== undefined && act.actual_pct_complete !== null
      ? Math.min(100, Math.max(0, act.actual_pct_complete))
      : 100;
  }

  const variance = Math.round((actualProgress - plannedProgress) * 10) / 10;
  const effectiveWeight = act.weight && act.weight > 0
    ? act.weight
    : (act.planned_quantity && act.planned_quantity > 0 ? act.planned_quantity : 10);

  return {
    plannedProgress,
    actualProgress,
    variance,
    effectiveWeight,
  };
}

/**
 * Calculates weighted rollup for any set of activities.
 * Formula:
 *   weighted_progress = Σ(weight_i × progress_i) / Σ(weight_i)
 *
 * Edge cases:
 * - Empty activity array -> 0% progress, 0 variance (no NaN)
 * - Total weight === 0 -> 0% progress (no division by zero)
 */
export function calculateWeightedProgress(activities: ScheduleActivity[]): {
  totalWeight: number;
  plannedProgress: number;
  actualProgress: number;
  variance: number;
} {
  if (!activities || activities.length === 0) {
    return { totalWeight: 0, plannedProgress: 0, actualProgress: 0, variance: 0 };
  }

  let totalWeight = 0;
  let weightedActualSum = 0;
  let weightedPlannedSum = 0;

  for (const act of activities) {
    const { plannedProgress, actualProgress, effectiveWeight } = deriveActivityProgress(act);
    totalWeight += effectiveWeight;
    weightedActualSum += effectiveWeight * actualProgress;
    weightedPlannedSum += effectiveWeight * plannedProgress;
  }

  if (totalWeight === 0) {
    return { totalWeight: 0, plannedProgress: 0, actualProgress: 0, variance: 0 };
  }

  const actualProgress = Math.round((weightedActualSum / totalWeight) * 10) / 10;
  const plannedProgress = Math.round((weightedPlannedSum / totalWeight) * 10) / 10;
  const variance = Math.round((actualProgress - plannedProgress) * 10) / 10;

  return {
    totalWeight,
    plannedProgress,
    actualProgress,
    variance,
  };
}

/**
 * Rollup WBS nodes for the given activities.
 */
export function calculateWBSProgress(
  activities: ScheduleActivity[],
  scheduleId: string,
  projectId: string
): WBSProgressSummary[] {
  // Group activities by wbs_code
  const groups = new Map<string, ScheduleActivity[]>();
  for (const act of activities) {
    const code = act.wbs_code || 'UNASSIGNED';
    if (!groups.has(code)) {
      groups.set(code, []);
    }
    groups.get(code)!.push(act);
  }

  const result: WBSProgressSummary[] = [];
  for (const [wbsCode, groupActs] of groups.entries()) {
    const { totalWeight, plannedProgress, actualProgress, variance } = calculateWeightedProgress(groupActs);
    const completedCount = groupActs.filter((a) => a.execution_state === 'COMPLETED').length;
    const inProgressCount = groupActs.filter((a) => a.execution_state === 'IN_PROGRESS' || a.execution_state === 'REOPENED').length;
    const notStartedCount = groupActs.filter((a) => a.execution_state === 'NOT_STARTED').length;
    const onHoldCount = groupActs.filter((a) => a.execution_state === 'ON_HOLD').length;

    result.push({
      wbs_code: wbsCode,
      stage_id: groupActs[0]?.stage_id || undefined,
      stage_name: groupActs[0]?.stage_name || undefined,
      schedule_version_id: scheduleId,
      project_id: projectId,
      total_weight: totalWeight,
      planned_progress: plannedProgress,
      actual_progress: actualProgress,
      variance,
      activities_count: groupActs.length,
      completed_count: completedCount,
      in_progress_count: inProgressCount,
      not_started_count: notStartedCount,
      on_hold_count: onHoldCount,
    });
  }

  // Sort by wbs_code
  return result.sort((a, b) => a.wbs_code.localeCompare(b.wbs_code));
}

/**
 * Calculates Stage progress and determines Stage completion.
 *
 * Stage Completion Rule (Section O):
 * A stage is COMPLETED when:
 * 1. It has at least one activity.
 * 2. ALL of its child activities are COMPLETED and their authoritative actual progress is 100%.
 * 3. None of its activities are in IN_PROGRESS, NOT_STARTED, ON_HOLD, REOPEN_REQUESTED, or REOPENED.
 */
export function calculateStageProgress(
  activities: ScheduleActivity[],
  stageDefinitions: ProjectStage[],
  scheduleId: string,
  projectId: string
): StageProgressSummary[] {
  return stageDefinitions.map((stage) => {
    // Match activities belonging to this stage by stage_id or wbsPrefix
    const stageActs = activities.filter(
      (a) =>
        (a.stage_id && a.stage_id === stage.id) ||
        (a.wbs_code && stage.wbsPrefix && a.wbs_code.startsWith(stage.wbsPrefix))
    );

    if (stageActs.length === 0) {
      return {
        stage_id: stage.id,
        stage_name: stage.name,
        stage_number: stage.stageNumber,
        discipline: stage.discipline,
        wbs_prefix: stage.wbsPrefix,
        total_weight: 0,
        planned_progress: 0,
        actual_progress: 0,
        variance: 0,
        status: 'NOT_STARTED' as const,
        activities_count: 0,
        completed_count: 0,
        is_completed: false,
      };
    }

    const { totalWeight, plannedProgress, actualProgress, variance } = calculateWeightedProgress(stageActs);
    const completedCount = stageActs.filter((a) => a.execution_state === 'COMPLETED').length;
    const isCompleted =
      stageActs.length > 0 &&
      stageActs.every((a) => a.execution_state === 'COMPLETED' && (a.actual_pct_complete ?? a.baseline_pct_complete ?? 0) >= 100);

    let status: 'COMPLETED' | 'ACTIVE' | 'IN_PROGRESS' | 'NOT_STARTED' = 'NOT_STARTED';
    if (isCompleted) {
      status = 'COMPLETED';
    } else if (actualProgress > 0 || stageActs.some((a) => a.execution_state === 'IN_PROGRESS' || a.execution_state === 'REOPENED')) {
      status = stage.status === 'ACTIVE' ? 'ACTIVE' : 'IN_PROGRESS';
    } else {
      status = 'NOT_STARTED';
    }

    return {
      stage_id: stage.id,
      stage_name: stage.name,
      stage_number: stage.stageNumber,
      discipline: stage.discipline,
      wbs_prefix: stage.wbsPrefix,
      total_weight: totalWeight,
      planned_progress: plannedProgress,
      actual_progress: actualProgress,
      variance,
      status,
      activities_count: stageActs.length,
      completed_count: completedCount,
      is_completed: isCompleted,
    };
  });
}

/**
 * Calculates complete Project Progress Summary for the given active project and active schedule version.
 */
export function calculateProjectProgress(
  activities: ScheduleActivity[],
  stageDefinitions: ProjectStage[],
  scheduleId: string,
  projectId: string
): ProjectProgressSummary {
  const { totalWeight, plannedProgress, actualProgress, variance } = calculateWeightedProgress(activities);

  const completedActivities = activities.filter((a) => a.execution_state === 'COMPLETED').length;
  const inProgressActivities = activities.filter((a) => a.execution_state === 'IN_PROGRESS').length;
  const onHoldActivities = activities.filter((a) => a.execution_state === 'ON_HOLD').length;
  const notStartedActivities = activities.filter((a) => a.execution_state === 'NOT_STARTED').length;
  const reopenedActivities = activities.filter((a) => a.execution_state === 'REOPENED').length;
  const criticalActivities = activities.filter((a) => a.is_critical === true).length;

  const stageSummaries = calculateStageProgress(activities, stageDefinitions, scheduleId, projectId);

  return {
    project_id: projectId,
    schedule_version_id: scheduleId,
    total_weight: totalWeight,
    overall_planned: plannedProgress,
    overall_actual: actualProgress,
    variance,
    total_activities: activities.length,
    completed_activities: completedActivities,
    in_progress_activities: inProgressActivities + reopenedActivities,
    on_hold_activities: onHoldActivities,
    not_started_activities: notStartedActivities,
    reopened_activities: reopenedActivities,
    critical_activities: criticalActivities,
    stages: stageSummaries,
  };
}

export interface ContractorInput {
  id: string;
  projectId: string;
  name: string;
  code: string;
  description?: string;
  status: 'ACTIVE' | 'INACTIVE' | 'ON_HOLD';
  contactName?: string;
  contactRole?: string;
  contactEmail?: string;
  contactPhone?: string;
  active: boolean;
}

export interface WorkPackageInput {
  id: string;
  projectId: string;
  scheduleId: string;
  contractorId: string;
  name: string;
  code: string;
  description?: string;
  discipline: string;
  stageId?: string;
  wbsId?: string;
  status: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED' | 'ON_HOLD';
  plannedStart?: string;
  plannedFinish?: string;
  activityIds: string[];
}

/**
 * Derives authoritative weighted progress for a single Work Package.
 */
export function calculateWorkPackageProgress(
  wp: WorkPackageInput,
  activities: ScheduleActivity[],
  contractorName?: string
): WorkPackageProgressSummary {
  const wpActivities = activities.filter(
    (a) => (wp.activityIds && wp.activityIds.includes(a.activity_id)) || a.work_package_id === wp.id
  );

  const { totalWeight, plannedProgress, actualProgress, variance } = calculateWeightedProgress(wpActivities);

  const completedCount = wpActivities.filter((a) => a.execution_state === 'COMPLETED').length;
  const inProgressCount = wpActivities.filter((a) => a.execution_state === 'IN_PROGRESS' || a.execution_state === 'REOPENED').length;
  const notStartedCount = wpActivities.filter((a) => a.execution_state === 'NOT_STARTED').length;
  const onHoldCount = wpActivities.filter((a) => a.execution_state === 'ON_HOLD').length;

  let status: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED' | 'ON_HOLD' = wp.status;
  if (wpActivities.length > 0) {
    if (completedCount === wpActivities.length && wpActivities.every((a) => (a.actual_pct_complete ?? 0) >= 100)) {
      status = 'COMPLETED';
    } else if (actualProgress > 0 || inProgressCount > 0) {
      status = 'IN_PROGRESS';
    } else if (onHoldCount === wpActivities.length) {
      status = 'ON_HOLD';
    } else {
      status = 'NOT_STARTED';
    }
  }

  return {
    work_package_id: wp.id,
    work_package_code: wp.code,
    name: wp.name,
    description: wp.description,
    contractor_id: wp.contractorId,
    contractor_name: contractorName,
    project_id: wp.projectId,
    schedule_version_id: wp.scheduleId,
    discipline: wp.discipline,
    stage_id: wp.stageId,
    wbs_id: wp.wbsId,
    status,
    planned_start: wp.plannedStart,
    planned_finish: wp.plannedFinish,
    activities_count: wpActivities.length,
    completed_count: completedCount,
    in_progress_count: inProgressCount,
    not_started_count: notStartedCount,
    on_hold_count: onHoldCount,
    total_weight: totalWeight,
    planned_progress: plannedProgress,
    actual_progress: actualProgress,
    variance,
    activity_ids: wpActivities.map((a) => a.activity_id),
  };
}

/**
 * Derives authoritative weighted progress for a Contractor across all their Work Packages and activities.
 */
export function calculateContractorProgress(
  contractor: ContractorInput,
  workPackages: WorkPackageInput[],
  activities: ScheduleActivity[]
): ContractorProgressSummary {
  const contractorWps = workPackages.filter((wp) => wp.contractorId === contractor.id);
  const wpSummaries = contractorWps.map((wp) => calculateWorkPackageProgress(wp, activities, contractor.name));

  const contractorActs = activities.filter(
    (a) => a.contractor_id === contractor.id || contractorWps.some((wp) => wp.activityIds && wp.activityIds.includes(a.activity_id))
  );

  const { totalWeight, plannedProgress, actualProgress, variance } = calculateWeightedProgress(contractorActs);

  const completedCount = contractorActs.filter((a) => a.execution_state === 'COMPLETED').length;
  const inProgressCount = contractorActs.filter((a) => a.execution_state === 'IN_PROGRESS' || a.execution_state === 'REOPENED').length;

  return {
    contractor_id: contractor.id,
    contractor_code: contractor.code,
    name: contractor.name,
    description: contractor.description,
    project_id: contractor.projectId,
    status: contractor.status,
    active: contractor.active,
    contact_name: contractor.contactName,
    contact_role: contractor.contactRole,
    contact_email: contractor.contactEmail,
    contact_phone: contractor.contactPhone,
    work_packages_count: contractorWps.length,
    activities_count: contractorActs.length,
    completed_count: completedCount,
    in_progress_count: inProgressCount,
    total_weight: totalWeight,
    planned_progress: plannedProgress,
    actual_progress: actualProgress,
    variance,
    work_packages: wpSummaries,
  };
}

/**
 * Derives progress summaries for all contractors in a project.
 */
export function calculateAllContractorsProgress(
  contractors: ContractorInput[],
  workPackages: WorkPackageInput[],
  activities: ScheduleActivity[]
): ContractorProgressSummary[] {
  return contractors.map((c) => calculateContractorProgress(c, workPackages, activities));
}
