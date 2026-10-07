/**
 * Project context for v2 mode. Provides the SAME `useProjectState()` interface the shell, project switcher, project gate and route guards already use, backed by
 * the v2 API:
 *   projects      <- GET /projects (the caller's active memberships)
 *   role / can()  <- GET /projects/{id}: `my_role` and `my_permissions` (server-authoritative; see v2/permissions.ts)
 *   versions      <- GET /projects/{id}/schedule-versions
 * A project without an active schedule is still 'ready' (a Project Manager must be able to open it to import one); pages say what is missing.
 * Which schedule version is being VIEWED (the active one by default, or a historical one) is a UI choice only: claims are always filed against the active schedule.
 */
import React, { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/auth/AuthProvider';
import { ProjectContext, type Project, type ProjectStage, type ProjectStateType, type ProjectStatus, type ScheduleVersion } from '@/context/ProjectContext';
import { progressApi, stagesApi } from '@/api/projects';
import type { ProjectListItem } from '@/api/projects';
import { projectsApi, scheduleApi } from '@/v2/api/endpoints';
import { V2Error } from '@/v2/api/http';
import type { ProjectDetail, V2Role, VersionRow } from '@/v2/api/types';
import { toUiPermissions } from '@/v2/permissions';
import { v2Session } from '@/v2/session';
import { apiContext } from '@/lib/apiContext';

export interface V2Extra {
  projectId: string;
  detail: ProjectDetail;
  role: V2Role;
  isArchived: boolean;
  /** the schedule version whose figures are on screen; undefined = the active one */
  viewVersionId: string | undefined;
  viewingHistorical: boolean;
  activeVersionId: string | null;
  /** select a project right after it was created (before the project list has refetched) */
  selectProject: (id: string) => void;
}
const V2ExtraContext = createContext<V2Extra | null>(null);

/** For v2 pages: only rendered once a project is selected (the shell's ProjectGate guarantees it). */
export function useV2Project(): V2Extra {
  const x = useContext(V2ExtraContext);
  if (!x) throw new Error('useV2Project() used before a project was selected');
  return x;
}

function toVersion(v: VersionRow): ScheduleVersion {
  return {
    id: v.version_id, versionNumber: `v${v.version_no}`, name: `v${v.version_no}${v.baseline_name ? ` · ${v.baseline_name}` : ''}`,
    status: v.status === 'ACTIVE' ? 'ACTIVE' : 'ARCHIVED', isCurrent: v.status === 'ACTIVE', isImmutable: v.status !== 'ACTIVE',
    effectiveDate: v.data_date ?? '', sourceType: v.kind, activitiesCount: v.activities, dependenciesCount: -1, supersedes: v.parent_version_id,
  };
}

export function ProjectProviderV2({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, user } = useAuth();
  const qc = useQueryClient();
  const [chosen, setChosen] = useState<string | null>(() => v2Session.getProject());
  const [viewVersion, setViewVersion] = useState<string | null>(null);
  const [selectedStageId, setSelectedStageId] = useState<string | null>(null);

  const projectsQ = useQuery({ queryKey: ['v2', 'projects', user?.id], queryFn: ({ signal }) => projectsApi.list({ signal }), enabled: isAuthenticated, retry: false, staleTime: 30_000 });
  const rows = projectsQ.data ?? [];
  const projectId = useMemo(() => (rows.length ? (rows.some((p) => p.project_id === chosen) ? chosen! : rows[0].project_id) : null), [rows, chosen]);
  useEffect(() => { if (projectId) v2Session.setProject(projectId); }, [projectId]);

  const detailQ = useQuery({ queryKey: ['v2', 'project', projectId], queryFn: ({ signal }) => projectsApi.get(projectId!, { signal }), enabled: !!projectId, retry: false });
  const versionsQ = useQuery({ queryKey: ['v2', 'versions', projectId], queryFn: ({ signal }) => scheduleApi.versions(projectId!, { signal }), enabled: !!projectId, retry: false });

  const list: ProjectListItem[] = useMemo(() => rows.map((p) => ({
    project_id: p.project_id, project_code: p.project_code, project_name: p.project_name, status: p.lifecycle_status, assigned_role: p.my_role,
  })), [rows]);

  // only versions that can be VIEWED: the active one and historical (superseded) ones; drafts / validated ones live on the Schedule page
  const versions = useMemo(() => (versionsQ.data ?? []).filter((v) => v.status === 'ACTIVE' || v.status === 'SUPERSEDED').map(toVersion), [versionsQ.data]);
  const active = versions.find((v) => v.isCurrent) ?? null;
  const viewing = versions.find((v) => v.id === viewVersion) ?? active;

  // the legacy pages call the legacy API contract (served by the v2 server): it needs the explicit project + schedule-version context on every request
  const viewingId = (versionsQ.data ?? []).find((v) => v.version_id === viewVersion && (v.status === 'ACTIVE' || v.status === 'SUPERSEDED'))?.version_id
    ?? (versionsQ.data ?? []).find((v) => v.status === 'ACTIVE')?.version_id ?? null;
  useLayoutEffect(() => {                         // before any child effect fires a request
    apiContext.set(projectId ?? null, viewingId);
    return () => apiContext.clear();
  }, [projectId, viewingId]);

  // authoritative progress + stages of the viewed schedule version, exactly as the original project context loads them (served from the v2 ledgers)
  const readyForProgress = !!projectId && !!viewingId;
  const progressQ = useQuery({
    queryKey: ['v2', 'progress', projectId, viewingId],
    queryFn: async () => {
      const [breakdown, stages] = await Promise.all([progressApi.breakdown(projectId!, viewingId!), stagesApi.list(projectId!, viewingId!)]);
      return { breakdown, stages };
    },
    enabled: readyForProgress, retry: false,
  });
  useEffect(() => {
    const refreshProgress = () => { qc.invalidateQueries({ queryKey: ['v2'] }); };
    const events = ['setu:activity-progress-changed', 'setu:activity-state-changed', 'setu:reopen-changed'];
    events.forEach((e) => window.addEventListener(e, refreshProgress));
    return () => events.forEach((e) => window.removeEventListener(e, refreshProgress));
  }, [qc]);
  const progress = progressQ.data?.breakdown ?? null;

  const detail = detailQ.data;
  const currentProject: Project | null = useMemo(() => {
    const row = rows.find((p) => p.project_id === projectId);
    if (!row) return null;
    const byId = new Map((progress?.stages ?? []).map((x) => [x.stage_id, x]));
    const stageList: ProjectStage[] = [...(progressQ.data?.stages ?? [])].sort((a, b) => a.sequence_order - b.sequence_order).map((x) => {
      const pr = byId.get(x.stage_id);
      const total = pr?.activity_count ?? 0;
      const status = x.status === 'BLOCKED' || x.status === 'ON_HOLD' ? x.status : total > 0 && (pr?.completed_count ?? 0) >= total ? 'COMPLETED' : (pr?.progress_pct ?? 0) > 0 ? 'IN_PROGRESS' : x.status;
      return { id: x.stage_id, stageNumber: x.sequence_order, code: x.stage_code, name: x.stage_name, status, weightPct: x.weight_pct, actualPct: pr?.progress_pct ?? 0, activitiesCount: pr?.activity_count ?? 0, completedCount: pr?.completed_count ?? 0, plannedStart: x.planned_start, plannedFinish: x.planned_finish };
    });
    return {
      id: row.project_id, code: row.project_code, name: row.project_name, status: row.lifecycle_status, role: row.my_role, region: detail?.location ?? row.location,
      client: detail?.client_name ?? null, description: detail?.description ?? null, scheduleVersions: versions, stages: stageList, totalStages: stageList.length, overallActual: progress?.overall_progress_pct ?? 0,
      activeStage: stageList.find((x) => x.status === 'IN_PROGRESS')?.name ?? null,
    };
  }, [rows, projectId, detail, versions, progressQ.data, progress]);

  let status: ProjectStatus;
  let errorText: string | null = null;
  const firstError = [projectsQ.error, detailQ.error].find(Boolean) as unknown;
  if (!isAuthenticated) status = 'unauthenticated';
  else if (projectsQ.isPending) status = 'loading';
  else if (firstError) { status = 'error'; errorText = firstError instanceof V2Error ? firstError.message : 'Could not load your projects.'; }
  else if (!rows.length) status = 'no-projects';
  else if (detailQ.isPending || versionsQ.isPending || !currentProject) status = 'loading';
  // a project without an active schedule: a Project Manager still opens it (to import one); everyone else is told what is missing
  else if (!active) status = detail?.my_role === 'PROJECT_MANAGER' ? 'ready' : 'needs-schedule';
  else if (progressQ.isPending && readyForProgress) status = 'loading';
  else status = 'ready';

  const permissions = useMemo(() => toUiPermissions(detail?.my_permissions ?? []), [detail]);
  const refresh = useCallback(() => { qc.invalidateQueries({ queryKey: ['v2'] }); }, [qc]);

  const setCurrentProjectId = useCallback((id: string) => {
    if (!rows.some((p) => p.project_id === id)) return;
    setChosen(id); setViewVersion(null); setSelectedStageId(null);
  }, [rows]);
  const setCurrentScheduleVersionId = useCallback((id: string) => {
    if (versions.some((v) => v.id === id)) setViewVersion(id);
  }, [versions]);

  const state: ProjectStateType = {
    status, error: errorText, projects: list, currentProject, currentScheduleVersion: viewing, scheduleVersions: versions,
    setCurrentProjectId, setCurrentScheduleVersionId, role: detail?.my_role ?? currentProject?.role ?? null, permissions,
    can: (p) => permissions.includes(p), selectedStageId, setSelectedStageId, progress, refresh,
  };

  const extra: V2Extra | null = detail && projectId ? {
    projectId, detail, role: detail.my_role, isArchived: detail.record_status === 'ARCHIVED',
    viewVersionId: viewVersion && active && viewVersion !== active.id ? viewVersion : undefined,
    viewingHistorical: !!(viewVersion && active && viewVersion !== active.id), activeVersionId: active?.id ?? detail.active_version?.version_id ?? null,
    selectProject: (id) => { setChosen(id); setViewVersion(null); setSelectedStageId(null); },
  } : null;

  return (
    <ProjectContext.Provider value={state}>
      <V2ExtraContext.Provider value={extra}>{children}</V2ExtraContext.Provider>
    </ProjectContext.Provider>
  );
}
