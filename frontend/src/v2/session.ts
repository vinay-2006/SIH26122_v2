/** v2 session storage. Keys are namespaced so a v2 token can never be read by (or leak into) the legacy client, and vice versa. */
export const V2_TOKEN_KEY = 'setu_v2_access_token';
export const V2_PROJECT_KEY = 'setu_v2_selected_project';

const safe = <T,>(fn: () => T, fallback: T): T => {
  try { return fn(); } catch { return fallback; }
};

export const v2Session = {
  getToken: (): string | null => safe(() => localStorage.getItem(V2_TOKEN_KEY), null),
  setToken: (t: string) => safe(() => localStorage.setItem(V2_TOKEN_KEY, t), undefined),
  clear: () => safe(() => { localStorage.removeItem(V2_TOKEN_KEY); localStorage.removeItem(V2_PROJECT_KEY); }, undefined),
  getProject: (): string | null => safe(() => localStorage.getItem(V2_PROJECT_KEY), null),
  setProject: (id: string) => safe(() => localStorage.setItem(V2_PROJECT_KEY, id), undefined),
};
