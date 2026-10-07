// ONE authorized hosted claim through the deployed UI (Site Engineer Ritu Baruah, NRL-EXPANSION). Exactly one POST /api/v1/claims/text is allowed through;
// every other non-GET API call and the audited briefing GET are aborted by the harness. No retry. No cleanup.
import fs from 'node:fs';
import path from 'node:path';
import { launch, ART } from './lib.mjs';
const WEB = 'https://anvyra-web.vercel.app', API = 'https://anvyra-api.vercel.app';
const pw = /^V2_HOSTED_DEMO_PASSWORD=(.+)$/m.exec(fs.readFileSync(path.resolve(import.meta.dirname, '../../.local/hosted.env'), 'utf8'))[1].trim();
const TEXT = 'Hydrotreater process piping fabrication: 12 joints welded today, HDT_PIPING_JOINTS';
const seen = { posts: 0, blocked: [], claimResponse: null, requests: [] };
const browser = await launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
await ctx.route(`${API}/**`, (route) => {
  const r = route.request(), u = new URL(r.url()), m = r.method();
  if (m === 'OPTIONS') return route.continue();
  if (m === 'GET' || m === 'HEAD') {
    if (/\/agent\//.test(u.pathname)) { seen.blocked.push(`GET ${u.pathname} (audited briefing)`); return route.abort('blockedbyclient'); }
    return route.continue();
  }
  if (m === 'POST' && u.pathname === '/api/v1/claims/text' && seen.posts === 0) { seen.posts++; seen.requests.push(`${m} ${u.pathname}`); return route.continue(); }
  seen.blocked.push(`${m} ${u.pathname}`); return route.abort('blockedbyclient');
});
page.on('response', async (res) => {
  const u = new URL(res.url());
  if (res.url().startsWith(API) && u.pathname === '/api/v1/claims/text' && res.request().method() === 'POST') {
    let body = null; try { body = await res.json(); } catch { /* not JSON */ }
    seen.claimResponse = { status: res.status(), body };
  }
});
page.on('requestfailed', (r) => { if (r.url().startsWith(API) && r.method() === 'POST') seen.failed = `${r.method()} ${new URL(r.url()).pathname}: ${r.failure()?.errorText}`; });
await page.goto(`${WEB}/login`);
await page.locator('input[type=email]').fill('ritu.baruah@anvyra.demo');
await page.locator('input[type=password]').fill(pw);
await page.getByRole('button', { name: /sign in/i }).click();
await page.waitForFunction(() => location.pathname === '/intake', null, { timeout: 60000 });
await page.waitForSelector('aside');
await page.waitForTimeout(2500);
// the project selected in the header must be NRL-EXPANSION (the engineer has only this project)
await page.getByRole('tab', { name: /type update/i }).click();
await page.locator('#claim-text-input').fill(TEXT);
console.log('typed claim text length', (await page.locator('#claim-text-input').inputValue()).length);
await page.getByRole('button', { name: /^submit claim$/i }).click();
const t0 = Date.now();
while (!seen.claimResponse && !seen.failed && Date.now() - t0 < 120000) await new Promise((r) => setTimeout(r, 500));
await page.waitForTimeout(4000);
await page.screenshot({ path: path.join(ART, 'hosted_single_claim.png') });
console.log('POSTs allowed through:', seen.posts, JSON.stringify(seen.requests));
console.log('blocked by harness:', JSON.stringify(seen.blocked));
console.log('claims/text response status:', seen.claimResponse?.status, '| request failed:', seen.failed || 'no');
fs.writeFileSync(path.join(ART, 'hosted_single_claim_response.json'), JSON.stringify(seen.claimResponse, null, 1));
console.log('page shows:', (await page.locator('main').innerText()).replace(/\s+/g, ' ').slice(0, 400));
await browser.close();
