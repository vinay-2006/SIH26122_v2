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

/** Largest upload the deployment accepts, in bytes (0 = no deployment ceiling). Vercel refuses request bodies above 4.5 MB before the API sees them, so a Vercel build sets VITE_MAX_UPLOAD_MB=4. */
export const MAX_UPLOAD_BYTES = Math.max(0, Number(import.meta.env.VITE_MAX_UPLOAD_MB || 0)) * 1024 * 1024;
export const uploadCeilingProblem = (file: { size: number }): string | null =>
  MAX_UPLOAD_BYTES && file.size > MAX_UPLOAD_BYTES ? `This file is ${(file.size / 1048576).toFixed(1)} MB; this server accepts uploads up to ${MAX_UPLOAD_BYTES / 1048576} MB. Reduce the file size and try again.` : null;

/** Domain of the seeded demo people (their e-mail is <handle>@<domain>). */
export const V2_DEMO_EMAIL_DOMAIN = ((import.meta.env.VITE_V2_DEMO_DOMAIN as string | undefined) || 'anvyra.demo').trim();
/** OPTIONAL demo convenience: when a build sets VITE_V2_DEMO_PASSWORD, the demo role buttons also fill the password. A VITE_* value is compiled into the public JavaScript, so set it
 *  only for a demo window on data that is fictional, and rotate the password afterwards. Unset (the default): the password is never prefilled. */
export const V2_DEMO_PASSWORD = ((import.meta.env.VITE_V2_DEMO_PASSWORD as string | undefined) || '').trim();
