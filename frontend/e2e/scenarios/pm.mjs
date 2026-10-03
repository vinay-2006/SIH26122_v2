import { WEB, api, eq, fixture, navLabels, ok, projectId, shot, signIn, text, tmpFile, until } from '../lib.mjs';
import fs from 'node:fs';

export default [
  ['PM: lands on the portfolio; menu is limited to project management; counts only', async ({ browser, S }) => {
    const { page, log } = await signIn(browser, 'anita.bora');
    eq(new URL(page.url()).pathname, '/portfolio', 'landing page');
    const nav = await navLabels(page);
    for (const need of ['Portfolio', 'Overview', 'Schedule', 'WBS & Activities', 'Issues & Delays', 'Notifications', 'Audit Trail', 'Project Settings']) ok(nav.includes(need), `PM menu lacks ${need}: ${nav}`);
    for (const never of ['Review Queue', 'Submit Claim', 'My Claims']) ok(!nav.includes(never), `PM menu must not contain ${never}`);
    await page.waitForSelector('[data-testid=project-card]');
    await until(async () => !/Loading progress/.test(await page.locator('[data-testid=portfolio-page]').innerText()), 'project cards finish loading their progress');
    const cards = Object.fromEntries(await page.locator('[data-testid=project-card]').evaluateAll((els) => els.map((e) => [e.dataset.projectCode, e.innerText.replace(/\s+/g, ' ')])));
    ok(/100%\s*actual/.test(cards['NNB-CRUDE']) && /Completed/.test(cards['NNB-CRUDE']), `completed project card: ${cards['NNB-CRUDE']}`);
    ok(/0%\s*actual/.test(cards['SMP-PIPE']) && /Upcoming/.test(cards['SMP-PIPE']), `upcoming project card: ${cards['SMP-PIPE']}`);
    ok(/claims pending/.test(cards['NNB-CRUDE']) && !/Welding|joints/i.test(cards['NNB-CRUDE']), 'only counts are shown');
    ok(await page.getByTestId('new-project').isVisible(), 'PM with the CREATE_PROJECT grant sees New project');
    await page.getByPlaceholder('Search projects…').fill('siliguri');
    eq(await page.locator('[data-testid=project-card]').count(), 1, 'search filters the cards');
    await shot(page, 'pm_portfolio'); eq(log.errors, [], 'no browser errors');
    S.pm = { page };
  }],

  ['PM: cannot reach claim review, claim forms or claim content (UI and API)', async ({ S }) => {
    const { page } = S.pm;
    for (const p of ['/review', '/claims/new', '/claims/mine', '/claims/00000000-0000-0000-0000-000000000000']) {
      await page.goto(`${WEB}${p}`); await page.waitForSelector('aside');
      await until(async () => new URL(page.url()).pathname === '/portfolio', `${p} should bounce to the portfolio, at ${page.url()}`);
    }
    const a = await projectId(page, 'NNB-CRUDE');
    eq((await api(page, 'GET', `/projects/${a}/claim-counts`)).status, 200, 'counts are allowed');
    const q = await api(page, 'GET', `/projects/${a}/review-queue`); eq(q.status, 403, 'queue is forbidden');
    const c = await api(page, 'GET', `/projects/${a}/claims/00000000-0000-0000-0000-000000000000`); eq([c.status, c.json.error.code], [403, 'CLAIM_CONTENT_FORBIDDEN'], 'claim content');
    eq((await api(page, 'POST', `/projects/${a}/claims`, { event_date: '2026-01-01', raw_text: 'pm claim', claimed_pct: 10 })).status, 403, 'a PM cannot file claims');
    eq((await api(page, 'POST', `/projects/${a}/claims/00000000-0000-0000-0000-000000000000/decision`, { action: 'APPROVE' })).status, 403, 'a PM cannot decide');
  }],

  ['PM: project overview and activities show approved progress with the approximation disclosure', async ({ S }) => {
    const { page } = S.pm;
    await page.goto(`${WEB}/overview`); await page.waitForSelector('[data-testid=stat-actual]');
    ok(/100%/.test(await text(page, 'stat-actual')), 'completed project is 100%');
    ok(/approximation/i.test(await text(page, 'approx-note')), 'planned progress disclaimer is shown');
    ok(/not earned value/i.test(await text(page, 'stat-spi')), 'SPI is labelled as not earned value');
    ok(/Feb\s+20,\s+2023/.test(await text(page, 'stat-datadate')), 'data date shown');
    await page.waitForSelector('[data-testid=timeline-chart]');
    await page.goto(`${WEB}/activities`); await page.waitForSelector('[data-testid=activity-row]');
    await page.locator('[data-testid=activity-row]').first().click();
    await page.waitForSelector('[data-testid=activity-drawer]');
    ok(/Approved progress/.test(await text(page, 'activity-drawer')), 'activity detail opens');
    await shot(page, 'pm_activity_drawer');
    await page.keyboard.press('Escape');
    // a historical version is selectable from the shared switcher only when it exists; D has a single version
    await page.getByTestId('project-switcher').click(); await page.getByRole('option').filter({ hasText: 'Siliguri' }).first().click();
    await page.goto(`${WEB}/overview`); await page.waitForSelector('[data-testid=stat-actual]');
    ok(/^.*0%/.test(await text(page, 'stat-actual')), 'upcoming project: zero progress');
    ok(/DURATION/.test(await text(page, 'overview-page')), 'weight basis shown for the upcoming project');
  }],

  ['PM (rohit): creates a project, rejects an invalid schedule, maps labels, builds, activates, and the project starts at zero', async ({ browser, S }) => {
    const { page, log } = await signIn(browser, 'rohit.menon');
    S.rohit = { page };
    await page.goto(`${WEB}/portfolio`); await page.getByTestId('new-project').click();
    const dlg = page.getByRole('dialog');
    await dlg.getByLabel('Project code').fill('UI-E2E-1');
    await dlg.getByLabel('Project name').fill('UI End-to-End Pipeline');
    await dlg.getByLabel('Location').fill('Assam');
    await dlg.getByRole('button', { name: /create project/i }).click();
    await until(async () => new URL(page.url()).pathname === '/schedule', 'after creation the PM lands on Schedule');
    await page.waitForSelector('[data-testid=no-versions]');
    S.newProject = await projectId(page, 'UI-E2E-1');

    // invalid file: duplicate activity id -> nothing is staged
    const nsp = fs.readFileSync(fixture('nsp.csv'), 'utf8');
    const bad = tmpFile('bad.csv', nsp + 'A1000,Duplicate,Northern Spur Test Pipeline > Civil & ROW Preparation,Civil Works,Task,5.0,12-01-2026,16-01-2026,0,\n');
    await page.setInputFiles('[data-testid=schedule-file]', bad);
    await page.setInputFiles('[data-testid=resource-file]', fixture('nsp_resources.csv'));
    await page.getByTestId('upload-schedule').click();
    await page.waitForSelector('[data-testid=import-errors]');
    ok(/DUPLICATE_ACTIVITY_ID/.test(await text(page, 'import-errors')), 'validation error shown');
    eq(await page.getByTestId('import-report').count(), 0, 'an invalid import is not staged');
    // .mpp is refused on the client with the supported alternatives
    await page.setInputFiles('[data-testid=schedule-file]', tmpFile('plan.mpp', 'x'));
    ok(await page.getByText(/Native \.mpp files are not supported/).isVisible(), '.mpp message');

    // valid file with an unknown discipline label and unit
    const mapped = tmpFile('mapped.csv', nsp + 'X1,Radiograph welds,Northern Spur Test Pipeline > Commissioning,Radiography & NDT,Task,10.0,15-06-2026,26-06-2026,2,A2030FS\n');
    const res = tmpFile('mapped_res.csv', fs.readFileSync(fixture('nsp_resources.csv'), 'utf8') + 'X1,FILMS,Radiographic films,Material,400,reels\n');
    await page.setInputFiles('[data-testid=schedule-file]', mapped);
    await page.setInputFiles('[data-testid=resource-file]', res);
    await page.getByLabel('Baseline name').fill('Contract baseline Rev 0');
    await page.getByTestId('upload-schedule').click();
    await page.waitForSelector('[data-testid=mapping-panel]');
    ok(await page.getByTestId('build-version').isDisabled(), 'build is blocked until the mapping is done');
    await page.getByLabel(/Discipline for/).selectOption('PIPING');
    await page.getByLabel(/Unit for/).selectOption('NOS');
    await page.getByTestId('apply-mapping').click();
    await until(async () => !(await page.getByTestId('build-version').isDisabled()), 'build enabled after mapping');
    await page.getByTestId('build-version').click();
    await page.waitForSelector('[data-testid=built-notice]');
    const row = page.locator('[data-testid=version-row][data-version="1"]');
    eq(await row.getAttribute('data-status'), 'VALIDATED', 'built version is validated, not active');

    // activation needs an explicit confirmation
    await row.getByTestId('activate-btn').click();
    await page.waitForSelector('[data-testid=activate-dialog]');
    await shot(page, 'pm_activate_confirm');
    await page.getByTestId('confirm-activate').click();
    await until(async () => (await page.locator('[data-testid=version-row][data-version="1"]').getAttribute('data-status')) === 'ACTIVE', 'version becomes ACTIVE');

    // zero progress
    await page.goto(`${WEB}/overview`); await page.waitForSelector('[data-testid=stat-actual]');
    ok(/^\S*\s*0%/.test(await text(page, 'stat-actual')) || /0%/.test((await text(page, 'stat-actual')).split(' ')[2] ?? '0%'), 'actual progress starts at 0%');
    ok(/13 activities|0 of 13/.test(await text(page, 'stat-actual')), `activity count: ${await text(page, 'stat-actual')}`);
    eq(await api(page, 'GET', `/projects/${S.newProject}/dashboard/summary`).then((r) => [Number(r.json.physical_pct), r.json.claims.pending_total]), [0, 0], 'server agrees: zero progress, no claims');
    eq(log.errors, [], 'no browser errors');
  }],

  ['PM (rohit): manages members and settings; invitations show the link once', async ({ S }) => {
    const { page } = S.rohit;
    await page.goto(`${WEB}/settings`); await page.waitForSelector('[data-testid=settings-page]');
    await page.getByRole('tab', { name: 'Members' }).click();
    await page.getByLabel('Member email').fill('sneha.pillai@seed.setuai.local');
    await page.getByLabel('Member role').selectOption('SITE_ENGINEER');
    await page.getByTestId('add-member').click();
    await page.waitForSelector('[data-testid=member-row][data-email="sneha.pillai@seed.setuai.local"]');
    await page.getByLabel('Member email').fill('nobody@nowhere.test'); await page.getByTestId('add-member').click();
    await page.waitForSelector('text=No active registered user');
    await page.getByLabel('Member email').fill('farah.khan@seed.setuai.local'); await page.getByTestId('invite-member').click();
    const link = await page.getByTestId('invite-link').inputValue();
    ok(/accept-invitation\?token=/.test(link), 'invitation link shown');
    await page.getByRole('tab', { name: 'Progress rules' }).click();
    await page.getByLabel('Over-baseline tolerance').fill('12');
    await page.getByTestId('save-settings').click(); await page.waitForSelector('text=Settings saved');
    await page.reload(); await page.getByRole('tab', { name: 'Progress rules' }).click();
    eq(await page.getByLabel('Over-baseline tolerance').inputValue(), '12', 'setting persisted');
    const bad = await api(page, 'PATCH', `/projects/${S.newProject}/settings`, { over_baseline_tolerance_pct: 500 });
    eq(bad.status, 422, 'the server validates settings too');
  }],

  ['PM (rohit): imports a revision, resolves the rename, builds, compares versions, and the history is audited', async ({ S }) => {
    const { page } = S.rohit;
    await page.goto(`${WEB}/schedule`); await page.waitForSelector('[data-testid=schedule-page]');
    await page.setInputFiles('[data-testid=schedule-file]', fixture('nsp_rev1.csv'));
    await page.setInputFiles('[data-testid=resource-file]', fixture('nsp_rev1_resources.csv'));
    await page.getByLabel('Baseline name').fill('Revision 1');
    await page.getByTestId('upload-schedule').click();
    await page.waitForSelector('[data-testid=recon-summary]');
    ok(await page.getByTestId('build-version').isDisabled(), 'the revision cannot be built while a rename is undecided');
    await page.waitForSelector('[data-testid=recon-item]');
    await page.getByRole('button', { name: /Same activity/ }).first().click();                                   // CG-1010 is the renamed A1010
    await page.waitForSelector('[data-testid=recon-split]'); await page.getByRole('button', { name: 'Confirm split' }).click();
    await page.getByRole('button', { name: 'Confirm merge' }).click();
    await page.waitForSelector('[data-testid=recon-clear]');
    await until(async () => !(await page.getByTestId('build-version').isDisabled()), 'build enabled once every proposal is decided');
    await page.getByTestId('build-version').click(); await page.waitForSelector('[data-testid=built-notice]');
    const v2 = page.locator('[data-testid=version-row][data-version="2"]');
    eq(await v2.getAttribute('data-status'), 'VALIDATED', 'revision built');
    await v2.getByTestId('activate-btn').click(); await page.getByTestId('confirm-activate').click();
    await until(async () => (await page.locator('[data-testid=version-row][data-version="2"]').getAttribute('data-status')) === 'ACTIVE', 'revision active');
    eq(await page.locator('[data-testid=version-row][data-version="1"]').getAttribute('data-status'), 'SUPERSEDED', 'old version superseded');
    await page.getByLabel('Select v1 to compare').check(); await page.getByLabel('Select v2 to compare').check();
    await page.getByTestId('compare-btn').click(); await page.waitForSelector('[data-testid=compare-dialog]');
    await until(async () => !/Loading/.test(await text(page, 'compare-dialog')), 'comparison loads');
    ok(/ADDED|RENAMED|CHANGED|REMOVED|SAME|MOVED/.test(await text(page, 'compare-dialog')), `comparison lists the differences: ${await text(page, 'compare-dialog')}`);
    await shot(page, 'pm_compare'); await page.keyboard.press('Escape');
    // rollback needs a reason
    await page.locator('[data-testid=version-row][data-version="1"]').getByTestId('rollback-btn').click();
    ok(await page.getByTestId('confirm-activate').isDisabled(), 'rollback needs a reason');
    await page.keyboard.press('Escape');
    ok(/version activated/.test(await text(page, 'schedule-history')), 'history from the audit trail');
    await page.goto(`${WEB}/audit`); await page.waitForSelector('[data-testid=audit-table]');
    await page.getByTestId('verify-chain').click(); await page.waitForSelector('[data-testid=verify-result]');
    ok(/intact/.test(await text(page, 'verify-result')), 'audit chain verifies');
  }],
];
