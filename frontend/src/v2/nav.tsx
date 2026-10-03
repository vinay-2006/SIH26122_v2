/** v2 navigation + live badges for the shared AppShell. Items are filtered by the server-derived capabilities exactly like the legacy items. */
import { useQuery } from '@tanstack/react-query';
import { AlertOctagon, BellRing, Briefcase, ClipboardList, Fingerprint, FolderTree, Layers, LayoutDashboard, PlusCircle, Settings2 } from 'lucide-react';
import type { Permission } from '@/api/projects';
import { claimsApi, dashboardApi } from '@/v2/api/endpoints';

export interface NavItem { label: string; path: string; icon: any; badge?: number; requires: Permission[] }

/** pending counts for the badges; only asked when v2 mode is active, a project is selected and the role may see them */
export function useV2NavData(enabled: boolean, pid: string | null, can: (p: Permission) => boolean): { items: NavItem[] } {
  const reviewer = can('REVIEW_CLAIM');
  const counts = useQuery({
    queryKey: ['v2', 'nav-counts', pid], queryFn: () => claimsApi.counts(pid!), enabled: enabled && reviewer && !!pid, staleTime: 20_000, refetchInterval: 60_000, retry: false,
  });
  const unread = useQuery({
    queryKey: ['v2', 'nav-unread', pid], queryFn: () => dashboardApi.notifications(pid!, { unread_only: true, limit: 100 }), enabled: enabled && !!pid, staleTime: 20_000, refetchInterval: 45_000, retry: false,
  });
  const decidable = counts.data ? counts.data.EXTRACTED + counts.data.MATCHED + counts.data.VALIDATED + counts.data.DISPUTED : 0;
  const unreadCount = unread.data?.items.length ?? 0;
  const items: NavItem[] = [
    { label: 'Portfolio',        path: '/portfolio',     icon: Briefcase,       requires: ['MANAGE_PROJECT'] },
    { label: 'Overview',         path: '/overview',      icon: LayoutDashboard, requires: ['VIEW_PROJECT'] },
    { label: 'Schedule',         path: '/schedule',      icon: FolderTree,      requires: ['MANAGE_SCHEDULE'] },
    { label: 'WBS & Activities', path: '/activities',    icon: Layers,          requires: ['VIEW_SCHEDULE'] },
    { label: 'Submit Claim',     path: '/claims/new',    icon: PlusCircle,      requires: ['CREATE_EXECUTION_EVENT'] },
    { label: 'My Claims',        path: '/claims/mine',   icon: ClipboardList,   requires: ['CREATE_EXECUTION_EVENT'] },
    { label: 'Review Queue',     path: '/review',        icon: ClipboardList,   badge: decidable, requires: ['REVIEW_CLAIM'] },
    { label: 'Issues & Delays',  path: '/issues',        icon: AlertOctagon,    requires: ['VIEW_PROJECT'] },
    { label: 'Notifications',    path: '/notifications', icon: BellRing,        badge: unreadCount, requires: ['VIEW_PROJECT'] },
    { label: 'Audit Trail',      path: '/audit',         icon: Fingerprint,     requires: ['VIEW_AUDIT'] },
    { label: 'Project Settings', path: '/settings',      icon: Settings2,       requires: ['MANAGE_PROJECT'] },
  ];
  return { items };
}
