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
} from './api';

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
        raw_claim_text: 'Rebar placement for column C4, level B2 finished. 75% total progress claimed.',
        input_channel: 'VOICE',
        language_detected: 'en',
        reported_activity_id: 'ACT-ASSAM-202',
        matched_activity_id: 'ACT-ASSAM-202',
        discipline: 'CIVIL',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: 'COL-C4',
        location: 'Block-2 North',
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 75,
        delay_reason: null,
        supervisor_id: null,
        photo_path: null,
        status: 'REVIEW_REQUIRED',
        created_at: new Date().toISOString(),
        priority_score: 0.88,
        priority_reasons: ['Critical Path activity', 'High variance risk with previous shift log'],
        is_escalated: true,
        priority_rank: 1,
        field_provenance: {
          discipline: { field_name: 'discipline', source: 'AI_EXTRACTED', source_detail: 'Extracted from voice keyword "Rebar placement"', confidence: 0.96 },
          location: { field_name: 'location', source: 'ENGINEER_ENTERED', source_detail: 'Explicit site engineer voice report: "Block-2 North"' },
          asset_tag: { field_name: 'asset_tag', source: 'SCHEDULE_AUTO_FILLED', source_detail: 'Resolved from activity master ACT-ASSAM-202 tag COL-C4' },
          claimed_pct: { field_name: 'claimed_pct', source: 'ENGINEER_ENTERED', source_detail: 'Stated directly in voice log (75%)' },
          matched_activity_id: { field_name: 'matched_activity_id', source: 'AI_EXTRACTED', source_detail: 'FAISS match on Column C4 with 88% confidence', confidence: 0.88 },
        },
      },
      {
        event_id: 'evt-assam-103',
        document_id: null,
        schedule_id: 'SCHED-ASSAM-V2',
        event_date: TODAY,
        raw_claim_text: 'Completed 8 weld joints on 14" Crude Trunkline near PS-3 header.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-ASSAM-301',
        matched_activity_id: 'ACT-ASSAM-301',
        discipline: 'PIPING',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'INCREMENTAL_QUANTITY',
        asset_tag: 'PIPE-14-L1',
        location: 'Sector Duliajan-Km12',
        claimed_quantity: 8,
        claimed_uom: 'joints',
        claimed_pct: 40,
        delay_reason: null,
        supervisor_id: null,
        photo_path: null,
        status: 'VALIDATED',
        created_at: new Date().toISOString(),
      },
      {
        event_id: 'evt-assam-104',
        document_id: 'doc-ass-004',
        schedule_id: 'SCHED-ASSAM-V2',
        event_date: TODAY,
        raw_claim_text: 'SCADA RTU & Solar Skid installation complete and powered up.',
        input_channel: 'FILE_UPLOAD',
        language_detected: 'en',
        reported_activity_id: 'ACT-ASSAM-401',
        matched_activity_id: 'ACT-ASSAM-401',
        discipline: 'ELECTRICAL',
        action: 'ACTUAL_FINISH',
        event_type: 'ACTUAL_FINISH',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: 'RTU-VS04',
        location: 'Sector Valve Station 4',
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 100,
        delay_reason: null,
        supervisor_id: null,
        photo_path: '/uploads/rtu_vs04.jpg',
        status: 'APPROVED',
        created_at: new Date(Date.now() - 7200000).toISOString(),
      },
      {
        event_id: 'evt-assam-105',
        document_id: null,
        schedule_id: 'SCHED-ASSAM-V2',
        event_date: TODAY,
        raw_claim_text: 'Loop testing for flow transmitter MIT-101 delayed due to missing calibration cert.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-ASSAM-501',
        matched_activity_id: 'ACT-ASSAM-501',
        discipline: 'INSTRUMENTATION',
        action: 'DELAY',
        event_type: 'DELAY',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: 'MIT-101',
        location: 'Numaligarh Terminal',
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 10,
        delay_reason: 'Vendor missing calibration certificates for test bench',
        supervisor_id: null,
        photo_path: null,
        status: 'HOLD',
        created_at: new Date(Date.now() - 3600000).toISOString(),
      },
      {
        event_id: 'evt-assam-106',
        document_id: null,
        schedule_id: 'SCHED-ASSAM-V2',
        event_date: TODAY,
        raw_claim_text: 'Monsoon bund inspection along pipeline ROW corridor completed.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-ASSAM-601',
        matched_activity_id: 'ACT-ASSAM-601',
        discipline: 'HSE',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: null,
        location: 'Pipeline ROW Corridor',
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 100,
        delay_reason: null,
        supervisor_id: null,
        photo_path: null,
        status: 'APPROVED',
        created_at: new Date(Date.now() - 5400000).toISOString(),
      },
    ],
    candidates: [
      {
        candidate_id: 'cand-ass-101',
        event_id: 'evt-assam-102',
        schedule_id: 'SCHED-ASSAM-V2',
        activity_id: 'ACT-ASSAM-202',
        rank_order: 1,
        match_tier: 'EXACT',
        composite_confidence: 0.88,
        semantic_score: 0.92,
        fuzzy_score: 0.84,
        location_score: 0.95,
        discipline_score: 1.0,
        supporting_signals: 'Exact match on Column C4; matched discipline CIVIL; matching location Block-2',
        disqualifying_signals: null,
      },
    ],
    validationIssues: [
      {
        issue_id: 'flag-ass-101',
        event_id: 'evt-assam-102',
        rule_code: 'CONFIDENCE_THRESHOLD_UNMET',
        severity: 'WARNING',
        description: 'Top candidate confidence (0.88) is below automated approval threshold (0.90). Supervisor review required.',
      },
      {
        issue_id: 'flag-ass-102',
        event_id: 'evt-assam-102',
        rule_code: 'QUANTITY_VARIANCE',
        severity: 'WARNING',
        description: 'Claimed progress (+15%) exceeds baseline planned daily rate of 8% for Column C4.',
      },
    ],
    decisions: [
      {
        decision_id: 'dec-ass-101',
        event_id: 'evt-assam-104',
        selected_activity_id: 'ACT-ASSAM-401',
        action: 'APPROVE',
        approved_pct: 100,
        approved_qty: null,
        planner_id: 'usr-supervisor-01',
        justification: 'Verified physical RTU installation and solar controller energization certificate.',
        decided_at: new Date(Date.now() - 7200000).toISOString(),
      },
    ],
    dashboard: {
      total_claims: 148,
      pending_review: 14,
      actuals: 124,
      conflicts: 2,
      discipline_breakdown: [
        { discipline: 'CIVIL', name: 'CIVIL', count: 42, value: 42 },
        { discipline: 'PIPING', name: 'PIPING', count: 38, value: 38 },
        { discipline: 'ELECTRICAL', name: 'ELECTRICAL', count: 24, value: 24 },
        { discipline: 'INSTRUMENTATION', name: 'INSTRUMENTATION', count: 18, value: 18 },
        { discipline: 'HSE', name: 'HSE', count: 12, value: 12 },
      ],
      claims_trend_pct: 12,
    },
    delayReasons: [
      { reason: 'Adverse Monsoon Rain / Waterlogging Shutdown', count: 8 },
      { reason: 'Vendor Missing Calibration Certificates', count: 5 },
      { reason: 'Pipeline ROW Land Access Clearance Delay', count: 4 },
      { reason: 'River HDD Drill Mud Circulation Loss', count: 3 },
    ],
    institutionalMemory: [
      { topic: 'Duliajan Black Cotton Soil Foundation Curing', resolution: 'Accelerate with rapid-hardening admixture when moisture exceeds 85%', count: 14 },
      { topic: 'Burhi Dihing River HDD Guidance Protocol', resolution: 'Maintain drill mud specific gravity at 1.15 to prevent bore collapse', count: 9 },
    ],
    forecast: {
      discipline: 'CIVIL',
      historical_ratio: 1.12,
      total_activities: 3,
      activities: [
        { activity_id: 'ACT-ASSAM-201 (Pump PS-3 Foundation)', discipline: 'CIVIL', planned_duration: 10, historical_ratio: 1.12, forecast_duration: 12, slippage_days: 2 },
        { activity_id: 'ACT-ASSAM-301 (14" Trunkline Weld)', discipline: 'PIPING', planned_duration: 16, historical_ratio: 1.18, forecast_duration: 19, slippage_days: 3 },
        { activity_id: 'ACT-ASSAM-302 (River HDD Boring)', discipline: 'PIPING', planned_duration: 18, historical_ratio: 1.20, forecast_duration: 22, slippage_days: 4 },
      ],
    },
    summaryReport: {
      text: 'During the current reporting cycle on the Assam Pipeline Expansion, 148 progress claims were processed across Civil, Piping, and Electrical disciplines. Foundation concrete pours and trunkline welds achieved 92.4% baseline alignment, with minor weather holds at Pump Station PS-3. Critical path progression on River HDD crossing is progressing with 85% schedule alignment.',
      highlights: [
        'Foundation Pour for Pump Station PS-3 completed 50 cu.m today.',
        'SCADA RTU & Solar Skid power up verified by Lead Electrical Engineer.',
        'Zero environmental non-conformances across Burhi Dihing corridor audit.',
      ],
    },
  },

  'PRJ-RAJ-02': {
    activities: {
      'SCHED-RAJ-V1': [
        {
          activity_id: 'ACT-RAJ-101',
          schedule_id: 'SCHED-RAJ-V1',
          activity_name: 'Gas Dehydration Column (TEG) Foundation Blinding',
          wbs_code: 'WBS-RAJ-3.1',
          discipline: 'CIVIL',
          location: 'Processing Area A',
          asset_tag: 'TEG-FND-01',
          planned_start: '2026-09-02',
          planned_finish: '2026-09-12',
          planned_quantity: 120,
          uom: 'cu.m',
          baseline_pct_complete: 40,
          total_float: 0,
          is_critical: true,
        },
        {
          activity_id: 'ACT-RAJ-102',
          schedule_id: 'SCHED-RAJ-V1',
          activity_name: 'High Pressure Gas Compressor Skid #2 Erection',
          wbs_code: 'WBS-RAJ-3.2',
          discipline: 'STATIC_ROTATING_EQUIPMENT',
          location: 'Compressor Bay 2',
          asset_tag: 'COMP-SKID-02',
          planned_start: '2026-09-06',
          planned_finish: '2026-09-20',
          planned_quantity: 2,
          uom: 'units',
          baseline_pct_complete: 25,
          total_float: 0,
          is_critical: true,
        },
        {
          activity_id: 'ACT-RAJ-201',
          schedule_id: 'SCHED-RAJ-V1',
          activity_name: 'Super Duplex Piping Spool Erection — Manifold M-1',
          wbs_code: 'WBS-RAJ-2.2',
          discipline: 'PIPING',
          location: 'Manifold Area',
          asset_tag: 'SD-PIPE-01',
          planned_start: '2026-09-04',
          planned_finish: '2026-09-16',
          planned_quantity: 45,
          uom: 'spools',
          baseline_pct_complete: 50,
          total_float: 3,
          is_critical: false,
        },
        {
          activity_id: 'ACT-RAJ-301',
          schedule_id: 'SCHED-RAJ-V1',
          activity_name: '33kV Substation Transformer Switchgear Cabling',
          wbs_code: 'WBS-RAJ-3.3',
          discipline: 'ELECTRICAL',
          location: 'Main Substation',
          asset_tag: 'SS-XFMR-01',
          planned_start: '2026-09-01',
          planned_finish: '2026-09-10',
          planned_quantity: 800,
          uom: 'meters',
          baseline_pct_complete: 80,
          total_float: 4,
          is_critical: false,
        },
        {
          activity_id: 'ACT-RAJ-401',
          schedule_id: 'SCHED-RAJ-V1',
          activity_name: 'Gas Chromatograph & ESD Safety Interlock Testing',
          wbs_code: 'WBS-RAJ-4.1',
          discipline: 'INSTRUMENTATION',
          location: 'Central Analyzer House',
          asset_tag: 'GC-ESD-01',
          planned_start: '2026-09-08',
          planned_finish: '2026-09-18',
          planned_quantity: 6,
          uom: 'loops',
          baseline_pct_complete: 15,
          total_float: 2,
          is_critical: false,
        },
        {
          activity_id: 'ACT-RAJ-501',
          schedule_id: 'SCHED-RAJ-V1',
          activity_name: 'Desert Heat Wave Safety & Hydration Protocol Audit',
          wbs_code: 'WBS-RAJ-1.2',
          discipline: 'HSE',
          location: 'Site Wide',
          asset_tag: null,
          planned_start: '2026-09-05',
          planned_finish: '2026-09-05',
          planned_quantity: 1,
          uom: 'report',
          baseline_pct_complete: 100,
          total_float: 8,
          is_critical: false,
        },
      ],
      'SCHED-RAJ-INIT': [
        {
          activity_id: 'ACT-RAJ-101',
          schedule_id: 'SCHED-RAJ-INIT',
          activity_name: 'Gas Dehydration Column (TEG) Foundation Blinding',
          wbs_code: 'WBS-RAJ-3.1',
          discipline: 'CIVIL',
          location: 'Processing Area A',
          asset_tag: 'TEG-FND-01',
          planned_start: '2025-12-01',
          planned_finish: '2025-12-20',
          planned_quantity: 100,
          uom: 'cu.m',
          baseline_pct_complete: 100,
          total_float: 0,
          is_critical: true,
        },
      ],
      'SCHED-RAJ-REV': [
        {
          activity_id: 'ACT-RAJ-102',
          schedule_id: 'SCHED-RAJ-REV',
          activity_name: 'High Pressure Gas Compressor Skid #2 Erection (Night Shift)',
          wbs_code: 'WBS-RAJ-3.2',
          discipline: 'STATIC_ROTATING_EQUIPMENT',
          location: 'Compressor Bay 2',
          asset_tag: 'COMP-SKID-02',
          planned_start: '2026-09-06',
          planned_finish: '2026-09-18',
          planned_quantity: 2,
          uom: 'units',
          baseline_pct_complete: 35,
          total_float: 0,
          is_critical: true,
        },
      ],
    },
    events: [
      {
        event_id: 'evt-raj-101',
        document_id: 'doc-raj-001',
        schedule_id: 'SCHED-RAJ-V1',
        event_date: TODAY,
        raw_claim_text: 'Erected Compressor Skid #2 on Foundation Bay 2; torque checks underway.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-RAJ-102',
        matched_activity_id: 'ACT-RAJ-102',
        discipline: 'STATIC_ROTATING_EQUIPMENT',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: 'COMP-SKID-02',
        location: 'Compressor Bay 2',
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 50,
        delay_reason: null,
        supervisor_id: null,
        photo_path: '/uploads/skid2_erect.jpg',
        status: 'REVIEW_REQUIRED',
        created_at: new Date().toISOString(),
        priority_score: 0.92,
        priority_reasons: ['Critical Path Rotating Equipment', 'Foundation torque verification pending'],
        is_escalated: true,
        priority_rank: 1,
      },
      {
        event_id: 'evt-raj-102',
        document_id: null,
        schedule_id: 'SCHED-RAJ-V1',
        event_date: TODAY,
        raw_claim_text: 'Completed installation of 12 Super Duplex spools on Inlet Gas Manifold M-1.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-RAJ-201',
        matched_activity_id: 'ACT-RAJ-201',
        discipline: 'PIPING',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'INCREMENTAL_QUANTITY',
        asset_tag: 'SD-PIPE-01',
        location: 'Manifold Area',
        claimed_quantity: 12,
        claimed_uom: 'spools',
        claimed_pct: 60,
        delay_reason: null,
        supervisor_id: null,
        photo_path: null,
        status: 'VALIDATED',
        created_at: new Date().toISOString(),
      },
      {
        event_id: 'evt-raj-103',
        document_id: 'doc-raj-003',
        schedule_id: 'SCHED-RAJ-V1',
        event_date: TODAY,
        raw_claim_text: 'TEG Column Foundation concrete pour 45 cu.m completed in Processing Area A.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-RAJ-101',
        matched_activity_id: 'ACT-RAJ-101',
        discipline: 'CIVIL',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'INCREMENTAL_QUANTITY',
        asset_tag: 'TEG-FND-01',
        location: 'Processing Area A',
        claimed_quantity: 45,
        claimed_uom: 'cu.m',
        claimed_pct: 100,
        delay_reason: null,
        supervisor_id: null,
        photo_path: '/uploads/teg_pour.jpg',
        status: 'APPROVED',
        created_at: new Date(Date.now() - 7200000).toISOString(),
      },
      {
        event_id: 'evt-raj-104',
        document_id: null,
        schedule_id: 'SCHED-RAJ-V1',
        event_date: TODAY,
        raw_claim_text: 'Gas Chromatograph calibration delayed awaiting specialty calibration gas bottles from Barmer supply hub.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-RAJ-401',
        matched_activity_id: 'ACT-RAJ-401',
        discipline: 'INSTRUMENTATION',
        action: 'DELAY',
        event_type: 'DELAY',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: 'GC-ESD-01',
        location: 'Central Analyzer House',
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 10,
        delay_reason: 'Calibration gas shipment stuck at Jaisalmer border',
        supervisor_id: null,
        photo_path: null,
        status: 'HOLD',
        created_at: new Date(Date.now() - 3600000).toISOString(),
      },
    ],
    candidates: [
      {
        candidate_id: 'cand-raj-101',
        event_id: 'evt-raj-101',
        schedule_id: 'SCHED-RAJ-V1',
        activity_id: 'ACT-RAJ-102',
        rank_order: 1,
        match_tier: 'EXACT',
        composite_confidence: 0.94,
        semantic_score: 0.96,
        fuzzy_score: 0.90,
        location_score: 0.95,
        discipline_score: 1.0,
        supporting_signals: 'Exact match on Compressor Skid #2 tag; matched discipline STATIC_ROTATING_EQUIPMENT',
        disqualifying_signals: null,
      },
    ],
    validationIssues: [
      {
        issue_id: 'flag-raj-101',
        event_id: 'evt-raj-101',
        rule_code: 'HOLD_POINT_CLEARANCE',
        severity: 'WARNING',
        description: 'Vendor technician alignment verification required before full torque approval.',
      },
    ],
    decisions: [
      {
        decision_id: 'dec-raj-101',
        event_id: 'evt-raj-103',
        selected_activity_id: 'ACT-RAJ-101',
        action: 'APPROVE',
        approved_pct: 100,
        approved_qty: 45,
        planner_id: 'usr-supervisor-01',
        justification: 'Compressor pad cube test clearance 28-day certificate accepted.',
        decided_at: new Date(Date.now() - 7200000).toISOString(),
      },
    ],
    dashboard: {
      total_claims: 86,
      pending_review: 6,
      actuals: 74,
      conflicts: 1,
      discipline_breakdown: [
        { discipline: 'STATIC_ROTATING_EQUIPMENT', name: 'EQUIPMENT', count: 26, value: 26 },
        { discipline: 'CIVIL', name: 'CIVIL', count: 22, value: 22 },
        { discipline: 'PIPING', name: 'PIPING', count: 20, value: 20 },
        { discipline: 'ELECTRICAL', name: 'ELECTRICAL', count: 12, value: 12 },
        { discipline: 'INSTRUMENTATION', name: 'INSTRUMENTATION', count: 6, value: 6 },
      ],
      claims_trend_pct: 8,
    },
    delayReasons: [
      { reason: 'Sandstorm High Wind Crane Stoppage', count: 5 },
      { reason: 'Specialty Gas Cylinder Delivery Logistics', count: 3 },
      { reason: 'Desert Daytime Heat Shift Adjustments', count: 2 },
    ],
    institutionalMemory: [
      { topic: 'Jaisalmer Desert Soil Thermal Expansion', resolution: 'Incorporate 15mm expansion gap around compressor foundation pads', count: 8 },
      { topic: 'High Ambient Temperature Welding Protocol', resolution: 'Perform root welding during night shifts (8 PM - 5 AM) when temperature drops below 35°C', count: 11 },
    ],
    forecast: {
      discipline: 'STATIC_ROTATING_EQUIPMENT',
      historical_ratio: 1.20,
      total_activities: 2,
      activities: [
        { activity_id: 'ACT-RAJ-102 (Compressor Skid Erection)', discipline: 'STATIC_ROTATING_EQUIPMENT', planned_duration: 14, historical_ratio: 1.20, forecast_duration: 17, slippage_days: 3 },
        { activity_id: 'ACT-RAJ-201 (Manifold M-1 Spools)', discipline: 'PIPING', planned_duration: 12, historical_ratio: 1.08, forecast_duration: 13, slippage_days: 1 },
      ],
    },
    summaryReport: {
      text: 'Rajasthan Gas Processing Upgrade has completed 51.2% physical execution against 55.0% planned. Gas dehydration civil foundations have completed curing, and Compressor Skid #2 erection is progressing with zero safety violations under night-shift operational protocols.',
      highlights: [
        'Erected Compressor Skid #2 on Foundation Bay 2 in Jaisalmer Processing Unit.',
        '12 Super Duplex spools installed on Inlet Gas Manifold M-1.',
        '100% compliance with Desert Heat Safety & Hydration audits.',
      ],
    },
  },

  'PRJ-KG-03': {
    activities: {
      'SCHED-KG-BL1': [
        {
          activity_id: 'ACT-KG-101',
          schedule_id: 'SCHED-KG-BL1',
          activity_name: 'Subsea 18" Infield Flowline Pipelay Barge Operations',
          wbs_code: 'WBS-KG-2.1',
          discipline: 'PIPING',
          location: 'Offshore Sector Block-KG7',
          asset_tag: 'PLB-18-01',
          planned_start: '2026-08-20',
          planned_finish: '2026-09-25',
          planned_quantity: 12,
          uom: 'km',
          baseline_pct_complete: 45,
          total_float: 0,
          is_critical: true,
        },
        {
          activity_id: 'ACT-KG-102',
          schedule_id: 'SCHED-KG-BL1',
          activity_name: 'Offshore Platform Riser Tie-In & Hyperbaric Welding',
          wbs_code: 'WBS-KG-2.2',
          discipline: 'PIPING',
          location: 'Platform PL-A',
          asset_tag: 'RISER-TIE-01',
          planned_start: '2026-09-05',
          planned_finish: '2026-09-22',
          planned_quantity: 4,
          uom: 'risers',
          baseline_pct_complete: 20,
          total_float: 0,
          is_critical: true,
        },
        {
          activity_id: 'ACT-KG-201',
          schedule_id: 'SCHED-KG-BL1',
          activity_name: 'Onshore Finger Slug Catcher Foundation Piling',
          wbs_code: 'WBS-KG-3.1',
          discipline: 'CIVIL',
          location: 'Kakinada Terminal North',
          asset_tag: 'SLUG-FND-01',
          planned_start: '2026-09-01',
          planned_finish: '2026-09-18',
          planned_quantity: 60,
          uom: 'piles',
          baseline_pct_complete: 30,
          total_float: 3,
          is_critical: false,
        },
        {
          activity_id: 'ACT-KG-301',
          schedule_id: 'SCHED-KG-BL1',
          activity_name: 'Electro-Hydraulic Subsea Umbilical Pull-In',
          wbs_code: 'WBS-KG-4.1',
          discipline: 'INSTRUMENTATION',
          location: 'Landfall Valve Station',
          asset_tag: 'UMB-PULL-01',
          planned_start: '2026-09-10',
          planned_finish: '2026-09-24',
          planned_quantity: 8,
          uom: 'km',
          baseline_pct_complete: 10,
          total_float: 2,
          is_critical: false,
        },
        {
          activity_id: 'ACT-KG-401',
          schedule_id: 'SCHED-KG-BL1',
          activity_name: 'Marine Mammal & Coastal CRZ Environmental Compliance',
          wbs_code: 'WBS-KG-1.3',
          discipline: 'HSE',
          location: 'Coastal Zone 2',
          asset_tag: null,
          planned_start: '2026-09-01',
          planned_finish: '2026-09-30',
          planned_quantity: 4,
          uom: 'audits',
          baseline_pct_complete: 100,
          total_float: 15,
          is_critical: false,
        },
      ],
      'SCHED-KG-MON': [
        {
          activity_id: 'ACT-KG-101',
          schedule_id: 'SCHED-KG-MON',
          activity_name: 'Subsea 18" Infield Flowline Pipelay Barge Operations (Weather Adjusted)',
          wbs_code: 'WBS-KG-2.1',
          discipline: 'PIPING',
          location: 'Offshore Sector Block-KG7',
          asset_tag: 'PLB-18-01',
          planned_start: '2026-08-20',
          planned_finish: '2026-10-05',
          planned_quantity: 12,
          uom: 'km',
          baseline_pct_complete: 50,
          total_float: 0,
          is_critical: true,
        },
      ],
    },
    events: [
      {
        event_id: 'evt-kg-101',
        document_id: 'doc-kg-001',
        schedule_id: 'SCHED-KG-BL1',
        event_date: TODAY,
        raw_claim_text: 'Pipelay Barge Seven Oceans completed 1.8 km of 18" subsea flowline welding & lay today.',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-KG-101',
        matched_activity_id: 'ACT-KG-101',
        discipline: 'PIPING',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'INCREMENTAL_QUANTITY',
        asset_tag: 'PLB-18-01',
        location: 'Offshore Sector Block-KG7',
        claimed_quantity: 1.8,
        claimed_uom: 'km',
        claimed_pct: 50,
        delay_reason: null,
        supervisor_id: null,
        photo_path: '/uploads/subsea_lay.jpg',
        status: 'REVIEW_REQUIRED',
        created_at: new Date().toISOString(),
        priority_score: 0.95,
        priority_reasons: ['Critical Offshore Pipelay', 'Bathymetric touch-down verification verified by ROV'],
        is_escalated: true,
        priority_rank: 1,
      },
      {
        event_id: 'evt-kg-102',
        document_id: null,
        schedule_id: 'SCHED-KG-BL1',
        event_date: TODAY,
        raw_claim_text: 'Completed driven piling for Slug Catcher bay 4 (6 reinforced concrete piles).',
        input_channel: 'TYPED_TEXT',
        language_detected: 'en',
        reported_activity_id: 'ACT-KG-201',
        matched_activity_id: 'ACT-KG-201',
        discipline: 'CIVIL',
        action: 'PROGRESS_UPDATE',
        event_type: 'PROGRESS_UPDATE',
        claim_mode: 'INCREMENTAL_QUANTITY',
        asset_tag: 'SLUG-FND-01',
        location: 'Kakinada Terminal North',
        claimed_quantity: 6,
        claimed_uom: 'piles',
        claimed_pct: 40,
        delay_reason: null,
        supervisor_id: null,
        photo_path: null,
        status: 'VALIDATED',
        created_at: new Date().toISOString(),
      },
      {
        event_id: 'evt-kg-103',
        document_id: 'doc-kg-003',
        schedule_id: 'SCHED-KG-BL1',
        event_date: TODAY,
        raw_claim_text: 'Platform PL-A Riser Clamp installation dive inspection signed off by Lloyd\'s surveyor.',
        input_channel: 'FILE_UPLOAD',
        language_detected: 'en',
        reported_activity_id: 'ACT-KG-102',
        matched_activity_id: 'ACT-KG-102',
        discipline: 'PIPING',
        action: 'ACTUAL_FINISH',
        event_type: 'ACTUAL_FINISH',
        claim_mode: 'CUMULATIVE_PCT',
        asset_tag: 'RISER-TIE-01',
        location: 'Platform PL-A',
        claimed_quantity: null,
        claimed_uom: null,
        claimed_pct: 100,
        delay_reason: null,
        supervisor_id: null,
        photo_path: '/uploads/riser_dive.jpg',
        status: 'APPROVED',
        created_at: new Date(Date.now() - 7200000).toISOString(),
      },
    ],
    candidates: [
      {
        candidate_id: 'cand-kg-101',
        event_id: 'evt-kg-101',
        schedule_id: 'SCHED-KG-BL1',
        activity_id: 'ACT-KG-101',
        rank_order: 1,
        match_tier: 'EXACT',
        composite_confidence: 0.95,
        semantic_score: 0.98,
        fuzzy_score: 0.92,
        location_score: 0.96,
        discipline_score: 1.0,
        supporting_signals: 'Subsea pipelay keywords matched; matched discipline PIPING; barge GPS match',
        disqualifying_signals: null,
      },
    ],
    validationIssues: [
      {
        issue_id: 'flag-kg-101',
        event_id: 'evt-kg-101',
        rule_code: 'ROV_SURVEY_ATTACHMENT',
        severity: 'WARNING',
        description: 'ROV acoustic transponder depth log verified within 0.5m tolerance.',
      },
    ],
    decisions: [
      {
        decision_id: 'dec-kg-101',
        event_id: 'evt-kg-103',
        selected_activity_id: 'ACT-KG-102',
        action: 'APPROVE',
        approved_pct: 100,
        approved_qty: 4,
        planner_id: 'usr-supervisor-01',
        justification: 'Lloyd\'s Register dive inspection report and NDT weld testing verified.',
        decided_at: new Date(Date.now() - 7200000).toISOString(),
      },
    ],
    dashboard: {
      total_claims: 62,
      pending_review: 5,
      actuals: 54,
      conflicts: 0,
      discipline_breakdown: [
        { discipline: 'PIPING', name: 'OFFSHORE PIPING', count: 32, value: 32 },
        { discipline: 'CIVIL', name: 'CIVIL', count: 14, value: 14 },
        { discipline: 'INSTRUMENTATION', name: 'SUBSEA SCADA', count: 10, value: 10 },
        { discipline: 'HSE', name: 'CRZ / MARINE HSE', count: 6, value: 6 },
      ],
      claims_trend_pct: 15,
    },
    delayReasons: [
      { reason: 'Bay of Bengal High Swell Sea State 4 Stoppage', count: 4 },
      { reason: 'ROV Umbilical Cable Maintenance Routine', count: 2 },
      { reason: 'Subsea Choke Valve Customs Clearance', count: 2 },
    ],
    institutionalMemory: [
      { topic: 'Bay of Bengal Monsoon Wave Height Restrictions', resolution: 'Cease pipelay barge operations when significant wave height Hs exceeds 2.2 meters', count: 12 },
      { topic: 'Hyperbaric Tie-In Habitat Oxygen Monitoring', resolution: 'Maintain continuous dual-sensor oxygen analyzer logging inside weld habitat', count: 7 },
    ],
    forecast: {
      discipline: 'PIPING',
      historical_ratio: 1.05,
      total_activities: 2,
      activities: [
        { activity_id: 'ACT-KG-101 (Subsea Pipelay)', discipline: 'PIPING', planned_duration: 36, historical_ratio: 1.05, forecast_duration: 38, slippage_days: 2 },
        { activity_id: 'ACT-KG-102 (Platform Riser Tie-In)', discipline: 'PIPING', planned_duration: 17, historical_ratio: 1.04, forecast_duration: 18, slippage_days: 1 },
      ],
    },
    summaryReport: {
      text: 'KG Basin Offshore Tie-In is currently tracking at 39.5% physical progress against 38.0% planned (+1.5% ahead). Pipelay barge operations in Block KG-7 have laid 1.8 km of subsea flowline today, and onshore slug catcher foundation piling is progressing on schedule.',
      highlights: [
        '1.8 km of 18" subsea flowline welded and laid by Pipelay Barge.',
        'Platform PL-A Riser Tie-In hyperbaric weld inspection approved by Lloyd\'s surveyor.',
        'Zero coastal marine ecology non-conformances in CRZ Zone 2.',
      ],
    },
  },
};

export const MOCK_DYNAMIC_EVENTS = new Map<string, ExecutionEvent>();

export function getDatasetForCurrentProject(): ProjectMockDataset {
  const pId = getActiveProjectId();
  return MOCK_DATASETS[pId] || MOCK_DATASETS['PRJ-ASSAM-01'];
}

export function getActivitiesForCurrentSchedule(scheduleId?: string): ScheduleActivity[] {
  const dataset = getDatasetForCurrentProject();
  const vId = scheduleId || getActiveScheduleVersionId();
  if (dataset.activities[vId]) {
    return dataset.activities[vId];
  }
  const firstKey = Object.keys(dataset.activities)[0];
  return dataset.activities[firstKey] || [];
}

export function getAllEventsForCurrentProject(): ExecutionEvent[] {
  const dataset = getDatasetForCurrentProject();
  const dynamic = Array.from(MOCK_DYNAMIC_EVENTS.values()).filter(
    (e) => e.schedule_id === getActiveScheduleVersionId() || e.schedule_id === getActiveProjectId()
  );
  return [...dataset.events, ...dynamic];
}

export function extractMockClaimFields(text: string): {
  discipline: Discipline | null;
  event_type: EventType;
  claimed_pct: number | null;
  claimed_quantity: number | null;
  claimed_uom: string | null;
  clarification_status: ClarificationStatus;
  clarification_question: string | null;
} {
  const lower = text.toLowerCase();

  let discipline: Discipline | null = null;
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

  let event_type: EventType = 'PROGRESS_UPDATE';
  if (lower.includes('finish') || lower.includes('complete') || lower.includes('done') || lower.includes('energiz') || lower.includes('handed over')) {
    event_type = 'ACTUAL_FINISH';
  } else if (lower.includes('start') || lower.includes('commenced') || lower.includes('began')) {
    event_type = 'ACTUAL_START';
  } else if (lower.includes('delay') || lower.includes('behind') || lower.includes('waiting') || lower.includes('stuck')) {
    event_type = 'DELAY';
  } else if (lower.includes('block') || lower.includes('stopped') || lower.includes('hold')) {
    event_type = 'BLOCKER';
  }

  let claimed_pct: number | null = null;
  let claimed_quantity: number | null = null;
  let claimed_uom: string | null = null;

  const pctMatch = text.match(/(\d+(?:\.\d+)?)\s*%/);
  if (pctMatch) {
    claimed_pct = Math.min(100, Math.max(0, parseFloat(pctMatch[1])));
  } else if (event_type === 'ACTUAL_FINISH') {
    claimed_pct = 100;
  }

  const qtyMatch = text.match(/(\d+(?:\.\d+)?)\s*(cu\.m|m3|m|meters|joints|nos|tons|kg|units|piles|km|spools|loops)/i);
  if (qtyMatch) {
    claimed_quantity = parseFloat(qtyMatch[1]);
    claimed_uom = qtyMatch[2];
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
