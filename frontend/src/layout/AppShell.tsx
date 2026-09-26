import React, { useState, useEffect } from 'react';
import { Outlet, Navigate, useLocation, Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/auth/AuthProvider';
import { useTheme } from '@/theme/ThemeProvider';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import { digestApi } from '@/api';
import {
  LayoutDashboard,
  ClipboardList,
  Layers,
  Clock,
  Activity,
  LogOut,
  ChevronLeft,
  ChevronRight,
  Menu,
  X,
  PlusCircle,
  HardHat,
  ShieldCheck,
  Sun,
  Moon,
  Flame,
  FolderTree,
  Sparkles,
  Bot,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import GlobalIndustrialBackground from '@/components/GlobalIndustrialBackground';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { IS_MOCK_MODE } from '@/api';
import { ProjectSwitcher } from '@/components/ProjectSwitcher';

const SIDEBAR_COLLAPSED_KEY = 'setu_sidebar_collapsed_v1';

export default function AppShell() {
  const { user, isAuthenticated, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();

  const [isCollapsed, setIsCollapsed] = useState<boolean>(() => {
    return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true';
  });
  const [isMobileOpen, setIsMobileOpen] = useState(false);

  useEffect(() => {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(isCollapsed));
  }, [isCollapsed]);

  if (!isAuthenticated || !user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  const isSupervisor = user.role === 'SUPERVISOR';

  // Live pending/actionable count for the sidebar badges (ISS-19). Claims with
  // status REVIEW_REQUIRED or VALIDATED are the ones a Supervisor still needs
  // to act on -- same definition Dashboard.tsx's KPI card uses. A 60s refetch
  // interval keeps this current without polling aggressively.
  const { data: pendingClaims } = useQuery({
    queryKey: ['sidebar-pending-count'],
    queryFn: () => digestApi.getAll(),
    enabled: isSupervisor,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const pendingCount = (pendingClaims || []).filter(
    (c) => c.status === 'REVIEW_REQUIRED' || c.status === 'VALIDATED'
  ).length;

  const supervisorNavItems = [
    { label: t('nav.dailyDigest'),     path: '/digest',    icon: ClipboardList, badge: pendingCount },
    { label: t('nav.reviewWorkspace'), path: '/review',    icon: Layers, badge: pendingCount },
    { label: t('nav.timeAgent', { defaultValue: 'Time Agent' }), path: '/time-agent', icon: Bot },
    { label: t('nav.dashboard'),       path: '/dashboard', icon: LayoutDashboard },
    { label: t('nav.activityHistory'), path: '/history',   icon: Clock },
    { label: t('nav.impactPreview'),   path: '/impact',    icon: Activity },
    { label: t('wbs.navLabel'),        path: '/wbs',       icon: FolderTree },
    { label: t('nav.executionSummary'), path: '/summary',  icon: Sparkles },
  ];

  const siteEngineerNavItems = [
    { label: t('nav.claimIntake'), path: '/intake', icon: PlusCircle, badge: 0 },
    { label: t('nav.timeAgent', { defaultValue: 'Time Agent' }), path: '/time-agent', icon: Bot },
  ];

  const navItems = isSupervisor ? supervisorNavItems : siteEngineerNavItems;

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const userInitials = isSupervisor ? 'SP' : 'SE';

  return (
    <div className="min-h-screen flex text-foreground font-sans antialiased selection:bg-[#FF7A18] selection:text-white transition-colors duration-200 relative bg-background">
      {/* Reusable Global Industrial Refinery Background + Readability Overlays */}
      <GlobalIndustrialBackground variant="ambient" />

      {/* Mobile Backdrop */}
      {isMobileOpen && (
        <div
          className="fixed inset-0 bg-black/70 backdrop-blur-xs z-40 lg:hidden"
          onClick={() => setIsMobileOpen(false)}
        />
      )}

      {/* ── LEFT SIDEBAR ─────────────────────────────────────────────────────── */}
      <aside
        className={cn(
          'fixed lg:sticky top-0 left-0 h-screen z-50 flex flex-col shrink-0 transition-all duration-300 ease-in-out shadow-2xl backdrop-blur-md',
          'bg-gradient-to-b from-[#001D5E] via-[#002675] to-[#00164A] border-r border-white/10',
          'dark:from-[#061526]/98 dark:via-[#071B2D]/95 dark:to-[#061526]/98 dark:border-r dark:border-[#1E3A5F]',
          isCollapsed ? 'w-[72px]' : 'w-64',
          isMobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
        )}
      >
        {/* Sidebar Header — Brand */}
        <div
          className={cn(
            'flex shrink-0 border-b transition-all duration-300',
            isCollapsed
              ? 'px-2 py-3 flex-col items-center gap-2'
              : 'p-4 flex-row items-center justify-between'
          )}
          style={{ borderColor: 'rgba(30, 58, 95, 0.5)' }}
        >
          <div className={cn('flex items-center overflow-hidden', isCollapsed ? 'justify-center' : 'gap-3')}>
            {/* Setu AI Orange Flame Icon in gradient circle */}
            <div
              className="w-10 h-10 rounded-full flex items-center justify-center shrink-0 shadow-lg shadow-orange-500/30 bg-gradient-to-br from-[#FF7A18] to-[#FF941F]"
            >
              <Flame className="w-5 h-5 text-white" />
            </div>
            {!isCollapsed && (
              <div className="flex flex-col min-w-0">
                <span className="font-bold text-base text-white leading-tight truncate tracking-tight">
                  Setu <span className="text-[#FF941F]">AI</span>
                </span>
                <span className="text-[10px] font-semibold tracking-wider truncate text-[#94A8B8]">
                  SIH26122 · Oil India
                </span>
              </div>
            )}
          </div>

          {/* Desktop Collapse Toggle */}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setIsCollapsed(!isCollapsed)}
            className={cn(
              'hidden lg:flex rounded-lg text-white/70 hover:text-white hover:bg-white/10 shrink-0',
              isCollapsed ? 'h-7 w-7' : 'h-8 w-8'
            )}
            title={isCollapsed ? t('nav.expandSidebar') : t('nav.collapseSidebar')}
            aria-label={isCollapsed ? t('nav.expandSidebar') : t('nav.collapseSidebar')}
          >
            {isCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
          </Button>

          {/* Mobile Close Button */}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setIsMobileOpen(false)}
            className="lg:hidden text-white/70 hover:text-white h-8 w-8"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </Button>
        </div>

        {/* Navigation Items */}
        <div className="flex-1 py-4 px-3 space-y-1.5 overflow-y-auto overflow-x-hidden">
          {navItems.map((item) => {
            const isActive =
              location.pathname === item.path ||
              (item.path !== '/' && location.pathname.startsWith(item.path));
            const Icon = item.icon;
            return (
              <Link
                key={item.path}
                to={item.path}
                onClick={() => setIsMobileOpen(false)}
                className={cn(
                  'flex items-center gap-3 px-3.5 py-2.5 rounded-xl font-medium text-sm transition-all group relative',
                  isActive
                    ? 'bg-gradient-to-r from-[#FF7A18] to-[#FF941F] text-white shadow-md shadow-orange-500/35 font-bold'
                    : 'text-[#D8E2EA] hover:text-white hover:bg-white/10 dark:hover:bg-[#0A2340]/80'
                )}
                title={isCollapsed ? item.label : undefined}
              >
                <Icon
                  className={cn(
                    'w-5 h-5 shrink-0 transition-transform group-hover:scale-105',
                    isActive ? 'text-white' : 'text-[#94A8B8] group-hover:text-white'
                  )}
                />
                {!isCollapsed && (
                  <span className="whitespace-nowrap flex-1">{item.label}</span>
                )}
                {!!item.badge && (
                  <span
                    className={cn(
                      'flex items-center justify-center rounded-full text-[10px] font-bold leading-none shrink-0',
                      isCollapsed
                        ? 'absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1'
                        : 'min-w-[20px] h-5 px-1.5',
                      isActive ? 'bg-white text-[#FF7A18]' : 'bg-[#FF7A18] text-white'
                    )}
                    aria-label={`${item.badge} pending`}
                  >
                    {item.badge > 99 ? '99+' : item.badge}
                  </span>
                )}
                {/* Collapsed tooltip / active pip */}
                {isCollapsed && isActive && (
                  <span
                    className="absolute right-0 top-1/2 -translate-y-1/2 w-1.5 h-6 rounded-l-full bg-white shadow-xs"
                  />
                )}
              </Link>
            );
          })}
        </div>

        {/* Sidebar Footer — User & Logout (Pinned to Viewport Bottom) */}
        <div
          className="p-3 shrink-0 border-t bg-[#001438]/90 dark:bg-[#061526]/95 backdrop-blur-md"
          style={{ borderColor: 'rgba(30, 58, 95, 0.6)' }}
        >
          <div className={cn('flex items-center gap-2', isCollapsed ? 'justify-center' : 'justify-between')}>
            {!isCollapsed && (
              <div className="flex items-center gap-2.5 overflow-hidden">
                <div
                  className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 border border-[#1E3A5F] bg-[#0A2340]"
                >
                  {isSupervisor
                    ? <ShieldCheck className="w-4 h-4 text-[#14B8A6]" />
                    : <HardHat className="w-4 h-4 text-[#FF8A25]" />}
                </div>
                <div className="flex flex-col min-w-0">
                  <span className="text-xs font-semibold text-[#F5F7FA] truncate">
                    {user.full_name || (isSupervisor ? 'Supervisor' : 'Site Engineer')}
                  </span>
                  <span className="text-[10px] font-medium truncate flex items-center gap-1 text-[#94A8B8]">
                    <span
                      className="w-1.5 h-1.5 rounded-full"
                      style={{ backgroundColor: isSupervisor ? '#14B8A6' : '#FF8A25' }}
                    />
                    {user.role}
                  </span>
                </div>
              </div>
            )}

            <Button
              variant="ghost"
              size="icon"
              onClick={handleLogout}
              className="text-[#94A8B8] hover:text-rose-300 hover:bg-rose-500/20 h-8 w-8 rounded-lg shrink-0 focus-visible:ring-2 focus-visible:ring-rose-400 cursor-pointer"
              title={t('common.logout')}
              aria-label={t('common.logout')}
            >
              <LogOut className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </aside>

      {/* ── MAIN CONTENT AREA ────────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 min-h-screen overflow-x-hidden relative z-10">
        {/* Top Header Bar */}
        <header className="h-16 px-4 lg:px-8 flex items-center justify-between sticky top-0 z-30 shadow-xs bg-white/85 dark:bg-[#071B2D]/85 backdrop-blur-md border-b border-slate-200/60 dark:border-[#1E3A5F] transition-colors duration-200">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0">
            {/* Mobile hamburger */}
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsMobileOpen(true)}
              className="lg:hidden text-foreground hover:bg-card-subtle shrink-0"
            >
              <Menu className="w-5 h-5" />
            </Button>

            {/* Global Project Switcher */}
            <ProjectSwitcher />
          </div>

          <div className="flex items-center gap-3">
            {IS_MOCK_MODE && (
              <div
                id="mock-mode-indicator"
                className="flex items-center gap-1 px-2.5 py-1 rounded-full bg-amber-500/20 border border-amber-400 text-amber-300 text-xs font-bold font-mono tracking-wide"
                title="Mock mode active (VITE_USE_MOCKS=true)"
              >
                <span>⚠️ DEMO / MOCK DATA MODE</span>
              </div>
            )}
            {/* Language Toggle */}
            <LanguageSwitcher variant="adaptive" />

            {/* Theme Toggle */}
            <Button
              variant="outline"
              size="sm"
              onClick={toggleTheme}
              className="h-8 px-3 gap-1.5 text-xs rounded-xl border border-slate-300 dark:border-[#1E3A5F] bg-white/95 dark:bg-[#0A2340] hover:bg-[#EEF5FC] dark:hover:bg-[#0B2D4A] text-foreground shadow-2xs font-semibold"
              title={theme === 'dark' ? t('common.switchToLight') : t('common.switchToDark')}
            >
              {theme === 'dark'
                ? <><Sun className="w-3.5 h-3.5 text-amber-400" /><span className="hidden sm:inline">{t('common.lightMode')}</span></>
                : <><Moon className="w-3.5 h-3.5 text-[#0284C7]" /><span className="hidden sm:inline">{t('common.darkMode')}</span></>
              }
            </Button>

            {/* Human-in-the-Loop Badge */}
            <div className="px-3 py-1 rounded-full text-[11px] font-mono font-semibold flex items-center gap-1.5 border border-[#FF7A18]/40 bg-[#FF7A18]/10 text-[#FF7A18] dark:text-[#FF941F] shadow-2xs">
              <span className="w-1.5 h-1.5 rounded-full bg-[#FF7A18] animate-pulse" />
              <span className="hidden sm:inline">{t('common.humanInTheLoop')}</span>
              <span className="sm:hidden">{t('common.hil')}</span>
            </div>

            {/* User Initial Avatar */}
            <div
              className="w-8 h-8 rounded-full bg-gradient-to-br from-[#14B8A6] to-[#0D9488] text-white font-bold text-xs flex items-center justify-center shadow-xs shrink-0"
              title={user.full_name || user.role}
            >
              {userInitials}
            </div>
          </div>
        </header>

        {/* Main Content Viewport */}
        <main className="flex-1 p-4 sm:p-6 lg:p-8 max-w-7xl w-full mx-auto">
          {/* Keeps the sidebar/header alive if a page crashes; resets on navigation. */}
          <ErrorBoundary label="This page" resetKey={location.pathname + location.search}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
