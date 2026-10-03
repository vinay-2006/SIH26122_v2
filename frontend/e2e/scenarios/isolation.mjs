import { API, CFG, WEB, api, eq, navLabels, ok, projectId, signIn, until } from '../lib.mjs';

export default [
  ['Login (v2): the role shortcuts fill the email only, never a password; a wrong password is refused and nothing is stored', async ({ browser }) => {
    const ctx = await browser.newContext(); const page = await ctx.newPage();
    await page.goto(`${WEB}/login`);
    for (const [id, who] of [['identity-anita.bora', 'anita.bora'], ['identity-lakshmi.iyer', 'lakshmi.iyer'], ['identity-arun.nair', 'arun.nair']]) {
      await page.getByTestId(id).click();
      eq(await page.locator('input[type=email]').inputValue(), `${who}@seed.setuai.local`, 'the shortcut fills the email');
      eq(await page.locator('input[type=password]').inputValue(), '', 'a password is never prefilled');
    }
    await page.locator('input[type=password]').fill('definitely-not-the-password');
    await page.getByRole('button', { name: /sign in/i }).click();
    await page.waitForSelector('text=Incorrect email or password');
    eq(new URL(page.url()).pathname, '/login', 'still on the sign-in page');
    ok(await page.evaluate(() => localStorage.getItem('setu_v2_access_token')) === null, 'no token is stored after a failed sign-in');
    await ctx.close();
  }],

  ['Isolation: editing local storage cannot change the role or the project; the server decides', async ({ S }) => {
    const { page } = S.se;
    const a = await projectId(S.rohit.page, 'UI-E2E-1').catch(() => null);
    await page.evaluate(() => { localStorage.setItem('user', JSON.stringify({ id: 'x', role: 'PROJECT_MANAGER', full_name: 'Mallory' })); localStorage.setItem('setu_v2_selected_project', '00000000-0000-0000-0000-000000000000'); localStorage.setItem('role', 'PROJECT_MANAGER'); });
    await page.goto(`${WEB}/portfolio`); await page.waitForSelector('aside');
    await until(async () => new URL(page.url()).pathname === '/intake', 'a forged role does not open the PM pages: the engineer lands on Claim Intake');
    const nav = await navLabels(page); ok(!nav.includes('Schedule') && !nav.includes('Portfolio'), `menu unchanged after tampering: ${nav}`);
    ok(/Ritu/i.test(await page.locator('aside').innerText()), 'still the real person');
    ok(/SITE_ENGINEER/.test(await page.locator('aside').innerText()), 'role comes from the server, not from local storage');
    if (a) eq((await api(page, 'GET', `/projects/${a}/dashboard/summary`)).json.error.code, 'NOT_A_MEMBER', "another project's data is refused by the API");
    eq((await api(page, 'POST', `/projects/${S.pid}/schedule-imports`)).status, 403, 'schedule management is refused by the API whatever the UI shows');
  }],

  ['Isolation: invalid, expired or revoked sessions end at the login page', async ({ browser }) => {
    const ctx = await browser.newContext(); const page = await ctx.newPage();
    await page.addInitScript(() => localStorage.setItem('setu_v2_access_token', 'not.a.valid.token'));
    await page.goto(`${WEB}/`); await until(async () => new URL(page.url()).pathname === '/login', 'garbage token -> login');
    ok(await page.evaluate(() => localStorage.getItem('setu_v2_access_token')) === null, 'the dead token is cleared');
    await ctx.close();
  }],

  ['Isolation: a session that dies mid-use returns to sign-in on the next request', async ({ S }) => {
    const { page } = S.se;
    await page.goto(`${WEB}/intake`); await page.waitForSelector('aside');
    await page.evaluate(() => localStorage.setItem('setu_v2_access_token', 'expired.token.value'));
    await page.getByRole('link', { name: 'My Updates' }).click({ timeout: 3000 }).catch(() => {});      // a background poll may already have hit the 401
    await until(async () => new URL(page.url()).pathname === '/login', 'a 401 sends the person back to sign in');
  }],

  ['Isolation: a network failure shows a clear, recoverable error (never a blank page)', async ({ browser }) => {
    const { page, ctx } = await signIn(browser, 'meera.das');
    await ctx.route('**/api/v2/projects', (r) => r.abort());
    await page.reload();
    await page.waitForSelector('[role=alert]'); ok(/Unable to reach the SetuAI v2 server|Could not load/.test(await page.locator('[role=alert]').innerText()), 'clear message');
    await ctx.unroute('**/api/v2/projects');
    await page.getByRole('button', { name: /retry/i }).click();
    await page.waitForSelector('aside a'); await until(async () => (await page.locator('[role=alert]').count()) === 0, 'recovered after retry');
    await ctx.close();
  }],

  ['Isolation: a suspended person loses access at once; reactivation restores it', async ({ S }) => {
    const pm = S.rohit.page;
    const pidB = S.newProject;                                               // the project rohit created; sneha was added to it as a Site Engineer
    const m = (await api(pm, 'GET', `/projects/${pidB}/members`)).json;
    const members = Array.isArray(m) ? m : (m.items ?? m.members ?? []);
    const sneha = members.find((m) => m.email === 'sneha.pillai@seed.setuai.local');
    eq((await api(pm, 'PATCH', `/projects/${pidB}/members/${sneha.user_id}`, { status: 'SUSPENDED' })).status, 200, 'PM suspends');
    const { page: p2 } = await signIn(S.browser, 'sneha.pillai');
    const codes = (await api(p2, 'GET', '/projects')).json.map((p) => p.project_code);
    ok(!codes.includes('UI-E2E-1') && codes.includes('AEC-OFFSHORE'), `the suspended project is gone from her list; her other project remains: ${codes}`);
    eq((await api(p2, 'GET', `/projects/${pidB}/dashboard/summary`)).json.error.code, 'NOT_A_MEMBER', 'the API refuses her at once');
    eq((await api(p2, 'POST', `/projects/${pidB}/claims`, { event_date: '2026-01-01', raw_text: 'after suspension', claimed_pct: 5 })).status, 403, 'she cannot file claims there');
    await p2.reload(); await p2.waitForSelector('aside a');
    await p2.getByTestId('project-switcher').click();
    ok(!/UI-E2E-1/.test(await p2.getByRole('listbox').innerText()), 'the UI no longer offers the suspended project'); await p2.keyboard.press('Escape');
    await api(pm, 'PATCH', `/projects/${pidB}/members/${sneha.user_id}`, { status: 'ACTIVE' });
    await p2.reload(); await p2.waitForSelector('aside a');
    ok((await api(p2, 'GET', '/projects')).json.some((p) => p.project_code === 'UI-E2E-1'), 'access restored: the project is back in her list'); ok((await navLabels(p2)).includes('Claim Intake'), 'and the engineer menu is intact');
  }],

  ['Isolation: the v2 UI only ever talks to the v2 API; the legacy demo build is unchanged and never touches it', async ({ browser, S }) => {
    for (const who of [S.se, S.sup]) {
      const bad = who.log?.requests?.filter((u) => !u.startsWith(API) && !u.startsWith(`http://127.0.0.1:${CFG.apiPort}/`) && !/fonts\.(googleapis|gstatic)/.test(u));
      eq(bad ?? [], [], 'only the configured v2 API (and web fonts) were contacted');
      ok((who.log?.requests ?? []).length > 0, 'requests were observed');
    }
    const ctx = await browser.newContext(); const page = await ctx.newPage(); const reqs = [];
    page.on('request', (r) => reqs.push(r.url()));
    await page.goto(`http://127.0.0.1:${CFG.legacyPort}/login`); await page.waitForSelector('input[type=email]');
    const body = await page.locator('body').innerText();
    ok(/Supervisor/.test(body) && /Site Engineer/.test(body) && !/Project Manager/.test(body) && !/anita\.bora/.test(body), 'legacy login page unchanged (two demo roles, no v2 identities)');
    ok(!reqs.some((u) => u.includes(`:${CFG.apiPort}`)), 'the legacy build never calls the v2 API');
    ok(await page.evaluate(() => localStorage.getItem('setu_v2_access_token')) === null, 'no v2 session keys in the legacy build');
    await ctx.close();
  }],
];
