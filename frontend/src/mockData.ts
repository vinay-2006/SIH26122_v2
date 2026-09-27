import {
  ExecutionEvent,
  CandidateMatch,
  ValidationIssue,
  PlannerDecision,
  ScheduleActivity,
  DisciplineForecastData,
  Discipline,
  EventType,
  ClarificationStatus,
  ExecutionState,
  ReopenRequest,
  ClaimStatus,
  UserRole,
  DecisionAction,
  Contractor,
  WorkPackage,
  QualityGate,
} from './api';
import {
  calculateWeightedProgress,
  calculateWBSProgress,
  calculateStageProgress,
  calculateProjectProgress,
  calculateWorkPackageProgress,
  calculateContractorProgress,
  calculateAllContractorsProgress,
  deriveActivityProgress,
  type ActivityProgressSummary,
  type WBSProgressSummary,
  type StageProgressSummary,
  type ProjectProgressSummary,
  type WorkPackageProgressSummary,
  type ContractorProgressSummary,
} from '@/lib/progressEngine';
import { DEMO_PROJECTS } from '@/context/ProjectContext';

export function getActiveProjectId(): string {
  const saved = localStorage.getItem('setu_selected_project_id_v7');
  if (saved && (saved === 'PRJ-ASSAM-01' || saved === 'PRJ-RAJ-02' || saved === 'PRJ-KG-03')) {
    return saved;
  }
  return 'PRJ-ASSAM-01';
}

export function getActiveScheduleVersionId(): string {
  const saved = localStorage.getItem('setu_selected_sched_version_v7');
  if (saved) return saved;
  const pId = getActiveProjectId();
  if (pId === 'PRJ-RAJ-02') return 'SCHED-RAJ-V1';
  if (pId === 'PRJ-KG-03') return 'SCHED-KG-BL1';
  return 'SCHED-ASSAM-V2';
}

export const TODAY = new Date().toISOString().split('T')[0];

export interface ProjectMockDataset {
  activities: Record<string, ScheduleActivity[]>;
  events: ExecutionEvent[];
  candidates: CandidateMatch[];
  validationIssues: ValidationIssue[];
  decisions: PlannerDecision[];
  dashboard: {
    total_claims: number;
    pending_review: number;
    actuals: number;
    conflicts: number;
    discipline_breakdown: { discipline: string; name: string; count: number; value: number }[];
    claims_trend_pct: number | null;
  };
  delayReasons: { reason: string; count: number }[];
  institutionalMemory: { topic: string; resolution: string; count: number }[];
  forecast: DisciplineForecastData;
  summaryReport: {
    text: string;
    highlights: string[];
  };
}

export const MOCK_DATASETS: Record<string, ProjectMockDataset> = {
  'PRJ-ASSAM-01': {
    activities: {
      'SCHED-ASSAM-V2': [
        {
          activity_id: 'ACT-ASSAM-101',
          schedule_id: 'SCHED-ASSAM-V2',
          activity_name: 'Topographical Route Survey & Geotechnical Soil Boring',
          wbs_code: 'WBS-1.1.1',
          discipline: 'CIVIL',
          location: 'Pipeline Corridor Chainage 0-45km',
          asset_tag: 'SRV-01',
          planned_start: '2026-06-01',
          planned_finish: '2026-06-30',
          planned_quantity: 45,
          uom: 'km',
          baseline_pct_complete: 100,
          total_float: 0,
          is_critical: false,
          execution_state: 'COMPLETED',
          actual_start: '2026-06-01',
          actual_finish: '2026-06-28',
          actual_pct_complete: 100,
          stage_id: 'STG-ASSAM-1',
          stage_name: 'Stage 1 — Engineering & Detail Survey',
          is_stage_completed: true,
          contractor_id: 'CTR-ASSAM-01',
          contractor_name: 'Brahmaputra Infrastructure & Civil EPC Ltd',
          work_package_id: 'WP-ASSAM-SRV-01',
          work_package_code: 'WP-1.1-GEN-01',
          work_package_name: 'Detail Route Survey & HSE Clearance Package',
        },
        {
          activity_id: 'ACT-ASSAM-201',
          schedule_id: 'SCHED-ASSAM-V2',
          activity_name: 'Foundation Concrete Pouring — Pump Station PS-3',
          wbs_code: 'WBS-1.3.1',
          discipline: 'CIVIL',
          location: 'Pump Station 3',
          asset_tag: 'FND-PS3',
          planned_start: '2026-09-01',
          planned_finish: '2026-09-10',
          planned_quantity: 200,
          uom: 'cu.m',
          baseline_pct_complete: 70,
          total_float: 0,
          is_critical: true,
          execution_state: 'IN_PROGRESS',
          actual_start: '2026-09-01',
          actual_finish: null,
          actual_pct_complete: 70,
          stage_id: 'STG-ASSAM-3',
          stage_name: 'Stage 3 — Civil & Foundation Works',
          is_stage_completed: false,
          contractor_id: 'CTR-ASSAM-01',
          contractor_name: 'Brahmaputra Infrastructure & Civil EPC Ltd',
          work_package_id: 'WP-ASSAM-CIV-01',
          work_package_code: 'WP-1.3-CIV-01',
          work_package_name: 'Pump Station Civil & Structural Foundation Package',
        },
        {
          activity_id: 'ACT-ASSAM-202',
          schedule_id: 'SCHED-ASSAM-V2',
          activity_name: 'Rebar Placement — Column C4 & Grade Beam',
          wbs_code: 'WBS-1.3.2',
          discipline: 'CIVIL',
          location: 'Block-2 North',
          asset_tag: 'COL-C4',
          planned_start: '2026-09-05',
          planned_finish: '2026-09-14',
          planned_quantity: 15,
          uom: 'MT',
          baseline_pct_complete: 60,
          total_float: 2,
          is_critical: false,
          execution_state: 'IN_PROGRESS',
          actual_start: '2026-09-05',
          actual_finish: null,
          actual_pct_complete: 60,
          stage_id: 'STG-ASSAM-3',
          stage_name: 'Stage 3 — Civil & Foundation Works',
          is_stage_completed: false,
          contractor_id: 'CTR-ASSAM-01',
          contractor_name: 'Brahmaputra Infrastructure & Civil EPC Ltd',
          work_package_id: 'WP-ASSAM-CIV-01',
          work_package_code: 'WP-1.3-CIV-01',
          work_package_name: 'Pump Station Civil & Structural Foundation Package',
        },
        {
          activity_id: 'ACT-ASSAM-301',
          schedule_id: 'SCHED-ASSAM-V2',
          activity_name: 'Field Welding — 14" Crude Trunkline Section L-1',
          wbs_code: 'WBS-1.4.1',
          discipline: 'PIPING',
          location: 'Sector Duliajan-Km12',
          asset_tag: 'PIPE-14-L1',
          planned_start: '2026-09-02',
          planned_finish: '2026-09-18',
          planned_quantity: 80,
          uom: 'joints',
          baseline_pct_complete: 45,
          total_float: 0,
          is_critical: true,
          execution_state: 'IN_PROGRESS',
          actual_start: '2026-09-02',
          actual_finish: null,
          actual_pct_complete: 45,
          stage_id: 'STG-ASSAM-4',
          stage_name: 'Stage 4 — Mainline Pipeline & Crossings',
          is_stage_completed: false,
          contractor_id: 'CTR-ASSAM-02',
          contractor_name: 'Eastern River HDD & Crossings Corp',
          work_package_id: 'WP-ASSAM-PIP-01',
          work_package_code: 'WP-1.4-PIP-01',
          work_package_name: 'Mainline HDD River Crossing & Trenching Package',
        },
        {
          activity_id: 'ACT-ASSAM-302',
          schedule_id: 'SCHED-ASSAM-V2',
          activity_name: 'Mainline River Crossing HDD Boring',
          wbs_code: 'WBS-1.4.2',
          discipline: 'PIPING',
          location: 'Burhi Dihing River',
          asset_tag: 'HDD-BD01',
          planned_start: '2026-09-10',
          planned_finish: '2026-09-28',
          planned_quantity: 450,
          uom: 'meters',
          baseline_pct_complete: 20,
          total_float: 1,
          is_critical: true,
          execution_state: 'NOT_STARTED',
          actual_start: null,
          actual_finish: null,
          actual_pct_complete: 0,
          stage_id: 'STG-ASSAM-4',
          stage_name: 'Stage 4 — Mainline Pipeline & Crossings',
          is_stage_completed: false,
          contractor_id: 'CTR-ASSAM-02',
          contractor_name: 'Eastern River HDD & Crossings Corp',
          work_package_id: 'WP-ASSAM-PIP-01',
          work_package_code: 'WP-1.4-PIP-01',
          work_package_name: 'Mainline HDD River Crossing & Trenching Package',
        },
        {
          activity_id: 'ACT-ASSAM-401',
          schedule_id: 'SCHED-ASSAM-V2',
          activity_name: 'SCADA RTU & Solar Power Skid Installation',
          wbs_code: 'WBS-1.5.1',
          discipline: 'ELECTRICAL',
          location: 'Sector Valve Station 4',
          asset_tag: 'RTU-VS04',
          planned_start: '2026-09-01',
          planned_finish: '2026-09-08',
          planned_quantity: 1,
          uom: 'unit',
          baseline_pct_complete: 100,
          total_float: 5,
          is_critical: false,
          execution_state: 'COMPLETED',
          actual_start: '2026-09-01',
          actual_finish: '2026-09-08',
          actual_pct_complete: 100,
          stage_id: 'STG-ASSAM-5',
          stage_name: 'Stage 5 — Terminal Stations & Commissioning',
          is_stage_completed: false,
          contractor_id: 'CTR-ASSAM-03',
          contractor_name: 'NorthEast Electrical & SCADA Systems Pvt Ltd',
          work_package_id: 'WP-ASSAM-ELE-01',
          work_package_code: 'WP-1.5-ELE-01',
          work_package_name: 'SCADA RTU, Solar Skid & Metering Package',
        },
        {
          activity_id: 'ACT-ASSAM-501',
          schedule_id: 'SCHED-ASSAM-V2',
          activity_name: 'Custody Metering Skid Loop Calibration',
          wbs_code: 'WBS-1.5.2',
          discipline: 'INSTRUMENTATION',
          location: 'Numaligarh Terminal',
          asset_tag: 'MIT-101',
          planned_start: '2026-09-08',
          planned_finish: '2026-09-16',
          planned_quantity: 12,
          uom: 'loops',
          baseline_pct_complete: 10,
          total_float: 3,
          is_critical: false,
          execution_state: 'REOPENED',
          actual_start: '2026-09-08',
          actual_finish: null,
          actual_pct_complete: 85,
          stage_id: 'STG-ASSAM-5',
          stage_name: 'Stage 5 — Terminal Stations & Commissioning',
          is_stage_completed: false,
          contractor_id: 'CTR-ASSAM-03',
          contractor_name: 'NorthEast Electrical & SCADA Systems Pvt Ltd',
          work_package_id: 'WP-ASSAM-ELE-01',
          work_package_code: 'WP-1.5-ELE-01',
          work_package_name: 'SCADA RTU, Solar Skid & Metering Package',
        },
        {
          activity_id: 'ACT-ASSAM-601',
          schedule_id: 'SCHED-ASSAM-V2',
          activity_name: 'Environmental Clearance & Monsoon HSE Audit',
          wbs_code: 'WBS-1.1.3',
          discipline: 'HSE',
          location: 'Pipeline ROW Corridor',
          asset_tag: null,
          planned_start: '2026-09-08',
          planned_finish: '2026-09-08',
          planned_quantity: 1,
          uom: 'report',
          baseline_pct_complete: 100,
          total_float: 10,
          is_critical: false,
          execution_state: 'COMPLETED',
          actual_start: '2026-09-08',
          actual_finish: '2026-09-08',
          actual_pct_complete: 100,
          stage_id: 'STG-ASSAM-1',
          stage_name: 'Stage 1 — Engineering & Detail Survey',
          is_stage_completed: true,
          contractor_id: 'CTR-ASSAM-01',
          contractor_name: 'Brahmaputra Infrastructure & Civil EPC Ltd',
          work_package_id: 'WP-ASSAM-SRV-01',
          work_package_code: 'WP-1.1-GEN-01',
          work_package_name: 'Detail Route Survey & HSE Clearance Package',
        },
      ],
      'SCHED-ASSAM-V1': [
        {
          activity_id: 'ACT-ASSAM-201',
          schedule_id: 'SCHED-ASSAM-V1',
          activity_name: 'Foundation Concrete Pouring — Pump Station PS-3',
          wbs_code: 'WBS-1.3.1',
          discipline: 'CIVIL',
          location: 'Pump Station 3',
          asset_tag: 'FND-PS3',
          planned_start: '2026-01-20',
          planned_finish: '2026-02-10',
          planned_quantity: 180,
          uom: 'cu.m',
          baseline_pct_complete: 100,
          total_float: 0,
          is_critical: true,
          execution_state: 'COMPLETED',
          actual_start: '2026-01-20',
          actual_finish: '2026-02-08',
          actual_pct_complete: 100,
          stage_id: 'STG-ASSAM-3',
          stage_name: 'Stage 3 — Civil & Foundation Works',
          is_stage_completed: false,
          contractor_id: 'CTR-ASSAM-01',
          contractor_name: 'Brahmaputra Infrastructure & Civil EPC Ltd',
          work_package_id: 'WP-ASSAM-V1-CIV-01',
          work_package_code: 'WP-V1-1.3-CIV',
          work_package_name: 'Pump Station Civil Foundation Baseline Package',
        },
        {
          activity_id: 'ACT-ASSAM-301',
          schedule_id: 'SCHED-ASSAM-V1',
          activity_name: 'Field Welding — 14" Crude Trunkline Section L-1',
          wbs_code: 'WBS-1.4.1',
          discipline: 'PIPING',
          location: 'Sector Duliajan-Km12',
          asset_tag: 'PIPE-14-L1',
          planned_start: '2026-02-15',
          planned_finish: '2026-03-30',
          planned_quantity: 80,
          uom: 'joints',
          baseline_pct_complete: 100,
          total_float: 0,
          is_critical: true,
          execution_state: 'COMPLETED',
          actual_start: '2026-02-15',
          actual_finish: '2026-03-28',
          actual_pct_complete: 100,
          stage_id: 'STG-ASSAM-4',
          stage_name: 'Stage 4 — Mainline Pipeline & Crossings',
          is_stage_completed: false,
          contractor_id: 'CTR-ASSAM-02',
          contractor_name: 'Eastern River HDD & Crossings Corp',
          work_package_id: 'WP-ASSAM-V1-PIP-01',
          work_package_code: 'WP-V1-1.4-PIP',
          work_package_name: 'Mainline Trunkline Welding Baseline Package',
        },
      ],
      'SCHED-ASSAM-REC': [
        {
          activity_id: 'ACT-ASSAM-201',
          schedule_id: 'SCHED-ASSAM-REC',
          activity_name: 'Foundation Concrete Pouring — Pump Station PS-3 (Fast-Track)',
          wbs_code: 'WBS-1.3.1',
          discipline: 'CIVIL',
          location: 'Pump Station 3',
          asset_tag: 'FND-PS3',
          planned_start: '2026-09-01',
          planned_finish: '2026-09-08',
          planned_quantity: 200,
          uom: 'cu.m',
          baseline_pct_complete: 80,
          total_float: 0,
          is_critical: true,
          execution_state: 'IN_PROGRESS',
          actual_start: '2026-09-01',
          actual_finish: null,
          actual_pct_complete: 80,
          stage_id: 'STG-ASSAM-3',
          stage_name: 'Stage 3 — Civil & Foundation Works',
          is_stage_completed: false,
          contractor_id: 'CTR-ASSAM-01',
          contractor_name: 'Brahmaputra Infrastructure & Civil EPC Ltd',
          work_package_id: 'WP-ASSAM-REC-CIV-01',
          work_package_code: 'WP-REC-1.3-CIV',
          work_package_name: 'Pump Station Fast-Track Concrete Pouring Package',
        },
      ],
    },
    events: [
      {
        event_id: 'evt-assam-101',
        document_id: 'doc-ass-001',
        schedule_id: 'SCHED-ASSAM-V2',
        event_date: TODAY,
        raw_claim_text: 'Completed foundation pour F-4 in Pump Station PS-3, 50 cu.m poured today.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-ASSAM-201',
        matched_activity_id: 'ACT-ASSAM-201',
        discipline: 'CIVIL',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'INCREMENTAL_QUANTITY',
        asset_tag: 'FND-PS3',
        location: 'Pump Station 3',
        claimed_quantity: 50,
        claimed_uom: 'cu.m',
        claimed_pct: 100,
        delay_reason: null,
        supervisor_id: null,
        photo_path: '/uploads/ps3_pour.jpg',
        status: 'VALIDATED',
        created_at: new Date().toISOString(),
      },
      {
        event_id: 'evt-assam-102',
        document_id: 'doc-ass-002',
        schedule_id: 'SCHED-ASSAM-V2',
        event_date: TODAY,
        raw_claim_text: 'Re-calibrated custody metering skid MIT-101 loops 1 to 10 at Numaligarh terminal following reopen signoff.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-ASSAM-501',
        matched_activity_id: 'ACT-ASSAM-501',
        discipline: 'INSTRUMENTATION',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'INCREMENTAL_QUANTITY',
        asset_tag: 'MIT-101',
        location: 'Numaligarh Terminal',
        claimed_quantity: 10,
        claimed_uom: 'loops',
        claimed_pct: 85,
        delay_reason: null,
        supervisor_id: null,
        photo_path: '/uploads/meter_cal.pdf',
        status: 'REVIEW_REQUIRED',
        created_at: new Date().toISOString(),
      },
      {
        event_id: 'evt-assam-103',
        document_id: 'doc-ass-003',
        schedule_id: 'SCHED-ASSAM-V2',
        event_date: TODAY,
        raw_claim_text: 'Field engineer reported additional battery bank wiring on SCADA RTU RTU-VS04 at Valve Station 4.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-ASSAM-401',
        matched_activity_id: null,
        discipline: 'ELECTRICAL',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: 'RTU-VS04',
        location: 'Sector Valve Station 4',
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 100,
        delay_reason: null,
        supervisor_id: null,
        photo_path: null,
        status: 'REVIEW_REQUIRED',
        created_at: new Date().toISOString(),
        is_completed_activity_target: true,
      },
      {
        event_id: 'evt-assam-104',
        document_id: 'doc-ass-004',
        schedule_id: 'SCHED-ASSAM-V2',
        event_date: TODAY,
        raw_claim_text: 'Soil boring test log submitted for Chainage Km 12 (Survey stage).',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-ASSAM-101',
        matched_activity_id: null,
        discipline: 'CIVIL',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: 'SRV-01',
        location: 'Pipeline Corridor Chainage 0-45km',
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 100,
        delay_reason: null,
        supervisor_id: null,
        photo_path: null,
        status: 'REVIEW_REQUIRED',
        created_at: new Date().toISOString(),
      },
    ],
    candidates: [
      {
        candidate_id: 'cand-ass-01',
        event_id: 'evt-assam-101',
        schedule_id: 'SCHED-ASSAM-V2',
        activity_id: 'ACT-ASSAM-201',
        rank_order: 1,
        match_tier: 'EXACT',
        composite_confidence: 0.94,
        semantic_score: 0.92,
        fuzzy_score: 0.95,
        location_score: 1.0,
        discipline_score: 1.0,
        supporting_signals: 'Exact tag FND-PS3 and location Pump Station 3 match active civil package.',
        disqualifying_signals: null,
        execution_state: 'IN_PROGRESS',
        eligibility_explanation: 'Eligible for matching in PRJ-ASSAM-01 · SCHED-ASSAM-V2 · Stage 3 — Civil & Foundation Works (WBS-1.3.1). Execution State: IN_PROGRESS. All eligibility criteria passed.',
        stage_name: 'Stage 3 — Civil & Foundation Works',
        wbs_code: 'WBS-1.3.1',
        is_eligible: true,
      },
      {
        candidate_id: 'cand-ass-02',
        event_id: 'evt-assam-101',
        schedule_id: 'SCHED-ASSAM-V2',
        activity_id: 'ACT-ASSAM-202',
        rank_order: 2,
        match_tier: 'DISCIPLINE_LOCATION',
        composite_confidence: 0.68,
        semantic_score: 0.65,
        fuzzy_score: 0.70,
        location_score: 0.85,
        discipline_score: 1.0,
        supporting_signals: 'Civil discipline alignment; adjacent structure in Block-2.',
        disqualifying_signals: 'Different foundation tag (COL-C4 vs FND-PS3).',
        execution_state: 'IN_PROGRESS',
        eligibility_explanation: 'Eligible for matching in PRJ-ASSAM-01 · SCHED-ASSAM-V2 · Stage 3 — Civil & Foundation Works (WBS-1.3.2). Execution State: IN_PROGRESS.',
        stage_name: 'Stage 3 — Civil & Foundation Works',
        wbs_code: 'WBS-1.3.2',
        is_eligible: true,
      },
    ],
    validationIssues: [
      {
        issue_id: 'val-ass-01',
        event_id: 'evt-assam-101',
        rule_code: 'VAL-CIV-QTY',
        severity: 'WARNING',
        description: 'Cumulative pour volume reaches 150/200 cu.m (75%). Prerequisite curing test certificate pending upload.',
      },
    ],
    decisions: [
      {
        decision_id: 'dec-ass-01',
        event_id: 'evt-assam-101',
        selected_activity_id: 'ACT-ASSAM-201',
        action: 'APPROVE',
        approved_pct: 75,
        approved_qty: 50,
        planner_id: 'usr-supervisor-assam',
        justification: 'Batching plant delivery tickets #BP-9821 verified and slump test accepted by Civil QA.',
        decided_at: new Date(Date.now() - 7200000).toISOString(),
      },
    ],
    dashboard: {
      total_claims: 28,
      pending_review: 4,
      actuals: 22,
      conflicts: 1,
      discipline_breakdown: [
        { discipline: 'CIVIL', name: 'Civil', count: 12, value: 12 },
        { discipline: 'PIPING', name: 'Piping', count: 9, value: 9 },
        { discipline: 'ELECTRICAL', name: 'Electrical', count: 4, value: 4 },
        { discipline: 'INSTRUMENTATION', name: 'Instrumentation', count: 2, value: 2 },
        { discipline: 'HSE', name: 'HSE', count: 1, value: 1 },
      ],
      claims_trend_pct: 12.5,
    },
    delayReasons: [
      { reason: 'Heavy Monsoon Rain at Sector 4', count: 5 },
      { reason: 'River HDD Geological Slump', count: 3 },
      { reason: 'Custody Meter Vendor Calibration Delay', count: 2 },
    ],
    institutionalMemory: [
      { topic: 'Burhi Dihing River HDD Crossing', resolution: 'Bentonite slurry density adjusted to 1.18 g/cc for riverbed stability.', count: 4 },
      { topic: 'PS-3 Heavy Concrete Pouring', resolution: 'Chilled water batching used during high ambient humidity.', count: 6 },
    ],
    forecast: {
      discipline: 'CIVIL',
      historical_ratio: 1.08,
      total_activities: 14,
      activities: [
        { activity_id: 'ACT-ASSAM-201', discipline: 'CIVIL', planned_duration: 10, historical_ratio: 1.05, forecast_duration: 11, slippage_days: 1 },
        { activity_id: 'ACT-ASSAM-301', discipline: 'PIPING', planned_duration: 16, historical_ratio: 1.12, forecast_duration: 18, slippage_days: 2 },
      ],
    },
    summaryReport: {
      text: 'Assam Pipeline Expansion (Duliajan–Numaligarh) reported steady progression across Stage 3 Civil works with Pump Station 3 reaching 75% foundation volume. HDD pilot boring beneath Burhi Dihing River is currently 20% complete with double-shift drilling crews mobilized to offset monsoon river swell.',
      highlights: [
        'Pump Station PS-3 foundation pour F-4 completed (50 cu.m batching approved).',
        'River HDD crossing 14" reamer pull-back scheduled for Sept 18.',
        'Zero lost-time safety incidents recorded over 145,000 man-hours.',
      ],
    },
  },

  'PRJ-RAJ-02': {
    activities: {
      'SCHED-RAJ-V1': [
        {
          activity_id: 'ACT-RAJ-101',
          schedule_id: 'SCHED-RAJ-V1',
          activity_name: 'Boundary Wall & Substation Grading Work',
          wbs_code: 'WBS-2.1.1',
          discipline: 'CIVIL',
          location: 'Barmer Grid Yard',
          asset_tag: 'GRD-YARD',
          planned_start: '2026-07-01',
          planned_finish: '2026-07-31',
          planned_quantity: 1200,
          uom: 'meters',
          baseline_pct_complete: 100,
          total_float: 0,
          is_critical: false,
          execution_state: 'COMPLETED',
          actual_start: '2026-07-01',
          actual_finish: '2026-07-28',
          actual_pct_complete: 100,
          stage_id: 'STG-RAJ-1',
          stage_name: 'Stage 1 — Land Acquisition & Site Prep',
          is_stage_completed: true,
          contractor_id: 'CTR-RAJ-01',
          contractor_name: 'Thar Desert Civil & Structural Infra Pvt Ltd',
          work_package_id: 'WP-RAJ-CIV-01',
          work_package_code: 'WP-2.1-CIV-01',
          work_package_name: 'Substation Grading & Transformer Foundation Package',
        },
        {
          activity_id: 'ACT-RAJ-201',
          schedule_id: 'SCHED-RAJ-V1',
          activity_name: '220kV Transformer Foundation & Firewall Construction',
          wbs_code: 'WBS-2.3.1',
          discipline: 'CIVIL',
          location: 'Switchyard Bay 01',
          asset_tag: 'TR-FND-01',
          planned_start: '2026-09-01',
          planned_finish: '2026-09-15',
          planned_quantity: 350,
          uom: 'cu.m',
          baseline_pct_complete: 85,
          total_float: 0,
          is_critical: true,
          execution_state: 'IN_PROGRESS',
          actual_start: '2026-09-01',
          actual_finish: null,
          actual_pct_complete: 85,
          stage_id: 'STG-RAJ-3',
          stage_name: 'Stage 3 — Civil Foundations & Control Room',
          is_stage_completed: false,
          contractor_id: 'CTR-RAJ-01',
          contractor_name: 'Thar Desert Civil & Structural Infra Pvt Ltd',
          work_package_id: 'WP-RAJ-CIV-01',
          work_package_code: 'WP-2.1-CIV-01',
          work_package_name: 'Substation Grading & Transformer Foundation Package',
        },
        {
          activity_id: 'ACT-RAJ-301',
          schedule_id: 'SCHED-RAJ-V1',
          activity_name: 'Inverter Transformer Station 33kV Tie-in',
          wbs_code: 'WBS-2.4.1',
          discipline: 'ELECTRICAL',
          location: 'Inverter Block 04',
          asset_tag: 'INV-TR-04',
          planned_start: '2026-08-25',
          planned_finish: '2026-09-05',
          planned_quantity: 4,
          uom: 'units',
          baseline_pct_complete: 100,
          total_float: 2,
          is_critical: false,
          execution_state: 'COMPLETED',
          actual_start: '2026-08-25',
          actual_finish: '2026-09-05',
          actual_pct_complete: 100,
          stage_id: 'STG-RAJ-4',
          stage_name: 'Stage 4 — Electrical Equipment & Inverters',
          is_stage_completed: false,
          contractor_id: 'CTR-RAJ-02',
          contractor_name: 'Marwar Power & Electrical EPC Ltd',
          work_package_id: 'WP-RAJ-ELE-01',
          work_package_code: 'WP-2.4-ELE-01',
          work_package_name: '33kV Inverter Transformer Station Tie-In Package',
        },
      ],
      'SCHED-RAJ-V2': [
        {
          activity_id: 'ACT-RAJ-201',
          schedule_id: 'SCHED-RAJ-V2',
          activity_name: '220kV Transformer Foundation & Firewall Construction',
          wbs_code: 'WBS-2.3.1',
          discipline: 'CIVIL',
          location: 'Switchyard Bay 01',
          asset_tag: 'TR-FND-01',
          planned_start: '2026-09-01',
          planned_finish: '2026-09-20',
          planned_quantity: 350,
          uom: 'cu.m',
          baseline_pct_complete: 90,
          total_float: 1,
          is_critical: true,
          execution_state: 'IN_PROGRESS',
          actual_start: '2026-09-01',
          actual_finish: null,
          actual_pct_complete: 90,
          stage_id: 'STG-RAJ-3',
          stage_name: 'Stage 3 — Civil Foundations & Control Room',
          is_stage_completed: false,
          contractor_id: 'CTR-RAJ-01',
          contractor_name: 'Thar Desert Civil & Structural Infra Pvt Ltd',
          work_package_id: 'WP-RAJ-V2-CIV-01',
          work_package_code: 'WP-V2-2.3-CIV',
          work_package_name: '220kV Transformer Foundation Package',
        },
      ],
    },
    events: [
      {
        event_id: 'evt-raj-101',
        document_id: 'doc-raj-001',
        schedule_id: 'SCHED-RAJ-V1',
        event_date: TODAY,
        raw_claim_text: 'Completed 220kV transformer TR-FND-01 concrete pour firewall casting in Switchyard Bay 01.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-RAJ-201',
        matched_activity_id: 'ACT-RAJ-201',
        discipline: 'CIVIL',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: 'TR-FND-01',
        location: 'Switchyard Bay 01',
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 90,
        delay_reason: null,
        supervisor_id: null,
        photo_path: '/uploads/raj_bay01.jpg',
        status: 'VALIDATED',
        created_at: new Date().toISOString(),
      },
    ],
    candidates: [
      {
        candidate_id: 'cand-raj-01',
        event_id: 'evt-raj-101',
        schedule_id: 'SCHED-RAJ-V1',
        activity_id: 'ACT-RAJ-201',
        rank_order: 1,
        match_tier: 'EXACT',
        composite_confidence: 0.96,
        semantic_score: 0.94,
        fuzzy_score: 0.98,
        location_score: 1.0,
        discipline_score: 1.0,
        supporting_signals: 'Exact asset tag TR-FND-01 and switchyard location match active Rajasthan package.',
        disqualifying_signals: null,
        execution_state: 'IN_PROGRESS',
        eligibility_explanation: 'Eligible for matching in PRJ-RAJ-02 · SCHED-RAJ-V1 · Stage 3 — Civil Foundations (WBS-2.3.1). Execution State: IN_PROGRESS.',
        stage_name: 'Stage 3 — Civil Foundations & Control Room',
        wbs_code: 'WBS-2.3.1',
        is_eligible: true,
      },
    ],
    validationIssues: [],
    decisions: [],
    dashboard: {
      total_claims: 19,
      pending_review: 2,
      actuals: 16,
      conflicts: 0,
      discipline_breakdown: [
        { discipline: 'ELECTRICAL', name: 'Electrical', count: 10, value: 10 },
        { discipline: 'CIVIL', name: 'Civil', count: 7, value: 7 },
        { discipline: 'INSTRUMENTATION', name: 'Instrumentation', count: 2, value: 2 },
      ],
      claims_trend_pct: 8.4,
    },
    delayReasons: [
      { reason: 'Desert Dust Storm Work Stoppage', count: 4 },
      { reason: 'High Voltage Busbar Supplier Lead Time', count: 2 },
    ],
    institutionalMemory: [
      { topic: 'Barmer High Ambient Heat Concrete Pours', resolution: 'Night-shift pouring schedule enforced to prevent thermal cracking.', count: 8 },
    ],
    forecast: {
      discipline: 'ELECTRICAL',
      historical_ratio: 1.03,
      total_activities: 12,
      activities: [
        { activity_id: 'ACT-RAJ-201', discipline: 'CIVIL', planned_duration: 15, historical_ratio: 1.02, forecast_duration: 15, slippage_days: 0 },
        { activity_id: 'ACT-RAJ-301', discipline: 'ELECTRICAL', planned_duration: 12, historical_ratio: 1.04, forecast_duration: 13, slippage_days: 1 },
      ],
    },
    summaryReport: {
      text: 'Rajasthan Solar Grid Substation at Barmer is 82.5% complete overall. 220kV Transformer yard foundations are 90% poured and Switchgear room cabling termination is underway with commissioning targets on track.',
      highlights: [
        'Switchyard Bay 01 firewall structure successfully cast.',
        'Zero grid non-compliances flagged during CEA pre-energization audit.',
      ],
    },
  },

  'PRJ-KG-03': {
    activities: {
      'SCHED-KG-BL1': [
        {
          activity_id: 'ACT-KG-101',
          schedule_id: 'SCHED-KG-BL1',
          activity_name: 'Offshore Platform Deck Loadout & Transport',
          wbs_code: 'WBS-3.1.1',
          discipline: 'STATIC_ROTATING_EQUIPMENT',
          location: 'Kakinada Yard',
          asset_tag: 'BARGE-K01',
          planned_start: '2026-07-10',
          planned_finish: '2026-08-15',
          planned_quantity: 1,
          uom: 'module',
          baseline_pct_complete: 100,
          total_float: 0,
          is_critical: true,
          execution_state: 'COMPLETED',
          actual_start: '2026-07-10',
          actual_finish: '2026-08-12',
          actual_pct_complete: 100,
          stage_id: 'STG-KG-1',
          stage_name: 'Stage 1 — Offshore Engineering & Fabrication',
          is_stage_completed: true,
          contractor_id: 'CTR-KG-01',
          contractor_name: 'Coromandel Offshore Marine & Heavy Lift Ltd',
          work_package_id: 'WP-KG-TOP-01',
          work_package_code: 'WP-3.1-EQUIP-01',
          work_package_name: 'Offshore Platform Deck Loadout & Marine Tow Package',
        },
        {
          activity_id: 'ACT-KG-201',
          schedule_id: 'SCHED-KG-BL1',
          activity_name: 'High-Pressure Gas Riser Spool Tie-In Weld',
          wbs_code: 'WBS-3.3.1',
          discipline: 'PIPING',
          location: 'Offshore Platform Topside',
          asset_tag: 'RSR-SPL-01',
          planned_start: '2026-09-01',
          planned_finish: '2026-09-15',
          planned_quantity: 18,
          uom: 'spools',
          baseline_pct_complete: 55,
          total_float: 0,
          is_critical: true,
          execution_state: 'IN_PROGRESS',
          actual_start: '2026-09-01',
          actual_finish: null,
          actual_pct_complete: 55,
          stage_id: 'STG-KG-3',
          stage_name: 'Stage 3 — Platform Topsides Piping & Hookup',
          is_stage_completed: false,
          contractor_id: 'CTR-KG-02',
          contractor_name: 'Bay of Bengal Subsea Piping & Welds Ltd',
          work_package_id: 'WP-KG-PIP-01',
          work_package_code: 'WP-3.3-PIP-01',
          work_package_name: 'Gas Riser Spool Topside Tie-In Welding Package',
        },
      ],
      'SCHED-KG-BL2': [
        {
          activity_id: 'ACT-KG-201',
          schedule_id: 'SCHED-KG-BL2',
          activity_name: 'High-Pressure Gas Riser Spool Tie-In Weld',
          wbs_code: 'WBS-3.3.1',
          discipline: 'PIPING',
          location: 'Offshore Platform Topside',
          asset_tag: 'RSR-SPL-01',
          planned_start: '2026-09-01',
          planned_finish: '2026-09-20',
          planned_quantity: 18,
          uom: 'spools',
          baseline_pct_complete: 60,
          total_float: 1,
          is_critical: true,
          execution_state: 'IN_PROGRESS',
          actual_start: '2026-09-01',
          actual_finish: null,
          actual_pct_complete: 60,
          stage_id: 'STG-KG-3',
          stage_name: 'Stage 3 — Platform Topsides Piping & Hookup',
          is_stage_completed: false,
          contractor_id: 'CTR-KG-02',
          contractor_name: 'Bay of Bengal Subsea Piping & Welds Ltd',
          work_package_id: 'WP-KG-BL2-PIP-01',
          work_package_code: 'WP-BL2-3.3-PIP',
          work_package_name: 'Gas Riser Spool Revised Tie-In Package',
        },
      ],
    },
    events: [
      {
        event_id: 'evt-kg-101',
        document_id: 'doc-kg-001',
        schedule_id: 'SCHED-KG-BL1',
        event_date: TODAY,
        raw_claim_text: 'Completed welding joints 1-6 on Gas Riser Spool RSR-SPL-01 on offshore platform topside.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-KG-201',
        matched_activity_id: 'ACT-KG-201',
        discipline: 'PIPING',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'INCREMENTAL_QUANTITY',
        asset_tag: 'RSR-SPL-01',
        location: 'Offshore Platform Topside',
        claimed_quantity: 6,
        claimed_uom: 'joints',
        claimed_pct: 55,
        delay_reason: null,
        supervisor_id: null,
        photo_path: '/uploads/kg_riser.pdf',
        status: 'VALIDATED',
        created_at: new Date().toISOString(),
      },
    ],
    candidates: [
      {
        candidate_id: 'cand-kg-01',
        event_id: 'evt-kg-101',
        schedule_id: 'SCHED-KG-BL1',
        activity_id: 'ACT-KG-201',
        rank_order: 1,
        match_tier: 'EXACT',
        composite_confidence: 0.95,
        semantic_score: 0.94,
        fuzzy_score: 0.97,
        location_score: 1.0,
        discipline_score: 1.0,
        supporting_signals: 'High-pressure riser spool tag RSR-SPL-01 matched active offshore package.',
        disqualifying_signals: null,
        execution_state: 'IN_PROGRESS',
        eligibility_explanation: 'Eligible for matching in PRJ-KG-03 · SCHED-KG-BL1 · Stage 3 — Platform Topsides Hookup (WBS-3.3.1). Execution State: IN_PROGRESS.',
        stage_name: 'Stage 3 — Platform Topsides Piping & Hookup',
        wbs_code: 'WBS-3.3.1',
        is_eligible: true,
      },
    ],
    validationIssues: [],
    decisions: [],
    dashboard: {
      total_claims: 14,
      pending_review: 1,
      actuals: 12,
      conflicts: 0,
      discipline_breakdown: [
        { discipline: 'PIPING', name: 'Piping', count: 8, value: 8 },
        { discipline: 'STATIC_ROTATING_EQUIPMENT', name: 'Equipment', count: 4, value: 4 },
        { discipline: 'INSTRUMENTATION', name: 'Instrumentation', count: 2, value: 2 },
      ],
      claims_trend_pct: 6.2,
    },
    delayReasons: [
      { reason: 'Rough Sea Swell Hindering Crane Barge Operations', count: 3 },
      { reason: 'Non-Destructive Testing Radiography Re-shoot', count: 1 },
    ],
    institutionalMemory: [
      { topic: 'Deepwater Topside Tie-In Welds', resolution: 'Automated orbital TIG welding procedure qualified for duplex stainless steel.', count: 5 },
    ],
    forecast: {
      discipline: 'PIPING',
      historical_ratio: 1.06,
      total_activities: 8,
      activities: [
        { activity_id: 'ACT-KG-201', discipline: 'PIPING', planned_duration: 15, historical_ratio: 1.06, forecast_duration: 16, slippage_days: 1 },
      ],
    },
    summaryReport: {
      text: 'KG Basin Offshore Platform Modification is 58.4% completed. High-pressure riser spool welding is ongoing on the main topside module with hydrotest pre-checks scheduled next week.',
      highlights: [
        'Duplex gas riser joint welding passed 100% phased array ultrasonic inspection.',
        'Marine support vessel sea-keeping stability cleared under monsoon sea state 4.',
      ],
    },
  },
};

// Dynamic runtime storage for in-memory created claims, activities state, and reopen requests
export const MOCK_DYNAMIC_EVENTS = new Map<string, ExecutionEvent>();
export const MOCK_ACTIVITY_STATE_OVERRIDES = new Map<string, ExecutionState>();

// Dynamic Reopen Requests Store
const INITIAL_REOPEN_REQUESTS: ReopenRequest[] = [
  {
    request_id: 'REQ-REOPEN-001',
    reopen_id: 'REQ-REOPEN-001',
    activity_id: 'ACT-ASSAM-401',
    activity_name: 'SCADA RTU & Solar Power Skid Installation',
    schedule_id: 'SCHED-ASSAM-V2',
    project_id: 'PRJ-ASSAM-01',
    event_id: 'evt-assam-103',
    requested_by: 'Site Engineer (Field Team Alpha)',
    requested_by_role: 'SITE_ENGINEER',
    requested_by_name: 'Site Engineer (Field Team Alpha)',
    requested_at: '2026-09-12T10:30:00Z',
    created_at: '2026-09-12T10:30:00Z',
    reason: 'Scope Modification / Additional Work Required',
    justification: 'Secondary battery bank calibration and DC/DC converter rework required following vendor bulletin #V-881.',
    status: 'PENDING',
    original_actual_finish: '2026-09-08',
    original_actual_pct: 100,
    evidence_ref: 'Vendor_Bulletin_V881.pdf',
    reviewed_by: null,
    reviewed_at: null,
    review_comments: null,
    supervisor_notes: null,
    locked_actuals_summary: {
      actual_start: '2026-09-01',
      actual_finish: '2026-09-08',
      actual_pct: 100,
      actual_qty: 1,
    },
  },
  {
    request_id: 'REQ-REOPEN-002',
    reopen_id: 'REQ-REOPEN-002',
    activity_id: 'ACT-RAJ-301',
    activity_name: 'Inverter Transformer Station 33kV Tie-in',
    schedule_id: 'SCHED-RAJ-V1',
    project_id: 'PRJ-RAJ-02',
    event_id: 'evt-raj-102',
    requested_by: 'Site Engineer (Barmer Grid Lead)',
    requested_by_role: 'SITE_ENGINEER',
    requested_by_name: 'Site Engineer (Barmer Grid Lead)',
    requested_at: '2026-09-10T14:15:00Z',
    created_at: '2026-09-10T14:15:00Z',
    reason: 'Rework / QC Rectification Work',
    justification: 'Post-energization terminal box sealing and thermography re-inspection.',
    status: 'PENDING',
    original_actual_finish: '2026-09-05',
    original_actual_pct: 100,
    evidence_ref: 'Thermography_Report_IT33.pdf',
    reviewed_by: null,
    reviewed_at: null,
    review_comments: null,
    supervisor_notes: null,
    locked_actuals_summary: {
      actual_start: '2026-08-28',
      actual_finish: '2026-09-05',
      actual_pct: 100,
      actual_qty: 1,
    },
  },
];

export const MOCK_REOPEN_STORE = new Map<string, ReopenRequest>();
INITIAL_REOPEN_REQUESTS.forEach((r) => {
  if (r.request_id) MOCK_REOPEN_STORE.set(r.request_id, r);
  if (r.reopen_id) MOCK_REOPEN_STORE.set(r.reopen_id, r);
});

export function getMockReopenRequests(activityId?: string): ReopenRequest[] {
  const all = Array.from(MOCK_REOPEN_STORE.values());
  const activeProj = getActiveProjectId();
  // Filter deduplicated
  const unique = Array.from(new Map(all.map(r => [r.reopen_id || r.request_id, r])).values());
  const filtered = unique.filter((r) => r.project_id === activeProj);
  if (activityId) {
    return filtered.filter((r) => r.activity_id === activityId);
  }
  return filtered;
}

export function createMockReopenRequest(payload: {
  activity_id: string;
  reason: string;
  justification?: string;
  requested_by_role?: UserRole;
  requested_by_name?: string;
  event_id?: string;
  evidence_ref?: string;
}): ReopenRequest {
  const pId = getActiveProjectId();
  const sId = getActiveScheduleVersionId();
  const activities = getActivitiesForCurrentSchedule(sId);
  const act = activities.find((a) => a.activity_id === payload.activity_id);

  const reqId = `REQ-REOPEN-${Date.now()}`;
  const req: ReopenRequest = {
    request_id: reqId,
    reopen_id: reqId,
    activity_id: payload.activity_id,
    activity_name: act?.activity_name || payload.activity_id,
    schedule_id: sId,
    project_id: pId,
    event_id: payload.event_id || null,
    requested_by: payload.requested_by_name || 'Site Engineer',
    requested_by_role: payload.requested_by_role || 'SITE_ENGINEER',
    requested_by_name: payload.requested_by_name || 'Site Engineer',
    requested_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    reason: payload.reason,
    justification: payload.justification || payload.reason,
    status: 'PENDING',
    original_actual_finish: act?.actual_finish || '2026-09-08',
    original_actual_pct: act?.actual_pct_complete || 100,
    evidence_ref: payload.evidence_ref || 'Field_Correction_Notice.pdf',
    reviewed_by: null,
    reviewed_at: null,
    review_comments: null,
    supervisor_notes: null,
    locked_actuals_summary: {
      actual_start: act?.actual_start || act?.planned_start || '2026-09-01',
      actual_finish: act?.actual_finish || act?.planned_finish || '2026-09-08',
      actual_pct: act?.actual_pct_complete ?? act?.baseline_pct_complete ?? 100,
      actual_qty: act?.planned_quantity || null,
    },
  };

  MOCK_REOPEN_STORE.set(req.request_id!, req);
  MOCK_REOPEN_STORE.set(req.reopen_id!, req);
  MOCK_ACTIVITY_STATE_OVERRIDES.set(payload.activity_id, 'REOPEN_REQUESTED');
  window.dispatchEvent(new CustomEvent('setu:reopen-changed', { detail: req }));
  return req;
}

export function reviewMockReopenRequest(
  payloadOrId: string | { reopen_id: string; decision: 'APPROVED' | 'REJECTED'; supervisor_notes?: string; reviewer_name?: string },
  legacyPayload?: { action: 'APPROVE' | 'REJECT'; comments: string }
): ReopenRequest {
  const requestId = typeof payloadOrId === 'string' ? payloadOrId : payloadOrId.reopen_id;
  const isApproved = typeof payloadOrId === 'object' ? payloadOrId.decision === 'APPROVED' : legacyPayload?.action === 'APPROVE';
  const notes = typeof payloadOrId === 'object' ? payloadOrId.supervisor_notes : legacyPayload?.comments;
  const reviewer = typeof payloadOrId === 'object' ? payloadOrId.reviewer_name : 'Supervisor';

  const existing = MOCK_REOPEN_STORE.get(requestId) || Array.from(MOCK_REOPEN_STORE.values()).find(r => (r.reopen_id === requestId || r.request_id === requestId));
  if (!existing) {
    throw new Error(`Reopen request ${requestId} not found.`);
  }

  const updated: ReopenRequest = {
    ...existing,
    status: isApproved ? 'APPROVED' : 'REJECTED',
    reviewed_by: reviewer || 'Supervisor',
    reviewed_at: new Date().toISOString(),
    review_comments: notes || null,
    supervisor_notes: notes || null,
  };

  if (existing.request_id) MOCK_REOPEN_STORE.set(existing.request_id, updated);
  if (existing.reopen_id) MOCK_REOPEN_STORE.set(existing.reopen_id, updated);

  if (isApproved) {
    MOCK_ACTIVITY_STATE_OVERRIDES.set(existing.activity_id, 'REOPENED');
  } else {
    MOCK_ACTIVITY_STATE_OVERRIDES.set(existing.activity_id, 'COMPLETED');
  }

  window.dispatchEvent(new CustomEvent('setu:reopen-changed', { detail: updated }));
  return updated;
}

export const MOCK_ACTIVITY_PROGRESS_OVERRIDES = new Map<string, {
  actual_pct_complete: number;
  actual_start?: string | null;
  actual_finish?: string | null;
  execution_state?: ExecutionState;
}>();

export function recordApprovedActualProgress(
  activityId: string,
  approvedPct?: number | null,
  approvedQty?: number | null,
  action?: DecisionAction
): void {
  if (action === 'REJECT' || action === 'HOLD') {
    return;
  }

  const sId = getActiveScheduleVersionId();
  const acts = getActivitiesForCurrentSchedule(sId);
  const target = acts.find((a) => a.activity_id === activityId);
  if (!target) return;

  const pct = approvedPct !== undefined && approvedPct !== null ? Math.min(100, Math.max(0, approvedPct)) : 100;
  const newState: ExecutionState = pct >= 100 ? 'COMPLETED' : 'IN_PROGRESS';
  const finishDate = pct >= 100 ? (target.actual_finish || TODAY) : null;
  const startDate = target.actual_start || target.planned_start || TODAY;

  MOCK_ACTIVITY_PROGRESS_OVERRIDES.set(activityId, {
    actual_pct_complete: pct,
    actual_start: startDate,
    actual_finish: finishDate,
    execution_state: newState,
  });

  MOCK_ACTIVITY_STATE_OVERRIDES.set(activityId, newState);

  window.dispatchEvent(
    new CustomEvent('setu:activity-progress-changed', {
      detail: { activityId, actual_pct_complete: pct, execution_state: newState },
    })
  );
  window.dispatchEvent(
    new CustomEvent('setu:activity-state-changed', {
      detail: { activityId, state: newState },
    })
  );
}

export function updateActivityExecutionState(activityId: string, state: ExecutionState): ScheduleActivity | null {
  MOCK_ACTIVITY_STATE_OVERRIDES.set(activityId, state);
  window.dispatchEvent(new CustomEvent('setu:activity-state-changed', { detail: { activityId, state } }));
  const acts = getActivitiesForCurrentSchedule();
  return acts.find((a) => a.activity_id === activityId) || null;
}

export function getDatasetForCurrentProject(projectId?: string): ProjectMockDataset {
  const pId = projectId || getActiveProjectId();
  return MOCK_DATASETS[pId] || MOCK_DATASETS['PRJ-ASSAM-01'];
}

export function getActivitiesForCurrentSchedule(scheduleId?: string, projectId?: string): ScheduleActivity[] {
  const pId = projectId || getActiveProjectId();
  const ds = getDatasetForCurrentProject(pId);
  const sId = scheduleId || getActiveScheduleVersionId();
  const list = ds.activities[sId] || ds.activities['SCHED-ASSAM-V2'] || Object.values(ds.activities)[0] || [];

  return list.map((a) => {
    const overrideProgress = MOCK_ACTIVITY_PROGRESS_OVERRIDES.get(a.activity_id);
    const overrideState = MOCK_ACTIVITY_STATE_OVERRIDES.get(a.activity_id);

    let actualPct = a.actual_pct_complete ?? (a.baseline_pct_complete > 0 ? a.baseline_pct_complete : 0);
    let execState = a.execution_state || 'NOT_STARTED';
    let actualStart = a.actual_start;
    let actualFinish = a.actual_finish;

    if (overrideProgress) {
      actualPct = overrideProgress.actual_pct_complete;
      if (overrideProgress.execution_state) execState = overrideProgress.execution_state;
      if (overrideProgress.actual_start !== undefined) actualStart = overrideProgress.actual_start;
      if (overrideProgress.actual_finish !== undefined) actualFinish = overrideProgress.actual_finish;
    }

    if (overrideState) {
      execState = overrideState;
      if (overrideState === 'COMPLETED') {
        actualPct = 100;
        actualFinish = actualFinish || a.planned_finish || '2026-09-08';
      }
    }

    return {
      ...a,
      actual_pct_complete: actualPct,
      execution_state: execState,
      actual_start: actualStart,
      actual_finish: actualFinish,
      weight: a.weight && a.weight > 0 ? a.weight : (a.planned_quantity && a.planned_quantity > 0 ? a.planned_quantity : 10),
    };
  });
}

export function getMockProjectProgress(projectId?: string, scheduleId?: string): ProjectProgressSummary {
  const pId = projectId || getActiveProjectId();
  const sId = scheduleId || getActiveScheduleVersionId();
  const acts = getActivitiesForCurrentSchedule(sId, pId);
  const proj = DEMO_PROJECTS.find((p) => p.id === pId) || DEMO_PROJECTS[0];
  return calculateProjectProgress(acts, proj.stages, sId, pId);
}

export function getMockStageProgress(projectId?: string, scheduleId?: string, stageId?: string): StageProgressSummary[] {
  const pId = projectId || getActiveProjectId();
  const sId = scheduleId || getActiveScheduleVersionId();
  const acts = getActivitiesForCurrentSchedule(sId, pId);
  const proj = DEMO_PROJECTS.find((p) => p.id === pId) || DEMO_PROJECTS[0];
  const allStages = calculateStageProgress(acts, proj.stages, sId, pId);
  if (stageId) {
    return allStages.filter((s) => s.stage_id === stageId);
  }
  return allStages;
}

export function getMockWBSProgress(projectId?: string, scheduleId?: string): WBSProgressSummary[] {
  const pId = projectId || getActiveProjectId();
  const sId = scheduleId || getActiveScheduleVersionId();
  const acts = getActivitiesForCurrentSchedule(sId, pId);
  return calculateWBSProgress(acts, sId, pId);
}

export function getMockActivityProgress(activityId: string, projectId?: string, scheduleId?: string): ActivityProgressSummary | null {
  const pId = projectId || getActiveProjectId();
  const sId = scheduleId || getActiveScheduleVersionId();
  const acts = getActivitiesForCurrentSchedule(sId, pId);
  const act = acts.find((a) => a.activity_id === activityId);
  if (!act) return null;

  const { plannedProgress, actualProgress, variance, effectiveWeight } = deriveActivityProgress(act);
  return {
    activity_id: act.activity_id,
    activity_name: act.activity_name,
    project_id: pId,
    schedule_version_id: sId,
    stage_id: act.stage_id || 'STG-01',
    stage_name: act.stage_name || 'Stage 1',
    wbs_code: act.wbs_code || 'WBS',
    discipline: act.discipline,
    weight: effectiveWeight,
    planned_start: act.planned_start,
    planned_finish: act.planned_finish,
    actual_start: act.actual_start || null,
    actual_finish: act.actual_finish || null,
    planned_progress: plannedProgress,
    actual_progress: actualProgress,
    variance,
    execution_state: act.execution_state || 'NOT_STARTED',
    is_critical: act.is_critical ?? false,
  };
}

export const MOCK_CONTRACTORS: Record<string, Contractor[]> = {
  'PRJ-ASSAM-01': [
    {
      id: 'CTR-ASSAM-01',
      projectId: 'PRJ-ASSAM-01',
      name: 'Brahmaputra Infrastructure & Civil EPC Ltd',
      code: 'BICE-ASSAM',
      description: 'Master civil, earthworks, and structural foundation contractor for pump stations and pipeline ROW',
      status: 'ACTIVE',
      active: true,
      contactName: 'Ranjan Gogoi',
      contactRole: 'Project Director (Civil)',
      contactEmail: 'ranjan.gogoi@brahmaputrainfra.in',
      contactPhone: '+91 94350 12845',
    },
    {
      id: 'CTR-ASSAM-02',
      projectId: 'PRJ-ASSAM-01',
      name: 'Eastern River HDD & Crossings Corp',
      code: 'ERHC-ASSAM',
      description: 'Specialized trenchless Horizontal Directional Drilling (HDD) and river crossing pipeline contractor',
      status: 'ACTIVE',
      active: true,
      contactName: 'Devajit Saikia',
      contactRole: 'HDD Engineering Lead',
      contactEmail: 'devajit.s@easterncrossings.in',
      contactPhone: '+91 98640 45210',
    },
    {
      id: 'CTR-ASSAM-03',
      projectId: 'PRJ-ASSAM-01',
      name: 'NorthEast Electrical & SCADA Systems Pvt Ltd',
      code: 'NEES-ASSAM',
      description: 'Turnkey electrical substation, solar PV skid, RTU telemetry and instrumentation EPC contractor',
      status: 'ACTIVE',
      active: true,
      contactName: 'Prakash Baruah',
      contactRole: 'Commissioning Manager',
      contactEmail: 'prakash.b@nees-systems.com',
      contactPhone: '+91 94351 88920',
    },
  ],
  'PRJ-RAJ-02': [
    {
      id: 'CTR-RAJ-01',
      projectId: 'PRJ-RAJ-02',
      name: 'Thar Desert Civil & Structural Infra Pvt Ltd',
      code: 'TDC-RAJ',
      description: 'Site grading, boundary wall construction, and 220kV heavy equipment transformer yard contractor',
      status: 'ACTIVE',
      active: true,
      contactName: 'Vikram Rathore',
      contactRole: 'Site In-Charge',
      contactEmail: 'vikram.r@tharinfra.co.in',
      contactPhone: '+91 94140 33211',
    },
    {
      id: 'CTR-RAJ-02',
      projectId: 'PRJ-RAJ-02',
      name: 'Marwar Power & Electrical EPC Ltd',
      code: 'MPE-RAJ',
      description: 'High-voltage substation, 33kV inverter transformer station, and solar farm tie-in EPC contractor',
      status: 'ACTIVE',
      active: true,
      contactName: 'Suresh Choudhary',
      contactRole: 'Chief Electrical Engineer',
      contactEmail: 'suresh.c@marwarpower.in',
      contactPhone: '+91 98290 77154',
    },
  ],
  'PRJ-KG-03': [
    {
      id: 'CTR-KG-01',
      projectId: 'PRJ-KG-03',
      name: 'Coromandel Offshore Marine & Heavy Lift Ltd',
      code: 'COMH-KG',
      description: 'Offshore platform deck module loadout, barge transportation, and deepwater marine operations',
      status: 'ACTIVE',
      active: true,
      contactName: 'Capt. K. V. Rao',
      contactRole: 'Marine Operations Director',
      contactEmail: 'kv.rao@coromandelmarine.in',
      contactPhone: '+91 98480 11200',
    },
    {
      id: 'CTR-KG-02',
      projectId: 'PRJ-KG-03',
      name: 'Bay of Bengal Subsea Piping & Welds Ltd',
      code: 'BOBSP-KG',
      description: 'High-pressure gas riser spool topside fabrication, subsea hyperbaric welding, and hook-up EPC',
      status: 'ACTIVE',
      active: true,
      contactName: 'Dr. M. S. Murthy',
      contactRole: 'Subsea Welding Lead',
      contactEmail: 'murthy.ms@bayofbengalsubsea.com',
      contactPhone: '+91 94401 66732',
    },
  ],
};

export const MOCK_WORK_PACKAGES: Record<string, Record<string, WorkPackage[]>> = {
  'PRJ-ASSAM-01': {
    'SCHED-ASSAM-V2': [
      {
        id: 'WP-ASSAM-CIV-01',
        projectId: 'PRJ-ASSAM-01',
        scheduleId: 'SCHED-ASSAM-V2',
        contractorId: 'CTR-ASSAM-01',
        name: 'Pump Station Civil & Structural Foundation Package',
        code: 'WP-1.3-CIV-01',
        description: 'Pump Station 3 foundation concrete pouring, column rebar cages, and grade beams',
        discipline: 'CIVIL',
        stageId: 'STG-ASSAM-3',
        wbsId: 'WBS-1.3.1',
        status: 'IN_PROGRESS',
        plannedStart: '2026-09-01',
        plannedFinish: '2026-09-14',
        activityIds: ['ACT-ASSAM-201', 'ACT-ASSAM-202'],
      },
      {
        id: 'WP-ASSAM-PIP-01',
        projectId: 'PRJ-ASSAM-01',
        scheduleId: 'SCHED-ASSAM-V2',
        contractorId: 'CTR-ASSAM-02',
        name: 'Mainline HDD River Crossing & Trenching Package',
        code: 'WP-1.4-PIP-01',
        description: '14" Crude trunkline welding and Horizontal Directional Drilling under Burhi Dihing River',
        discipline: 'PIPING',
        stageId: 'STG-ASSAM-4',
        wbsId: 'WBS-1.4.1',
        status: 'IN_PROGRESS',
        plannedStart: '2026-09-02',
        plannedFinish: '2026-09-28',
        activityIds: ['ACT-ASSAM-301', 'ACT-ASSAM-302'],
      },
      {
        id: 'WP-ASSAM-ELE-01',
        projectId: 'PRJ-ASSAM-01',
        scheduleId: 'SCHED-ASSAM-V2',
        contractorId: 'CTR-ASSAM-03',
        name: 'SCADA RTU, Solar Skid & Metering Package',
        code: 'WP-1.5-ELE-01',
        description: 'Valve Station 4 RTU solar skid power installation and Numaligarh terminal custody metering loops',
        discipline: 'ELECTRICAL',
        stageId: 'STG-ASSAM-5',
        wbsId: 'WBS-1.5.1',
        status: 'IN_PROGRESS',
        plannedStart: '2026-09-01',
        plannedFinish: '2026-09-16',
        activityIds: ['ACT-ASSAM-401', 'ACT-ASSAM-501'],
      },
      {
        id: 'WP-ASSAM-SRV-01',
        projectId: 'PRJ-ASSAM-01',
        scheduleId: 'SCHED-ASSAM-V2',
        contractorId: 'CTR-ASSAM-01',
        name: 'Detail Route Survey & HSE Clearance Package',
        code: 'WP-1.1-GEN-01',
        description: 'Topographical corridor survey, geotechnical soil borings, and monsoon environmental clearance audit',
        discipline: 'CIVIL',
        stageId: 'STG-ASSAM-1',
        wbsId: 'WBS-1.1.1',
        status: 'COMPLETED',
        plannedStart: '2026-06-01',
        plannedFinish: '2026-09-08',
        activityIds: ['ACT-ASSAM-101', 'ACT-ASSAM-601'],
      },
    ],
    'SCHED-ASSAM-V1': [
      {
        id: 'WP-ASSAM-V1-CIV-01',
        projectId: 'PRJ-ASSAM-01',
        scheduleId: 'SCHED-ASSAM-V1',
        contractorId: 'CTR-ASSAM-01',
        name: 'Pump Station Civil Foundation Baseline Package',
        code: 'WP-V1-1.3-CIV',
        description: 'Original approved civil scope for Pump Station 3',
        discipline: 'CIVIL',
        stageId: 'STG-ASSAM-3',
        wbsId: 'WBS-1.3.1',
        status: 'COMPLETED',
        plannedStart: '2026-01-20',
        plannedFinish: '2026-02-10',
        activityIds: ['ACT-ASSAM-201'],
      },
      {
        id: 'WP-ASSAM-V1-PIP-01',
        projectId: 'PRJ-ASSAM-01',
        scheduleId: 'SCHED-ASSAM-V1',
        contractorId: 'CTR-ASSAM-02',
        name: 'Mainline Trunkline Welding Baseline Package',
        code: 'WP-V1-1.4-PIP',
        description: 'Original approved trunkline welding package',
        discipline: 'PIPING',
        stageId: 'STG-ASSAM-4',
        wbsId: 'WBS-1.4.1',
        status: 'COMPLETED',
        plannedStart: '2026-02-15',
        plannedFinish: '2026-03-30',
        activityIds: ['ACT-ASSAM-301'],
      },
    ],
    'SCHED-ASSAM-REC': [
      {
        id: 'WP-ASSAM-REC-CIV-01',
        projectId: 'PRJ-ASSAM-01',
        scheduleId: 'SCHED-ASSAM-REC',
        contractorId: 'CTR-ASSAM-01',
        name: 'Pump Station Fast-Track Concrete Pouring Package',
        code: 'WP-REC-1.3-CIV',
        description: 'Accelerated monsoon catch-up civil package',
        discipline: 'CIVIL',
        stageId: 'STG-ASSAM-3',
        wbsId: 'WBS-1.3.1',
        status: 'IN_PROGRESS',
        plannedStart: '2026-09-01',
        plannedFinish: '2026-09-08',
        activityIds: ['ACT-ASSAM-201'],
      },
    ],
  },
  'PRJ-RAJ-02': {
    'SCHED-RAJ-V1': [
      {
        id: 'WP-RAJ-CIV-01',
        projectId: 'PRJ-RAJ-02',
        scheduleId: 'SCHED-RAJ-V1',
        contractorId: 'CTR-RAJ-01',
        name: 'Substation Grading & Transformer Foundation Package',
        code: 'WP-2.1-CIV-01',
        description: 'Barmer Grid Yard boundary wall, switchyard site grading, and 220kV transformer foundation casting',
        discipline: 'CIVIL',
        stageId: 'STG-RAJ-1',
        wbsId: 'WBS-2.1.1',
        status: 'IN_PROGRESS',
        plannedStart: '2026-07-01',
        plannedFinish: '2026-09-15',
        activityIds: ['ACT-RAJ-101', 'ACT-RAJ-201'],
      },
      {
        id: 'WP-RAJ-ELE-01',
        projectId: 'PRJ-RAJ-02',
        scheduleId: 'SCHED-RAJ-V1',
        contractorId: 'CTR-RAJ-02',
        name: '33kV Inverter Transformer Station Tie-In Package',
        code: 'WP-2.4-ELE-01',
        description: 'Inverter Transformer Station 33kV bus tie-in, cabling, and pre-energization verification',
        discipline: 'ELECTRICAL',
        stageId: 'STG-RAJ-4',
        wbsId: 'WBS-2.4.1',
        status: 'COMPLETED',
        plannedStart: '2026-08-25',
        plannedFinish: '2026-09-05',
        activityIds: ['ACT-RAJ-301'],
      },
    ],
    'SCHED-RAJ-V2': [
      {
        id: 'WP-RAJ-V2-CIV-01',
        projectId: 'PRJ-RAJ-02',
        scheduleId: 'SCHED-RAJ-V2',
        contractorId: 'CTR-RAJ-01',
        name: '220kV Transformer Foundation Package',
        code: 'WP-V2-2.3-CIV',
        description: 'Revised switchyard bay transformer foundation construction',
        discipline: 'CIVIL',
        stageId: 'STG-RAJ-3',
        wbsId: 'WBS-2.3.1',
        status: 'IN_PROGRESS',
        plannedStart: '2026-09-01',
        plannedFinish: '2026-09-20',
        activityIds: ['ACT-RAJ-201'],
      },
    ],
  },
  'PRJ-KG-03': {
    'SCHED-KG-BL1': [
      {
        id: 'WP-KG-TOP-01',
        projectId: 'PRJ-KG-03',
        scheduleId: 'SCHED-KG-BL1',
        contractorId: 'CTR-KG-01',
        name: 'Offshore Platform Deck Loadout & Marine Tow Package',
        code: 'WP-3.1-EQUIP-01',
        description: 'Offshore platform deck module loadout at Kakinada Yard and marine barge transportation',
        discipline: 'STATIC_ROTATING_EQUIPMENT',
        stageId: 'STG-KG-1',
        wbsId: 'WBS-3.1.1',
        status: 'COMPLETED',
        plannedStart: '2026-07-10',
        plannedFinish: '2026-08-15',
        activityIds: ['ACT-KG-101'],
      },
      {
        id: 'WP-KG-PIP-01',
        projectId: 'PRJ-KG-03',
        scheduleId: 'SCHED-KG-BL1',
        contractorId: 'CTR-KG-02',
        name: 'Gas Riser Spool Topside Tie-In Welding Package',
        code: 'WP-3.3-PIP-01',
        description: 'High-pressure subsea gas riser spool tie-in welding on offshore platform topside',
        discipline: 'PIPING',
        stageId: 'STG-KG-3',
        wbsId: 'WBS-3.3.1',
        status: 'IN_PROGRESS',
        plannedStart: '2026-09-01',
        plannedFinish: '2026-09-15',
        activityIds: ['ACT-KG-201'],
      },
    ],
    'SCHED-KG-BL2': [
      {
        id: 'WP-KG-BL2-PIP-01',
        projectId: 'PRJ-KG-03',
        scheduleId: 'SCHED-KG-BL2',
        contractorId: 'CTR-KG-02',
        name: 'Gas Riser Spool Revised Tie-In Package',
        code: 'WP-BL2-3.3-PIP',
        description: 'Revised schedule for high-pressure gas riser spools',
        discipline: 'PIPING',
        stageId: 'STG-KG-3',
        wbsId: 'WBS-3.3.1',
        status: 'IN_PROGRESS',
        plannedStart: '2026-09-01',
        plannedFinish: '2026-09-20',
        activityIds: ['ACT-KG-201'],
      },
    ],
  },
};

export function getMockContractors(projectId?: string): Contractor[] {
  const pId = projectId || getActiveProjectId();
  return MOCK_CONTRACTORS[pId] || [];
}

export function getMockWorkPackages(projectId?: string, scheduleId?: string): WorkPackage[] {
  const pId = projectId || getActiveProjectId();
  const sId = scheduleId || getActiveScheduleVersionId();
  const projWps = MOCK_WORK_PACKAGES[pId] || {};
  return projWps[sId] || Object.values(projWps)[0] || [];
}

export function getMockContractorProgress(projectId?: string, scheduleId?: string): ContractorProgressSummary[] {
  const pId = projectId || getActiveProjectId();
  const sId = scheduleId || getActiveScheduleVersionId();
  const contractors = getMockContractors(pId);
  const workPackages = getMockWorkPackages(pId, sId);
  const activities = getActivitiesForCurrentSchedule(sId, pId);
  return calculateAllContractorsProgress(contractors, workPackages, activities);
}

export function getMockWorkPackageProgress(projectId?: string, scheduleId?: string, wpId?: string): WorkPackageProgressSummary[] {
  const pId = projectId || getActiveProjectId();
  const sId = scheduleId || getActiveScheduleVersionId();
  const contractors = getMockContractors(pId);
  const workPackages = getMockWorkPackages(pId, sId);
  const activities = getActivitiesForCurrentSchedule(sId, pId);

  const contractorMap = new Map(contractors.map((c) => [c.id, c.name]));
  const results = workPackages.map((wp) => calculateWorkPackageProgress(wp, activities, contractorMap.get(wp.contractorId)));

  if (wpId) {
    return results.filter((r) => r.work_package_id === wpId);
  }
  return results;
}

// ─── V7 Quality Gates / ITP / Hold Points Foundation ──────────────────────────

export const MOCK_QUALITY_GATES: QualityGate[] = [
  // ── Project 1: PRJ-ASSAM-01 (SCHED-ASSAM-V2) ──────────────────────────────
  // 1. Completed Hold Point: ACT-ASSAM-401 (SCADA RTU & Solar Power Skid Installation)
  {
    id: 'QG-ASSAM-401-1',
    projectId: 'PRJ-ASSAM-01',
    scheduleId: 'SCHED-ASSAM-V2',
    stageId: 'STG-ASSAM-5',
    wbsId: 'WBS-1.5.1',
    activityId: 'ACT-ASSAM-401',
    gateType: 'HOLD_POINT',
    name: 'Pre-Energization Solar Skid & RTU QA/QC Sign-off',
    description: 'Factory acceptance test & field loop check sign-off prior to 24V DC bus energization.',
    status: 'COMPLETED',
    required: true,
    sequence: 1,
    ownerRole: 'SUPERVISOR',
    dueDate: '2026-09-08',
    completedAt: '2026-09-08T15:00:00.000Z',
    completedBy: 'usr-supervisor-01',
    evidenceRequired: true,
    evidenceIds: ['doc-assam-401-fat'],
  },

  // 2. Pending Required Hold Point: ACT-ASSAM-201 (Hydrotesting & Nitrogen Purging)
  {
    id: 'QG-ASSAM-201-1',
    projectId: 'PRJ-ASSAM-01',
    scheduleId: 'SCHED-ASSAM-V2',
    stageId: 'STG-ASSAM-2',
    wbsId: 'WBS-1.2.1',
    activityId: 'ACT-ASSAM-201',
    gateType: 'HOLD_POINT',
    name: 'Hydrostatic Pressure Test 24-Hour Hold Sign-off',
    description: 'Mandatory pressure chart verification and joint leak inspection at 1.5x design pressure.',
    status: 'PENDING',
    required: true,
    sequence: 1,
    ownerRole: 'SUPERVISOR',
    dueDate: '2026-09-22',
    completedAt: null,
    completedBy: null,
    evidenceRequired: true,
    evidenceIds: [],
  },

  // 3. Partially Completed Multiple Gates: ACT-ASSAM-101 (Sector Valve Station 4 Civil Foundations)
  {
    id: 'QG-ASSAM-101-1',
    projectId: 'PRJ-ASSAM-01',
    scheduleId: 'SCHED-ASSAM-V2',
    stageId: 'STG-ASSAM-1',
    wbsId: 'WBS-1.1.1',
    activityId: 'ACT-ASSAM-101',
    gateType: 'WITNESS_POINT',
    name: 'Reinforcement Rebar & Formwork Pre-Pour Inspection',
    description: 'Clear cover, bar spacing, and embedment bolt alignment verification.',
    status: 'COMPLETED',
    required: false,
    sequence: 1,
    ownerRole: 'QA_QC_INSPECTOR',
    dueDate: '2026-08-18',
    completedAt: '2026-08-18T10:30:00.000Z',
    completedBy: 'usr-supervisor-01',
    evidenceRequired: false,
  },
  {
    id: 'QG-ASSAM-101-2',
    projectId: 'PRJ-ASSAM-01',
    scheduleId: 'SCHED-ASSAM-V2',
    stageId: 'STG-ASSAM-1',
    wbsId: 'WBS-1.1.1',
    activityId: 'ACT-ASSAM-101',
    gateType: 'ITP_CHECK',
    name: '28-Day Concrete Cube Compressive Strength Test (M30 Grade)',
    description: 'Certified laboratory crush test results for foundation batch casting.',
    status: 'READY',
    required: true,
    sequence: 2,
    ownerRole: 'SUPERVISOR',
    dueDate: '2026-09-15',
    completedAt: null,
    completedBy: null,
    evidenceRequired: true,
  },
  {
    id: 'QG-ASSAM-101-3',
    projectId: 'PRJ-ASSAM-01',
    scheduleId: 'SCHED-ASSAM-V2',
    stageId: 'STG-ASSAM-1',
    wbsId: 'WBS-1.1.1',
    activityId: 'ACT-ASSAM-101',
    gateType: 'REVIEW_POINT',
    name: 'Backfilling & Compaction Field Density Test Review',
    description: 'Proctor density test report review for trench backfill layers.',
    status: 'PENDING',
    required: false,
    sequence: 3,
    ownerRole: 'SUPERVISOR',
    dueDate: '2026-09-20',
    completedAt: null,
    completedBy: null,
    evidenceRequired: false,
  },

  // ── Project 2: PRJ-RAJ-02 (SCHED-RAJ-V1) ──────────────────────────────────
  // 4. Blocked Gate: ACT-RAJ-101 (Switchyard Substation Boundary Wall & Grading)
  {
    id: 'QG-RAJ-101-1',
    projectId: 'PRJ-RAJ-02',
    scheduleId: 'SCHED-RAJ-V1',
    stageId: 'STG-RAJ-1',
    wbsId: 'WBS-2.1.1',
    activityId: 'ACT-RAJ-101',
    gateType: 'HOLD_POINT',
    name: 'Environmental Clearance & Desert Soil Contamination Hold',
    description: 'Soil stabilization and hazardous dust containment clearance certificate from State Pollution Board.',
    status: 'BLOCKED',
    required: true,
    sequence: 1,
    ownerRole: 'SUPERVISOR',
    dueDate: '2026-08-30',
    completedAt: null,
    completedBy: null,
    evidenceRequired: true,
  },
  // Completed Hold Point: ACT-RAJ-301
  {
    id: 'QG-RAJ-301-1',
    projectId: 'PRJ-RAJ-02',
    scheduleId: 'SCHED-RAJ-V1',
    stageId: 'STG-RAJ-4',
    wbsId: 'WBS-2.4.1',
    activityId: 'ACT-RAJ-301',
    gateType: 'HOLD_POINT',
    name: 'High Voltage Insulation & Flashover Test Sign-off',
    description: 'Pre-commissioning 33kV switchgear insulation resistance and bus dielectric tests.',
    status: 'COMPLETED',
    required: true,
    sequence: 1,
    ownerRole: 'SUPERVISOR',
    dueDate: '2026-09-04',
    completedAt: '2026-09-04T16:00:00.000Z',
    completedBy: 'usr-supervisor-02',
    evidenceRequired: true,
  },

  // ── Project 3: PRJ-KG-03 (SCHED-KG-BL1) ───────────────────────────────────
  // Completed Hold Point: ACT-KG-101 (Offshore Platform Deck Module Loadout)
  {
    id: 'QG-KG-101-1',
    projectId: 'PRJ-KG-03',
    scheduleId: 'SCHED-KG-BL1',
    stageId: 'STG-KG-1',
    wbsId: 'WBS-3.1.1',
    activityId: 'ACT-KG-101',
    gateType: 'HOLD_POINT',
    name: 'Marine Warranty Surveyor Loadout & Sea-Fastening Approval',
    description: 'MWS third-party certification of barge stability and weld sea-fastenings.',
    status: 'COMPLETED',
    required: true,
    sequence: 1,
    ownerRole: 'SUPERVISOR',
    dueDate: '2026-08-14',
    completedAt: '2026-08-14T11:00:00.000Z',
    completedBy: 'usr-supervisor-03',
    evidenceRequired: true,
  },
  // Pending Required Hold Point: ACT-KG-201 (Gas Riser Spool Topside Tie-In Welding)
  {
    id: 'QG-KG-201-1',
    projectId: 'PRJ-KG-03',
    scheduleId: 'SCHED-KG-BL1',
    stageId: 'STG-KG-3',
    wbsId: 'WBS-3.3.1',
    activityId: 'ACT-KG-201',
    gateType: 'HOLD_POINT',
    name: '100% Radiographic Testing (RT) & PAUT Weld Quality Hold',
    description: 'NDT Level III inspector sign-off on duplex stainless steel spool girth welds.',
    status: 'PENDING',
    required: true,
    sequence: 1,
    ownerRole: 'SUPERVISOR',
    dueDate: '2026-09-18',
    completedAt: null,
    completedBy: null,
    evidenceRequired: true,
  },
];

// In-memory dynamic state store for Quality Gates
export const MOCK_QUALITY_GATES_STATE = new Map<string, QualityGate>(
  MOCK_QUALITY_GATES.map((g) => [g.id, { ...g }])
);

export function getMockQualityGates(
  projectId?: string,
  scheduleId?: string,
  activityId?: string
): QualityGate[] {
  const pId = projectId || getActiveProjectId();
  const sId = scheduleId || getActiveScheduleVersionId();

  let gates = Array.from(MOCK_QUALITY_GATES_STATE.values()).filter(
    (g) => g.projectId === pId && g.scheduleId === sId
  );

  if (activityId) {
    gates = gates.filter((g) => g.activityId === activityId);
  }

  return gates.sort((a, b) => a.sequence - b.sequence);
}

export function completeMockQualityGate(
  gateId: string,
  completedBy = 'usr-supervisor-01',
  evidenceId?: string
): QualityGate {
  const gate = MOCK_QUALITY_GATES_STATE.get(gateId);
  if (!gate) {
    throw new Error(`Quality Gate '${gateId}' not found.`);
  }

  const updated: QualityGate = {
    ...gate,
    status: 'COMPLETED',
    completedAt: new Date().toISOString(),
    completedBy,
    evidenceIds: evidenceId ? [...(gate.evidenceIds || []), evidenceId] : gate.evidenceIds,
  };

  MOCK_QUALITY_GATES_STATE.set(gateId, updated);
  return updated;
}

export function waiveMockQualityGate(
  gateId: string,
  justification: string,
  waivedBy = 'usr-supervisor-01'
): QualityGate {
  const gate = MOCK_QUALITY_GATES_STATE.get(gateId);
  if (!gate) {
    throw new Error(`Quality Gate '${gateId}' not found.`);
  }
  if (!justification || !justification.trim()) {
    throw new Error('A waiver justification is required to waive a Quality Gate.');
  }

  const updated: QualityGate = {
    ...gate,
    status: 'WAIVED',
    waiverJustification: justification.trim(),
    waivedBy,
    waivedAt: new Date().toISOString(),
  };

  MOCK_QUALITY_GATES_STATE.set(gateId, updated);
  return updated;
}

export function getActivityQualitySummary(
  activityId: string,
  projectId?: string,
  scheduleId?: string
): {
  totalGates: number;
  completedGates: number;
  pendingRequiredHoldPoint: boolean;
  blockedRequiredHoldPoint: boolean;
  hasBlockingHoldPoint: boolean;
  blockingHoldPointName?: string;
  gates: QualityGate[];
} {
  const gates = getMockQualityGates(projectId, scheduleId, activityId);
  const totalGates = gates.length;
  const completedGates = gates.filter((g) => g.status === 'COMPLETED' || g.status === 'WAIVED').length;

  const pendingHoldGate = gates.find(
    (g) => g.gateType === 'HOLD_POINT' && g.required && g.status === 'PENDING'
  );
  const blockedHoldGate = gates.find(
    (g) => g.gateType === 'HOLD_POINT' && g.required && g.status === 'BLOCKED'
  );

  const pendingRequiredHoldPoint = Boolean(pendingHoldGate);
  const blockedRequiredHoldPoint = Boolean(blockedHoldGate);
  const hasBlockingHoldPoint = pendingRequiredHoldPoint || blockedRequiredHoldPoint;
  const blockingHoldPointName = (blockedHoldGate || pendingHoldGate)?.name;

  return {
    totalGates,
    completedGates,
    pendingRequiredHoldPoint,
    blockedRequiredHoldPoint,
    hasBlockingHoldPoint,
    blockingHoldPointName,
    gates,
  };
}

export function getAllEventsForCurrentProject(): ExecutionEvent[] {
  const ds = getDatasetForCurrentProject();
  const sId = getActiveScheduleVersionId();
  const dynamicForProj = Array.from(MOCK_DYNAMIC_EVENTS.values()).filter(
    (e) => e.schedule_id === sId
  );
  return [...dynamicForProj, ...ds.events];
}

/**
 * V7 Phase 2: State-Aware Eligibility-First Matching Engine
 */
export function runEligibilityFirstMatching(
  eventOrId?: ExecutionEvent | string,
  rawTextArg?: string,
  reportedActivityIdArg?: string | null,
  disciplineArg?: Discipline | null
): { status: ClaimStatus; matches: CandidateMatch[] } {
  const event = typeof eventOrId === 'object' && eventOrId ? eventOrId : undefined;
  const eventId = typeof eventOrId === 'string' ? eventOrId : event?.event_id || 'evt-demo';
  const rawText = event?.raw_claim_text || rawTextArg || '';
  const reportedActivityId = event?.reported_activity_id || reportedActivityIdArg || null;
  const discipline = event?.discipline || disciplineArg || null;
  const pId = getActiveProjectId();
  const sId = getActiveScheduleVersionId();
  const allActs = getActivitiesForCurrentSchedule(sId);
  const text = (rawText || '').toLowerCase();

  // 1. Check if claim targets a COMPLETED activity (Completed Activity Protection)
  const completedTarget = allActs.find(
    (a) =>
      a.execution_state === 'COMPLETED' &&
      (
        (reportedActivityId && a.activity_id.toLowerCase() === reportedActivityId.toLowerCase()) ||
        (a.asset_tag && text.includes(a.asset_tag.toLowerCase())) ||
        text.includes(a.activity_id.toLowerCase()) ||
        (a.activity_name && text.includes(a.activity_name.toLowerCase().slice(0, 15)))
      )
  );

  if (completedTarget) {
    const protectedMatch: CandidateMatch = {
      candidate_id: `cand-comp-${completedTarget.activity_id}`,
      event_id: eventId,
      schedule_id: sId,
      project_id: pId,
      activity_id: completedTarget.activity_id,
      stage_id: completedTarget.stage_id || undefined,
      stage_name: completedTarget.stage_name || 'Stage 5 — Terminal Stations & Commissioning',
      wbs_code: completedTarget.wbs_code || 'WBS-1.5.1',
      rank_order: 1,
      match_tier: 'COMPLETED_PROTECTED',
      composite_confidence: 0.98,
      semantic_score: 0.96,
      fuzzy_score: 0.98,
      location_score: 1.0,
      discipline_score: 1.0,
      supporting_signals: `${completedTarget.activity_id} — ${completedTarget.activity_name} (${completedTarget.discipline}) in ${completedTarget.stage_name || 'Stage 5 — Terminal Stations & Commissioning'} (${completedTarget.stage_id || 'STG-ASSAM-5'}). Completed on ${completedTarget.actual_finish || '2026-09-08'}.`,
      disqualifying_signals: 'Activity is in COMPLETED state. Excluded from normal matching.',
      execution_state: completedTarget.execution_state || 'COMPLETED',
      eligibility_explanation: `Activity ${completedTarget.activity_id} (${completedTarget.activity_name}) in ${completedTarget.stage_name || 'Stage 5 — Terminal Stations & Commissioning'} (${completedTarget.stage_id || 'STG-ASSAM-5'}) is already COMPLETED (100% finished on ${completedTarget.actual_finish || '2026-09-08'}). Actual finish is protected and cannot be modified by ordinary claims. Submit a Reopen Request for Supervisor approval.`,
      is_eligible: false,
      is_completed_protected: true,
    };

    if (event) {
      event.matched_activity_id = completedTarget.activity_id;
      event.reported_activity_id = completedTarget.activity_id;
      event.discipline = completedTarget.discipline;
      event.location = completedTarget.location;
      event.asset_tag = completedTarget.asset_tag;
      event.is_completed_activity_target = true;
      event.status = 'REVIEW_REQUIRED';
      event.field_provenance = {
        activity_id: { field_name: 'activity_id', source: 'AI_EXTRACTED', source_detail: completedTarget.activity_id },
        discipline: { field_name: 'discipline', source: 'SCHEDULE_AUTO_FILLED', source_detail: completedTarget.discipline },
        location: { field_name: 'location', source: 'SCHEDULE_AUTO_FILLED', source_detail: completedTarget.location || 'Site' },
        asset_tag: { field_name: 'asset_tag', source: 'SCHEDULE_AUTO_FILLED', source_detail: completedTarget.asset_tag || 'Master' },
      };
      MOCK_DYNAMIC_EVENTS.set(eventId, event);
    }

    return { status: 'REVIEW_REQUIRED', matches: [protectedMatch] };
  }

  // 2. Check if claim targets an activity in a COMPLETED stage (Completed Stage Protection)
  const completedStageTarget = allActs.find(
    (a) =>
      a.is_stage_completed &&
      (
        (reportedActivityId && a.activity_id.toLowerCase() === reportedActivityId.toLowerCase()) ||
        (a.asset_tag && text.includes(a.asset_tag.toLowerCase())) ||
        text.includes(a.activity_id.toLowerCase())
      )
  );

  if (completedStageTarget) {
    const stageMatch: CandidateMatch = {
      candidate_id: `cand-stg-${completedStageTarget.activity_id}`,
      event_id: eventId,
      schedule_id: sId,
      activity_id: completedStageTarget.activity_id,
      rank_order: 1,
      match_tier: 'STAGE_COMPLETED',
      composite_confidence: 0.90,
      semantic_score: 0.88,
      fuzzy_score: 0.92,
      location_score: 1.0,
      discipline_score: 1.0,
      supporting_signals: `Activity belongs to completed stage: ${completedStageTarget.stage_name}.`,
      disqualifying_signals: 'Stage is COMPLETED and archived. Excluded from normal matching.',
      execution_state: completedStageTarget.execution_state || 'COMPLETED',
      eligibility_explanation: `Stage "${completedStageTarget.stage_name}" is already COMPLETED and archived. Excluded from normal matching. Route to Supervisor review.`,
      stage_name: completedStageTarget.stage_name || 'Stage 1',
      wbs_code: completedStageTarget.wbs_code || 'WBS',
      is_eligible: false,
      is_stage_completed: true,
    };
    return { status: 'REVIEW_REQUIRED', matches: [stageMatch] };
  }

  // 3. Filter eligible activities (NOT COMPLETED, and NOT in completed stage)
  const eligibleActs = allActs.filter(
    (a) => a.execution_state !== 'COMPLETED' && !a.is_stage_completed
  );

  if (eligibleActs.length === 0) {
    return { status: 'UNMATCHED', matches: [] };
  }

  // Rank eligible activities based on reported activity ID, discipline, keywords, location
  const scored = eligibleActs.map((act) => {
    let score = 0;
    let matchTier = 'SEMANTIC';

    if (reportedActivityId && act.activity_id.toLowerCase() === reportedActivityId.toLowerCase()) {
      score += 0.6;
      matchTier = 'EXACT';
    }
    if (discipline && act.discipline === discipline) {
      score += 0.25;
    }
    if (act.asset_tag && text.includes(act.asset_tag.toLowerCase())) {
      score += 0.3;
      matchTier = 'EXACT';
    }
    if (act.location && text.includes(act.location.toLowerCase())) {
      score += 0.15;
    }
    if (act.activity_name && text.split(' ').some((w) => w.length > 3 && act.activity_name.toLowerCase().includes(w))) {
      score += 0.2;
    }
    if (act.execution_state === 'IN_PROGRESS' || act.execution_state === 'REOPENED') {
      score += 0.1;
    }

    const composite = Math.min(0.98, Math.max(0.45, score > 0 ? 0.65 + score * 0.3 : 0.45));
    return {
      act,
      matchTier,
      composite: Math.round(composite * 100) / 100,
      semScore: Math.round((composite - 0.04) * 100) / 100,
      fuzScore: Math.round((composite + 0.02) * 100) / 100,
      locScore: act.location && text.includes(act.location.toLowerCase()) ? 0.95 : 0.85,
      disScore: discipline && act.discipline === discipline ? 1.0 : 0.75,
    };
  });

  scored.sort((a, b) => b.composite - a.composite);
  const topMatches = scored.slice(0, 3).filter((s) => s.composite >= 0.5);

  if (topMatches.length === 0) {
    return { status: 'UNMATCHED', matches: [] };
  }

  const matches: CandidateMatch[] = topMatches.map((item, idx) => ({
    candidate_id: `cand-${pId}-${item.act.activity_id}-${idx + 1}`,
    event_id: eventId,
    schedule_id: sId,
    activity_id: item.act.activity_id,
    rank_order: (idx + 1) as 1 | 2 | 3,
    match_tier: item.matchTier,
    composite_confidence: item.composite,
    semantic_score: item.semScore,
    fuzzy_score: item.fuzScore,
    location_score: item.locScore,
    discipline_score: item.disScore,
    supporting_signals: `Passed eligibility check: Active project ${pId}, Schedule ${sId}, ${item.act.stage_name || 'Active Stage'}, State: ${item.act.execution_state}.`,
    disqualifying_signals: null,
    execution_state: item.act.execution_state,
    eligibility_explanation: `Eligible candidate in ${pId} · ${sId} · ${item.act.stage_name || 'Active Stage'} · ${item.act.wbs_code || ''} · Execution State: ${item.act.execution_state}. Unfinished & active for field reporting.`,
    stage_name: item.act.stage_name || 'Stage 3',
    wbs_code: item.act.wbs_code || 'WBS-1.3',
    is_eligible: true,
  }));

  return { status: 'MATCHED', matches };
}

export function extractMockClaimFields(text: string): {
  discipline: Discipline | null;
  event_type: EventType;
  claimed_pct: number | null;
  claimed_quantity: number | null;
  claimed_uom: string | null;
  clarification_status: ClarificationStatus;
  clarification_question: string | null;
  activity_id?: string | null;
  location?: string | null;
  asset_tag?: string | null;
} {
  const lower = text.toLowerCase();
  const sId = getActiveScheduleVersionId();
  const allActs = getActivitiesForCurrentSchedule(sId);
  const foundAct = allActs.find(
    (a) =>
      lower.includes(a.activity_id.toLowerCase()) ||
      (a.asset_tag && lower.includes(a.asset_tag.toLowerCase())) ||
      lower.includes(a.activity_name.toLowerCase().slice(0, 15))
  );

  let discipline: Discipline | null = foundAct ? foundAct.discipline : null;
  if (!discipline) {
    if (lower.includes('civil') || lower.includes('foundation') || lower.includes('rebar') || lower.includes('concrete') || lower.includes('column') || lower.includes('pour') || lower.includes('piling')) {
      discipline = 'CIVIL';
    } else if (lower.includes('piping') || lower.includes('pipe') || lower.includes('weld') || lower.includes('valve') || lower.includes('hydrotest') || lower.includes('riser') || lower.includes('spool')) {
      discipline = 'PIPING';
    } else if (lower.includes('electrical') || lower.includes('cable') || lower.includes('transformer') || lower.includes('panel') || lower.includes('switchgear') || lower.includes('solar')) {
      discipline = 'ELECTRICAL';
    } else if (lower.includes('instrument') || lower.includes('scada') || lower.includes('plc') || lower.includes('transmitter') || lower.includes('sensor') || lower.includes('calibration') || lower.includes('umbilical')) {
      discipline = 'INSTRUMENTATION';
    } else if (lower.includes('equipment') || lower.includes('pump') || lower.includes('compressor') || lower.includes('turbine') || lower.includes('skid') || lower.includes('teg')) {
      discipline = 'STATIC_ROTATING_EQUIPMENT';
    } else if (lower.includes('hse') || lower.includes('safety') || lower.includes('audit') || lower.includes('permit') || lower.includes('incident') || lower.includes('crz')) {
      discipline = 'HSE';
    }
  }

  let claimed_pct: number | null = null;
  let claimed_quantity: number | null = null;
  let claimed_uom: string | null = null;

  const pctMatch = text.match(/(\d+(?:\.\d+)?)\s*%/);
  if (pctMatch) {
    claimed_pct = Math.min(100, Math.max(0, parseFloat(pctMatch[1])));
  }

  const qtyMatch = text.match(/(\d+(?:\.\d+)?)\s*(cu\.m|m3|m|meters|joints|nos|tons|kg|units|piles|km|spools|loops)/i);
  if (qtyMatch) {
    claimed_quantity = parseFloat(qtyMatch[1]);
    claimed_uom = qtyMatch[2];
  }

  const isExplicitFinish =
    /\b(finish|finished|finalized|ended|energiz|handed over|commissioned)\b/i.test(lower) ||
    (/\b(completed|complete|done)\b/i.test(lower) && (claimed_pct === null || claimed_pct === 100) && claimed_quantity === null);
  const isStart = /\b(start|started|starting|commenced|commence|began|begin)\b/i.test(lower);
  const isDelay = /\b(delay|delayed|behind|waiting|stuck)\b/i.test(lower);
  const isBlocker = /\b(block|blocked|stopped|hold|stoppage|halted)\b/i.test(lower);

  let event_type: EventType = 'PROGRESS_UPDATE';
  if (claimed_pct !== null && claimed_pct < 100) {
    if (isDelay && !isStart && claimed_pct === 0) {
      event_type = 'DELAY';
    } else if (isBlocker && claimed_pct === 0) {
      event_type = 'BLOCKER';
    } else {
      event_type = 'PROGRESS_UPDATE';
    }
  } else if (isExplicitFinish || claimed_pct === 100) {
    event_type = 'ACTUAL_FINISH';
    if (claimed_pct === null) claimed_pct = 100;
  } else if (isStart) {
    event_type = 'ACTUAL_START';
  } else if (isDelay) {
    event_type = 'DELAY';
  } else if (isBlocker) {
    event_type = 'BLOCKER';
  }

  const hasDiscipline = discipline !== null;
  const hasProgress = claimed_pct !== null || claimed_quantity !== null;

  let clarification_status: ClarificationStatus = 'NONE';
  let clarification_question: string | null = null;

  if (!hasDiscipline && !hasProgress) {
    clarification_status = 'PENDING';
    clarification_question = 'Please clarify the engineering discipline and progress percentage or quantity for this activity.';
  } else if (!hasDiscipline) {
    clarification_status = 'PENDING';
    clarification_question = 'Please specify the engineering discipline (e.g. Civil, Piping, Electrical, Instrumentation, HSE).';
  } else if (!hasProgress) {
    clarification_status = 'PENDING';
    clarification_question = 'Please provide the claimed progress percentage (0-100%) or installed quantity.';
  }

  return {
    discipline,
    event_type,
    claimed_pct,
    claimed_quantity,
    claimed_uom,
    clarification_status,
    clarification_question,
  };
}

// ─── Schedule Dependencies & Compound Impact Data Store ──────────────────────

export interface ScheduleDependency {
  dependency_id: string;
  schedule_id: string;
  predecessor_activity_id: string;
  successor_activity_id: string;
  relationship_type: 'FS' | 'SS' | 'FF' | 'SF';
}

export const MOCK_SCHEDULE_DEPENDENCIES: Record<string, ScheduleDependency[]> = {
  'SCHED-ASSAM-V2': [
    {
      dependency_id: 'DEP-ASSAM-01',
      schedule_id: 'SCHED-ASSAM-V2',
      predecessor_activity_id: 'ACT-ASSAM-101',
      successor_activity_id: 'ACT-ASSAM-201',
      relationship_type: 'FS',
    },
    {
      dependency_id: 'DEP-ASSAM-02',
      schedule_id: 'SCHED-ASSAM-V2',
      predecessor_activity_id: 'ACT-ASSAM-201',
      successor_activity_id: 'ACT-ASSAM-202',
      relationship_type: 'FS',
    },
    {
      dependency_id: 'DEP-ASSAM-03',
      schedule_id: 'SCHED-ASSAM-V2',
      predecessor_activity_id: 'ACT-ASSAM-201',
      successor_activity_id: 'ACT-ASSAM-301',
      relationship_type: 'FS',
    },
    {
      dependency_id: 'DEP-ASSAM-04',
      schedule_id: 'SCHED-ASSAM-V2',
      predecessor_activity_id: 'ACT-ASSAM-201',
      successor_activity_id: 'ACT-ASSAM-501',
      relationship_type: 'FS',
    },
    {
      dependency_id: 'DEP-ASSAM-05',
      schedule_id: 'SCHED-ASSAM-V2',
      predecessor_activity_id: 'ACT-ASSAM-301',
      successor_activity_id: 'ACT-ASSAM-302',
      relationship_type: 'FS',
    },
    {
      dependency_id: 'DEP-ASSAM-06',
      schedule_id: 'SCHED-ASSAM-V2',
      predecessor_activity_id: 'ACT-ASSAM-302',
      successor_activity_id: 'ACT-ASSAM-401',
      relationship_type: 'FS',
    },
  ],
  'SCHED-ASSAM-V1': [
    {
      dependency_id: 'DEP-ASSAM-V1-01',
      schedule_id: 'SCHED-ASSAM-V1',
      predecessor_activity_id: 'ACT-ASSAM-201',
      successor_activity_id: 'ACT-ASSAM-301',
      relationship_type: 'FS',
    },
  ],
  'SCHED-ASSAM-REC': [
    {
      dependency_id: 'DEP-ASSAM-REC-01',
      schedule_id: 'SCHED-ASSAM-REC',
      predecessor_activity_id: 'ACT-ASSAM-201',
      successor_activity_id: 'ACT-ASSAM-301',
      relationship_type: 'FS',
    },
  ],
  'SCHED-RAJ-V1': [
    {
      dependency_id: 'DEP-RAJ-01',
      schedule_id: 'SCHED-RAJ-V1',
      predecessor_activity_id: 'ACT-RAJ-101',
      successor_activity_id: 'ACT-RAJ-102',
      relationship_type: 'FS',
    },
    {
      dependency_id: 'DEP-RAJ-02',
      schedule_id: 'SCHED-RAJ-V1',
      predecessor_activity_id: 'ACT-RAJ-101',
      successor_activity_id: 'ACT-RAJ-201',
      relationship_type: 'FS',
    },
  ],
  'SCHED-RAJ-BL': [
    {
      dependency_id: 'DEP-RAJ-BL-01',
      schedule_id: 'SCHED-RAJ-BL',
      predecessor_activity_id: 'ACT-RAJ-101',
      successor_activity_id: 'ACT-RAJ-102',
      relationship_type: 'FS',
    },
  ],
  'SCHED-KG-BL1': [
    {
      dependency_id: 'DEP-KG-01',
      schedule_id: 'SCHED-KG-BL1',
      predecessor_activity_id: 'ACT-KG-101',
      successor_activity_id: 'ACT-KG-102',
      relationship_type: 'FS',
    },
  ],
  'SCHED-KG-REV2': [
    {
      dependency_id: 'DEP-KG-02',
      schedule_id: 'SCHED-KG-REV2',
      predecessor_activity_id: 'ACT-KG-101',
      successor_activity_id: 'ACT-KG-102',
      relationship_type: 'FS',
    },
  ],
};

export function getMockScheduleDependencies(scheduleId?: string): ScheduleDependency[] {
  const sId = scheduleId || getActiveScheduleVersionId();
  return MOCK_SCHEDULE_DEPENDENCIES[sId] || [];
}

import {
  calculateActivityImpact,
  calculateScheduleImpacts,
  type CompoundImpact,
} from './lib/impactEngine';

export function getMockCompoundImpacts(projectId?: string, scheduleId?: string): CompoundImpact[] {
  const pId = projectId || getActiveProjectId();
  const sId = scheduleId || getActiveScheduleVersionId();
  const activities = getActivitiesForCurrentSchedule(sId);
  const dependencies = getMockScheduleDependencies(sId);
  const qualityGates = getMockQualityGates(pId, sId);

  return calculateScheduleImpacts(activities, dependencies, qualityGates, pId, sId);
}

export function getMockActivityImpact(
  activityId: string,
  projectId?: string,
  scheduleId?: string
): CompoundImpact | null {
  const pId = projectId || getActiveProjectId();
  const sId = scheduleId || getActiveScheduleVersionId();
  const activities = getActivitiesForCurrentSchedule(sId);
  const dependencies = getMockScheduleDependencies(sId);
  const qualityGates = getMockQualityGates(pId, sId);

  return calculateActivityImpact(activityId, activities, dependencies, qualityGates, pId, sId);
}

