/**
 * Authentication for v2 mode. Supplies the SAME context the rest of the app already uses (`useAuth()`), so the login page, shell and route guards are shared.
 *
 *  - Sign-in: Supabase Auth when configured (the v2 API verifies Supabase tokens), otherwise the server's LOCAL sign-in (POST /auth/local-login), which the operator
 *    must enable and which only works against a local database. No secret, key or default password lives in this code.
 *  - Who you are and what you may do come ONLY from the server (GET /me, GET /projects/{id}). The browser stores a token and nothing else: editing local storage
 *    or the URL cannot make anyone a Project Manager, because the API checks the token and the project membership on every request.
 *  - A 401 anywhere ends the session (the shared 'auth:unauthorized' event).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AuthContext, type AuthContextType, type User } from '@/auth/AuthProvider';
import { isSupabaseConfigured, supabase } from '@/lib/supabase';
import { authApi } from '@/v2/api/endpoints';
import { V2Error } from '@/v2/api/http';
import { primaryRole } from '@/v2/permissions';
import { v2Session } from '@/v2/session';

function toUser(me: Awaited<ReturnType<typeof authApi.me>>): User {
  return {
    id: me.id, email: me.email, full_name: me.full_name || me.email,
    role: primaryRole(me.projects.map((p) => p.my_role)), capabilities: me.capabilities,
  };
}

export function AuthProviderV2({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const hydrate = useCallback(async (): Promise<User> => toUser(await authApi.me()), []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (isSupabaseConfigured && supabase) {
          const { data } = await supabase.auth.getSession();
          if (data.session?.access_token) v2Session.setToken(data.session.access_token);
          else v2Session.clear();
        }
        if (v2Session.getToken()) {
          const u = await hydrate();
          if (!cancelled) setUser(u);
        }
      } catch {
        v2Session.clear();              // stale / revoked token: fall through to the login page
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    const onUnauthorized = () => {
      v2Session.clear();
      if (isSupabaseConfigured && supabase) supabase.auth.signOut().catch(() => {});
      if (!cancelled) setUser(null);
    };
    window.addEventListener('auth:unauthorized', onUnauthorized);
    let sub: { unsubscribe: () => void } | null = null;
    if (isSupabaseConfigured && supabase) {
      sub = supabase.auth.onAuthStateChange((_e, session) => {
        if (session?.access_token) v2Session.setToken(session.access_token);
      }).data.subscription;
    }
    return () => { cancelled = true; window.removeEventListener('auth:unauthorized', onUnauthorized); sub?.unsubscribe(); };
  }, [hydrate]);

  const login = useCallback(async (email: string, password: string) => {
    setError(null);
    setLoading(true);
    const e = email.trim().toLowerCase();
    try {
      if (isSupabaseConfigured && supabase) {
        const { data, error: err } = await supabase.auth.signInWithPassword({ email: e, password });
        if (err || !data.session) throw new Error(err?.message || 'Sign-in failed.');
        v2Session.setToken(data.session.access_token);
      } else {
        const r = await authApi.localLogin(e, password);
        v2Session.setToken(r.access_token);
      }
      setUser(await hydrate());
    } catch (err: any) {
      v2Session.clear();
      const message = err instanceof V2Error ? err.message : err?.message || 'Login failed.';
      setError(message);
      throw new Error(message);
    } finally {
      setLoading(false);
    }
  }, [hydrate]);

  const logout = useCallback(async () => {
    if (isSupabaseConfigured && supabase) await supabase.auth.signOut().catch(() => {});
    v2Session.clear();
    setUser(null);
  }, []);

  const value: AuthContextType = useMemo(() => ({
    user, isAuthenticated: !!user, loading, isLoading: loading, error, devMode: false,
    login, logout, clearError: () => setError(null),
  }), [user, loading, error, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
