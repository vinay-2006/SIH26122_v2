/** v2 navigation + live badges for the shared AppShell. Items are filtered by the server-derived capabilities exactly like the legacy items. */
import { Briefcase, FolderTree, LayoutDashboard, Settings2 } from 'lucide-react';
import type { Permission } from '@/api/projects';

export interface NavItem { label: string; path: string; icon: any; badge?: number; requires: Permission[] }

/** Project Manager items added to the ORIGINAL sidebar in v2 mode. The original items themselves are defined in layout/AppShell.tsx and are unchanged. */
export function useV2NavData(_enabled: boolean, _pid: string | null, _can: (p: Permission) => boolean): { items: NavItem[] } {
  const items: NavItem[] = [
    { label: 'Portfolio',        path: '/portfolio', icon: Briefcase,       requires: ['MANAGE_PROJECT'] },
    { label: 'Overview',         path: '/overview',  icon: LayoutDashboard, requires: ['MANAGE_PROJECT'] },
    { label: 'Schedule',         path: '/schedule',  icon: FolderTree,      requires: ['MANAGE_SCHEDULE'] },
    { label: 'Project Settings', path: '/settings',  icon: Settings2,       requires: ['MANAGE_PROJECT'] },
  ];
  return { items };
}
