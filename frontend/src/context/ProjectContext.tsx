import React, { createContext, useContext, useState, useEffect, useMemo } from 'react';
import { calculateProjectProgress } from '@/lib/progressEngine';
import { getActivitiesForCurrentSchedule } from '@/mockData';

export interface ScheduleVersion {
  id: string;
  versionNumber: string;
  name: string;
  description: string;
  status: 'ACTIVE' | 'ARCHIVED' | 'PROPOSED';
  isCurrent: boolean;
  isImmutable: boolean;
  effectiveDate: string;
  sourceType: 'PRIMAVERA_P6_XER' | 'CSV_XLSX' | 'MANUAL';
  activitiesCount: number;
  milestonesCount: number;
}

export interface ProjectStage {
  id: string;
  stageNumber: number;
  name: string;
  discipline: string;
  status: 'COMPLETED' | 'ACTIVE' | 'IN_PROGRESS' | 'NOT_STARTED';
  plannedPct: number;
  actualPct: number;
  variance: number;
  activitiesCount: number;
  wbsPrefix: string;
}

export interface Project {
  id: string;
  code: string;
  name: string;
  region: string;
  client: string;
  totalBudget: string;
  overallPlanned: number;
  overallActual: number;
  variance: number;
  activeStage: string;
  totalStages: number;
  criticalActivities: number;
  openClaims: number;
  qualityHolds: number;
  scheduleVersions: ScheduleVersion[];
  stages: ProjectStage[];
}

export const DEMO_PROJECTS: Project[] = [
  {
    id: 'PRJ-ASSAM-01',
    code: 'APE-2026',
    name: 'Assam Pipeline Expansion (Duliajan–Numaligarh)',
    region: 'Assam Asset / North East',
    client: 'Oil India Limited (OIL)',
    totalBudget: '₹ 485.60 Cr',
    overallPlanned: 72.0,
    overallActual: 68.4,
    variance: -3.6,
    activeStage: 'Stage 3 — Civil & Foundation Works',
    totalStages: 5,
    criticalActivities: 4,
    openClaims: 4,
    qualityHolds: 2,
    scheduleVersions: [
      {
        id: 'SCHED-ASSAM-V1',
        versionNumber: 'Baseline V1.0',
        name: 'Baseline V1.0 — Original Approved Schedule',
        description: 'Sanctioned Master EPC Baseline approved by OIL Board in Jan 2026',
        status: 'ARCHIVED',
        isCurrent: false,
        isImmutable: true,
        effectiveDate: '2026-01-15',
        sourceType: 'PRIMAVERA_P6_XER',
        activitiesCount: 28,
        milestonesCount: 6,
      },
      {
        id: 'SCHED-ASSAM-V2',
        versionNumber: 'Baseline V2.1',
        name: 'Baseline V2.1 — Revised Approved Baseline',
        description: 'Current working baseline with updated River HDD crossing schedule',
        status: 'ACTIVE',
        isCurrent: true,
        isImmutable: false,
        effectiveDate: '2026-06-01',
        sourceType: 'PRIMAVERA_P6_XER',
        activitiesCount: 32,
        milestonesCount: 10,
      },
      {
        id: 'SCHED-ASSAM-REC',
        versionNumber: 'Recovery V1.0',
        name: 'Recovery V1.0 — Monsoon Recovery Plan',
        description: 'Proposed monsoon catch-up schedule with dual-shift welding crews',
        status: 'PROPOSED',
        isCurrent: false,
        isImmutable: true,
        effectiveDate: '2026-08-20',
        sourceType: 'PRIMAVERA_P6_XER',
        activitiesCount: 34,
        milestonesCount: 11,
      },
    ],
    stages: [
      {
        id: 'STG-ASSAM-1',
        stageNumber: 1,
        name: 'Stage 1 — Engineering & Detail Survey',
        discipline: 'GENERAL',
        status: 'COMPLETED',
        plannedPct: 100,
        actualPct: 100,
        variance: 0,
        activitiesCount: 18,
        wbsPrefix: 'WBS-1.1',
      },
      {
        id: 'STG-ASSAM-2',
        stageNumber: 2,
        name: 'Stage 2 — Procurement & Long Lead Pipe Delivery',
        discipline: 'PROCUREMENT',
        status: 'IN_PROGRESS',
        plannedPct: 95,
        actualPct: 92,
        variance: -3,
        activitiesCount: 24,
        wbsPrefix: 'WBS-1.2',
      },
      {
        id: 'STG-ASSAM-3',
        stageNumber: 3,
        name: 'Stage 3 — Civil & Foundation Works',
        discipline: 'CIVIL',
        status: 'ACTIVE',
        plannedPct: 78,
        actualPct: 72,
        variance: -6,
        activitiesCount: 32,
        wbsPrefix: 'WBS-1.3',
      },
      {
        id: 'STG-ASSAM-4',
        stageNumber: 4,
        name: 'Stage 4 — Piping, Welding & River HDD Crossing',
        discipline: 'PIPING',
        status: 'IN_PROGRESS',
        plannedPct: 52,
        actualPct: 45,
        variance: -7,
        activitiesCount: 28,
        wbsPrefix: 'WBS-1.4',
      },
      {
        id: 'STG-ASSAM-5',
        stageNumber: 5,
        name: 'Stage 5 — Terminal Stations & Commissioning',
        discipline: 'COMMISSIONING',
        status: 'NOT_STARTED',
        plannedPct: 0,
        actualPct: 0,
        variance: 0,
        activitiesCount: 14,
        wbsPrefix: 'WBS-1.5',
      },
    ],
  },
  {
    id: 'PRJ-RAJ-02',
    code: 'RGPU-2026',
    name: 'Rajasthan Gas Processing Upgrade (Jaisalmer Basin)',
    region: 'Rajasthan / Western Asset',
    client: 'Oil India Limited (OIL)',
    totalBudget: '₹ 320.00 Cr',
    overallPlanned: 55.0,
    overallActual: 51.2,
    variance: -3.8,
    activeStage: 'Stage 2 — Gas Dehydration Unit Procurement',
    totalStages: 4,
    criticalActivities: 3,
    openClaims: 2,
    qualityHolds: 1,
    scheduleVersions: [
      {
        id: 'SCHED-RAJ-INIT',
        versionNumber: 'Feasibility V0.9',
        name: 'Feasibility Baseline V0.9 — Initial DPR',
        description: 'Preliminary feasibility schedule for Jaisalmer GGS capacity enhancement',
        status: 'ARCHIVED',
        isCurrent: false,
        isImmutable: true,
        effectiveDate: '2025-11-10',
        sourceType: 'CSV_XLSX',
        activitiesCount: 20,
        milestonesCount: 5,
      },
      {
        id: 'SCHED-RAJ-V1',
        versionNumber: 'Baseline V1.0',
        name: 'Baseline V1.0 — Approved EPC Master Schedule',
        description: 'Sanctioned baseline with TEG column delivery milestones',
        status: 'ACTIVE',
        isCurrent: true,
        isImmutable: false,
        effectiveDate: '2026-03-01',
        sourceType: 'PRIMAVERA_P6_XER',
        activitiesCount: 24,
        milestonesCount: 6,
      },
      {
        id: 'SCHED-RAJ-REV',
        versionNumber: 'Recovery V1.0',
        name: 'Recovery V1.0 — Summer Shift Adjusted Plan',
        description: 'Proposed night shift schedule to mitigate extreme desert daytime heat',
        status: 'PROPOSED',
        isCurrent: false,
        isImmutable: true,
        effectiveDate: '2026-07-15',
        sourceType: 'PRIMAVERA_P6_XER',
        activitiesCount: 26,
        milestonesCount: 7,
      },
    ],
    stages: [
      {
        id: 'STG-RAJ-1',
        stageNumber: 1,
        name: 'Stage 1 — FEED & Environmental Clearance',
        discipline: 'GENERAL',
        status: 'COMPLETED',
        plannedPct: 100,
        actualPct: 100,
        variance: 0,
        activitiesCount: 12,
        wbsPrefix: 'WBS-RAJ-1',
      },
      {
        id: 'STG-RAJ-2',
        stageNumber: 2,
        name: 'Stage 2 — Gas Dehydration Unit (TEG) Procurement',
        discipline: 'PROCUREMENT',
        status: 'ACTIVE',
        plannedPct: 70,
        actualPct: 64,
        variance: -6,
        activitiesCount: 20,
        wbsPrefix: 'WBS-RAJ-2',
      },
      {
        id: 'STG-RAJ-3',
        stageNumber: 3,
        name: 'Stage 3 — Compressor Skid & Civil Foundation',
        discipline: 'CIVIL',
        status: 'IN_PROGRESS',
        plannedPct: 30,
        actualPct: 25,
        variance: -5,
        activitiesCount: 26,
        wbsPrefix: 'WBS-RAJ-3',
      },
      {
        id: 'STG-RAJ-4',
        stageNumber: 4,
        name: 'Stage 4 — Hook-Up & First Gas Commissioning',
        discipline: 'COMMISSIONING',
        status: 'NOT_STARTED',
        plannedPct: 0,
        actualPct: 0,
        variance: 0,
        activitiesCount: 16,
        wbsPrefix: 'WBS-RAJ-4',
      },
    ],
  },
  {
    id: 'PRJ-KG-03',
    code: 'KGT-2026',
    name: 'KG Basin Offshore Tie-In (Kakinada Terminal)',
    region: 'Eastern Offshore Asset / Kakinada',
    client: 'Oil India Limited (OIL)',
    totalBudget: '₹ 740.00 Cr',
    overallPlanned: 38.0,
    overallActual: 39.5,
    variance: 1.5,
    activeStage: 'Stage 2 — Subsea Pipeline & Riser Laying',
    totalStages: 5,
    criticalActivities: 2,
    openClaims: 3,
    qualityHolds: 0,
    scheduleVersions: [
      {
        id: 'SCHED-KG-BL1',
        versionNumber: 'Master V1.0',
        name: 'Master Baseline V1.0 — Approved Offshore Plan',
        description: 'Sanctioned baseline including pipelay barge and diving campaigns',
        status: 'ACTIVE',
        isCurrent: true,
        isImmutable: false,
        effectiveDate: '2026-02-15',
        sourceType: 'PRIMAVERA_P6_XER',
        activitiesCount: 36,
        milestonesCount: 9,
      },
      {
        id: 'SCHED-KG-MON',
        versionNumber: 'Recovery V1.0',
        name: 'Recovery V1.0 — Post-Cyclone Bay of Bengal Schedule',
        description: 'Contingency schedule for sea state swell windows and offshore hyperbaric tie-in',
        status: 'PROPOSED',
        isCurrent: false,
        isImmutable: true,
        effectiveDate: '2026-08-01',
        sourceType: 'PRIMAVERA_P6_XER',
        activitiesCount: 38,
        milestonesCount: 10,
      },
    ],
    stages: [
      {
        id: 'STG-KG-1',
        stageNumber: 1,
        name: 'Stage 1 — Geotechnical & Bathymetric Survey',
        discipline: 'GENERAL',
        status: 'COMPLETED',
        plannedPct: 100,
        actualPct: 100,
        variance: 0,
        activitiesCount: 10,
        wbsPrefix: 'WBS-KG-1',
      },
      {
        id: 'STG-KG-2',
        stageNumber: 2,
        name: 'Stage 2 — Subsea Pipeline & Riser Laying',
        discipline: 'PIPING',
        status: 'ACTIVE',
        plannedPct: 45,
        actualPct: 48,
        variance: 3,
        activitiesCount: 30,
        wbsPrefix: 'WBS-KG-2',
      },
      {
        id: 'STG-KG-3',
        stageNumber: 3,
        name: 'Stage 3 — Onshore Slug Catcher & Metering',
        discipline: 'CIVIL',
        status: 'IN_PROGRESS',
        plannedPct: 20,
        actualPct: 18,
        variance: -2,
        activitiesCount: 22,
        wbsPrefix: 'WBS-KG-3',
      },
      {
        id: 'STG-KG-4',
        stageNumber: 4,
        name: 'Stage 4 — SCADA & Subsea Umbilicals',
        discipline: 'INSTRUMENTATION',
        status: 'IN_PROGRESS',
        plannedPct: 5,
        actualPct: 2,
        variance: -3,
        activitiesCount: 18,
        wbsPrefix: 'WBS-KG-4',
      },
      {
        id: 'STG-KG-5',
        stageNumber: 5,
        name: 'Stage 5 — Hydrotest & First Gas Introduction',
        discipline: 'COMMISSIONING',
        status: 'NOT_STARTED',
        plannedPct: 0,
        actualPct: 0,
        variance: 0,
        activitiesCount: 12,
        wbsPrefix: 'WBS-KG-5',
      },
    ],
  },
];

export const SELECTED_PROJECT_KEY = 'setu_selected_project_id_v7';
export const SELECTED_VERSION_KEY = 'setu_selected_sched_version_v7';

interface ProjectContextType {
  projects: Project[];
  currentProject: Project;
  setCurrentProjectId: (id: string) => void;
  currentScheduleVersion: ScheduleVersion;
  setCurrentScheduleVersionId: (id: string) => void;
  selectedStageId: string | null;
  setSelectedStageId: (stageId: string | null) => void;
  importScheduleVersion: (
    projectId: string,
    versionData: {
      name: string;
      versionNumber: string;
      description?: string;
      sourceType: 'PRIMAVERA_P6_XER' | 'CSV_XLSX';
      effectiveDate: string;
      activitiesCount: number;
    }
  ) => ScheduleVersion;
}

const ProjectContext = createContext<ProjectContextType | undefined>(undefined);

export function ProjectProvider({ children }: { children: React.ReactNode }) {
  const [projects, setProjects] = useState<Project[]>(() => {
    return DEMO_PROJECTS;
  });

  const [currentProjectId, setCurrentProjectIdState] = useState<string>(() => {
    const saved = localStorage.getItem(SELECTED_PROJECT_KEY);
    if (saved && DEMO_PROJECTS.some((p) => p.id === saved)) {
      return saved;
    }
    return DEMO_PROJECTS[0].id;
  });

  const [refreshTrigger, setRefreshTrigger] = useState(0);

  useEffect(() => {
    const handleUpdate = () => setRefreshTrigger((prev) => prev + 1);
    window.addEventListener('setu:activity-progress-changed', handleUpdate);
    window.addEventListener('setu:activity-state-changed', handleUpdate);
    window.addEventListener('setu:reopen-changed', handleUpdate);
    window.addEventListener('setu-project-changed', handleUpdate);
    window.addEventListener('setu-version-changed', handleUpdate);

    return () => {
      window.removeEventListener('setu:activity-progress-changed', handleUpdate);
      window.removeEventListener('setu:activity-state-changed', handleUpdate);
      window.removeEventListener('setu:reopen-changed', handleUpdate);
      window.removeEventListener('setu-project-changed', handleUpdate);
      window.removeEventListener('setu-version-changed', handleUpdate);
    };
  }, []);

  const rawProject =
    projects.find((p) => p.id === currentProjectId) || projects[0];

  const [currentScheduleVersionId, setCurrentScheduleVersionIdState] =
    useState<string>(() => {
      const saved = localStorage.getItem(SELECTED_VERSION_KEY);
      if (
        saved &&
        rawProject.scheduleVersions.some((v) => v.id === saved)
      ) {
        return saved;
      }
      const activeVer = rawProject.scheduleVersions.find((v) => v.isCurrent);
      return activeVer ? activeVer.id : rawProject.scheduleVersions[0].id;
    });

  const currentScheduleVersion =
    rawProject.scheduleVersions.find(
      (v) => v.id === currentScheduleVersionId
    ) || rawProject.scheduleVersions[0];

  const currentProject = useMemo(() => {
    const pId = rawProject.id;
    const sId = currentScheduleVersionId;
    const acts = getActivitiesForCurrentSchedule(sId, pId);
    const summary = calculateProjectProgress(acts, rawProject.stages, sId, pId);

    const updatedStages: ProjectStage[] = rawProject.stages.map((stg) => {
      const stageSum = summary.stages.find((s) => s.stage_id === stg.id);
      if (!stageSum) return stg;
      return {
        ...stg,
        plannedPct: stageSum.planned_progress,
        actualPct: stageSum.actual_progress,
        variance: stageSum.variance,
        status: stageSum.status,
        activitiesCount: stageSum.activities_count,
      };
    });

    const activeStageName =
      updatedStages.find((s) => s.status === 'ACTIVE' || s.status === 'IN_PROGRESS')?.name ||
      updatedStages[0]?.name ||
      rawProject.activeStage;

    return {
      ...rawProject,
      overallPlanned: summary.overall_planned,
      overallActual: summary.overall_actual,
      variance: summary.variance,
      criticalActivities: summary.critical_activities,
      activeStage: activeStageName,
      stages: updatedStages,
    };
  }, [rawProject, currentScheduleVersionId, refreshTrigger]);

  const [selectedStageId, setSelectedStageId] = useState<string | null>(null);

  // Sync state to localStorage
  useEffect(() => {
    localStorage.setItem(SELECTED_PROJECT_KEY, currentProjectId);
  }, [currentProjectId]);

  useEffect(() => {
    localStorage.setItem(SELECTED_VERSION_KEY, currentScheduleVersionId);
  }, [currentScheduleVersionId]);

  const setCurrentProjectId = (id: string) => {
    const target = projects.find((p) => p.id === id);
    if (!target) return;
    setCurrentProjectIdState(id);
    localStorage.setItem(SELECTED_PROJECT_KEY, id);
    const activeVer = target.scheduleVersions.find((v) => v.isCurrent) || target.scheduleVersions[0];
    setCurrentScheduleVersionIdState(activeVer.id);
    localStorage.setItem(SELECTED_VERSION_KEY, activeVer.id);
    setSelectedStageId(null);
    window.dispatchEvent(new CustomEvent('setu-project-changed', { detail: { projectId: id, versionId: activeVer.id } }));
  };

  const setCurrentScheduleVersionId = (id: string) => {
    const ver = currentProject.scheduleVersions.find((v) => v.id === id);
    if (!ver) return;
    setCurrentScheduleVersionIdState(id);
    localStorage.setItem(SELECTED_VERSION_KEY, id);
    window.dispatchEvent(new CustomEvent('setu-version-changed', { detail: { projectId: currentProject.id, versionId: id } }));
  };

  const importScheduleVersion = (
    projectId: string,
    versionData: {
      name: string;
      versionNumber: string;
      description?: string;
      sourceType: 'PRIMAVERA_P6_XER' | 'CSV_XLSX';
      effectiveDate: string;
      activitiesCount: number;
    }
  ): ScheduleVersion => {
    const newVersion: ScheduleVersion = {
      id: `SCHED-${Date.now()}`,
      versionNumber: versionData.versionNumber,
      name: versionData.name,
      description: versionData.description || `Imported ${versionData.sourceType} schedule version`,
      status: 'ACTIVE',
      isCurrent: true,
      isImmutable: false,
      effectiveDate: versionData.effectiveDate,
      sourceType: versionData.sourceType,
      activitiesCount: versionData.activitiesCount,
      milestonesCount: Math.ceil(versionData.activitiesCount / 3),
    };

    setProjects((prev) =>
      prev.map((proj) => {
        if (proj.id !== projectId) return proj;
        const updatedVersions = proj.scheduleVersions.map((v) => ({
          ...v,
          isCurrent: false,
          status: 'ARCHIVED' as const,
          isImmutable: true,
        }));
        return {
          ...proj,
          scheduleVersions: [newVersion, ...updatedVersions],
        };
      })
    );

    setCurrentScheduleVersionIdState(newVersion.id);
    localStorage.setItem(SELECTED_VERSION_KEY, newVersion.id);
    window.dispatchEvent(new CustomEvent('setu-version-changed', { detail: { projectId, versionId: newVersion.id } }));
    return newVersion;
  };

  return (
    <ProjectContext.Provider
      value={{
        projects,
        currentProject,
        setCurrentProjectId,
        currentScheduleVersion,
        setCurrentScheduleVersionId,
        selectedStageId,
        setSelectedStageId,
        importScheduleVersion,
      }}
    >
      {children}
    </ProjectContext.Provider>
  );
}

export function useProject() {
  const context = useContext(ProjectContext);
  if (!context) {
    throw new Error('useProject must be used within a ProjectProvider');
  }
  return context;
}
