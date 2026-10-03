/**
 * Project context: Authenticated user -> current PROJECT -> current SCHEDULE VERSION, from the real backend.
 *
 * - Projects are the caller's active memberships (GET /api/v1/projects). Nothing is hard-coded.
 * - The schedule version is always an explicit choice: the project's single active version by default;
 *   if there is none or more than one active version the user must pick (never a silent guess).
 * - The selection is pushed into lib/apiContext, which the API client reads on EVERY request
 *   (X-Project-ID / X-Schedule-ID), so no call site can forget the context.
 * - Role and permissions come from the server (GET /projects/{id}/me): the UI gates by the same RBAC
 *   permissions the backend enforces.
 */
import React, { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/auth/AuthProvider';
import {
  projectsApi,
  scheduleVersionsApi,
  progressApi,
  stagesApi,
  type Permission,
  type ProgressBreakdown,
  type ProjectListItem,
  type ProjectMe,
  type ProjectRole,
  type ScheduleVersionDto,
} from '@/api/projects';
import { ApiError } from '@/api/client';
import { apiContext, SELECTED_PROJECT_KEY, SELECTED_VERSION_KEY } from '@/lib/apiContext';

export { SELECTED_PROJECT_KEY, SELECTED_VERSION_KEY };

export interface ScheduleVersion {
  id: string;
  versionNumber: string;
  name: string;
  status: 'ACTIVE' | 'ARCHIVED';
  isCurrent: boolean;
  isImmutable: boolean;
  effectiveDate: string;
  sourceType: string;
  activitiesCount: number;
  dependenciesCount: number;
  supersedes: string | null;
}

export interface ProjectStage {
  id: string;
  stageNumber: number;
  code: string | null;
  name: string;
  status: string;
  weightPct: number | null;
  /** Authoritative weighted progress from the backend ProgressService (never computed in the browser). */
  actualPct: number;
  activitiesCount: number;
  completedCount: number;
  plannedStart: string | null;
  plannedFinish: string | null;
}

export interface Project {
  id: string;
  code: string;
  name: string;
  status: string;
  role: ProjectRole;
  region: string | null;
  client: string | null;
  description: string | null;
  scheduleVersions: ScheduleVersion[];
  stages: ProjectStage[];
  totalStages: number;
  /** Overall weighted progress of the selected schedule version (authoritative, from the backend). */
  overallActual: number;
  activeStage: string | null;
}

export type ProjectStatus = 'unauthenticated' | 'loading' | 'error' | 'no-projects' | 'needs-schedule' | 'ready';

export interface ProjectStateType {
  status: ProjectStatus;
  error: string | null;
  projects: ProjectListItem[];
  currentProject: Project | null;
  currentScheduleVersion: ScheduleVersion | null;
  scheduleVersions: ScheduleVersion[];
  setCurrentProjectId: (id: string) => void;
  setCurrentScheduleVersionId: (id: string) => void;
  role: ProjectRole | null;
  permissions: Permission[];
  can: (permission: Permission) => boolean;
  selectedStageId: string | null;
  setSelectedStageId: (stageId: string | null) => void;
  progress: ProgressBreakdown | null;
  refresh: () => void;
}

/** What pages get: only ever used while status === 'ready'. */
export interface ProjectContextType extends ProjectStateType {
  currentProject: Project;
  currentScheduleVersion: ScheduleVersion;
  role: ProjectRole;
}

export const ProjectContext = createContext<ProjectStateType | undefined>(undefined);

const versionKey = (projectId: string) => `${SELECTED_VERSION_KEY}:${projectId}`;

function toVersion(v: ScheduleVersionDto): ScheduleVersion {
  return {
    id: v.schedule_id,
    versionNumber: v.version_code || v.schedule_id,
    name: `${v.version_code || v.schedule_id}${v.active ? ' (active)' : ''}`,
    status: v.active ? 'ACTIVE' : 'ARCHIVED',
    isCurrent: v.active,
    isImmutable: !v.active,
    effectiveDate: v.data_date || (v.created_at ? v.created_at.slice(0, 10) : ''),
    sourceType: v.source_format || 'unknown',
    activitiesCount: v.activity_count,
    dependenciesCount: v.dependency_count,
    supersedes: v.supersedes_schedule_id,
  };
}

export function ProjectProvider({ children }: { children: React.ReactNode }) {
  const { user, isAuthenticated } = useAuth();
  const queryClient = useQueryClient();

  const [projectId, setProjectId] = useState<string | null>(() => localStorage.getItem(SELECTED_PROJECT_KEY));
  const [chosenVersionId, setChosenVersionId] = useState<string | null>(null);
  const [selectedStageId, setSelectedStageId] = useState<string | null>(null);

  // ── memberships ───────────────────────────────────────────────────────────────
  const projectsQuery = useQuery({
    queryKey: ['v7', 'projects', user?.id],
    queryFn: () => projectsApi.list(),
    enabled: isAuthenticated,
    staleTime: 60_000,
    retry: false,
  });
  const projects = projectsQuery.data ?? [];

  // The current project is one the user actually belongs to; otherwise fall back to their first membership.
  const effectiveProjectId = useMemo(() => {
    if (!projects.length) return null;
    return projects.some((p) => p.project_id === projectId) ? projectId : projects[0].project_id;
  }, [projects, projectId]);

  // ── schedule versions of the current project ─────────────────────────────────
  const versionsQuery = useQuery({
    queryKey: ['v7', 'versions', effectiveProjectId],
    queryFn: () => scheduleVersionsApi.list(effectiveProjectId!),
    enabled: !!effectiveProjectId,
    retry: false,
  });
  const scheduleVersions = useMemo(() => (versionsQuery.data ?? []).map(toVersion), [versionsQuery.data]);

  // Explicit selection: remembered choice if still valid, else the single ACTIVE version. Never a guess.
  const effectiveVersionId = useMemo(() => {
    if (!effectiveProjectId || !scheduleVersions.length) return null;
    const remembered = chosenVersionId ?? localStorage.getItem(versionKey(effectiveProjectId));
    if (remembered && scheduleVersions.some((v) => v.id === remembered)) return remembered;
    const active = scheduleVersions.filter((v) => v.isCurrent);
    return active.length === 1 ? active[0].id : null;
  }, [effectiveProjectId, scheduleVersions, chosenVersionId]);

  // Publish the context to the API client BEFORE any child effect fires a request.
  useLayoutEffect(() => {
    if (!isAuthenticated) {
      apiContext.clear();
      return;
    }
    apiContext.set(effectiveProjectId, effectiveProjectId ? effectiveVersionId : null);
  }, [isAuthenticated, effectiveProjectId, effectiveVersionId]);

  useEffect(() => {
    if (effectiveProjectId) localStorage.setItem(SELECTED_PROJECT_KEY, effectiveProjectId);
  }, [effectiveProjectId]);
  useEffect(() => {
    if (effectiveProjectId && effectiveVersionId) localStorage.setItem(versionKey(effectiveProjectId), effectiveVersionId);
  }, [effectiveProjectId, effectiveVersionId]);

  const ready = !!effectiveProjectId && !!effectiveVersionId;

  // ── role / permissions (server-authoritative) ────────────────────────────────
  const meQuery = useQuery<ProjectMe>({
    queryKey: ['v7', 'me', effectiveProjectId],
    queryFn: () => projectsApi.me(effectiveProjectId!),
    enabled: !!effectiveProjectId,
    retry: false,
  });

  // ── project details + authoritative progress ─────────────────────────────────
  const detailQuery = useQuery({
    queryKey: ['v7', 'project', effectiveProjectId],
    queryFn: () => projectsApi.get(effectiveProjectId!),
    enabled: !!effectiveProjectId,
    retry: false,
  });
  const progressQuery = useQuery({
    queryKey: ['v7', 'progress', effectiveProjectId, effectiveVersionId],
    queryFn: async () => {
      const [breakdown, stages] = await Promise.all([
        progressApi.breakdown(effectiveProjectId!, effectiveVersionId!),
        stagesApi.list(effectiveProjectId!, effectiveVersionId!),
      ]);
      return { breakdown, stages };
    },
    enabled: ready,
    retry: false,
  });

  // Anything that changes execution state elsewhere in the app refreshes progress.
  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['v7'] });
  }, [queryClient]);
  useEffect(() => {
    const events = ['setu:activity-progress-changed', 'setu:activity-state-changed', 'setu:reopen-changed'];
    events.forEach((e) => window.addEventListener(e, refresh));
    return () => events.forEach((e) => window.removeEventListener(e, refresh));
  }, [refresh]);

  const currentScheduleVersion = scheduleVersions.find((v) => v.id === effectiveVersionId) ?? null;
  const progress = progressQuery.data?.breakdown ?? null;

  const currentProject: Project | null = useMemo(() => {
    const item = projects.find((p) => p.project_id === effectiveProjectId);
    if (!item) return null;
    const detail = detailQuery.data;
    const stageDtos = [...(progressQuery.data?.stages ?? [])].sort((a, b) => a.sequence_order - b.sequence_order);
    const byId = new Map((progress?.stages ?? []).map((s) => [s.stage_id, s]));
    const stages: ProjectStage[] = stageDtos.map((s) => {
      const pr = byId.get(s.stage_id);
      // The stored stage status is not maintained by the workflow; derive the display status from the
      // authoritative approved progress (an explicit BLOCKED / ON_HOLD stage keeps its stored status).
      const total = pr?.activity_count ?? 0;
      const derivedStatus =
        s.status === 'BLOCKED' || s.status === 'ON_HOLD' ? s.status
        : total > 0 && (pr?.completed_count ?? 0) >= total ? 'COMPLETED'
        : (pr?.progress_pct ?? 0) > 0 ? 'IN_PROGRESS'
        : s.status;
      return {
        id: s.stage_id,
        stageNumber: s.sequence_order,
        code: s.stage_code,
        name: s.stage_name,
        status: derivedStatus,
        weightPct: s.weight_pct,
        actualPct: pr?.progress_pct ?? 0,
        activitiesCount: pr?.activity_count ?? 0,
        completedCount: pr?.completed_count ?? 0,
        plannedStart: s.planned_start,
        plannedFinish: s.planned_finish,
      };
    });
    const activeStage = stages.find((s) => s.status === 'IN_PROGRESS')?.name ?? null;
    return {
      id: item.project_id,
      code: item.project_code,
      name: item.project_name,
      status: item.status,
      role: item.assigned_role,
      region: detail?.location ?? null,
      client: detail?.client_name ?? null,
      description: detail?.description ?? null,
      scheduleVersions,
      stages,
      totalStages: stages.length,
      overallActual: progress?.overall_progress_pct ?? 0,
      activeStage,
    };
  }, [projects, effectiveProjectId, detailQuery.data, progressQuery.data, progress, scheduleVersions]);

  // ── overall status ───────────────────────────────────────────────────────────
  let status: ProjectStatus;
  let error: string | null = null;
  const firstError = [projectsQuery.error, versionsQuery.error, meQuery.error].find(Boolean) as unknown;
  if (!isAuthenticated) status = 'unauthenticated';
  // isPending (not isLoading): isLoading is briefly false right after a query becomes enabled, which would let a route
  // guard evaluate permissions that have not arrived yet and redirect.
  else if (projectsQuery.isPending) status = 'loading';
  else if (firstError) {
    status = 'error';
    error = firstError instanceof ApiError ? firstError.message : 'Could not load your projects.';
  } else if (!projects.length) status = 'no-projects';
  else if (versionsQuery.isPending || meQuery.isPending || detailQuery.isPending) status = 'loading';
  else if (!effectiveVersionId) status = 'needs-schedule';
  else if (progressQuery.isPending || !currentProject) status = 'loading';
  else status = 'ready';

  const permissions = meQuery.data?.permissions ?? [];
  const role = meQuery.data?.role ?? currentProject?.role ?? null;

  const setCurrentProjectId = useCallback(
    (id: string) => {
      if (!projects.some((p) => p.project_id === id)) return;
      setProjectId(id);
      setChosenVersionId(null);
      setSelectedStageId(null);
      window.dispatchEvent(new CustomEvent('setu-project-changed', { detail: { projectId: id } }));
    },
    [projects],
  );

  const setCurrentScheduleVersionId = useCallback(
    (id: string) => {
      if (!scheduleVersions.some((v) => v.id === id)) return;
      setChosenVersionId(id);
      setSelectedStageId(null);
      window.dispatchEvent(new CustomEvent('setu-version-changed', { detail: { projectId: effectiveProjectId, versionId: id } }));
    },
    [scheduleVersions, effectiveProjectId],
  );

  const value: ProjectStateType = {
    status,
    error,
    projects,
    currentProject,
    currentScheduleVersion,
    scheduleVersions,
    setCurrentProjectId,
    setCurrentScheduleVersionId,
    role,
    permissions,
    can: (permission) => permissions.includes(permission),
    selectedStageId,
    setSelectedStageId,
    progress,
    refresh,
  };

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

/** Nullable state for shells/gates. */
export function useProjectState(): ProjectStateType {
  const context = useContext(ProjectContext);
  if (!context) throw new Error('useProjectState must be used within a ProjectProvider');
  return context;
}

/** For pages: only rendered by ProjectGate when a project AND schedule version are selected. */
export function useProject(): ProjectContextType {
  const state = useProjectState();
  if (state.status !== 'ready' || !state.currentProject || !state.currentScheduleVersion || !state.role) {
    throw new Error('useProject() was used before a project and schedule version were selected');
  }
  return state as ProjectContextType;
}
