/**
 * Which backend this build talks to. Chosen explicitly at build / dev-server start, never inferred at runtime:
 *   VITE_BACKEND unset or "legacy"  ->  the original V7 demo backend (default; behaviour unchanged)
 *   VITE_BACKEND="v2"               ->  the v2 API (backend/v2) with the Project Manager / Site Engineer / Supervisor roles
 * The two modes use different token keys and different API clients, so project ids, tokens and records of one can never reach the other.
 */
export type BackendMode = 'legacy' | 'v2';

export const BACKEND_MODE: BackendMode = import.meta.env.VITE_BACKEND === 'v2' ? 'v2' : 'legacy';
export const IS_V2 = BACKEND_MODE === 'v2';

/** v2 API origin (no trailing slash). Only read in v2 mode. */
export const V2_BASE_URL = ((import.meta.env.VITE_V2_API_BASE_URL as string | undefined) || 'http://127.0.0.1:8020').replace(/\/+$/, '');

/** Show the local-sign-in shortcuts (the seeded demo people) on the login page. Needs the server to enable local sign-in as well. */
export const V2_LOCAL_LOGIN_HINTS = import.meta.env.VITE_V2_LOCAL_LOGIN === 'true';
