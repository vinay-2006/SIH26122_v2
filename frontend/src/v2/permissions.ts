/**
 * v2 role/permission model for the UI.
 *
 * The SERVER decides what a person may do: GET /projects/{id} returns `my_permissions` (backend/v2/permissions.py). This module only translates those names
 * into the capability names the shared shell and route guards already use, so hiding a menu item or a button never grants anything: every action is still
 * authorised by the API.
 */
import type { Permission } from '@/api/projects';
import type { V2Role } from '@/v2/api/types';

const MAP: Record<string, Permission[]> = {
  VIEW_PROJECT: ['VIEW_PROJECT'],
  VIEW_SCHEDULE: ['VIEW_SCHEDULE'],
  MANAGE_SCHEDULE: ['MANAGE_SCHEDULE'],
  ACTIVATE_SCHEDULE: ['MANAGE_SCHEDULE'],
  MANAGE_PROJECT: ['MANAGE_PROJECT'],
  MANAGE_SETTINGS: ['MANAGE_PROJECT'],
  MANAGE_MEMBERS: ['MANAGE_PROJECT'],
  SUBMIT_CLAIM: ['CREATE_EXECUTION_EVENT'],
  VIEW_OWN_CLAIMS: ['VIEW_EXECUTION_EVENTS'],
  REVIEW_CLAIMS: ['REVIEW_CLAIM', 'APPROVE_ACTUAL'],
  REPORT_ISSUE: ['REPORT_ISSUE'],
  RESOLVE_ISSUE: ['MANAGE_BLOCKERS'],
  VIEW_AUDIT: ['VIEW_AUDIT'],
  VIEW_ROOT_CAUSES: ['VIEW_MONITORING'],
  VIEW_WBS_EXPLORER: ['VIEW_WBS_EXPLORER'],
  VIEW_AUDIT_TRAIL: ['VIEW_AUDIT_TRAIL'],
  VIEW_PROJECT_INTELLIGENCE: ['VIEW_PROJECT_INTELLIGENCE'],
  VIEW_QUALITY: ['VIEW_QUALITY'],
  REQUEST_REOPEN: ['REQUEST_REOPEN'],
  APPROVE_REOPEN: ['APPROVE_REOPEN'],
  MANAGE_QUALITY: ['MANAGE_QUALITY', 'APPROVE_QUALITY', 'WAIVE_QUALITY'],
};

export function toUiPermissions(serverPermissions: string[]): Permission[] {
  return Array.from(new Set(serverPermissions.flatMap((p) => MAP[p] ?? [])));
}

/** Where each role lands after sign-in (decided from capabilities, which came from the server). Supervisors and Site Engineers land exactly where they did in the original demo. */
export function landingForV2(can: (p: Permission) => boolean): string {
  if (can('MANAGE_PROJECT')) return '/portfolio';
  if (can('REVIEW_CLAIM')) return '/dashboard';
  if (can('CREATE_EXECUTION_EVENT')) return '/intake';
  return '/wbs';
}

export const ROLE_LABEL: Record<V2Role, string> = {
  PROJECT_MANAGER: 'Project Manager',
  SUPERVISOR: 'Supervisor',
  SITE_ENGINEER: 'Site Engineer',
};

/** Display role for the person (highest of their memberships). Informational only: authority is always per project, from the server. */
export function primaryRole(roles: V2Role[]): V2Role {
  if (roles.includes('PROJECT_MANAGER')) return 'PROJECT_MANAGER';
  if (roles.includes('SUPERVISOR')) return 'SUPERVISOR';
  return 'SITE_ENGINEER';
}
