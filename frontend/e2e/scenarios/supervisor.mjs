import { WEB, api, eq, navLabels, ok, shot, signIn, text, until } from '../lib.mjs';
import { claimViaApi } from './engineer.mjs';

const num = (s) => Number(String(s).replace(/[^0-9.-]/g, ''));
const decideBtn = (page) => page.getByTestId('decide-btn');
async function open(page, id) { await page.goto(`${WEB}/claims/${id}`); await page.waitForSelector('[data-testid=claim-detail-page]'); await page.waitForSelector('[data-testid=claim-text]'); }
async function actual(page) { await page.goto(`${WEB}/overview`); await page.waitForSelector('[data-testid=stat-actual]'); return num((await text(page, 'stat-actual')).match(/([\d.]+)%/)[1]); }

export default [
  ['Supervisor: lands on the review queue; sees pending claims; no project-management menu', async ({ browser, S }) => {
    const { page, log } = await signIn(browser, 'lakshmi.iyer');
    S.sup = { page, log };
    eq(new URL(page.url()).pathname, '/review', 'landing page');
    const nav = await navLabels(page);
    for (const need of ['Review Queue', 'Overview', 'WBS & Activities', 'Issues & Delays', 'Notifications', 'Audit Trail']) ok(nav.includes(need), `menu lacks ${need}: ${nav}`);
    for (const never of ['Schedule', 'Project Settings', 'Portfolio', 'Submit Claim']) ok(!nav.includes(never), `menu must not contain ${never}`);
    await page.waitForSelector('[data-testid=queue-row]');
    ok(await page.locator('[data-testid=queue-row][data-activity="OSD-2320"]').count() === 1, "the engineer's new claim is in the queue");
    ok(/matched/.test(await text(page, 'queue-counts')), 'status counts shown');
    for (const p of ['/schedule', '/settings', '/claims/new']) { await page.goto(`${WEB}${p}`); await page.waitForSelector('aside'); await until(async () => new URL(page.url()).pathname === '/review', `${p} bounces to the queue`); }
    eq((await api(page, 'POST', `/projects/${S.pidB}/schedule-imports`)).status, 403, 'a Supervisor cannot manage schedules');
    eq((await api(page, 'POST', `/projects/${S.pidB}/claims`, { event_date: '2026-01-01', raw_text: 'sup claim', claimed_pct: 5 })).status, 403, 'a Supervisor cannot file claims');
  }],

  ['Supervisor: claim detail shows what was reported, evidence is downloadable, preview shows the effect, approval is recorded once', async ({ S }) => {
    const { page } = S.sup;
    const before = await actual(page);
    await open(page, S.claim1);
    ok(/200 m/i.test(await text(page, 'reported-quantities')) && /100 m3/i.test(await text(page, 'reported-quantities')), `reported quantities as filed: ${await text(page, 'reported-quantities')}`);
    ok(/drilling-log\.txt/.test(await page.getByTestId('evidence-download').innerText()), 'evidence listed');
    const [dl] = await Promise.all([page.waitForEvent('download'), page.getByTestId('evidence-download').click()]);
    eq(dl.suggestedFilename(), 'drilling-log.txt', 'evidence downloads through the API with its original name');
    await page.waitForSelector('[data-testid=pct-after]');
    const after = await text(page, 'pct-after'); ok(/\d/.test(after) && after !== '0%', `preview shows the resulting progress: ${after}`);
    eq(await api(page, 'GET', `/projects/${S.pidB}/my-claims`).then((r) => r.status), 403, 'the review role has no "my claims"');
    await decideBtn(page).click(); await page.waitForSelector('[data-testid=confirm-decision]');
    await page.getByTestId('confirm-decide').click();
    await page.waitForSelector('[data-testid=decision-done]');
    ok(/Decision recorded/.test(await text(page, 'decision-done')), 'confirmation');
    await until(async () => (await page.getByTestId('decision-entry').count()) === 1, 'decision history appears');
    const entry = await text(page, 'decision-entry');
    ok(/APPROVE/.test(entry) && /QUANTITIES_AS_CLAIMED/.test(entry), `decision recorded: ${entry}`);
    ok(/200 m/i.test(await text(page, 'applied-table')), 'reported and approved shown side by side');
    ok(await decideBtn(page).count() === 0, 'a decided claim cannot be decided again');
    const ov = await actual(page); ok(ov > before, `progress rose after the approval: ${before} -> ${ov}`);
    const ledger = await api(page, 'GET', `/projects/${S.pidB}/activities/${S.claimAct ?? (await api(page, 'GET', `/projects/${S.pidB}/activities?q=OSD-2320`)).json.items[0].activity_uid}/timeline`);
    eq(ledger.json.quantity_entries.length, 2, 'exactly one ledger entry per measured resource');
    await shot(page, 'sup_approved');
  }],

  ['Supervisor: a percentage-only claim needs an explicit method; the resulting quantities are recorded', async ({ S }) => {
    const { page } = S.sup;
    await open(page, S.claimPct);
    await page.waitForSelector('[data-testid=pct-method]');
    ok(/Nothing is converted automatically/.test(await text(page, 'pct-method')), 'explanation');
    ok(await decideBtn(page).isDisabled(), 'approval is blocked until a method is chosen');
    await page.getByLabel(/Apply 40% to/).check().catch(async () => { await page.locator('[data-testid=pct-method] input[type=radio]').check(); });
    await page.waitForSelector('[data-testid=applied-table]');
    ok(/PCT/.test(await text(page, 'preview')), 'the preview shows the quantities the percentage produces');
    await decideBtn(page).click(); await page.getByTestId('confirm-decide').click();
    await page.waitForSelector('[data-testid=decision-done]');
    await until(async () => (await page.getByTestId('decision-entry').count()) === 1, 'history');
    ok(/APPLY_PCT_TO_ASSIGNMENTS/.test(await text(page, 'decision-entry')), 'the method is recorded with the decision');
  }],

  ['Supervisor: over-baseline needs the tolerance acknowledgement; quantity is not capped; the note is kept', async ({ S }) => {
    const { page } = S.sup;
    await open(page, S.claimOver);
    await page.waitForSelector('[data-testid=overrun-ack]');
    ok(/tolerance/.test(await text(page, 'overrun-ack')), 'tolerance is stated');
    await until(async () => await decideBtn(page).isDisabled(), 'approval blocked without an acknowledgement');
    await page.getByLabel('Over-baseline acknowledgement').fill('Re-drill after a stuck pipe event; footage verified against the daily drilling report');
    await until(async () => await decideBtn(page).isEnabled(), 'enabled once acknowledged');
    await decideBtn(page).click(); await page.getByTestId('confirm-decide').click();
    await page.waitForSelector('[data-testid=decision-done]');
    await until(async () => (await page.getByTestId('decision-entry').count()) === 1, 'history');
    ok(/acknowledged/i.test(await text(page, 'decision-entry')) && /stuck pipe/.test(await text(page, 'decision-entry')), 'acknowledgement and note are shown with the decision');
    ok(/1,?250/.test(await text(page, 'applied-table')), `the approved quantity 1250 is not capped: ${await text(page, 'applied-table')}`);
    const d = (await api(page, 'GET', `/projects/${S.pidB}/claims/${S.claimOver}`)).json.decisions[0];
    eq([d.overrun_ack, d.overrun_ack_note.includes('stuck pipe')], [true, true], 'server kept the acknowledgement');
  }],

  ['Supervisor: approve-with-changes keeps both the reported and the approved figure', async ({ S }) => {
    const { page } = S.sup; const se = S.se.page;
    const { claimId } = await claimViaApi(se, S.pidB, 'OSD-2350', { W3_CSG13_JOINTS: 100 });
    await open(page, claimId);
    await page.getByRole('button', { name: 'Approve with changes' }).click();
    await page.waitForSelector('[data-testid=edit-rows]');
    await page.getByLabel(/Approved quantity of/).first().fill('90');
    await page.getByLabel('Justification').fill('Joint register shows 90 run');
    await page.waitForSelector('[data-testid=applied-table]');
    await decideBtn(page).click(); await page.getByTestId('confirm-decide').click();
    await page.waitForSelector('[data-testid=decision-done]');
    await until(async () => (await page.getByTestId('decision-entry').count()) === 1, 'history');
    const t = await text(page, 'reported-quantities'); ok(/100/.test(t), `reported figure still 100: ${t}`);
    ok(/90/.test(await text(page, 'applied-table')) && /EDIT/.test(await text(page, 'decision-entry')), 'approved figure 90 recorded as an EDIT');
  }],

  ['Supervisor rejects; engineer sees the reason and files a linked correction; supervisor approves the correction', async ({ S }) => {
    const { page } = S.sup; const se = S.se.page;
    const { claimId } = await claimViaApi(se, S.pidB, 'OSD-2360', { W3_DRILLED_12_M: 900 });
    await open(page, claimId);
    await page.getByRole('button', { name: 'Reject', exact: true }).click();
    ok(await decideBtn(page).isDisabled(), 'a reason is required');
    await page.getByLabel('Rejection reason').fill('Footage exceeds the daily drilling reports for the period');
    await decideBtn(page).click(); await page.waitForSelector('[data-testid=decision-done]');
    // engineer
    await open(se, claimId);
    ok(/Footage exceeds/.test(await text(se, 'decision-entry')), 'the engineer sees the reason');
    await se.getByTestId('correct-btn').click();
    await se.locator('[data-testid=claim-form] [data-resource="W3_DRILLED_12_M"] input[type=number]').fill('700');
    await se.getByLabel('Remarks').fill('Corrected after re-measurement: 700 m');
    await se.getByTestId('submit-claim').click(); await se.waitForSelector('[data-testid=claim-success]');
    const mine = (await api(se, 'GET', `/projects/${S.pidB}/my-claims`)).json.items;
    const fix = mine.find((c) => c.resubmits_event_id === claimId); ok(fix, 'a NEW claim linked to the rejected one exists');
    eq(mine.find((c) => c.event_id === claimId).status, 'REJECTED', 'the rejected claim stays rejected');
    await open(page, fix.event_id);
    await decideBtn(page).click(); await page.getByTestId('confirm-decide').click(); await page.waitForSelector('[data-testid=decision-done]');
  }],

  ['Supervisor asks a question; engineer answers; supervisor approves', async ({ S }) => {
    const { page } = S.sup; const se = S.se.page;
    const { claimId } = await claimViaApi(se, S.pidB, 'OSD-2370', { W3_CSG9_JOINTS: 60 });
    await open(page, claimId);
    await page.getByRole('button', { name: 'Ask a question' }).click();
    await page.getByLabel('Clarification question').fill('Which casing string and which tally sheet?');
    await decideBtn(page).click(); await page.waitForSelector('[data-testid=decision-done]');
    await open(se, claimId);
    ok(/Which casing string/.test(await text(se, 'clarification-question')), 'the question reaches the engineer');
    eq((await api(se, 'GET', `/projects/${S.pidB}/claims/${claimId}`)).json.status, 'DISPUTED', 'on hold: not approved');
    await se.getByTestId('answer-btn').click(); await se.getByRole('textbox', { name: 'Answer' }).fill('9.625 in string, tally sheet 14'); await se.getByTestId('confirm-action').click();
    await until(async () => /answered/i.test(await se.locator('main').innerText()), 'answered');
    await open(page, claimId);
    ok(/tally sheet 14/.test(await page.locator('main').innerText()), 'the answer is visible to the supervisor');
    await decideBtn(page).click(); await page.getByTestId('confirm-decide').click(); await page.waitForSelector('[data-testid=decision-done]');
  }],

  ['Issues: engineer reports a blocking issue; supervisor sees it blocking, groups it by root cause, resolves it, keeps the lesson', async ({ S }) => {
    const se = S.se.page; const sup = S.sup.page;
    await se.goto(`${WEB}/issues`); await se.waitForSelector('[data-testid=report-issue]');
    await se.getByLabel('Issue category').selectOption('EQUIPMENT_SHORTAGE');
    await se.getByLabel('Find activity').fill('OSD-2320'); await se.locator('[data-testid=activity-option][data-activity="OSD-2320"]').click();
    await se.getByLabel('Issue title').fill('Top drive failure on the rig');
    await se.getByLabel('Estimated impact in days').fill('4');
    await se.getByTestId('report-issue').click(); await se.waitForSelector('[data-testid=issue-msg]');
    const nBlocked = async () => (await api(sup, 'GET', `/projects/${S.pidB}/blockers`)).json.blocked_activities.length;
    const blockedNow = await nBlocked();
    await sup.goto(`${WEB}/overview`); await sup.waitForSelector('[data-testid=blockers-notice]');
    const act = (await api(sup, 'GET', `/projects/${S.pidB}/activities?q=OSD-2320`)).json.items[0].activity_uid;
    ok((await api(sup, 'GET', `/projects/${S.pidB}/blockers`)).json.blocked_activities.includes(act), 'the activity is now derived as blocked');
    ok(new RegExp(`blocked on ${blockedNow} activit`).test(await text(sup, 'blockers-notice')), `overview shows the derived blocked state: ${await text(sup, 'blockers-notice')}`);
    await sup.goto(`${WEB}/issues`); await sup.waitForSelector('[data-testid=issue-card]');
    const card = sup.locator('[data-testid=issue-card]', { hasText: 'Top drive failure' });
    await card.getByTestId('resolve-btn').click(); await sup.getByLabel('Resolution').fill('Top drive replaced by the rig contractor');
    await sup.getByLabel('Actual impact in days').fill('3'); await sup.getByTestId('confirm-resolve').click();
    await until(async () => (await sup.locator('[data-testid=issue-card][data-status=ACTIVE]', { hasText: 'Top drive' }).count()) === 0, 'issue leaves the open list');
    await sup.getByRole('button', { name: 'Resolved' }).click();
    const done = sup.locator('[data-testid=issue-card][data-status=RESOLVED]', { hasText: 'Top drive failure' });
    await done.waitFor(); ok(/actual 3 d/.test(await done.innerText()), 'actual impact recorded');
    await done.getByTestId('memory-btn').click(); await sup.getByLabel('Lesson learned').fill('Carry a spare top drive motor on every jack-up campaign');
    await sup.getByTestId('confirm-memory').click();
    await sup.getByRole('tab', { name: 'Lessons' }).click(); await sup.waitForSelector('[data-testid=memory-entry]');
    await sup.goto(`${WEB}/overview`); await sup.waitForSelector('[data-testid=stat-issues]');
    ok(!(await api(sup, 'GET', `/projects/${S.pidB}/blockers`)).json.blocked_activities.includes(act), 'the derived blocked state clears after resolution');
    eq(await nBlocked(), blockedNow - 1, 'one fewer blocked activity');
  }],

  ['Notifications and audit: the engineer is told about decisions; supervisors read and verify the audit trail', async ({ S }) => {
    const se = S.se.page; const sup = S.sup.page;
    await se.goto(`${WEB}/notifications`); await se.waitForSelector('[data-testid=notification]');
    const unread = await se.locator('[data-testid=notification][data-unread=true]').count(); ok(unread >= 3, `unread decisions: ${unread}`);
    await se.locator('[data-testid=notification][data-unread=true]').first().click();
    await until(async () => (await se.locator('[data-testid=notification][data-unread=true]').count()) < unread || new URL(se.url()).pathname.startsWith('/claims/'), 'opening marks it read');
    await sup.goto(`${WEB}/audit`); await sup.waitForSelector('[data-testid=audit-table]');
    ok(/claim approved/.test(await text(sup, 'audit-table')), 'approvals are audited');
    await sup.getByTestId('verify-chain').click(); await sup.waitForSelector('[data-testid=verify-result]');
    ok(/intact/.test(await text(sup, 'verify-result')), 'chain intact');
    eq((await api(se, 'GET', `/projects/${S.pidB}/audit`)).status, 403, 'engineers cannot read the audit trail');
  }],
];
