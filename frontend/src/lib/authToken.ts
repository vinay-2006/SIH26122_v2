/**
 * The bearer token of the CURRENT backend mode. The legacy client (api/client.ts, api.ts, apiContext.ts) reads it through here so the same page code works in both
 * modes: legacy builds keep their original storage keys; v2 builds use the namespaced v2 key (config.ts), so one mode's token can never reach the other.
 */
import { IS_V2 } from '@/config';
import { v2Session } from '@/v2/session';

export function getAuthToken(): string | null {
  try {
    if (IS_V2) return v2Session.getToken();
    return localStorage.getItem('supabase_access_token') || localStorage.getItem('auth_token');
  } catch {
    return null;
  }
}

export function clearAuthToken(): void {
  try {
    if (IS_V2) { v2Session.clear(); return; }
    localStorage.removeItem('supabase_access_token');
    localStorage.removeItem('auth_token');
  } catch {
    /* storage unavailable */
  }
}
