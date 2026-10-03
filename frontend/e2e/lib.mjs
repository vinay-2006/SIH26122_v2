// Browser end-to-end helpers: they drive the REAL frontend (v2 mode) against the REAL v2 API and a throw-away local database.
import { chromium } from 'playwright-core';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const FRONTEND = path.resolve(HERE, '..');
export const REPO = path.resolve(FRONTEND, '..');
export const ART = path.join(HERE, 'artifacts');
export const CFG = {
  db: process.env.E2E_DB || 'setuai_v2_fe_e2e',
  apiPort: Number(process.env.E2E_API_PORT || 8021),
  webPort: Number(process.env.E2E_WEB_PORT || 5191),
  legacyPort: Number(process.env.E2E_LEGACY_PORT || 5192),
};
export const WEB = `http://127.0.0.1:${CFG.webPort}`;
export const API = `http://127.0.0.1:${CFG.apiPort}/api/v2`;
export const DOMAIN = '@seed.setuai.local';

export function findChromium() {
  if (process.env.E2E_CHROMIUM && fs.existsSync(process.env.E2E_CHROMIUM)) return process.env.E2E_CHROMIUM;
  const roots = [path.join(os.homedir(), 'Library/Caches/ms-playwright'), path.join(os.homedir(), '.cache/ms-playwright')];
  for (const r of roots) {
    if (!fs.existsSync(r)) continue;
    for (const d of fs.readdirSync(r)) {
      if (!d.startsWith('chromium')) continue;
      const stack = [path.join(r, d)];
      while (stack.length) {
        const p = stack.pop();
        let st; try { st = fs.statSync(p); } catch { continue; }
        if (st.isDirectory()) { for (const c of fs.readdirSync(p)) stack.push(path.join(p, c)); }
        else if (['chrome-headless-shell', 'headless_shell', 'Chromium', 'chrome'].includes(path.basename(p)) && (st.mode & 0o111)) return p;
      }
    }
  }
  return null;
}

export function secrets() {
  const f = path.join(REPO, '.local', 'v2_dev.env');
  if (!fs.existsSync(f)) return {};
  return Object.fromEntries(fs.readFileSync(f, 'utf8').split('\n').filter(Boolean).map((l) => { const i = l.indexOf('='); return [l.slice(0, i), l.slice(i + 1)]; }));
}

function sh(cmd, args, env = {}) {
  const r = spawnSync(cmd, args, { cwd: REPO, env: { ...process.env, ...env }, encoding: 'utf8' });
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(' ')} failed:\n${r.stdout}\n${r.stderr}`);
  return r.stdout;
}

const children = [];
export async function startStack() {
  const env = { DB_V2_NAME: CFG.db, V2_API_PORT: String(CFG.apiPort), V2_CORS_ORIGINS: `${WEB},http://localhost:${CFG.webPort}` };
  sh('bash', ['scripts/v2_dev_stack.sh', 'reset'], env);                      // empties ONLY this throw-away local database, seeds it, starts the API
  const web = spawn('npx', ['vite', '--mode', 'v2', '--host', '127.0.0.1', '--port', String(CFG.webPort), '--strictPort'], { cwd: FRONTEND, env: { ...process.env, VITE_V2_API_BASE_URL: `http://127.0.0.1:${CFG.apiPort}` }, stdio: 'ignore' });
  const legacy = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', String(CFG.legacyPort), '--strictPort'], { cwd: FRONTEND, env: { ...process.env, VITE_API_BASE_URL: 'http://127.0.0.1:9' }, stdio: 'ignore' });
  children.push(web, legacy);
  for (const [url] of [[`${WEB}/login`], [`http://127.0.0.1:${CFG.legacyPort}/login`]]) {
    let ok = false;
    for (let i = 0; i < 80 && !ok; i++) { try { ok = (await fetch(url)).ok; } catch { /* starting */ } if (!ok) await new Promise((r) => setTimeout(r, 250)); }
    if (!ok) throw new Error(`web server did not start: ${url}`);
  }
}
export function stopStack() {
  children.forEach((c) => { try { c.kill('SIGTERM'); } catch { /* gone */ } });
  try { sh('bash', ['scripts/v2_dev_stack.sh', 'down'], { DB_V2_NAME: CFG.db, V2_API_PORT: String(CFG.apiPort) }); } catch { /* already down */ }
}

export async function launch() {
  const exe = findChromium();
  if (!exe) throw new Error('no Chromium found. Set E2E_CHROMIUM to a chrome-headless-shell / Chromium binary (npx playwright-core install chromium)');
  return chromium.launch({ executablePath: exe });
}

/** a signed-in page for a seeded person (real local sign-in through the UI) */
export async function signIn(browser, handle, { viewport = { width: 1440, height: 900 } } = {}) {
  const pw = secrets().V2_LOCAL_LOGIN_PASSWORD;
  if (!pw) throw new Error('V2_LOCAL_LOGIN_PASSWORD not found in .local/v2_dev.env');
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const log = { errors: [], requests: [] };
  page.on('pageerror', (e) => log.errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) log.errors.push(`console: ${m.text().slice(0, 200)}`); });
  page.on('request', (r) => { if (!r.url().startsWith(WEB) && !r.url().startsWith('data:') && !r.url().startsWith('blob:')) log.requests.push(r.url()); });
  await page.goto(`${WEB}/login`);
  await page.locator('input[type=email]').fill(`${handle}${DOMAIN}`);
  await page.locator('input[type=password]').fill(pw);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForFunction(() => !location.pathname.includes('login') && location.pathname !== '/', null, { timeout: 20000 });   // wait for the landing redirect to settle
  await page.waitForSelector('aside', { timeout: 20000 });
  return { ctx, page, log, handle };
}

export const token = (page) => page.evaluate(() => localStorage.getItem('setu_v2_access_token'));

/** call the API with this person's real token (from the page), to check what the SERVER says independent of the UI */
export async function api(page, method, p, body) {
  const t = await token(page);
  const init = { method, headers: { Authorization: `Bearer ${t}`, ...(body ? { 'Content-Type': 'application/json' } : {}) } };
  if (body) init.body = JSON.stringify(body);
  const r = await fetch(`${API}${p}`, init);
  let json = null; try { json = await r.json(); } catch { /* no body */ }
  return { status: r.status, json };
}

/** call the ORIGINAL (legacy-contract) API the restored pages use, as this person, for the given project (+ optional schedule version) */
export async function legacy(page, method, p, body, { project, version, form } = {}) {
  const t = await token(page);
  const headers = { Authorization: `Bearer ${t}`, ...(project ? { 'X-Project-ID': project } : {}), ...(version ? { 'X-Schedule-ID': version } : {}) };
  const init = { method, headers };
  if (form) init.body = form; else if (body) { headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  const r = await fetch(`http://127.0.0.1:${CFG.apiPort}${p}`, init);
  let json = null; try { json = await r.json(); } catch { /* no body */ }
  return { status: r.status, json };
}

export async function activeVersion(page, pid) {
  const r = await api(page, 'GET', `/projects/${pid}/schedule-versions`);
  const v = (r.json?.items ?? r.json ?? []).find((x) => x.status === 'ACTIVE');
  return v?.version_id;
}

export async function projectId(page, code) {
  const r = await api(page, 'GET', '/projects');
  const p = (r.json || []).find((x) => x.project_code === code);
  if (!p) throw new Error(`project ${code} not visible to this person`);
  return p.project_id;
}

export async function selectProject(page, name) {
  await page.getByTestId('project-switcher').click();
  await page.getByRole('option').filter({ hasText: name }).first().click();
  await page.waitForTimeout(400);
}

export const navLabels = async (page) => (await page.locator('aside a').allInnerTexts()).map((t) => t.replace(/\s+\d+\+?$/, '').trim());

// ---------------------------------------------------------------- tiny test runner
export class Fail extends Error {}
export const ok = (cond, msg) => { if (!cond) throw new Fail(msg); };
export const eq = (a, b, msg) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Fail(`${msg}: expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`); };
export async function until(fn, msg, ms = 10000) {
  const end = Date.now() + ms;
  let last;
  while (Date.now() < end) { try { const v = await fn(); if (v) return v; } catch (e) { last = e; } await new Promise((r) => setTimeout(r, 150)); }
  throw new Fail(`timed out: ${msg}${last ? ` (${last.message.split('\n')[0]})` : ''}`);
}
export async function shot(page, name) { fs.mkdirSync(ART, { recursive: true }); await page.screenshot({ path: path.join(ART, `${name}.png`) }); }
export const text = async (page, testid) => (await page.getByTestId(testid).first().innerText()).replace(/\s+/g, ' ').trim();
export const tmpFile = (name, content) => { const d = fs.mkdtempSync(path.join(os.tmpdir(), 'setu-e2e-')); const f = path.join(d, name); fs.writeFileSync(f, content); return f; };
export const fixture = (name) => path.join(REPO, 'tests', 'schedule_import', 'fixtures', name);
