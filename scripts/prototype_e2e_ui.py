#!/usr/bin/env python3
"""
Browser end-to-end for the prototype: a REAL headless Chrome (DevTools Protocol, no extra packages) against the running
demo backend (:8010) and frontend (:5183) on the isolated demo database. Logs in through the real login screen as each role.

    scripts/run_local_demo.sh setup | backend | frontend     (three terminals / background)
    scripts/prototype_e2e_api.py                              (optional: seeds decisions + an issue for the engineer's view)
    scripts/prototype_e2e_ui.py [--shots DIR]

Checks what a person sees: the four dashboards (DB-driven stage + discipline progress), the removed sections are GONE,
the batch upload, issue reporting with memory suggestions, root-cause view, and the engineer's decision updates.
CHROME_BIN overrides the browser path. It adds a batch and an issue to the demo database.
"""
import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import requests
import websockets

FRONT, BACK, PW = "http://127.0.0.1:5183", "http://127.0.0.1:8010", "Demo123456!"
CHROMES = [
    os.environ.get("CHROME_BIN", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    str(Path.home() / "Desktop/Google Chrome.app/Contents/MacOS/Google Chrome"),
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
FAILS, OKS = [], 0


def check(cond, msg):
    global OKS
    if cond:
        OKS += 1
        print(f"  ok    {msg}")
    else:
        FAILS.append(msg)
        print(f"  FAIL  {msg}")


class Browser:
    def __init__(self, shots: Path):
        self.shots, self.n, self.errors = shots, 0, []
        shots.mkdir(parents=True, exist_ok=True)

    async def start(self):
        exe = next((c for c in CHROMES if c and Path(c).exists()), None)
        if not exe:
            sys.exit("Chrome not found: set CHROME_BIN")
        self.profile = tempfile.mkdtemp(prefix="setuai-chrome-")
        self.proc = subprocess.Popen([exe, "--headless=new", "--remote-debugging-port=9333", f"--user-data-dir={self.profile}",
                                      "--window-size=1500,1100", "--no-first-run", "--no-default-browser-check", "--disable-gpu", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json"))
                page = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.5)
        else:
            sys.exit("could not reach Chrome")
        self.ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024)
        self.i, self.pending = 0, {}
        self.reader = asyncio.create_task(self._read())
        for m in ("Page.enable", "Runtime.enable", "DOM.enable"):
            await self.call(m)

    async def _read(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.pending:
                self.pending.pop(msg["id"]).set_result(msg)
            elif msg.get("method") == "Runtime.exceptionThrown":
                d = msg["params"]["exceptionDetails"]
                self.errors.append((d.get("exception", {}).get("description") or d.get("text", ""))[:300])

    async def call(self, method, **params):
        self.i += 1
        fut = asyncio.get_event_loop().create_future()
        self.pending[self.i] = fut
        await self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        msg = await asyncio.wait_for(fut, 60)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("text", "js error") + " " + json.dumps(r["exceptionDetails"])[:200])
        return r["result"].get("value")

    async def wait(self, expr, timeout=25, what=""):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if await self.js(f"Boolean({expr})"):
                    return True
            except RuntimeError:
                pass
            await asyncio.sleep(0.3)
        print(f"        (timed out waiting for: {what or expr[:80]})")
        return False

    async def goto(self, path):
        await self.call("Page.navigate", url=f"{FRONT}{path}")
        await asyncio.sleep(0.8)
        await self.wait("document.readyState === 'complete'", 15)

    async def text(self):
        return await self.js("document.body.innerText")

    async def shot(self, name):
        self.n += 1
        data = (await self.call("Page.captureScreenshot", format="png", captureBeyondViewport=True))["data"]
        path = self.shots / f"{self.n:02d}_{name}.png"
        path.write_bytes(base64.b64decode(data))
        return path

    async def fill(self, sel, value):
        await self.js("""((sel, v) => { const el = document.querySelector(sel); if (!el) throw new Error('missing ' + sel);
            const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
            el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true})); })(%s, %s)""" % (json.dumps(sel), json.dumps(value)))

    async def click_text(self, text, tag="button"):
        return await self.js("""((t, tag) => { const el = [...document.querySelectorAll(tag)].find(e => e.innerText && e.innerText.trim().includes(t) && !e.disabled);
            if (!el) return false; el.click(); return true; })(%s, %s)""" % (json.dumps(text), json.dumps(tag)))

    async def set_files(self, sel, files):
        doc = await self.call("DOM.getDocument", depth=1)
        node = await self.call("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector=sel)
        await self.call("DOM.setFileInputFiles", files=[str(f) for f in files], nodeId=node["nodeId"])

    async def login(self, email):
        await self.goto("/login")            # storage is per-origin: be on the app's origin before clearing it
        await self.js("localStorage.clear()")
        await self.goto("/login")
        await self.wait("document.querySelector('input[type=email]')", 20, "login form")
        await self.fill("input[type=email]", email)
        await self.fill("input[type=password]", PW)
        await self.js("document.querySelector('button[type=submit]').click()")
        await self.wait("location.pathname !== '/login'", 25, "redirect after login")
        await asyncio.sleep(1.5)

    async def select_project(self, pid, path):
        await self.js(f"localStorage.setItem('setu_selected_project_id_v7', {json.dumps(pid)})")
        await self.goto(path)

    async def close(self):
        try:
            await self.ws.close()
        finally:
            self.proc.terminate()


def api(email):
    tok = requests.post(f"{BACK}/api/v1/auth/local-login", json={"email": email, "password": PW}, timeout=30).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


async def main(shots_dir):
    h = api("supervisor@setuai.demo")
    projects = {p["project_code"]: p["project_id"] for p in requests.get(f"{BACK}/api/v1/projects", headers=h).json()}
    check(len(projects) == 4, f"the API lists exactly four projects: {sorted(projects)}")
    tmp = Path(tempfile.mkdtemp(prefix="setuai-ui-"))
    (tmp / "NRL_ui_report_2026-09-30.txt").write_text(
        "NRL DAILY PROGRESS REPORT\nReport date: 2026-09-30\n"
        "NRL-PEI-030 | Radiography and NDT of process welds | 28 % complete\n"
        "Fired heater erection reached 48 percent at Unit 5 heater bay\n"
        "Cable tray, cable laying and termination 38 percent complete at Unit 5 cable corridors\n")
    (tmp / "NRL_ui_progress_2026-09-30.csv").write_text(
        "Activity ID,Activity Name,Discipline,Report Date,Progress Pct\n"
        "NRL-PEI-060,Field instrument installation and tubing,INSTRUMENTATION,2026-09-30,26\n"
        "NRL-PEI-010,Process piping fabrication and spool preparation,PIPING,2026-09-30,60\n")

    b = Browser(shots_dir)
    await b.start()
    try:
        print("A. Supervisor: login screen -> dashboards for all four projects")
        await b.login("supervisor@setuai.demo")
        check(await b.js("location.pathname") in ("/dashboard", "/digest", "/"), f"supervisor lands on {await b.js('location.pathname')}")
        await b.select_project(projects["NRL-EXP-01"], "/dashboard")
        await b.wait("document.querySelector('[data-testid=project-progress-panel]')", 30, "progress panel")
        nav = await b.text()
        for item in ("Review Workspace", "Root Cause & Memory", "Dashboard"):
            check(item in nav, f"supervisor navigation has '{item}'")
        check("Issues & Delays" in nav and "My Updates" not in nav, "supervisor sees Issues & Delays but not the engineer's My Updates")
        for code, label, must in (("NNB-COP-01", "Completed", "100%"), ("AND-ODC-01", "Ongoing", None), ("NRL-EXP-01", "Ongoing", None), ("SMP-CCP-01", "Upcoming", "not started")):
            await b.select_project(projects[code], "/dashboard")
            await b.wait("document.querySelector('[data-testid=project-progress-panel]')", 30, f"panel {code}")
            t = await b.text()
            stages = await b.js("document.querySelectorAll('[data-testid=project-progress-panel] [data-testid=progress-row]').length")
            badge = await b.js("document.querySelector('[data-testid=lifecycle-badge]')?.innerText || ''")
            overall = await b.js("document.querySelector('[data-testid=overall-progress]')?.innerText || ''")
            check(label in badge, f"{code}: lifecycle badge reads '{badge.strip()}', overall {overall}")
            check("Stage-wise progress" in t and "Discipline-wise progress" in t, f"{code}: stage-wise and discipline-wise sections render ({stages} progress rows)")
            if must:
                check(must in t, f"{code}: dashboard shows '{must}'")
            if code == "NNB-COP-01":
                check(overall.strip() == "100%", f"{code}: overall progress is exactly 100%")
            if code == "SMP-CCP-01":
                check(overall.strip() == "0%" and "Planned · not started" in t, f"{code}: planned work is shown as not started, not as progress")
            check("Downstream Schedule Impact Watch" not in t, f"{code}: 'Downstream Schedule Impact Watch' is gone from the dashboard")
            await b.shot(f"dashboard_{code}")

        print("B. Supervisor: review workspace, issues, root cause & memory")
        await b.select_project(projects["NRL-EXP-01"], "/review")
        await b.wait("document.body.innerText.length > 800", 30, "review workspace")
        await asyncio.sleep(2)
        t = await b.text()
        check("Multi-Source Evidence Fusion" not in t and "Construction Knowledge Graph" not in t and "Evidence Fusion" not in t, "Review Workspace has no Evidence Fusion / Knowledge Graph section")
        await b.shot("review_workspace")
        await b.goto("/issues")
        await b.wait("document.querySelector('[data-testid=issue-card]')", 25, "issues")
        t = await b.text()
        check("Issues & Delays" in t and "Resolve" in t and "Report an issue or delay" not in t, "supervisor's Issues page lists issues with a Resolve action, and no reporting form")
        await b.shot("issues_supervisor")
        await b.goto("/root-cause")
        await b.wait("document.querySelector('[data-testid=category-pattern]')", 25, "root cause")
        await b.wait("document.querySelector('[data-testid=memory-record]')", 25, "memory")
        t = await b.text()
        check("Repeated pattern" in t and "Skilled-labour shortage" in t, "root-cause view flags the repeated labour-shortage pattern and lists the identified root cause")
        check("Shared ·" in t and "Naharkatiya" in t, "institutional memory shows a lesson shared from the completed pipeline project")
        await b.shot("root_cause_memory")

        print("C. Site Engineer: login, multi-file batch")
        await b.login("engineer@setuai.demo")
        check(await b.js("location.pathname") == "/intake", "engineer lands on Claim Intake")
        await b.select_project(projects["NRL-EXP-01"], "/intake")
        await b.wait("document.querySelector('[data-testid=batch-file-input]')", 30, "batch input")
        nav = await b.text()
        check("Issues & Delays" in nav and "My Updates" in nav and "Review Workspace" not in nav and "Root Cause" not in nav, "engineer navigation: Intake, Issues & Delays, My Updates only")
        check("Batch upload" in nav, "Claim Intake has a Batch upload tab")
        await b.set_files("[data-testid=batch-file-input]", [tmp / "NRL_ui_report_2026-09-30.txt", tmp / "NRL_ui_progress_2026-09-30.csv"])
        await b.wait("document.body.innerText.includes('Process 2 files')", 10, "selected files")
        await b.shot("batch_selected")
        check(await b.click_text("Process 2 files"), "clicked 'Process 2 files'")
        await b.wait("document.body.innerText.toLowerCase().includes('activities identified')", 90, "batch result")
        t = await b.text()
        check("Batch result" in t and "activities identified" in t.lower(), "batch result: files, activities identified and claims are shown")
        for f in ("NRL_ui_report_2026-09-30.txt", "NRL_ui_progress_2026-09-30.csv"):
            check(f in t, f"result lists '{f}'")
        for act in ("NRL-PEI-030", "NRL-PEI-060", "NRL-PEI-010"):
            check(act in t, f"claim matched to {act}")
        check("Rule-based extraction" in t and "Structured sheet" in t, "extraction method is visible per file (no LLM was used)")
        await b.shot("batch_result")

        print("D. Site Engineer: report an issue (memory suggestions), decisions")
        await b.goto("/issues")
        await b.wait("document.querySelector('select[aria-label=\"Issue category\"]')", 25, "issue form")
        await b.wait("document.querySelector('select[aria-label=\"Issue category\"]').options.length > 5", 20, "categories loaded")
        await b.wait("document.querySelector('select[aria-label=\"Affected stage\"]').options.length > 3", 20, "stages loaded")
        await b.fill("select[aria-label='Issue category']", "MATERIAL_DELIVERY_DELAY")
        stage_val = await b.js("[...document.querySelector('select[aria-label=\"Affected stage\"]').options].find(o => o.text.includes('Structural'))?.value || ''")
        await b.fill("select[aria-label='Affected stage']", stage_val)
        await b.fill("input[aria-label='Title']", "Cable tray consignment delayed in transit")
        await b.fill("textarea[aria-label='Description']", "Vendor logistics partner missed dispatch; cable tray consignment is delayed by three weeks.")
        await b.fill("input[aria-label='Expected duration in days']", "14")
        await b.wait("document.querySelector('[data-testid=similar-incidents]')", 20, "similar incidents")
        t = await b.text()
        check("Similar past incidents" in t and "Line pipe delivery delayed by mill logistics" in t, "typing the issue retrieves a similar past incident from institutional memory")
        await b.shot("issue_form_with_memory")
        check(await b.click_text("Report issue"), "submitted the issue")
        await b.wait("document.body.innerText.includes('Reported:')", 20, "confirmation")
        check("Reported:" in await b.text(), "issue reported and confirmed on screen")
        await b.shot("issue_reported")
        await b.goto("/updates")
        await b.wait("document.querySelector('[data-testid=update-card]')", 25, "updates")
        t = await b.text()
        for want in ("Rejected", "On hold", "Welding records for the counted joints are missing.", "Demo Supervisor"):
            check(want in t, f"My Updates shows '{want}'")
        check("Approved" in t, "My Updates shows an approval")
        await b.shot("my_updates")

        check(not b.errors, f"no uncaught browser exceptions ({len(b.errors)})")
        for e in b.errors[:5]:
            print("        exception:", e[:200])
    finally:
        await b.close()

    print(f"\n{OKS} checks ok, {len(FAILS)} failed. Screenshots: {shots_dir}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=str(Path(tempfile.gettempdir()) / "setuai-shots"))
    asyncio.run(main(Path(ap.parse_args().shots)))
