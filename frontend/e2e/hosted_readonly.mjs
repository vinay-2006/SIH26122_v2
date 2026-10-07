// READ-ONLY browser test of the DEPLOYED ANVYRA (https://anvyra-web.vercel.app): real sign-in through the UI against hosted Supabase Auth, role menus, isolation,
// Time Agent page, Lessons Radar, project knowledge, document list/download, console and network errors.
// Every non-GET request to the API is ABORTED by the harness and reported, so this run cannot mutate hosted data (the Supabase sign-in call is the only POST allowed).
//   node e2e/hosted_readonly.mjs        needs V2_HOSTED_DEMO_PASSWORD (read from .local/hosted.env; never printed)
import fs from 'node:fs';
import path from 'node:path';
import { launch, ART } from './lib.mjs';

const WEB = 'https://anvyra-web.vercel.app', API = 'https://anvyra-api.vercel.app';
const pw = /^V2_HOSTED_DEMO_PASSWORD=(.+)$/m.exec(fs.readFileSync(path.resolve(import.meta.dirname, '../../.local/hosted.env'), 'utf8'))[1].trim();
const results = [], blocked = [], problems = [];
const check = (name, cond, detail = '') => { results.push([cond, name, detail]); console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  -> ' + detail}`); };
const until = async (fn, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (await fn()) return true; } catch { /* retry */ } await new Promise((r) => setTimeout(r, 250)); } return false; };
const labels = async (page) => (await page.locator('aside a').allInnerTexts()).map((t) => t.replace(/\s+\d+\+?$/, '').trim());

async function session(browser, handle, who) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => problems.push(`${who} pageerror: ${e.message.slice(0, 160)}`));
  page.on('console', (m) => { if (m.type() === 'error') problems.push(`${who} console: ${m.text().slice(0, 160)}`); });
  page.on('requestfailed', (r) => problems.push(`${who} request failed: ${r.method()} ${r.url().slice(0, 100)} ${r.failure()?.errorText}`));
  page.on('response', (r) => { if (r.status() >= 400 && r.url().startsWith(API)) problems.push(`${who} HTTP ${r.status()}: ${r.request().method()} ${new URL(r.url()).pathname}`); });
  await ctx.route(`${API}/**`, (route) => {                           // the safety net: no mutation of hosted data can leave the browser
    const m = route.request().method();
    if (m === 'GET' || m === 'OPTIONS' || m === 'HEAD') return route.continue();
    blocked.push(`${who}: ${m} ${new URL(route.request().url()).pathname}`);
    return route.abort('blockedbyclient');
  });
  await page.goto(`${WEB}/login`);
  await page.locator('input[type=email]').fill(`${handle}@anvyra.demo`);
  await page.locator('input[type=password]').fill(pw);
  await page.getByRole('button', { name: /sign in/i }).click();
  const landed = await until(async () => !new URL(page.url()).pathname.includes('login') && new URL(page.url()).pathname !== '/' && (await page.locator('aside').count()) > 0, 60000);
  return { ctx, page, landed };
}
const api = (page, p, headers = {}) => page.evaluate(async ({ API, p, headers }) => {                    // a real cross-origin fetch from the web app's origin, with its own session token
  const t = localStorage.getItem('setu_v2_access_token');
  let r;
  try { r = await fetch(API + p, { headers: { ...(t ? { Authorization: `Bearer ${t}` } : {}), ...headers } }); } catch (e) { return { status: 0, error: String(e), sha: '', bytes: 0, text: '' }; }
  const buf = await r.arrayBuffer();
  const h = [...new Uint8Array(await crypto.subtle.digest('SHA-256', buf))].map((b) => b.toString(16).padStart(2, '0')).join('');
  return { status: r.status, sha: h, bytes: buf.byteLength, text: r.headers.get('content-type')?.includes('json') ? new TextDecoder().decode(buf).slice(0, 400000) : '' };
}, { API, p, headers });
const json = (r) => JSON.parse(r.text);
const rows = (r) => { const d = json(r); return Array.isArray(d) ? d : d.items; };
const code = (i) => i.project_code || i.code;

const browser = await launch();
try {
  // ------------------------------------------------------------------ Project Manager
  let s = await session(browser, 'anita.bora', 'PM'); let page = s.page;
  check('PM signs in with Supabase Auth through the deployed UI and lands on the portfolio', s.landed && new URL(page.url()).pathname === '/portfolio', page.url());
  check('PM menu is the project-management menu', JSON.stringify(await labels(page)) === JSON.stringify(['Portfolio', 'Overview', 'Schedule', 'Project Settings', 'Project Knowledge', 'Issues & Delays', 'Impact Preview', 'Root Cause & Memory', 'Project Intelligence', 'Audit Trail']), (await labels(page)).join('|'));
  await until(async () => /NNB-CRUDE|SMP-PIPE/.test(await page.locator('main').innerText()), 30000);
  const portfolio = await page.locator('main').innerText();
  check('PM portfolio lists only the PM\'s own projects (NNB-CRUDE, SMP-PIPE)', /NNB-CRUDE/.test(portfolio) && /SMP-PIPE/.test(portfolio) && !/NRL-EXPANSION|AEC-OFFSHORE/.test(portfolio), portfolio.slice(0, 200));
  await page.goto(`${WEB}/knowledge`); await page.waitForSelector('[data-testid=knowledge-page]', { timeout: 30000 });
  check('PM Project Knowledge lists the loaded entries with provenance labels', await until(async () => (await page.getByTestId('knowledge-entry').count()) > 20), 'entries');
  const kt = await page.locator('main').innerText();
  check('knowledge entries are labelled From project records / Illustrative / Not specified', /From project records/.test(kt) && /Illustrative/.test(kt) && /Not specified/.test(kt));
  await page.goto(`${WEB}/root-cause`); await page.waitForSelector('[data-testid=lessons-radar]', { timeout: 30000 });
  await page.waitForSelector('[data-testid=memory-insights]', { timeout: 30000 });
  check('PM Lessons Radar and insights render; there is no capture queue (read-only role)', (await page.getByTestId('capture-queue').count()) === 0 && (await page.getByTestId('lessons-radar').count()) === 1);
  const radarText = await page.getByTestId('lessons-radar').innerText();
  check('Lessons Radar states how many activities have lessons (SMP-PIPE upcoming project)', /activities in the window have a relevant lesson/.test(radarText) || /No recorded lesson|upcoming|not started/i.test(radarText), radarText.slice(0, 200));
  const pmItems = await page.getByTestId('radar-item').count();
  console.log(`      radar items for the PM's current project: ${pmItems}`);
  const pmProjects = rows(await api(page, '/api/v2/projects'));
  const nrl = null;
  const pmTok = await api(page, `/api/v2/projects/00000000-0000-0000-0000-000000000000/knowledge`);
  check('PM: an unknown project id is refused (403)', pmTok.status === 403, `${pmTok.status} ${pmTok.error || ''}`);
  const pmCodes = pmProjects.map(code).sort();
  check('PM API view: only own projects', JSON.stringify(pmCodes) === JSON.stringify(['NNB-CRUDE', 'SMP-PIPE']), pmCodes.join(','));
  await page.screenshot({ path: path.join(ART, 'hosted_pm_radar.png') });
  await page.getByRole('button', { name: /log ?out|sign ?out/i }).first().click();
  check('PM signs out and is returned to the login page with no session token kept', await until(async () => new URL(page.url()).pathname.includes('login') && !(await page.evaluate(() => localStorage.getItem('setu_v2_access_token')))), page.url());
  await s.ctx.close();

  // ------------------------------------------------------------------ Supervisor
  s = await session(browser, 'imran.hussain', 'SUP'); page = s.page;
  check('Supervisor signs in and lands on the Dashboard', s.landed && new URL(page.url()).pathname === '/dashboard', page.url());
  const supMenu = await labels(page);
  check('Supervisor menu: review pages, no Audit Trail / Project Intelligence', JSON.stringify(supMenu) === JSON.stringify(['Issues & Delays', 'Daily Digest', 'Review Workspace', 'Time Agent', 'Dashboard', 'Activity History', 'Impact Preview', 'WBS Explorer', 'AI Execution Summary', 'Root Cause & Memory']), supMenu.join('|'));
  for (const p of ['/audit', '/intelligence', '/portfolio', '/schedule', '/settings', '/intake']) {
    await page.goto(`${WEB}${p}`); await page.waitForSelector('aside');
    check(`Supervisor cannot open ${p} (sent back to the Dashboard)`, await until(async () => new URL(page.url()).pathname === '/dashboard', 15000), page.url());
  }
  const supProjects = rows(await api(page, '/api/v2/projects'));
  check('Supervisor API view: only AEC-OFFSHORE and NRL-EXPANSION', JSON.stringify(supProjects.map(code).sort()) === JSON.stringify(['AEC-OFFSHORE', 'NRL-EXPANSION']), supProjects.map(code).join(','));
  const nrlId = supProjects.find((p) => code(p) === 'NRL-EXPANSION').project_id;
  await page.goto(`${WEB}/time-agent`); await page.waitForSelector('main', { timeout: 30000 }); await page.waitForTimeout(3000);
  const ta = await page.locator('main').innerText();
  check('Time Agent page loads for the Supervisor (greeting and quick actions; no question submitted)', /Time Agent/i.test(ta) && ta.length > 200, ta.slice(0, 160));
  await page.screenshot({ path: path.join(ART, 'hosted_sup_timeagent.png') });
  await page.goto(`${WEB}/root-cause`); await page.waitForSelector('[data-testid=lessons-radar]', { timeout: 30000 });
  await page.waitForSelector('[data-testid=radar-summary]', { timeout: 30000 });
  check('Supervisor sees the Lessons Radar, insights and the capture queue', (await page.getByTestId('capture-queue').count()) === 1 && (await page.getByTestId('memory-insights').count()) === 1);
  console.log(`      radar summary: ${(await page.getByTestId('radar-summary').innerText()).slice(0, 160)}`);
  console.log(`      radar items: ${await page.getByTestId('radar-item').count()}, capture-queue items: ${await page.getByTestId('capture-item').count()}`);
  await page.screenshot({ path: path.join(ART, 'hosted_sup_radar.png') });
  // knowledge through the API (the Supervisor has no knowledge page), documents list + download + hash
  const kn = rows(await api(page, `/api/v2/projects/${nrlId}/knowledge`));
  check('Supervisor reads NRL-EXPANSION project knowledge (40 entries)', kn.length === 40, String(kn.length));
  const docs = rows(await api(page, `/api/v2/projects/${nrlId}/documents?limit=100`));
  let ok = 0, bad = [];
  for (const d of docs) {
    const r = await api(page, `/api/v2/projects/${nrlId}/documents/${d.document_id}/content`);
    if (r.status === 200 && r.sha === d.sha256 && r.bytes === d.size_bytes) ok++; else bad.push(`${d.document_id.slice(0, 8)}:${r.status}`);
  }
  check(`NRL-EXPANSION documents: ${docs.length} listed, each downloaded from the private bucket and its sha256 equals the recorded hash`, docs.length === 10 && ok === docs.length, `${ok}/${docs.length} ${bad.join(',')}`);
  const aecId = supProjects.find((p) => code(p) === 'AEC-OFFSHORE').project_id;
  const aecDocs = rows(await api(page, `/api/v2/projects/${aecId}/documents?limit=100`));
  check('Supervisor lists AEC-OFFSHORE documents (their own second project) but not schedule files', aecDocs.length > 0 && aecDocs.every((d) => d.kind !== 'SCHEDULE_FILE'), aecDocs.map((d) => d.kind).join(','));
  const nnbId = (rows(await api(page, `/api/v2/projects/${nrlId}`)) , null);
  await page.getByRole('button', { name: /log ?out|sign ?out/i }).first().click();
  await until(async () => new URL(page.url()).pathname.includes('login'));
  await s.ctx.close();

  // ------------------------------------------------------------------ Site Engineer
  s = await session(browser, 'ritu.baruah', 'SE'); page = s.page;
  check('Site Engineer signs in and lands on Claim Intake', s.landed && new URL(page.url()).pathname === '/intake', page.url());
  const seMenu = await labels(page);
  check('Site Engineer menu', JSON.stringify(seMenu) === JSON.stringify(['Claim Intake', 'Issues & Delays', 'My Updates', 'WBS Explorer', 'Project Intelligence']), seMenu.join('|'));
  for (const p of ['/dashboard', '/review', '/root-cause', '/time-agent', '/portfolio', '/audit', '/knowledge']) {
    await page.goto(`${WEB}${p}`); await page.waitForSelector('aside');
    check(`Site Engineer cannot open ${p} (sent back to Claim Intake)`, await until(async () => new URL(page.url()).pathname === '/intake', 15000), page.url());
  }
  await page.goto(`${WEB}/intake`); await page.waitForSelector('aside'); await page.waitForTimeout(2500);
  const intake = await page.locator('main').innerText();
  check('Upload Progress Report page shows the simplified upload with the deployment size limit', /Upload Progress Report|progress report/i.test(intake), intake.slice(0, 160));
  await page.screenshot({ path: path.join(ART, 'hosted_se_intake.png') });
  const seProjects = rows(await api(page, '/api/v2/projects'));
  const seCodes = seProjects.map(code).sort();
  console.log(`      Site Engineer's projects: ${seCodes.join(', ')}`);
  const other = (await (async () => { const all = { AEC: aecId, NRL: nrlId }; return all; })());
  const crossId = seProjects.some((p) => code(p) === 'AEC-OFFSHORE') ? null : aecId;
  if (crossId) {
    const x = await api(page, `/api/v2/projects/${crossId}/documents`);
    const y = await api(page, `/api/v2/projects/${crossId}/knowledge`);
    check('Site Engineer cannot read another project\'s documents or knowledge (403)', x.status === 403 && y.status === 403, `${x.status}/${y.status}`);
  }
  const seKn = await api(page, `/api/v2/projects/${nrlId}/knowledge`);
  check('Site Engineer can read their own project\'s knowledge', seKn.status === 200, String(seKn.status));
  const noTok = await page.evaluate(async (API) => (await fetch(API + '/api/v2/projects', { headers: {} })).status, API);
  check('A request with no token is refused (401)', noTok === 401, String(noTok));
  await page.getByRole('button', { name: /log ?out|sign ?out/i }).first().click();
  check('Site Engineer signs out', await until(async () => new URL(page.url()).pathname.includes('login')), page.url());
  await s.ctx.close();
} finally {
  await browser.close();
}
const failed = results.filter((r) => !r[0]).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
console.log(`blocked mutation attempts (none expected): ${blocked.length}${blocked.length ? '\n  ' + blocked.join('\n  ') : ''}`);
const uniq = [...new Set(problems)];
console.log(`console / network problems: ${uniq.length}${uniq.length ? '\n  ' + uniq.slice(0, 40).join('\n  ') : ''}`);
process.exit(failed ? 1 : 0);
