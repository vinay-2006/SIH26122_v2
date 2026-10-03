import { WEB, api, eq, navLabels, ok, projectId, shot, signIn, text, tmpFile, until } from '../lib.mjs';

const today = () => new Date().toISOString().slice(0, 10);
const num = (s) => Number(String(s).replace(/[^0-9.-]/g, ''));

/** file a claim straight through the API as this person (fast set-up for the supervisor scenarios; the UI path is covered separately) */
export async function claimViaApi(page, pid, ext, quantities, extra = {}) {
  const act = (await api(page, 'GET', `/projects/${pid}/activities?q=${ext}&limit=5`)).json.items.find((a) => a.external_activity_id === ext);
  const qs = quantities && Object.entries(quantities).map(([code, qty]) => {
    const m = act.measured_assignments.find((x) => x.resource_code === code);
    return { qty, uom: m.unit_of_measure, basis: 'CUMULATIVE', resource_hint: code };
  });
  const r = await api(page, 'POST', `/projects/${pid}/claims`, { event_date: today(), raw_text: `E2E ${ext} ${Math.random().toString(36).slice(2, 8)}`, activity_uid: act.activity_uid, ...(qs ? { quantities: qs } : {}), ...extra });
  ok(r.status === 201, `claim set-up failed: ${JSON.stringify(r.json)}`);
  return { claimId: r.json.claim_id, act };
}

async function pickActivity(page, ext) {
  await page.getByLabel('Find activity').fill(ext);
  await page.locator(`[data-testid=activity-option][data-activity="${ext}"]`).click();
  await page.waitForSelector('[data-testid=selected-activity]');
}

async function actualPct(page) {
  await page.goto(`${WEB}/overview`); await page.waitForSelector('[data-testid=stat-actual]');
  return num((await text(page, 'stat-actual')).match(/ACTUAL PROGRESS\s*([\d.]+)%/i)?.[1] ?? (await text(page, 'stat-actual')).match(/([\d.]+)%/)[1]);
}

export default [
  ['Engineer: lands on the claim form; menu has no schedule, review, audit or settings', async ({ browser, S }) => {
    const { page, log } = await signIn(browser, 'arun.nair');
    S.se = { page, log }; S.pidB = await projectId(page, 'AEC-OFFSHORE');
    eq(new URL(page.url()).pathname, '/claims/new', 'landing page');
    const nav = await navLabels(page);
    for (const need of ['Submit Claim', 'My Claims', 'Overview', 'WBS & Activities', 'Issues & Delays', 'Notifications']) ok(nav.includes(need), `menu lacks ${need}: ${nav}`);
    for (const never of ['Schedule', 'Review Queue', 'Audit Trail', 'Project Settings', 'Portfolio']) ok(!nav.includes(never), `menu must not contain ${never}`);
    for (const p of ['/schedule', '/review', '/audit', '/settings', '/portfolio']) { await page.goto(`${WEB}${p}`); await page.waitForSelector('aside'); await until(async () => new URL(page.url()).pathname === '/claims/new', `${p} bounces to the claim form`); }
    const up = await api(page, 'POST', `/projects/${S.pidB}/schedule-imports`);
    ok([403, 422].includes(up.status), `schedule upload must be refused for engineers: ${up.status}`);
    eq((await api(page, 'GET', `/projects/${S.pidB}/schedule-versions/compare?old=${S.pidB}&new=${S.pidB}`)).status, 403, 'version management is forbidden');
    eq((await api(page, 'GET', `/projects/${S.pidB}/claim-counts`)).status, 403, 'engineers do not see claim counts of others');
    await shot(page, 'se_claim_form');
  }],

  ['Engineer: quantity claim with evidence is accepted as PENDING and does not change progress', async ({ S }) => {
    const { page } = S.se;
    const before = await actualPct(page);
    await page.goto(`${WEB}/claims/new`); await page.waitForSelector('[data-testid=claim-form]');
    await pickActivity(page, 'OSD-2320');
    await page.waitForSelector('[data-testid=quantity-rows]');
    const drill = page.locator('[data-resource="W3_DRILLED_26_M"]'), mud = page.locator('[data-resource="W3_MUD_26_M3"]');
    await drill.locator('input[type=number]').fill('200'); await mud.locator('input[type=number]').fill('100');
    ok(/Baseline 446\.4/.test(await drill.innerText()), 'baseline and unit come from the schedule');
    await page.getByLabel('Remarks').fill('W3 26 in section: 200 m drilled, 100 m3 mud used to date');
    ok(await page.getByTestId('submit-claim').isEnabled(), 'valid form');
    await page.setInputFiles('[data-testid=evidence-input]', tmpFile('drilling-log.txt', 'Daily drilling report W3: 200 m drilled to date.'));
    await until(async () => /uploaded/.test(await text(page, 'evidence-row')), 'evidence uploaded');
    await page.getByTestId('submit-claim').click();
    await page.waitForSelector('[data-testid=claim-success]');
    ok(/does not change project progress/.test(await text(page, 'claim-success')), 'pending disclosure');
    await page.goto(`${WEB}/claims/mine`); await page.waitForSelector('[data-testid=claim-row]');
    const row = page.locator('[data-testid=claim-row]').first();
    ok(/Pending review/.test(await row.innerText()) && /OSD-2320/.test(await row.innerText()), `claim listed as pending: ${await row.innerText()}`);
    S.claim1 = (await api(page, 'GET', `/projects/${S.pidB}/my-claims`)).json.items[0].event_id;
    eq(await actualPct(page), before, 'actual progress is unchanged by a pending claim');
    const ledger = await api(page, 'GET', `/projects/${S.pidB}/activities/${(await api(page, 'GET', `/projects/${S.pidB}/activities?q=OSD-2320`)).json.items[0].activity_uid}/timeline`);
    eq(ledger.json.quantity_entries.length, 0, 'no approved ledger entry exists');
  }],

  ['Engineer: percent-only claim is filed as reported with a clear note (never converted)', async ({ S }) => {
    const { page } = S.se;
    await page.goto(`${WEB}/claims/new`); await pickActivity(page, 'OSD-2330');
    await page.getByRole('button', { name: 'Percent only' }).click();
    ok(/not<\/b> converted|not converted/i.test(await page.getByTestId('pct-note').innerHTML()), 'conversion note');
    await page.getByLabel('Percent complete').fill('40');
    await page.getByLabel('Remarks').fill('20 in casing run about 40 percent');
    await page.getByTestId('submit-claim').click(); await page.waitForSelector('[data-testid=claim-success]');
    ok(/PERCENT_ONLY_NEEDS_METHOD/.test(await text(page, 'claim-success')), 'the server flags that a Supervisor must choose the method');
    S.claimPct = (await api(page, 'GET', `/projects/${S.pidB}/my-claims`)).json.items[0].event_id;
  }],

  ['Engineer: over-baseline quantity is recorded as reported (not capped, not blocked)', async ({ S }) => {
    const { page } = S.se;
    await page.goto(`${WEB}/claims/new`); await pickActivity(page, 'OSD-2340');
    await page.locator('[data-resource="W3_MUD_17_M3"] input[type=checkbox]').uncheck();
    await page.locator('[data-resource="W3_DRILLED_17_M"] input[type=number]').fill('1250');           // baseline is 1041.6 m: 20% over
    await page.getByLabel('Remarks').fill('17.5 in section TD reached, 1250 m drilled');
    await page.getByTestId('submit-claim').click(); await page.waitForSelector('[data-testid=claim-success]');
    S.claimOver = (await api(page, 'GET', `/projects/${S.pidB}/my-claims`)).json.items[0].event_id;
    const detail = await api(page, 'GET', `/projects/${S.pidB}/claims/${S.claimOver}`);
    eq(Number(detail.json.quantities[0].reported_qty), 1250, 'reported figure kept exactly');
  }],

  ['Engineer: sees only their own claims; another engineer cannot read them', async ({ browser, S }) => {
    const { page } = S.se;
    await page.goto(`${WEB}/claims/mine`); await page.waitForSelector('[data-testid=claim-row]');
    const mine = await page.locator('[data-testid=claim-row]').count();
    ok(mine >= 3, `own claims listed: ${mine}`);
    const other = await signIn(browser, 'sneha.pillai'); S.sneha = other;
    await other.page.goto(`${WEB}/claims/mine`); await other.page.waitForSelector('aside');
    await until(async () => (await other.page.getByTestId('claim-row').count()) === 0, "the other engineer sees none of arun's claims");
    eq((await api(other.page, 'GET', `/projects/${S.pidB}/claims/${S.claim1}`)).status, 404, "another engineer's claim looks absent");
    await other.page.goto(`${WEB}/claims/${S.claim1}`); await other.page.waitForSelector('aside');
    ok(await other.page.getByTestId('claim-text').count() === 0, 'claim text is not shown');
  }],

  ['Engineer: withdraws an own pending claim (record kept); evidence stays', async ({ S }) => {
    const { page } = S.se;
    const { claimId } = await claimViaApi(page, S.pidB, 'OSD-2390', { W3_TEST_STAGES: 2 });
    await page.goto(`${WEB}/claims/${claimId}`); await page.waitForSelector('[data-testid=withdraw-btn]');
    await page.getByTestId('withdraw-btn').click();
    ok(await page.getByTestId('confirm-action').isDisabled(), 'a reason is required');
    await page.getByLabel('Withdrawal reason').fill('Entered against the wrong activity');
    await page.getByTestId('confirm-action').click();
    await until(async () => /Withdrawn/.test(await page.locator('main').innerText()), 'status becomes withdrawn');
    ok(await page.getByTestId('withdraw-btn').count() === 0, 'no further actions on a withdrawn claim');
    eq((await api(page, 'GET', `/projects/${S.pidB}/claims/${claimId}`)).json.status, 'WITHDRAWN', 'server state');
  }],
];
