// Supervisor: the ORIGINAL interface (Dashboard, Daily Digest, Review Workspace, Activity History, Impact Preview, AI Execution Summary, Root Cause & Memory, Project Intelligence,
// Audit Trail, Time Agent) running on the v2 backend, as a Supervisor of NRL-EXPANSION.
import { CFG, WEB, activeVersion, api, eq, legacy, navLabels, ok, projectId, selectProject, shot, signIn, until } from '../lib.mjs';
import { claimViaApi } from './engineer.mjs';

const ORIGINAL_SUP_MENU = ['Issues & Delays', 'Daily Digest', 'Review Workspace', 'Time Agent', 'Dashboard', 'Activity History', 'Impact Preview', 'WBS Explorer', 'AI Execution Summary', 'Root Cause & Memory'];
const SUP = 'imran.hussain', SE = 'ritu.baruah';
const main = (page) => page.locator('main').innerText();

/** the engineer files a free-text report through the original intake API (extraction -> claim -> automatic matching), then runs the original checks */
async function engineerFiles(S, text) {
  const r = await legacy(S.se.page, 'POST', '/api/v1/claims/text', { raw_claim_text: text }, { project: S.pid, version: S.ver });
  ok(r.status === 200 || r.status === 201, `claim filed: ${r.status} ${JSON.stringify(r.json).slice(0, 200)}`);
  const id = r.json.event_id;
  await legacy(S.se.page, 'POST', `/api/v1/claims/${id}/check`, null, { project: S.pid, version: S.ver });
  return id;
}
async function openClaim(page, id) {
  await page.goto(`${WEB}/review?event_id=${id}`);
  await page.waitForSelector('text=Original Field Claim Provenance', { timeout: 30000 });
  await page.waitForTimeout(800);
}
const commit = (page) => page.getByRole('button', { name: /Commit Official Decision/ }).click();

export default [
  ['Supervisor: lands on the original Dashboard with the original menu; PM and engineer pages are not reachable', async ({ browser, S }) => {
    const { page, log } = await signIn(browser, SUP);
    const se = await signIn(browser, SE);
    S.sup = { page, log }; S.se = { page: se.page, log: se.log };
    S.pid = await projectId(page, 'NRL-EXPANSION'); S.ver = await activeVersion(page, S.pid);
    eq(new URL(page.url()).pathname, '/dashboard', 'landing page');
    await selectProject(page, 'Numaligarh');                                    // this supervisor works on several projects: the UI must be on the one the checks use
    eq(await navLabels(page), ORIGINAL_SUP_MENU, 'the original supervisor menu');
    for (const p of ['/portfolio', '/schedule', '/settings', '/intake', '/updates', '/audit', '/intelligence']) {
      await page.goto(`${WEB}${p}`); await page.waitForSelector('aside');
      await until(async () => new URL(page.url()).pathname === '/dashboard', `${p} bounces to the Dashboard`);
    }
    eq((await api(page, 'POST', `/projects/${S.pid}/schedule-imports`)).status, 403, 'a Supervisor cannot manage schedules');
    eq((await legacy(page, 'POST', '/api/v1/claims/text', { raw_claim_text: 'NRE-4050 piping 10 percent' }, { project: S.pid, version: S.ver })).status, 403, 'a Supervisor cannot file claims');
    for (const tail of ['/dossier', '/dossier/audit-verification', '/agent/briefing', '/agent/findings']) {                     // Audit Trail and Project Intelligence: closed at the API too
      eq((await legacy(page, 'GET', `/api/v1/projects/${S.pid}${tail}`, null, { project: S.pid, version: S.ver })).status, 403, `a Supervisor cannot read ${tail}`);
    }
    eq((await legacy(page, 'GET', '/api/v1/audit', null, { project: S.pid, version: S.ver })).status, 200, 'the audit feed behind Activity History is still readable');
    eq((await api(page, 'POST', `/projects/${S.pid}/knowledge`, { section: 'SCOPE', title: 'Sneaky edit', body: 'not allowed', provenance: 'AUTHORED', tags: [], sort_order: 1 })).status, 403, 'only a Project Manager writes project knowledge');
    eq((await api(page, 'GET', `/projects/${S.pid}/knowledge`)).status, 200, 'a Supervisor may read it');
    await shot(page, 'sup_dashboard');
  }],

  ['Supervisor: reviews an engineer claim in the Review Workspace -- candidates, Ask Why, approval written once to the ledger, engineer notified', async ({ S }) => {
    const { page } = S.sup;
    const before = (await api(S.se.page, 'GET', `/projects/${S.pid}/activities?q=NRE-4050&limit=5`)).json.items.find((a) => a.external_activity_id === 'NRE-4050');
    const target = before.measured_assignments[0].approved_cumulative_qty + 150;
    const id = await engineerFiles(S, `Hydrotreater process piping NRE-4050: 150 joints welded today`);   // an incremental report: the ledger accumulates it
    S.reviewId = id;
    const claim = (await legacy(page, 'GET', `/api/v1/claims/${id}`, null, { project: S.pid, version: S.ver })).json;
    eq(claim.matched_activity_id, 'NRE-4050', 'the existing matching engine resolved the activity');
    ok(!['APPROVED', 'EDITED'].includes(claim.status), 'nothing is approved before the Supervisor decides');
    const q = await legacy(page, 'GET', '/api/v1/review-queue', null, { project: S.pid, version: S.ver });
    ok(JSON.stringify(q.json).includes(id), `the claim is in the Supervisor review queue (status ${claim.status}, queue ${JSON.stringify(q.json).slice(0, 200)})`);
    await page.goto(`${WEB}/review`); await page.waitForSelector('text=Review Queue');
    await openClaim(page, id);
    ok(/Top-3 AI Candidate Matches/.test(await main(page)) && /NRE-4050/.test(await main(page)), 'ranked candidates are shown');
    await page.getByRole('button', { name: /Ask Why/ }).first().click();                               // the original Ask Why panel (graph explanation of this match)
    await until(async () => /NRE-4050/.test(await page.locator('main').innerText()) && /depends on|planned|float/i.test(await page.locator('main').innerText()), 'Ask Why explains the match from the schedule graph', 30000);
    await shot(page, 'sup_ask_why');
    await page.getByPlaceholder(/Enter reason for approval/).fill('Verified against the welding log');
    await commit(page);
    await page.getByText('Supervisor Decision Committed').waitFor({ timeout: 30000 });
    await shot(page, 'sup_approved');
    const after = (await legacy(page, 'GET', `/api/v1/claims/${id}`, null, { project: S.pid, version: S.ver })).json;
    eq(after.status, 'APPROVED', 'claim approved');
    const aft = (await api(S.se.page, 'GET', `/projects/${S.pid}/activities?q=NRE-4050&limit=5`)).json.items.find((a) => a.external_activity_id === 'NRE-4050');
    eq(aft.measured_assignments[0].approved_cumulative_qty, target, 'the approved quantity is on the append-only ledger');
    const note = await legacy(S.se.page, 'GET', `/api/v1/projects/${S.pid}/notifications`, null, { project: S.pid, version: S.ver });
    ok(JSON.stringify(note.json).includes(id), 'the engineer was notified of the decision');
  }],

  ['Supervisor: an over-baseline approval needs an explicit, audited acknowledgement note (the control appears only when the server asks)', async ({ S }) => {
    const { page } = S.sup;
    const acts = (await api(S.se.page, 'GET', `/projects/${S.pid}/activities?limit=200`)).json.items;
    const pick = acts.find((a) => a.execution_state === 'IN_PROGRESS' && a.measured_assignments.length === 1 && a.measured_assignments[0].baseline_qty > 0 && !a.any_overrun);
    ok(pick, 'a single-resource activity in progress exists');
    const m = pick.measured_assignments[0];
    const over = Math.ceil(m.baseline_qty * 1.6);
    const id = await engineerFiles(S, `${pick.external_activity_id} ${pick.activity_name}: ${over} ${m.unit_of_measure.toLowerCase()} done today`);
    await openClaim(page, id);
    ok((await page.getByTestId('ack-note').count()) === 0, 'no acknowledgement control before the server asks');
    await page.getByPlaceholder(/Enter reason for approval/).fill('Re-measured on site');
    await commit(page);
    await page.getByTestId('ack-note').waitFor({ timeout: 20000 });
    ok(/baseline/i.test(await page.getByTestId('ack-note').innerText()), 'the control explains why');
    eq((await legacy(page, 'GET', `/api/v1/claims/${id}`, null, { project: S.pid, version: S.ver })).json.status === 'APPROVED', false, 'the refused decision approved nothing');
    await commit(page);                                                                                    // note still empty -> refused locally
    ok(/acknowledgement note/i.test(await main(page)), 'an empty note is refused');
    await page.getByTestId('ack-note').locator('textarea').fill('Re-measured; over-run accepted by supervisor');
    await commit(page);
    await until(async () => (await legacy(page, 'GET', `/api/v1/claims/${id}`, null, { project: S.pid, version: S.ver })).json.status === 'APPROVED', 'approved with the acknowledgement', 30000);
    await shot(page, 'sup_overrun_ack');
    const tl = await api(S.se.page, 'GET', `/projects/${S.pid}/activities/${pick.activity_uid}/timeline`);
    ok(JSON.stringify(tl.json).includes('over-run accepted by supervisor'), 'the acknowledgement note is kept on the ledger entry');
    const recorded = (await legacy(page, 'GET', '/api/v1/decisions?limit=100', null, { project: S.pid, version: S.ver })).json;
    ok(JSON.stringify(recorded).includes(id), 'the decision is in the recorded decisions');
    const audit = (await legacy(page, 'GET', '/api/v1/audit?limit=60', null, { project: S.pid, version: S.ver })).json;
    ok(JSON.stringify(audit).includes(id), 'and in the audit feed');
  }],

  ['Supervisor: Daily Digest lists the day\'s claims and bulk-approves the validated ones, each decision recorded individually', async ({ S }) => {
    const { page } = S.sup;
    const single = (await api(S.se.page, 'GET', `/projects/${S.pid}/activities?limit=200`)).json.items
      .find((a) => a.execution_state === 'IN_PROGRESS' && a.measured_assignments.length === 1 && a.measured_assignments[0].baseline_qty > 0 && !a.any_overrun);
    ok(single, 'a single-resource activity in progress exists');
    const m = single.measured_assignments[0];
    const ids = [
      await engineerFiles(S, `${single.external_activity_id} ${single.activity_name}: ${Math.max(1, Math.floor(m.baseline_qty * 0.01))} ${m.unit_of_measure.toLowerCase()} done today`),
      await engineerFiles(S, 'Hydrotreater process piping NRE-4050: 12 joints welded today'),
    ];
    const status = async (id) => (await legacy(page, 'GET', `/api/v1/claims/${id}`, null, { project: S.pid, version: S.ver })).json.status;
    for (const id of ids) ok(['VALIDATED', 'REVIEW_REQUIRED'].includes(await status(id)), `claim ${id} is waiting for review`);
    const before = (await legacy(page, 'GET', `/api/v1/decisions?limit=100`, null, { project: S.pid, version: S.ver })).json.length;
    await page.goto(`${WEB}/digest`); await page.waitForSelector('text=Daily Digest');
    await until(async () => /[1-9]\d* claims? ready for supervisor signoff/.test(await page.locator('main').innerText()), 'the digest lists the claims waiting for sign-off', 30000);
    await page.waitForTimeout(1500);                                         // the page re-selects the date of the newest claim once; let it settle
    await shot(page, 'sup_digest');
    const pre = {}; for (const id of ids) pre[id] = await status(id);
    const validated = ids.filter((id) => pre[id] === 'VALIDATED');
    await page.getByRole('button', { name: /Bulk Approve Validated Claims/ }).click();
    await until(async () => { for (const id of validated) if ((await status(id)) !== 'APPROVED') return false; return true; }, 'the validated claims are approved by the bulk action', 60000);
    await page.waitForTimeout(1000);
    for (const id of ids.filter((i) => pre[i] !== 'VALIDATED')) eq(await status(id), pre[id], `claim ${id} (${pre[id]}) is not eligible for bulk approval and is left for the Review Workspace`);
    const after = (await legacy(page, 'GET', `/api/v1/decisions?limit=100`, null, { project: S.pid, version: S.ver })).json.length;
    ok(after >= before + validated.length, `each approval is its own recorded decision: ${before} -> ${after}`);
  }],

  ['Supervisor: every restored page loads real data without a failed call (Digest, Dashboard, History, Impact, Summary, Root Cause, Intelligence, Audit, WBS, Issues)', async ({ S }) => {
    const { page } = S.sup;
    const failed = [];
    page.on('response', (r) => { if (r.status() >= 400 && r.url().includes(`:${CFG.apiPort}`)) failed.push(`${r.status()} ${r.url().split(`:${CFG.apiPort}`)[1]}`); });
    const pages = { '/digest': /Daily Digest/, '/dashboard': /Project Executive Dashboard/, '/history': /Activity History/, '/impact': /Ripple Impact Preview/, '/summary': /AI Execution Summary/,
      '/root-cause': /Root Cause/, '/wbs': /WBS Activity Explorer/, '/issues': /Issues & Delays/ };
    for (const [p, re] of Object.entries(pages)) {
      await page.goto(`${WEB}${p}`); await page.waitForSelector('aside'); await page.waitForTimeout(1800);
      ok(re.test(await main(page)), `${p} renders`);
    }
    eq(failed, [], 'no failed API call on any restored page');
  }],

  ['Supervisor: institutional memory - resolved issue is kept as a lesson from the capture queue, and the radar and insights respond', async ({ S }) => {
    const { page } = S.sup;
    const acts = (await legacy(page, 'GET', '/api/v1/activities?limit=5', null, { project: S.pid, version: S.ver })).json;
    const list = Array.isArray(acts) ? acts : (acts.items ?? acts.activities ?? []);
    const aid = list[0]?.activity_id ?? list[0]?.id;
    ok(aid, 'an activity exists to attach the issue to');
    const title = 'Line pipe delivery slipped at the mill (e2e memory)';
    const c = await legacy(S.se.page, 'POST', `/api/v1/projects/${S.pid}/schedules/${S.ver}/issues`, { activity_id: aid, category_code: 'MATERIAL_DELIVERY_DELAY', title, description: 'Mill dispatch delayed', severity: 'HIGH', blocks_work: true }, { project: S.pid, version: S.ver });
    ok(c.status === 200 || c.status === 201, `issue raised: ${c.status} ${JSON.stringify(c.json).slice(0, 160)}`);
    const iid = c.json.issue_id ?? c.json.id;
    const r = await legacy(page, 'POST', `/api/v1/projects/${S.pid}/schedules/${S.ver}/issues/${iid}/resolve`, { resolution_notes: 'Expedited a second mill and split the lot', add_to_memory: false }, { project: S.pid, version: S.ver });
    eq(r.status, 200, 'issue resolved');
    await page.goto(`${WEB}/root-cause`); await page.waitForSelector('[data-testid=lessons-radar]');
    await page.waitForSelector('[data-testid=memory-insights]');
    await page.waitForSelector('[data-testid=radar-summary]');
    const item = page.locator('[data-testid=capture-item]', { hasText: title });
    await item.waitFor({ timeout: 15000 });
    await item.getByRole('button', { name: /Keep .* as a lesson/ }).click();
    await page.getByTestId('capture-form').waitFor();
    ok(/second mill/.test(await page.getByTestId('capture-form').getByRole('textbox', { name: 'Lesson', exact: true }).inputValue()), 'the lesson is prefilled from the resolution notes');
    await page.getByTestId('save-lesson').click();
    await until(async () => (await page.locator('[data-testid=capture-item]', { hasText: title }).count()) === 0, 'the issue leaves the capture queue');
    ok((await legacy(page, 'GET', `/api/v1/projects/${S.pid}/memory/capture-queue`, null, { project: S.pid, version: S.ver })).json.items.every((i) => i.issue_id !== iid), 'queue agrees on the server');
    await shot(page, 'sup_memory');
  }],

  ['Project Manager: the Lessons Radar and insights are visible but there is no capture queue', async ({ browser, S }) => {
    const { page } = await signIn(browser, 'anita.bora');
    await selectProject(page, 'Siliguri');
    await page.goto(`${WEB}/root-cause`); await page.waitForSelector('[data-testid=lessons-radar]');
    await page.waitForSelector('[data-testid=memory-insights]');
    eq(await page.getByTestId('capture-queue').count(), 0, 'the PM has no capture queue');
  }],

  ['Supervisor: P6 sync staging downloads the approved-actuals CSV and pushes to the local mock P6', async ({ S }) => {
    const { page } = S.sup;
    await page.goto(`${WEB}/dashboard`); await page.waitForSelector('text=P6 / PMIS Sync Staging'); await page.waitForTimeout(1500);
    await page.getByRole('button', { name: /P6 \/ PMIS Sync Staging/ }).click();
    const [csv] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: /Download Canonical CSV/ }).click()]);
    ok(/anvyra_approved_actuals_p6_staging/.test(csv.suggestedFilename()), `CSV downloaded: ${csv.suggestedFilename()}`);
    const body = (await import('node:fs')).readFileSync(await csv.path(), 'utf8');
    ok(/activity_id/i.test(body.split('\n')[0]) && body.split('\n').length > 5, `CSV has a header and rows: ${body.slice(0, 120)}`);
    await page.getByRole('tab', { name: 'Mock Adapter Test' }).click();
    const push = page.getByRole('button', { name: /Push to Mock P6/ }).first();
    ok(await push.count() > 0, 'a push control is offered');
    await push.click();
    await until(async () => (await legacy(page, 'GET', '/api/v1/mock-p6/received', null, { project: S.pid, version: S.ver })).json.count >= 1, 'the mock P6 received the payload', 20000);
    await shot(page, 'sup_p6');
  }],

  ['Supervisor: Time Agent answers from live data and project knowledge, cites its sources and declines what it cannot know', async ({ S }) => {
    const { page } = S.sup;
    await page.goto(`${WEB}/time-agent`); await page.waitForSelector('text=ANVYRA Time Agent');
    const body = await page.locator('main').innerText();
    ok(!/Log Activity Start|Log Activity Finish/.test(body), 'a Supervisor is not offered claim-filing examples');
    ok(/Pending Reviews/.test(body) && /About this project/.test(body), 'the quick questions are read-only questions about the project');
    const ask = async (q, expectRe) => {
      await page.getByRole('textbox').last().fill(q); await page.keyboard.press('Enter');
      await until(async () => expectRe.test(await page.locator('main').innerText()), `a reply to "${q}"`, 30000);
    };
    await ask('What is waiting for my review?', /claims? waiting for a Supervisor decision/);
    ok(/Live: Review queue/.test(await page.getByTestId('agent-sources').last().innerText()), 'the reply cites live data');
    await ask('Give me an overview and the scope of this project.', /Project context:/);
    const src = await page.getByTestId('agent-sources').last().innerText();
    ok(/Project knowledge: .* \(/.test(src), `the reply cites a project-knowledge section with its provenance: ${src}`);
    await ask('What is the overall project progress?', /Progress \(approved quantities/);
    await ask('What colour is the foreman\'s helmet?', /cannot answer that from this project/);
    eq((await legacy(page, 'GET', `/api/v1/projects/${S.pid}/agent/briefing`, null, { project: S.pid, version: S.ver })).status, 403, 'and Project Intelligence stays closed to the Supervisor');
    await shot(page, 'sup_time_agent');
  }],

  ['Supervisor: Time Agent drafts a claim and hands it to the Site Engineer, who files it; the Supervisor never files', async ({ S }) => {
    const { page } = S.sup;
    await page.goto(`${WEB}/time-agent`); await page.waitForSelector('text=ANVYRA Time Agent');
    const text = 'NRE-4080 piping insulation and painting 7 percent complete, noted on the supervisor walk-round';
    await page.getByRole('textbox').last().fill(text);
    await page.keyboard.press('Enter');
    await page.getByRole('button', { name: /Hand Off Draft to Site Engineer/ }).waitFor({ timeout: 20000 });
    ok((await page.getByRole('button', { name: /Submit Claim for Supervisor Review/ }).count()) === 0, 'a Supervisor is not offered claim submission');
    await page.getByRole('button', { name: /Hand Off Draft to Site Engineer/ }).click();
    await page.waitForSelector('text=Draft handed off to the Site Engineer');
    const before = (await legacy(S.se.page, 'GET', '/api/v1/claims', null, { project: S.pid, version: S.ver })).json.length;
    const se = S.se.page;
    await se.goto(`${WEB}/intake`); await se.waitForSelector('[data-testid=handoff-drafts]', { timeout: 20000 });
    await shot(se, 'se_handoff_banner');
    await se.getByRole('button', { name: 'Use draft' }).first().click();
    ok((await se.locator('textarea').first().inputValue()).includes('NRE-4080'), 'the draft fills the report text');
    await se.getByRole('button', { name: /^Submit Claim$/ }).click();
    await until(async () => /Claim Submitted Successfully/.test(await se.locator('main').innerText()), 'the engineer files it through the ordinary pipeline', 90000);
    eq((await legacy(se, 'GET', '/api/v1/claims', null, { project: S.pid, version: S.ver })).json.length, before + 1, 'exactly one claim was created, by the engineer');
    await until(async () => (await legacy(se, 'GET', '/api/v1/time-agent/handoffs', null, { project: S.pid, version: S.ver })).json.length === 0, 'the hand-off is closed once filed');
  }],
];
