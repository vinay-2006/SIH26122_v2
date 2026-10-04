#!/usr/bin/env node
// Real-browser end-to-end tests of the v2-mode frontend against the real v2 API and a throw-away local database.
//   npm run e2e                 all scenarios          npm run e2e -- --only "Supervisor"   scenarios whose title contains the text
// Needs: a local PostgreSQL cluster (scripts/db_v2.sh), Chromium (E2E_CHROMIUM or the Playwright cache). Uses database setuai_v2_fe_e2e, API :8021, web :5191/:5192.
import { ART, Fail, launch, shot, startStack, stopStack, warmUp } from './lib.mjs';
import pm from './scenarios/pm.mjs';
import engineer from './scenarios/engineer.mjs';
import supervisor from './scenarios/supervisor.mjs';
import isolation from './scenarios/isolation.mjs';

const only = process.argv.includes('--only') ? process.argv[process.argv.indexOf('--only') + 1] : null;
const all = [...pm, ...engineer, ...supervisor, ...isolation];
const results = [];
let browser;
const S = {};
try {
  await startStack();
  browser = await launch();
  S.browser = browser;
  await warmUp(browser);
  for (const [title, fn] of all) {
    if (only && !title.includes(only)) continue;
    const t0 = Date.now();
    try { await fn({ browser, S }); results.push({ title, ok: true, ms: Date.now() - t0 }); console.log(`PASS  ${title}  (${((Date.now() - t0) / 1000).toFixed(1)}s)`); }
    catch (e) {
      results.push({ title, ok: false, ms: Date.now() - t0, error: e });
      console.log(`FAIL  ${title}\n      ${e instanceof Fail ? e.message : e.stack?.split('\n').slice(0, 4).join('\n      ')}`);
      for (const [k, v] of Object.entries(S)) if (v?.page) { try { await shot(v.page, `FAIL_${results.length}_${k}`); } catch { /* page closed */ } }
    }
  }
} catch (e) {
  console.error('harness error:', e.message); results.push({ title: 'harness', ok: false, error: e });
} finally {
  try { await browser?.close(); } catch { /* closed */ }
  stopStack();
}
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} scenarios passed${failed.length ? `; screenshots of failures are in ${ART}` : ''}`);
process.exit(failed.length ? 1 : 0);
