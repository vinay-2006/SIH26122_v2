#!/usr/bin/env python3
"""Generate the NRL-EXPANSION demo progress reports (CSV, TXT, PDF, XLSX, DOCX, two evidence photographs and one deliberately flawed CSV) from
sample_data/demo_v2/nrl_expansion/basis.json: the seeded schedule's activities, measured resources, planned quantities and approved cumulative quantities
(a read-only snapshot of the synthetic demo project, 2026-10-04). Fully deterministic; every figure is fictional demonstration data.

    python3 scripts/make_demo_reports_v2.py            # writes into sample_data/demo_v2/nrl_expansion/
Each file is well under 1 MB (the deployed upload limit is about 4 MB)."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sample_data" / "demo_v2" / "nrl_expansion"
BASIS = {a["id"]: a for a in json.loads((OUT / "basis.json").read_text())}
PROJECT = "Numaligarh Refinery Expansion (NRL-EXPANSION)"
FOOT = "Fictional demonstration data for ANVYRA. Not a real project record."


def res(act: str, code: str) -> dict:
    return next(r for r in BASIS[act]["res"] if r["code"] == code)


def row(date, act, code, today, desc, status="Ongoing", remark="", evidence=""):
    a, r = BASIS[act], res(act, code)
    prior = round(r["cum"])
    cum = prior + today
    measured = [x for x in a["res"] if x["mp"]]
    pct = round(min(99.9, a["pct"] + max(0.3, 100.0 * today / r["base"] / max(1, len(measured)))), 1) if a["state"] == "IN_PROGRESS" else round(min(100.0, 100.0 * cum / r["base"]), 1)
    return dict(date=date, act=act, disc=a["disc"].title().replace("_", " "), code=code, uom=r["uom"], planned=round(r["base"]), prior=prior, today=today, cum=cum, pct=pct,
                desc=desc, status=status, remark=remark, evidence=evidence, task=f"{act}-01")


# ------------------------------------------------------------------------------------------------ the figures of each report
CSV_ROWS = [
    row("2026-10-03", "NRE-2020", "CDU2_STEEL_T", 38, "Structural steel erection, CDU2 main pipe rack bays 14-16", remark="38 t erected; bolt torque checks done", evidence="NRL_site_photo_CDU2_steel_2026-10-03.jpg"),
    row("2026-10-03", "NRE-2030", "CDU2_VESSELS_NOS", 1, "CDU2 column C-2201 lifted and set on its skirt foundation", remark="1 vessel set; grouting to follow"),
    row("2026-10-03", "NRE-2060", "CDU2_PANELS_NOS", 2, "CDU2 electrical: LV distribution panels installed in substation SS-2", remark="2 panels placed and levelled"),
    row("2026-10-03", "NRE-2070", "CDU2_LOOPS_NOS", 18, "CDU2 instrumentation: loops terminated and loop-checked", remark="18 loops complete"),
    row("2026-10-03", "NRE-2080", "CDU2_INSUL_M2", 620, "CDU2 insulation (CDU2_INSUL_M2): hot insulation cladding on lines P-2090 to P-2102", remark="620 m2 cladded"),
    row("2026-10-03", "NRE-4050", "HDT_PIPING_JOINTS", 120, "Hydrotreater process piping fabrication: joints welded on reactor feed lines", remark="120 joints; two repairs after radiography", evidence="NRL_site_photo_HDT_piping_2026-10-03.jpg"),
    row("2026-10-03", "NRE-4060", "HDT_PANELS_NOS", 1, "Hydrotreater electrical: MCC panel installed in substation SS-4", remark="1 panel placed"),
    row("2026-10-03", "NRE-7020", "DCS_CABINETS", 1, "DCS system cabinet installed in the control room", remark="cabinet 8 of 24 set and anchored"),
]
TXT_ITEMS = [("NRE-4060", "Hydrotreater electrical cabling and power distribution: 175 m of cable tray installed today (HDT_TRAY_M).")]
PDF_ITEMS = [("NRE-4050", "Hydrotreater process piping fabrication and erection: 9 spools erected today (HDT_SPOOLS_NOS).")]
DOCX_ITEMS = [("NRE-4050", "Hydrotreater process piping fabrication and erection: 95 joints welded today (HDT_PIPING_JOINTS).")]
TYPED = [   # for "Type Update" (Site Engineer). Wording matters to the rule-based reader: the full activity words, one measure, the unit, the resource code in brackets.
    ("NRE-2060", "Crude Distillation Unit 2 electrical cabling and power distribution: 2 panels installed today (CDU2_PANELS_NOS).", "clean claim"),
    ("NRE-4050", "Hydrotreater process piping fabrication and erection: 110 joints welded today (HDT_PIPING_JOINTS).", "clean claim"),
    ("NRE-4060", "Hydrotreater electrical cabling and power distribution: 150 m of cable tray installed today (HDT_TRAY_M).", "clean claim"),
    ("NRE-4070", "Hydrotreater instrumentation installation and loop checking commenced today: 300 m of instrument cable laid (HDT_INSTR_CABLE_M).", "first report on a not-started activity (actual start)"),
    ("NRE-4060", "Hydrotreater electrical cabling and power distribution: 60 panels installed today (HDT_PANELS_NOS).", "more panels than the baseline holds: above-baseline warning, Supervisor acknowledgement needed"),
    ("NRE-4050", "Hydrotreater process piping fabrication and erection: 8 tonnes of pipe material received today.", "wrong unit for the activity (it measures joints and spools)"),
    ("-", "Good progress on site today, all teams working well.", "vague: no activity, no quantity, so it is not matched and goes to a Supervisor"),
]
DAILY_TXT = [  # multi-item daily report: needs the language-model reader on the API (the rule-based reader cannot read two-part activity ids)
    row("2026-10-02", "NRE-2030", "CDU2_VESSELS_NOS", 1, "CDU2 vessel V-2204 lifted and set on its saddles"),
    row("2026-10-02", "NRE-2020", "CDU2_STEEL_T", 41, "CDU2 pipe rack structural steel erection, bays 11-13"),
    row("2026-10-02", "NRE-4050", "HDT_SPOOLS_NOS", 9, "Hydrotreater piping spools erected on the rack"),
    row("2026-10-02", "NRE-4060", "HDT_TRAY_M", 150, "Hydrotreater cable tray installed along the pipe rack"),
    row("2026-10-02", "NRE-4070", "HDT_INSTR_CABLE_M", 300, "Hydrotreater instrument cable laying commenced", status="Started"),
    row("2026-10-02", "NRE-7020", "DCS_CABINETS", 1, "DCS cabinet installed in the control room"),
]
DAILY_PDF = [
    row("2026-10-01", "NRE-2060", "CDU2_PANELS_NOS", 3, "CDU2 LV distribution panels installed in substation SS-2"),
    row("2026-10-01", "NRE-2070", "CDU2_LOOPS_NOS", 18, "CDU2 instrument loops terminated and loop-checked"),
    row("2026-10-01", "NRE-2080", "CDU2_INSUL_M2", 540, "CDU2 hot insulation cladding on lines P-2090 to P-2102"),
    row("2026-10-01", "NRE-4060", "HDT_CABLE_M", 2900, "Hydrotreater power cable pulled and terminated"),
    row("2026-10-01", "NRE-2030", "CDU2_EXCH_NOS", 1, "CDU2 shell and tube exchanger E-2210 set on foundations"),
]
DAILY_DOCX = [
    row("2026-09-30", "NRE-2020", "CDU2_STEEL_T", 35, "CDU2 structural steel erection continued on the pipe rack"),
    row("2026-09-30", "NRE-4050", "HDT_PIPING_JOINTS", 110, "Hydrotreater process piping joints welded"),
    row("2026-09-30", "NRE-4060", "HDT_PANELS_NOS", 1, "Hydrotreater MCC panel installed in substation SS-4"),
    row("2026-09-30", "NRE-7020", "DCS_CABINETS", 1, "DCS cabinet set and anchored in the control room"),
]


def daily_text(rows, iso_date: str, title: str) -> str:
    out = [f"{title} - {iso_date}", f"Report date: {iso_date}", f"{PROJECT}", "Reporting period: 0800 h to 1800 h", "", "WORK EXECUTED"]
    for i, r in enumerate(rows, 1):
        unit = {"JOINT": "joints", "TONNE": "tonnes", "NOS": "nos"}.get(r["uom"], r["uom"].lower())
        out += [f"{i}. {r['act']} | {r['disc']} | {r['desc']}.", f"   Today: {r['today']} {unit} | Cumulative: {r['cum']}/{r['planned']} {unit} | Physical progress: {r['pct']}%"]
    out += ["", "Prepared by: R. Baruah, Site Engineer", FOOT]
    return "\n".join(out) + "\n"


XLSX_SHEETS = {
    "Piping": [row("2026-09-30", "NRE-4050", "HDT_PIPING_JOINTS", 115, "Hydrotreater piping joints welded"), row("2026-09-30", "NRE-2080", "CDU2_INSUL_M2", 580, "CDU2 hot insulation cladding")],
    "Electrical": [row("2026-09-30", "NRE-2060", "CDU2_PANELS_NOS", 1, "CDU2 LV panel installed"), row("2026-09-30", "NRE-4060", "HDT_PANELS_NOS", 1, "Hydrotreater MCC panel installed")],
    "Instrumentation": [row("2026-09-30", "NRE-2070", "CDU2_LOOPS_NOS", 14, "CDU2 loops terminated and checked"), row("2026-09-30", "NRE-7020", "DCS_CABINETS", 1, "DCS cabinet installed")],
    "Structural": [row("2026-09-30", "NRE-2020", "CDU2_STEEL_T", 36, "CDU2 structural steel erected")],
}
FLAWED = [  # (date, id, discipline, description, unit, today, prior, planned, pct, remark): rows that parse but should NOT all sail through
    ("2026-10-03", "NRE-9999", "Civil", "Pipe rack painting at unit 9", "M2", 40, 0, 0, 12.0, "Activity id is not in the schedule"),
    ("2026-10-03", "NRE-4040", "Static Rotating Equipment", "Hydrotreater pumps and compressors installation", "NOS", 2, 0, 0, 100.0, "Activity is already complete"),
    ("2026-10-03", "NRE-2050", "Piping", "CDU2 process piping fabrication and erection", "JOINT", 150, 15030, 27140, 56.9, "Activity is blocked by an open issue"),
    ("2026-10-03", "NRE-2090", "Process", "CDU2 hydrotest packs completed (CDU2_TEST_PACKS)", "NOS", 15, 0, 483, 3.1, "Activity has not started: piping is only 56% complete"),
    ("2026-10-03", "NRE-4060", "Electrical", "Hydrotreater electrical cabling and power distribution", "M", 1500, 0, 0, 40.0, "Reported progress is below what is already approved (54%)"),
    ("2026-10-03", "", "Piping", "Piping work carried out at the hydrotreater, good progress today", "", "", "", "", 30.0, "No activity id and no quantity"),
]
INVALID = [("2026-10-03", "NRE-4060", "Electrical", "Hydrotreater MCC panels installed (HDT_PANELS_NOS)", "NOS", 60, 40, 74, 135.1, "Percentage above 100 is impossible: the whole file is refused")]
HEADER = ["Report Date", "Activity ID", "Task ID", "Discipline", "Work Description", "Unit", "Planned Qty", "Prior Actual", "Today Actual", "Cumulative Actual", "Progress Pct", "Status", "Evidence Reference", "Remarks"]


# ------------------------------------------------------------------------------------------------ writers
def write_csv(path: Path, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            w.writerow([r["date"], r["act"], r["task"], r["disc"], r["desc"], r["uom"], r["planned"], r["prior"], r["today"], r["cum"], r["pct"], r["status"], r["evidence"], r["remark"]])


def report_text(items, iso_date: str, title: str = "SITE FIELD NOTE") -> str:
    lines = [f"{title} - {iso_date}", f"Report date: {iso_date}", f"{PROJECT}, CDU2 and Hydrotreater units", ""]
    lines += [t for _, t in items]
    lines += ["", "Prepared by R. Baruah, Site Engineer.", FOOT]
    return "\n".join(lines) + "\n"


def write_pdf(path: Path, text: str) -> None:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    y = 50
    for line in text.splitlines():
        if y > 780:
            page = doc.new_page(); y = 50
        page.insert_text((50, y), line, fontsize=9.5, fontname="helv")
        y += 13
    doc.save(path)


def write_xlsx(path: Path) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in XLSX_SHEETS.items():
        ws = wb.create_sheet(title)
        ws.append([f"{title} Daily Discipline Report"])
        ws.append([f"{PROJECT} | Report date: 30 Sep 2026"])
        ws.append([])
        ws.append(["L5 Activity ID", "L6 Task ID", "Work description", "Unit", "Planned qty", "Prior actual", "Today actual", "Cumulative actual", "Progress", "Status and evidence"])
        for r in rows:
            ws.append([r["act"], r["task"], r["desc"], r["uom"], r["planned"], r["prior"], r["today"], r["cum"], r["pct"], "Ongoing"])
    wb.save(path)


def write_docx(path: Path, items, lines=None) -> None:
    paras = lines if lines is not None else report_text(items, "2026-09-30", "SITE FIELD NOTE").splitlines()
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(p)}</w:t></w:r></w:p>" for p in paras)
    doc = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct); z.writestr("_rels/.rels", rels); z.writestr("word/document.xml", doc)


def write_flawed(path: Path, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for date, act, disc, desc, uom, today, prior, planned, pct, remark in rows:
            cum = (prior + today) if isinstance(today, int) and isinstance(prior, int) else ""
            w.writerow([date, act, f"{act}-01" if act else "", disc, desc, uom, planned, prior, today, cum, pct, "Ongoing", "", remark])


def write_photo(path: Path, title: str, kind: str) -> None:
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1280, 720))
    d = ImageDraw.Draw(im)
    for y in range(720):                                                   # evening sky
        d.line([(0, y), (1280, y)], fill=(int(30 + y * 0.12), int(60 + y * 0.10), int(110 + y * 0.05)))
    d.rectangle([0, 560, 1280, 720], fill=(58, 52, 46))                    # ground
    if kind == "steel":
        for i in range(6):                                                 # columns and beams of a pipe rack
            x = 140 + i * 190
            d.rectangle([x, 220, x + 18, 560], fill=(176, 90, 40))
        for yb in (240, 360, 480):
            d.rectangle([120, yb, 1180, yb + 14], fill=(196, 104, 48))
        d.line([(1000, 560), (1000, 90), (780, 70)], fill=(240, 200, 40), width=8)    # crane boom
    else:
        d.rectangle([90, 430, 1200, 470], fill=(150, 154, 158))            # a long pipe run on sleepers
        d.rectangle([90, 395, 1200, 425], fill=(132, 136, 140))
        for x in range(120, 1200, 160):
            d.rectangle([x, 470, x + 30, 560], fill=(92, 86, 80))
        for x in range(200, 1200, 320):
            d.ellipse([x, 380, x + 70, 440], fill=(255, 190, 90))          # weld glow
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 30)
    except Exception:
        font = ImageFont.load_default()
    d.rectangle([0, 650, 1280, 720], fill=(0, 0, 0))
    d.text((24, 668), title, fill=(255, 255, 255), font=font)
    im.save(path, "JPEG", quality=82)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "NRL_daily_progress_2026-10-03.csv", CSV_ROWS)
    (OUT / "NRL_field_note_HDT_tray_2026-10-02.txt").write_text(report_text(TXT_ITEMS, "2026-10-02"), encoding="utf-8")
    write_pdf(OUT / "NRL_field_note_HDT_spools_2026-10-01.pdf", report_text(PDF_ITEMS, "2026-10-01"))
    (OUT / "NRL_daily_report_2026-10-02.txt").write_text(daily_text(DAILY_TXT, "2026-10-02", "DAILY PROGRESS REPORT"), encoding="utf-8")
    write_pdf(OUT / "NRL_daily_report_2026-10-01.pdf", daily_text(DAILY_PDF, "2026-10-01", "DAILY PROGRESS REPORT"))
    write_docx(OUT / "NRL_site_progress_note_2026-09-30.docx", [], lines=daily_text(DAILY_DOCX, "2026-09-30", "SITE PROGRESS NOTE").splitlines())
    write_xlsx(OUT / "NRL_discipline_progress_2026-09-30.xlsx")
    write_docx(OUT / "NRL_field_note_HDT_joints_2026-09-30.docx", DOCX_ITEMS)
    write_flawed(OUT / "NRL_field_note_with_errors_2026-10-03.csv", FLAWED)
    (OUT / "typed_claims.json").write_text(json.dumps([{"activity": a, "text": t, "expected": e} for a, t, e in TYPED], indent=1))
    write_flawed(OUT / "NRL_invalid_values_2026-10-03.csv", INVALID)
    write_photo(OUT / "NRL_site_photo_CDU2_steel_2026-10-03.jpg", "SYNTHETIC DEMO IMAGE  |  CDU2 pipe rack steel erection  |  03 Oct 2026", "steel")
    write_photo(OUT / "NRL_site_photo_HDT_piping_2026-10-03.jpg", "SYNTHETIC DEMO IMAGE  |  Hydrotreater piping welding  |  03 Oct 2026", "pipe")
    for p in sorted(OUT.iterdir()):
        if p.suffix != ".json" and p.name != "README.md":
            print(f"{p.name:48s} {p.stat().st_size:>8,} bytes")


if __name__ == "__main__":
    main()
