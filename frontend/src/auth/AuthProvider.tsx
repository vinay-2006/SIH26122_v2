import React, { createContext, useContext, useState, useEffect } from 'react';
import { supabase, isSupabaseConfigured } from '@/lib/supabase';
import { authApi } from '@/api';
import { BASE_URL } from '@/api/client';

export type UserRole = 'SITE_ENGINEER' | 'SUPERVISOR' | 'PROJECT_MANAGER';

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  /** v2 only: platform grants from the server (e.g. CREATE_PROJECT). Never trusted from the browser. */
  capabilities?: string[];
}

export interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  loading: boolean;
  isLoading: boolean;
  error: string | null;
  devMode: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  clearError: () => void;
}

const TOKEN_KEY = 'supabase_access_token';
const DEV_EMAIL_KEY = 'setu_dev_email_v1';

// Local dev-mode fallback, used only when VITE_SUPABASE_URL/ANON_KEY aren't
// configured (see lib/supabase.ts). These ids are the same fixed
// TEST_SITE_ENGINEER_ID/TEST_SUPERVISOR_ID backend/shared/seed.py seeds
// into the real `profiles` table for local/test environments -- login here
// mints an unsigned dev token carrying one of these as its `sub` claim and
// hands it to the real backend, which resolves the actual role from that
// profiles row via shared/auth.py's explicit AUTH_DEV_MODE fallback. This
// is not a UI-only fake: it round-trips through the real backend and its
// real database, so a misconfigured/missing profile genuinely fails.
const DEV_MODE_ACCOUNTS: Record<string, { password: string; id: string }> = {
  'site.engineer@sih26122.internal': { password: 'Demo123456!', id: '811a1e0f-976d-42ea-a37f-1096186daf36' },
  'supervisor@sih26122.internal': { password: 'Demo123456!', id: '4b8e6901-de81-490c-8bec-9761f62bee70' },
  'engineer@setu.ai': { password: 'Demo123456!', id: '811a1e0f-976d-42ea-a37f-1096186daf36' },
  'planner@setu.ai': { password: 'Demo123456!', id: '4b8e6901-de81-490c-8bec-9761f62bee70' },
};

function base64UrlEncode(obj: unknown): string {
  const json = JSON.stringify(obj);
  const b64 = btoa(unescape(encodeURIComponent(json)));
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// An unsigned JWT-shaped token: three dot-separated segments so PyJWT's
// jwt.decode(..., options={"verify_signature": False}) on the backend can
// parse it structurally. It carries no real signature -- backend/shared/
// auth.py only accepts this path when AUTH_DEV_MODE=true AND no real
// Supabase verification config is present; every other path (JWKS, HMAC
// secret, live Supabase HTTP check) rejects it outright.
function mintDevToken(userId: string): string {
  const header = base64UrlEncode({ alg: 'none', typ: 'JWT' });
  const payload = base64UrlEncode({ sub: userId, iat: Math.floor(Date.now() / 1000) });
  return `${header}.${payload}.`;
}

export const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const clearError = () => setError(null);

  // Resolves the authenticated role/profile from the REAL backend --
  // never trusted from anything set client-side, in either auth mode.
  const hydrateFromBackend = async (email: string): Promise<User> => {
    const profile = await authApi.getMe(email);
    const resolvedUser: User = {
      id: profile.id,
      email: email || profile.email,
      full_name: profile.full_name || (profile.role === 'SUPERVISOR' ? 'Supervisor' : 'Site Engineer'),
      role: profile.role,
    };
    localStorage.setItem('user', JSON.stringify(resolvedUser));
    return resolvedUser;
  };

  useEffect(() => {
    let cancelled = false;

    const restoreSession = async () => {
      try {
        if (isSupabaseConfigured && supabase) {
          const { data } = await supabase.auth.getSession();
          const session = data.session;
          if (session?.access_token) {
            localStorage.setItem(TOKEN_KEY, session.access_token);
            const restored = await hydrateFromBackend(session.user?.email ?? '');
            if (!cancelled) setUser(restored);
          } else {
            localStorage.removeItem(TOKEN_KEY);
            localStorage.removeItem('user');
          }
        } else {
          const token = localStorage.getItem(TOKEN_KEY);
          const email = localStorage.getItem(DEV_EMAIL_KEY);
          if (token && email) {
            const restored = await hydrateFromBackend(email);
            if (!cancelled) setUser(restored);
          } else {
            localStorage.removeItem(TOKEN_KEY);
            localStorage.removeItem(DEV_EMAIL_KEY);
            localStorage.removeItem('user');
          }
        }
      } catch {
        // Stale/invalid token -- clear it and fall through to the login screen
        // rather than getting stuck on a broken session.
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(DEV_EMAIL_KEY);
        localStorage.removeItem('user');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    restoreSession();

    // Centralized 401 handling (ISS-22): api.ts's apiFetch clears the dead
    // token and fires this event on any 401 from any page's request. Drop
    // `user` here so ProtectedRoute's existing redirect-to-/login kicks in
    // immediately, instead of leaving a signed-out user staring at a page
    // that keeps silently failing every request.
    const handleUnauthorized = () => {
      if (isSupabaseConfigured && supabase) {
        supabase.auth.signOut().catch(() => {});
      }
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(DEV_EMAIL_KEY);
      localStorage.removeItem('user');
      if (!cancelled) setUser(null);
    };
    window.addEventListener('auth:unauthorized', handleUnauthorized);

    if (isSupabaseConfigured && supabase) {
      const { data: subscription } = supabase.auth.onAuthStateChange((_event, session) => {
        if (session?.access_token) {
          localStorage.setItem(TOKEN_KEY, session.access_token);
        } else {
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem('user');
          setUser(null);
        }
      });
      return () => {
        cancelled = true;
        window.removeEventListener('auth:unauthorized', handleUnauthorized);
        subscription.subscription.unsubscribe();
      };
    }

    return () => {
      cancelled = true;
      window.removeEventListener('auth:unauthorized', handleUnauthorized);
    };
  }, []);

  const login = async (email: string, password: string): Promise<void> => {
    setError(null);
    setLoading(true);
    const emailTrimmed = email.trim().toLowerCase();

    try {
      if (isSupabaseConfigured && supabase) {
        const { data, error: authError } = await supabase.auth.signInWithPassword({
          email: emailTrimmed,
          password,
        });
        if (authError || !data.session) {
          throw new Error(authError?.message || 'Sign-in failed.');
        }
        localStorage.removeItem('user');
        localStorage.setItem(TOKEN_KEY, data.session.access_token);
        const loggedInUser = await hydrateFromBackend(data.session.user?.email ?? emailTrimmed);
        setUser(loggedInUser);
        return;
      }

      // Local integration login: a REAL signed JWT from the backend, honoured only against the isolated DB.
      if (import.meta.env.VITE_LOCAL_DEMO_AUTH === 'true') {
        const res = await fetch(`${BASE_URL}/api/v1/auth/local-login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: emailTrimmed, password }),
        });
        if (!res.ok) throw new Error(res.status === 401 ? 'Incorrect email or password.' : 'Local demo sign-in is not available.');
        const { access_token } = await res.json();
        localStorage.removeItem('user');
        localStorage.setItem(TOKEN_KEY, access_token);
        localStorage.setItem(DEV_EMAIL_KEY, emailTrimmed);
        const loggedInUser = await hydrateFromBackend(emailTrimmed);
        setUser(loggedInUser);
        return;
      }

      // Dev-mode fallback (no Supabase project configured).
      const account = DEV_MODE_ACCOUNTS[emailTrimmed];
      if (!account || account.password !== password) {
        throw new Error('Incorrect email or password.');
      }
      localStorage.removeItem('user');
      const devToken = mintDevToken(account.id);
      localStorage.setItem(TOKEN_KEY, devToken);
      localStorage.setItem(DEV_EMAIL_KEY, emailTrimmed);
      const loggedInUser = await hydrateFromBackend(emailTrimmed);
      setUser(loggedInUser);
    } catch (err: any) {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(DEV_EMAIL_KEY);
      localStorage.removeItem('user');
      const message = err?.message || 'Login failed.';
      setError(message);
      throw new Error(message);
    } finally {
      setLoading(false);
    }
  };

  const logout = async (): Promise<void> => {
    if (isSupabaseConfigured && supabase) {
      await supabase.auth.signOut();
    }
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(DEV_EMAIL_KEY);
    localStorage.removeItem('user');
    setUser(null);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        loading,
        isLoading: loading,
        error,
        devMode: !isSupabaseConfigured,
        login,
        logout,
        clearError,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
