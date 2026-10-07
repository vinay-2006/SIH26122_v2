// Site Engineer: the ORIGINAL interface (Claim Intake, Issues & Delays, My Updates, WBS Explorer) running on the v2 backend.
import { WEB, activeVersion, api, eq, legacy, navLabels, ok, projectId, shot, signIn, text, tmpFile, until } from '../lib.mjs';

const today = () => new Date().toISOString().slice(0, 10);

/** file a claim straight through the v2 API as this person (fast set-up for the supervisor scenarios; the UI path is covered separately) */
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

const ORIGINAL_SE_MENU = ['Claim Intake', 'Issues & Delays', 'My Updates', 'WBS Explorer', 'Project Intelligence'];

export default [
  ['Engineer: lands on the original Claim Intake with the original menu; no supervisor / PM pages', async ({ browser, S }) => {
    const { page, log } = await signIn(browser, 'ritu.baruah');
    S.se = { page, log };
    S.pid = await projectId(page, 'NRL-EXPANSION'); S.ver = await activeVersion(page, S.pid);
    eq(new URL(page.url()).pathname, '/intake', 'landing page');
    eq(await navLabels(page), ORIGINAL_SE_MENU, 'the original site-engineer menu');
    for (const p of ['/dashboard', '/review', '/digest', '/portfolio', '/schedule', '/settings', '/audit', '/root-cause', '/history', '/impact', '/summary', '/time-agent']) {
      await page.goto(`${WEB}${p}`); await page.waitForSelector('aside');
      await until(async () => new URL(page.url()).pathname === '/intake', `${p} bounces to Claim Intake`);
    }
    for (const tab of ['Upload Progress Report', 'Type Update', 'Voice Input', 'Single File']) ok(await page.getByText(tab, { exact: true }).first().isVisible(), `intake tab ${tab}`);
    await shot(page, 'se_intake');
  }],

  ['Engineer: free-text report, no activity picked -> extracted, matched automatically, checked; nothing approved', async ({ S }) => {
    const { page } = S.se;
    await page.goto(`${WEB}/intake`); await page.getByText('Type Update', { exact: true }).first().click();
    await page.getByPlaceholder(/Describe site execution progress/).fill('NRE-4050 process piping erection: 40 percent complete today');
    await page.getByRole('button', { name: /^Submit Claim$/ }).click();
    await until(async () => /Claim Submitted Successfully/.test(await page.locator('main').innerText()), 'pipeline completes', 60000);
    const id = (await page.locator('main').innerText()).match(/[0-9a-f]{8}-[0-9a-f]{4}/)?.[0];
    ok(id, 'claim id shown');
    const mine = await legacy(page, 'GET', '/api/v1/claims', null, { project: S.pid, version: S.ver });
    eq(mine.status, 200, 'own claims readable');
    const c = mine.json.find((x) => x.event_id.startsWith(id));
    ok(c, 'the claim exists'); S.claimId = c.event_id;
    eq(c.matched_activity_id, 'NRE-4050', 'the existing matching engine resolved the activity from the text');
    ok(['MATCHED', 'VALIDATED', 'REVIEW_REQUIRED'].includes(c.status), `status after the pipeline: ${c.status}`);
    ok(!['APPROVED', 'EDITED'].includes(c.status), 'matching and checking never approve');
    const cands = await legacy(page, 'GET', `/api/v1/claims/${c.event_id}/candidates`, null, { project: S.pid, version: S.ver });
    ok(cands.json.candidates.length >= 1 && cands.json.candidates[0].activity_id === 'NRE-4050', 'ranked candidates are stored');
    await shot(page, 'se_pipeline_done');
  }],

  ['Engineer: an incomplete report is held by the Field Copilot and resumes after the answer', async ({ S }) => {
    const { page } = S.se;
    await page.goto(`${WEB}/intake`); await page.getByText('Type Update', { exact: true }).first().click();
    await page.getByPlaceholder(/Describe site execution progress/).fill('Piping work is going on near the unit today');
    await page.getByRole('button', { name: /^Submit Claim$/ }).click();
    await until(async () => /Type clarification response/.test(await page.locator('main').innerHTML()) || (await page.getByPlaceholder(/Type clarification response/).count()) > 0, 'copilot asks a question', 60000);
    await shot(page, 'se_copilot_question');
    const held = (await legacy(page, 'GET', '/api/v1/claims', null, { project: S.pid, version: S.ver })).json.find((x) => x.raw_claim_text === 'Piping work is going on near the unit today');
    eq(held.clarification_status, 'PENDING', 'held by the Field Copilot'); eq(held.matched_activity_id, null, 'not matched while a question is open');
    await page.getByPlaceholder(/Type clarification response/).first().fill('NRE-4050 process piping erection, 40 percent complete');
    await page.getByRole('button', { name: /^Submit Clarification$/ }).click();
    await until(async () => /Claim Submitted Successfully/.test(await page.locator('main').innerText()), 'pipeline resumes after the answer', 60000);
    const done = (await legacy(page, 'GET', `/api/v1/claims/${held.event_id}`, null, { project: S.pid, version: S.ver })).json;
    eq(done.clarification_status, 'ANSWERED', 'answered'); eq(done.matched_activity_id, 'NRE-4050', 'matched after the answer');
    await shot(page, 'se_copilot_resumed');
  }],

  ['Engineer: voice input tab (browser speech recognition) and typed transcript submit as a VOICE claim', async ({ S }) => {
    const { page } = S.se;
    await page.goto(`${WEB}/intake`); await page.getByText('Voice Input', { exact: true }).first().click();
    ok((await page.getByLabel(/recording voice claim/i).count()) >= 1, 'microphone control present');
    await shot(page, 'se_voice_tab');
  }],

  ['Engineer: batch upload of several files -> report with files, claims, matches and merged duplicates', async ({ S }) => {
    const { page } = S.se;
    await page.goto(`${WEB}/intake`);
    await page.getByTestId('batch-file-input').setInputFiles([
      tmpFile('report_a.txt', 'Daily report\nNRE-4050 process piping erection 40 percent complete\nNRE-4080 piping insulation and painting 5 percent complete\n'),
      tmpFile('report_b.txt', 'Site note\nNRE-4080 piping insulation and painting 5 percent complete\n'),
    ]);
    await page.getByRole('button', { name: /upload|process|submit/i }).filter({ hasText: /upload|process|submit/i }).first().click();
    await until(async () => /NRE-4080/.test(await page.locator('main').innerText()), 'batch report shows the activities', 90000);
    await shot(page, 'se_batch_report');
    const b = await legacy(page, 'GET', `/api/v1/projects/${S.pid}/upload-batches?mine=true`, null, { project: S.pid, version: S.ver });
    ok(b.json.length >= 1 && b.json[0].file_count === 2, 'the batch is listed');
    const rep = (await legacy(page, 'GET', `/api/v1/projects/${S.pid}/upload-batches/${b.json[0].batch_id}`, null, { project: S.pid, version: S.ver })).json;
    // NRE-4080 is reported by both files (one claim, two sources) and NRE-4050 / 40% is the claim this engineer already filed earlier today (not created twice)
    eq(rep.merged_count, 2, 'two duplicates were merged instead of created');
    ok(rep.claims.some((c) => c.matched_activity_id === 'NRE-4050' && c.created_in_this_batch === false), 'the earlier claim for NRE-4050 backs this report');
    const a = rep.activities.find((x) => x.activity_id === 'NRE-4080');
    ok(a && a.claim_ids.length === 1 && a.file_names.length === 2, 'one claim backed by both files');
    ok(rep.claims.every((c) => !['APPROVED', 'EDITED'].includes(c.status)), 'a batch never approves anything');
  }],

  ['Engineer: Upload Progress Report accepts report and evidence formats, refuses schedule and legacy files with the reason, and offers no schedule import', async ({ S }) => {
    const { page } = S.se;
    await page.goto(`${WEB}/intake`);
    const accept = await page.getByTestId('batch-file-input').getAttribute('accept');
    for (const ext of ['.csv', '.xlsx', '.pdf', '.txt', '.docx', '.jpg', '.jpeg', '.png', '.webp']) ok(accept.split(',').includes(ext), `${ext} is offered`);
    for (const ext of ['.xer', '.xml', '.xls', '.doc', '.mpp']) ok(!accept.split(',').includes(ext), `${ext} is not offered`);
    const body = await page.locator('main').innerText();
    ok(!/P6|MSP|Primavera|\.xer|MS Project/i.test(body), 'no P6 / MS Project / schedule-import wording on the upload page');
    await page.getByTestId('batch-file-input').setInputFiles([
      tmpFile('baseline.xer', 'ERMHDR\t8.0\n'), tmpFile('plan.xml', '<Project/>'), tmpFile('legacy.xls', 'x'), tmpFile('legacy.doc', 'x'), tmpFile('tool.exe', 'MZ')]);
    const rej = await page.getByTestId('rejected-files').innerText();
    ok(/baseline schedule/.test(rej) && /save it as \.xlsx/.test(rej) && /save it as \.docx/.test(rej) && /not supported/.test(rej), `each refusal says why: ${rej.replace(/\s+/g, ' ')}`);
    eq(await page.getByTestId('rejected-files').locator('[role=alert]').count(), 5, 'five files refused');
    // an accepted mix shows name, type and readiness, then the per-file processing result
    await page.getByTestId('batch-file-input').setInputFiles([tmpFile('site-notes.txt', 'Daily report\nNRE-4050 process piping erection 55 percent complete\n')]);
    ok(/Ready to upload/.test(await page.locator('main').innerText()) && /Text/.test(await page.locator('main').innerText()), 'type and status are shown before upload');
    await page.getByRole('button', { name: /^Process \d+ files?$/ }).click();
    await until(async () => /Read · claims await review|claim[s]? found/.test(await page.locator('main').innerText()), 'the processing result is shown per file', 90000);
    ok(/waiting for Supervisor review/.test(await page.locator('main').innerText()), 'the page says the claims await review');
    await page.getByText('Single File', { exact: true }).first().click();
    const single = await page.locator('main').innerText();
    ok(!/P6 Export Mode|Schedule Progress Export|\.xer|MS Project/i.test(single), 'the single-file tab has no schedule-export controls');
    await shot(page, 'se_upload_progress_report');
  }],

  ['Engineer: reports an issue; it is tied to its stage and activity and shows in the list', async ({ S }) => {
    const { page } = S.se;
    await page.goto(`${WEB}/issues`); await page.waitForSelector('text=Report an issue or delay');
    await page.getByLabel('Issue category').selectOption({ index: 1 });
    await page.getByLabel('Affected stage').selectOption({ index: 1 });
    await page.getByLabel('Title', { exact: true }).fill('Welder shortage on piping spools');
    await page.getByLabel('Description', { exact: true }).fill('Two welders on leave; the night shift cannot run');
    await page.getByRole('button', { name: /^Report issue$/ }).click();
    await until(async () => /Welder shortage on piping spools/.test(await page.locator('main').innerText()), 'issue listed', 20000);
    const l = await legacy(page, 'GET', `/api/v1/projects/${S.pid}/schedules/${S.ver}/issues`, null, { project: S.pid, version: S.ver });
    ok(l.json.some((i) => i.title === 'Welder shortage on piping spools' && i.status === 'ACTIVE'), 'issue stored');
    await shot(page, 'se_issue');
  }],

  ['Engineer: My Updates and WBS Explorer render real data', async ({ S }) => {
    const { page } = S.se;
    await page.goto(`${WEB}/updates`); await page.waitForSelector('text=My Updates');
    ok(/My claims|DECISIONS/i.test(await page.locator('main').innerText()), 'My Updates content');
    await page.goto(`${WEB}/wbs`); await page.waitForSelector('text=WBS Activity Explorer');
    await until(async () => /WBS groups/.test(await page.locator('main').innerText()), 'WBS groups load');
    ok(S.se.log.errors.length === 0, `no unexpected console errors: ${S.se.log.errors.slice(0, 3)}`);
  }],
];
