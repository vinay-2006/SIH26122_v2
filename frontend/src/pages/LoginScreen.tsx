import { BRAND_NAME } from '@/brand';
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/auth/AuthProvider';
import { useTheme } from '@/theme/ThemeProvider';
import { Navigate, useLocation } from 'react-router-dom';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import {
  Lock,
  Mail,
  Eye,
  EyeOff,
  ShieldCheck,
  HardHat,
  AlertCircle,
  ArrowRight,
  Sun,
  Moon,
  Flame,
  Droplets,
} from 'lucide-react';

import GlobalIndustrialBackground from '@/components/GlobalIndustrialBackground';
import { IS_V2, V2_DEMO_EMAIL_DOMAIN, V2_DEMO_PASSWORD, V2_LOCAL_LOGIN_HINTS } from '@/config';

// Local integration demo (isolated DB only): the two prototype identities provisioned by backend/prototype_seed. Enabled by
// VITE_LOCAL_DEMO_AUTH=true; the backend refuses local-login anywhere except the isolated integration database.
const LOCAL_DEMO_AUTH = import.meta.env.VITE_LOCAL_DEMO_AUTH === 'true';
const LOCAL_IDENTITIES = [
  { label: 'Supervisor', email: 'supervisor@setuai.demo' },
  { label: 'Site Engineer', email: 'engineer@setuai.demo' },
];

// v2 mode: shortcuts for the demo team of NRL-EXPANSION (the richest demo project). They fill the e-mail, and the password only when the build sets VITE_V2_DEMO_PASSWORD
// (see config.ts). Shown only when VITE_V2_LOCAL_LOGIN=true (the default of vite.config.ts).
const V2_IDENTITIES = [
  { label: 'Project Manager', handle: 'farah.khan', Icon: ShieldCheck, color: '#0284C7' },
  { label: 'Supervisor', handle: 'imran.hussain', Icon: ShieldCheck, color: '#0D9488' },
  { label: 'Site Engineer', handle: 'ritu.baruah', Icon: HardHat, color: '#EA580C' },
].map((i) => ({ ...i, email: `${i.handle}@${V2_DEMO_EMAIL_DOMAIN}` }));

export default function LoginScreen() {
  const { login, isAuthenticated, user, error, clearError, isLoading } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { t } = useTranslation();
  const location = useLocation();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [fieldError, setFieldError] = useState('');

  if (isAuthenticated && user) {
    const defaultRoute = IS_V2 ? '/' : user.role === 'SUPERVISOR' ? '/digest' : '/intake';
    const fromLocation = (location.state as any)?.from;
    const fromPath = fromLocation?.pathname;
    const fullFrom = fromLocation ? `${fromLocation.pathname}${fromLocation.search || ''}` : null;
    let targetRoute = defaultRoute;
    if (IS_V2) {
      // v2: every route guard decides from the SERVER's project permissions, so return to where the person was headed (or the landing page)
      targetRoute = fullFrom && fromPath !== '/login' ? fullFrom : '/';
    } else if (fromPath && fromPath !== '/' && fromPath !== '/login') {
      if (user.role === 'SITE_ENGINEER' && fromPath === '/intake') {
        targetRoute = fullFrom || '/intake';
      } else if (user.role === 'SUPERVISOR' && fromPath !== '/intake') {
        targetRoute = fullFrom || fromPath;
      }
    }
    return <Navigate to={targetRoute} replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFieldError('');
    clearError();

    if (!email.trim()) { setFieldError(t('login.errEmailRequired')); return; }
    if (!email.includes('@')) { setFieldError(t('login.errEmailInvalid')); return; }
    if (!password.trim()) { setFieldError(t('login.errPasswordRequired')); return; }

    try {
      await login(email.trim(), password);
    } catch {
      // Error state handled in AuthContext
    }
  };

  const fillDemo = (demoEmail: string) => {
    setEmail(demoEmail);
    setPassword(IS_V2 ? V2_DEMO_PASSWORD : 'Demo123456!');
    setFieldError('');
    clearError();
  };

  return (
    <div
      className="min-h-screen flex font-sans antialiased selection:bg-[#FF7A18] selection:text-white transition-colors duration-200 relative overflow-hidden bg-background"
    >
      {/* Global Industrial Background */}
      <GlobalIndustrialBackground variant="hero" />

      {/* ── LEFT PANEL — Industrial Refinery Hero Visual (Directly over Background, No Containers) ── */}
      <div
        className="hidden lg:flex lg:w-[48%] flex-col justify-between p-12 relative overflow-hidden z-10"
      >
        {/* Top Brand & Hero Content — directly over background */}
        <div className="relative z-10">
          <div className="flex items-center gap-3.5 mb-8">
            <div
              className="w-12 h-12 rounded-full flex items-center justify-center shadow-xl shadow-orange-500/30 bg-gradient-to-br from-[#FF7A18] to-[#FF941F]"
            >
              <Flame className="w-6 h-6 text-white" />
            </div>
            <div>
              <div className="font-extrabold text-2xl text-[#071A2D] dark:text-white tracking-tight">
                {BRAND_NAME}
              </div>
              <div className="text-xs font-bold text-[#334155] dark:text-[#94A8B8]">
                {t('login.tagline')}
              </div>
            </div>
          </div>

          <h2 className="text-3xl font-extrabold text-[#071A2D] dark:text-white leading-snug mt-10 tracking-tight">
            {t('login.heroTitleLine1')}<br />
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-[#FF7A18] to-[#FF941F]">{t('login.heroTitleLine2')}</span><br />
            {t('login.heroTitleLine3')}
          </h2>

          <p className="text-sm mt-4 text-[#1E293B] dark:text-[#D8E2EA] leading-relaxed max-w-md font-semibold">
            {t('login.heroDescription')}
          </p>
        </div>

        {/* Feature Highlights — directly over background */}
        <div className="relative z-10 space-y-3.5">
          {[
            { icon: ShieldCheck, label: t('login.featureHitl'), color: '#0D9488' },
            { icon: Droplets, label: t('login.featureP6'), color: '#0284C7' },
            { icon: Flame, label: t('login.featureOcr'), color: '#EA580C' },
          ].map(({ icon: Icon, label, color }) => (
            <div key={label} className="flex items-center gap-3">
              <div
                className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 border shadow-2xs bg-white/85 dark:bg-[#0A2340]/80 border-slate-300/80 dark:border-[#1E3A5F]"
              >
                <Icon className="w-4 h-4" style={{ color }} />
              </div>
              <span className="text-sm text-[#071A2D] dark:text-[#F5F7FA] font-bold">{label}</span>
            </div>
          ))}
          <p className="text-xs pt-3 text-[#334155] dark:text-[#94A8B8] font-semibold">
            {t('login.footerTagline')}
          </p>
        </div>
      </div>

      {/* ── RIGHT PANEL — Floating Login Card ───────────────────────────────── */}
      <div className="flex-1 flex flex-col items-center justify-center p-6 lg:p-12 relative z-10">

        {/* Language & Theme Toggle — top right */}
        <div className="absolute top-4 right-4 flex items-center gap-2">
          <LanguageSwitcher variant="adaptive" />

          <Button
            variant="outline"
            size="sm"
            onClick={toggleTheme}
            className="border-slate-300 dark:border-[#1E3A5F] text-slate-800 dark:text-[#F5F7FA] h-9 px-3 gap-2 text-xs rounded-xl shadow-xs bg-white/90 dark:bg-[#0A2340]/90 hover:bg-[#EEF5FC] dark:hover:bg-[#0B2D4A] font-semibold transition-colors"
          >
            {theme === 'dark'
              ? <><Sun className="w-4 h-4 text-amber-400" />{t('common.lightMode')}</>
              : <><Moon className="w-4 h-4 text-[#0284C7]" />{t('common.darkMode')}</>
            }
          </Button>
        </div>

        {/* Mobile brand badge */}
        <div className="flex lg:hidden items-center gap-2 mb-8">
          <div
            className="w-10 h-10 rounded-full flex items-center justify-center bg-gradient-to-br from-[#FF7A18] to-[#FF941F] shadow-md shadow-orange-500/30"
          >
            <Flame className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="font-bold text-xl tracking-tight text-slate-900 dark:text-white">
              {BRAND_NAME}
            </div>
            <div className="text-[10px] text-slate-500 dark:text-[#94A8B8] font-medium">{t('common.brandTagline')}</div>
          </div>
        </div>

        {/* Login Card — Floating Elevated Surface */}
        <div
          className="w-full max-w-md rounded-2xl overflow-hidden bg-white/98 dark:bg-[#071B2D]/95 border border-slate-300 dark:border-[#1E3A5F] shadow-2xl backdrop-blur-md transition-all"
        >
          {/* Card header stripe */}
          <div
            className="px-8 py-5 flex items-center justify-between bg-gradient-to-r from-[#002875] to-[#003893] dark:from-[#061526] dark:to-[#071B2D] border-b border-slate-200 dark:border-[#1E3A5F]/40 text-white"
          >
            <div>
              <h1 className="text-lg font-bold text-white tracking-tight">{t('login.signInTitle')}</h1>
              <p className="text-xs mt-0.5 text-white/80">
                {t('login.signInSubtitle')}
              </p>
            </div>
            <div
              className="w-10 h-10 rounded-full flex items-center justify-center bg-gradient-to-br from-[#FF7A18] to-[#FF941F] shadow-md shadow-orange-500/30 shrink-0"
            >
              <Flame className="w-5 h-5 text-white" />
            </div>
          </div>

          {/* Orange accent stripe */}
          <div className="h-1 bg-gradient-to-r from-[#FF7A18] via-[#FF941F] to-[#14B8A6]" />

          {/* Form body */}
          <div className="px-8 py-7 space-y-5">
            {/* Error banner */}
            {(error || fieldError) && (
              <div className="p-3.5 rounded-xl flex items-start gap-2.5 text-xs bg-rose-50 dark:bg-rose-950/50 border border-rose-200 dark:border-rose-900 text-rose-700 dark:text-rose-300 font-semibold">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <span>{fieldError || error}</span>
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              {/* Email */}
              <div className="space-y-1.5">
                <Label className="text-xs font-bold text-[#071A2D] dark:text-[#D8E2EA]">
                  {t('login.emailLabel')}
                </Label>
                <div className="relative">
                  <Mail className="w-4 h-4 absolute left-3 top-3.5 text-slate-500 dark:text-[#94A8B8]" />
                  <Input
                    type="email"
                    placeholder={t('login.emailPlaceholder')}
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="pl-9 text-sm h-11 rounded-xl bg-[#F8FAFC] dark:bg-[#0A2340] border-slate-300 dark:border-[#1E3A5F] text-[#071A2D] dark:text-[#F5F7FA] placeholder:text-slate-400 dark:placeholder:text-[#94A8B8]/60 font-medium focus-visible:ring-[#FF7A18]"
                  />
                </div>
              </div>

              {/* Password */}
              <div className="space-y-1.5">
                <Label className="text-xs font-bold text-[#071A2D] dark:text-[#D8E2EA]">
                  {t('login.passwordLabel')}
                </Label>
                <div className="relative">
                  <Lock className="w-4 h-4 absolute left-3 top-3.5 text-slate-500 dark:text-[#94A8B8]" />
                  <Input
                    type={showPassword ? 'text' : 'password'}
                    placeholder="••••••••"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="pl-9 pr-10 text-sm h-11 rounded-xl bg-[#F8FAFC] dark:bg-[#0A2340] border-slate-300 dark:border-[#1E3A5F] text-[#071A2D] dark:text-[#F5F7FA] placeholder:text-slate-400 dark:placeholder:text-[#94A8B8]/60 font-medium focus-visible:ring-[#FF7A18]"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-3.5 text-slate-500 hover:text-slate-800 dark:text-[#94A8B8] dark:hover:text-white transition-colors"
                  >
                    {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              {/* Submit Button */}
              <Button
                type="submit"
                disabled={isLoading}
                isLoading={isLoading}
                className="w-full text-white font-bold h-11 shadow-md shadow-orange-500/30 flex items-center justify-center gap-2 rounded-xl text-sm cursor-pointer bg-gradient-to-r from-[#FF7A18] to-[#FF941F] hover:from-[#E06810] hover:to-[#FF7A18]"
              >
                <span>{t('login.signIn')}</span>
                <ArrowRight className="w-4 h-4" />
              </Button>
            </form>

            {/* Demo Credentials — Level 3 Nested Surfaces */}
            {(!IS_V2 || V2_LOCAL_LOGIN_HINTS) && <div className="pt-2 space-y-3">
              <div className="flex items-center gap-3">
                <div className="flex-1 h-px bg-slate-300 dark:bg-[#1E3A5F]" />
                <span className="text-[11px] font-bold uppercase tracking-wider text-[#334155] dark:text-[#94A8B8]">
                  {t('login.demoAccess')}
                </span>
                <div className="flex-1 h-px bg-slate-300 dark:bg-[#1E3A5F]" />
              </div>
              <div className={IS_V2 ? 'grid grid-cols-3 gap-2.5' : 'grid grid-cols-2 gap-2.5'}>
                {IS_V2 ? V2_IDENTITIES.map(({ label, email: e, Icon, color }) => (
                      <button
                        key={e}
                        type="button"
                        onClick={() => fillDemo(e)}
                        data-testid={`identity-${e.split('@')[0]}`}
                        className="p-3 rounded-xl text-left transition-all border bg-[#F1F5F9] dark:bg-[#0A2340] border-slate-300 dark:border-[#1E3A5F] hover:border-[#FF7A18] dark:hover:border-[#FF7A18] hover:bg-white dark:hover:bg-[#0B2D4A] shadow-2xs group cursor-pointer"
                      >
                        <div className="flex items-center gap-1.5 text-xs font-bold text-[#071A2D] dark:text-[#F5F7FA] group-hover:text-[#FF7A18] transition-colors">
                          <Icon className="w-4 h-4" style={{ color }} />
                          {label}
                        </div>
                        <div className="text-[10px] font-mono mt-0.5 truncate text-[#334155] dark:text-[#94A8B8] font-semibold">{e.split('@')[0]}</div>
                      </button>
                    ))
                  : LOCAL_DEMO_AUTH
                  ? LOCAL_IDENTITIES.map((id) => (
                      <button
                        key={id.email}
                        type="button"
                        onClick={() => fillDemo(id.email)}
                        className="p-3 rounded-xl text-left transition-all border bg-[#F1F5F9] dark:bg-[#0A2340] border-slate-300 dark:border-[#1E3A5F] hover:border-[#FF7A18] dark:hover:border-[#FF7A18] hover:bg-white dark:hover:bg-[#0B2D4A] shadow-2xs group cursor-pointer"
                      >
                        <div className="flex items-center gap-1.5 text-xs font-bold text-[#071A2D] dark:text-[#F5F7FA] group-hover:text-[#FF7A18] transition-colors">
                          <ShieldCheck className="w-4 h-4 text-[#0D9488]" />
                          {id.label}
                        </div>
                        <div className="text-[10px] font-mono mt-0.5 truncate text-[#334155] dark:text-[#94A8B8] font-semibold">{id.email}</div>
                      </button>
                    ))
                  : (
                    <>
                <button
                  type="button"
                  onClick={() => fillDemo('supervisor@sih26122.internal')}
                  className="p-3 rounded-xl text-left transition-all border bg-[#F1F5F9] dark:bg-[#0A2340] border-slate-300 dark:border-[#1E3A5F] hover:border-[#FF7A18] dark:hover:border-[#FF7A18] hover:bg-white dark:hover:bg-[#0B2D4A] shadow-2xs group cursor-pointer"
                >
                  <div className="flex items-center gap-1.5 text-xs font-bold text-[#071A2D] dark:text-[#F5F7FA] group-hover:text-[#FF7A18] transition-colors">
                    <ShieldCheck className="w-4 h-4 text-[#0D9488]" />
                    {t('login.supervisor')}
                  </div>
                  <div className="text-[10px] font-mono mt-0.5 truncate text-[#334155] dark:text-[#94A8B8] font-semibold">supervisor@sih26122.internal</div>
                </button>

                <button
                  type="button"
                  onClick={() => fillDemo('site.engineer@sih26122.internal')}
                  className="p-3 rounded-xl text-left transition-all border bg-[#F1F5F9] dark:bg-[#0A2340] border-slate-300 dark:border-[#1E3A5F] hover:border-[#FF7A18] dark:hover:border-[#FF7A18] hover:bg-white dark:hover:bg-[#0B2D4A] shadow-2xs group cursor-pointer"
                >
                  <div className="flex items-center gap-1.5 text-xs font-bold text-[#071A2D] dark:text-[#F5F7FA] group-hover:text-[#FF7A18] transition-colors">
                    <HardHat className="w-4 h-4 text-[#EA580C]" />
                    {t('login.siteEngineer')}
                  </div>
                  <div className="text-[10px] font-mono mt-0.5 truncate text-[#334155] dark:text-[#94A8B8] font-semibold">site.engineer@sih26122.internal</div>
                </button>
                    </>
                  )}
              </div>
            </div>}
          </div>
        </div>

        <p className="text-center text-xs mt-6 text-[#334155] dark:text-[#94A8B8] font-semibold">
          {t('login.footerTagline')}
        </p>
      </div>
    </div>
  );
}
