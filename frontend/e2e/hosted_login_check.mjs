// READ-ONLY check of the deployed login: the demo buttons fill the @anvyra.demo email AND the password, sign-in works with no typing, role menus, merged PM page. Non-GET API calls are aborted.
import { launch } from './lib.mjs';
const WEB = 'https://anvyra-web.vercel.app', API = 'https://anvyra-api.vercel.app';
const b = await launch(); const out = []; const blocked = [];
const check = (n, c, d = '') => { out.push(c); console.log(`${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  -> ' + d}`); };
const labels = async (p) => (await p.locator('aside a').allInnerTexts()).map((t) => t.replace(/\s+\d+\+?$/, '').trim());
for (const [chip, handle, landing, extra] of [['Project Manager', 'farah.khan', '/portfolio', 'pm'], ['Supervisor', 'imran.hussain', '/dashboard', ''], ['Site Engineer', 'ritu.baruah', '/intake', '']]) {
  const ctx = await b.newContext({ viewport: { width: 1280, height: 900 } }); const page = await ctx.newPage();
  await ctx.route(`${API}/**`, (r) => { const m = r.request().method(); if (['GET', 'HEAD', 'OPTIONS'].includes(m) && !/\/agent\//.test(r.request().url())) return r.continue(); blocked.push(`${m} ${new URL(r.request().url()).pathname}`); return r.abort('blockedbyclient'); });
  await page.goto(`${WEB}/login`);
  await page.getByTestId(`identity-${handle}`).click();
  const email = await page.locator('input[type=email]').inputValue(); const pwLen = (await page.locator('input[type=password]').inputValue()).length;
  check(`${chip}: button fills ${handle}@anvyra.demo`, email === `${handle}@anvyra.demo`, email);
  check(`${chip}: button fills the password too`, pwLen >= 12, String(pwLen));
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForFunction(() => location.pathname !== '/login' && location.pathname !== '/', null, { timeout: 60000 }); await page.waitForSelector('aside', { timeout: 30000 });
  check(`${chip}: signed in with no typing and landed on ${landing}`, new URL(page.url()).pathname === landing, page.url());
  if (extra === 'pm') {
    const nav = await labels(page);
    check('PM menu has ONE Project Intelligence entry and no separate Project Knowledge', nav.filter((l) => /Intelligence/.test(l)).length === 1 && !nav.includes('Project Knowledge'), nav.join('|'));
    await page.goto(`${WEB}/intelligence?tab=knowledge`); await page.waitForSelector('[data-testid=pi-tab-knowledge]', { timeout: 30000 }); await page.waitForSelector('[data-testid=knowledge-page]', { timeout: 30000 });
    check('Knowledge tab shows the entries inside Project Intelligence', (await page.getByTestId('pi-tab-intelligence').count()) === 1 && await page.getByTestId('knowledge-entry').count() > 20);
    await page.goto(`${WEB}/knowledge`); await page.waitForURL(/intelligence\?tab=knowledge/, { timeout: 20000 });
    check('/knowledge redirects to the tab', /tab=knowledge/.test(page.url()));
  }
  await ctx.close();
}
await b.close();
console.log(`${out.filter(Boolean).length}/${out.length} checks passed; blocked non-GET calls: ${blocked.length}`);
process.exit(out.every(Boolean) ? 0 : 1);
