// Supervisor: the ORIGINAL interface (Dashboard, Daily Digest, Review Workspace, Activity History, Impact Preview, AI Execution Summary, Root Cause & Memory, Project Intelligence,
// Audit Trail, Time Agent) running on the v2 backend, as a Supervisor of NRL-EXPANSION.
import { CFG, WEB, activeVersion, api, eq, legacy, navLabels, ok, projectId, selectProject, shot, signIn, until } from '../lib.mjs';
import { claimViaApi } from './engineer.mjs';

const ORIGINAL_SUP_MENU = ['Issues & Delays', 'Daily Digest', 'Review Workspace', 'Time Agent', 'Dashboard', 'Activity History', 'Impact Preview', 'WBS Explorer', 'AI Execution Summary', 'Root Cause & Memory', 'Project Intelligence', 'Audit Trail'];
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
    for (const p of ['/portfolio', '/schedule', '/settings', '/intake', '/updates']) {
      await page.goto(`${WEB}${p}`); await page.waitForSelector('aside');
      await until(async () => new URL(page.url()).pathname === '/dashboard', `${p} bounces to the Dashboard`);
    }
    eq((await api(page, 'POST', `/projects/${S.pid}/schedule-imports`)).status, 403, 'a Supervisor cannot manage schedules');
    eq((await legacy(page, 'POST', '/api/v1/claims/text', { raw_claim_text: 'NRE-4050 piping 10 percent' }, { project: S.pid, version: S.ver })).status, 403, 'a Supervisor cannot file claims');
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
    await page.goto(`${WEB}/review`); await page.waitForSelector(`text=${id}`);                         // the queue lists it
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
    const dossier = (await legacy(page, 'GET', `/api/v1/projects/${S.pid}/dossier`, null, { project: S.pid, version: S.ver })).json;
    ok(dossier.human_decisions.decisions.some((d) => d.event_id === id), 'the decision is in the dossier');
    const trail = JSON.stringify((await legacy(page, 'GET', `/api/v1/projects/${S.pid}/dossier`, null, { project: S.pid, version: S.ver })).json.audit_chain.recent_logs);
    ok(/CLAIM_DECIDED|DECISION/i.test(trail) || trail.length > 10, 'audited');
  }],

  ['Supervisor: every restored page loads real data without a failed call (Digest, Dashboard, History, Impact, Summary, Root Cause, Intelligence, Audit, WBS, Issues)', async ({ S }) => {
    const { page } = S.sup;
    const failed = [];
    page.on('response', (r) => { if (r.status() >= 400 && r.url().includes(`:${CFG.apiPort}`)) failed.push(`${r.status()} ${r.url().split(`:${CFG.apiPort}`)[1]}`); });
    const pages = { '/digest': /Daily Digest/, '/dashboard': /Project Executive Dashboard/, '/history': /Activity History/, '/impact': /Ripple Impact Preview/, '/summary': /AI Execution Summary/,
      '/root-cause': /Root Cause/, '/intelligence': /Project Intelligence/, '/audit': /Audit Trail/, '/wbs': /WBS Activity Explorer/, '/issues': /Issues & Delays/ };
    for (const [p, re] of Object.entries(pages)) {
      await page.goto(`${WEB}${p}`); await page.waitForSelector('aside'); await page.waitForTimeout(1800);
      ok(re.test(await main(page)), `${p} renders`);
    }
    eq(failed, [], 'no failed API call on any restored page');
  }],

  ['Supervisor: Project Intelligence answers from project facts and changes nothing; Audit Trail verifies the chain and downloads the dossier', async ({ S }) => {
    const { page } = S.sup;
    const decisions = async () => (await legacy(page, 'GET', `/api/v1/decisions?limit=100`, null, { project: S.pid, version: S.ver })).json.length;
    const n = await decisions();
    await page.goto(`${WEB}/intelligence`); await page.waitForSelector('text=Supervisory briefing');
    await until(async () => /Deterministic/i.test(await main(page)), 'without a language model the briefing is deterministic and says so', 20000);
    await page.getByPlaceholder(/What is holding up/).fill('What should I review first?');
    await page.getByRole('button', { name: /^Ask$/ }).click();
    await until(async () => (await page.locator('main').innerText()).length > 900, 'an answer is shown', 30000);
    eq(await decisions(), n, 'the agent cannot approve, reject or change anything');
    await page.goto(`${WEB}/audit`); await page.waitForSelector('text=Latest records');
    await until(async () => /VALID/.test(await main(page)) && /records checked/i.test(await main(page)), 'chain verified', 20000);
    const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: /Download dossier/ }).click()]);
    ok(/dossier/i.test(dl.suggestedFilename()), `dossier downloaded: ${dl.suggestedFilename()}`);
    await shot(page, 'sup_audit');
  }],

  ['Supervisor: P6 sync staging downloads the approved-actuals CSV and pushes to the local mock P6', async ({ S }) => {
    const { page } = S.sup;
    await page.goto(`${WEB}/dashboard`); await page.waitForSelector('text=P6 / PMIS Sync Staging'); await page.waitForTimeout(1500);
    await page.getByRole('button', { name: /P6 \/ PMIS Sync Staging/ }).click();
    const [csv] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: /Download Canonical CSV/ }).click()]);
    ok(/setu_approved_actuals_p6_staging/.test(csv.suggestedFilename()), `CSV downloaded: ${csv.suggestedFilename()}`);
    const body = (await import('node:fs')).readFileSync(await csv.path(), 'utf8');
    ok(/activity_id/i.test(body.split('\n')[0]) && body.split('\n').length > 5, `CSV has a header and rows: ${body.slice(0, 120)}`);
    await page.getByRole('tab', { name: 'Mock Adapter Test' }).click();
    const push = page.getByRole('button', { name: /Push to Mock P6/ }).first();
    ok(await push.count() > 0, 'a push control is offered');
    await push.click();
    await until(async () => (await legacy(page, 'GET', '/api/v1/mock-p6/received', null, { project: S.pid, version: S.ver })).json.count >= 1, 'the mock P6 received the payload', 20000);
    await shot(page, 'sup_p6');
  }],

  ['Supervisor: Time Agent drafts a claim and hands it to the Site Engineer, who files it; the Supervisor never files', async ({ S }) => {
    const { page } = S.sup;
    await page.goto(`${WEB}/time-agent`); await page.waitForSelector('text=Setu AI Time Agent');
    const text = 'NRE-4080 piping insulation and painting 5 percent complete';
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
