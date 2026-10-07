"""Small helpers for the HTTP tests."""
from __future__ import annotations

import io
from datetime import date, timedelta

TODAY = date.today()


def url(kit, path: str) -> str:
    return f"/api/v2/projects/{kit.project}{path}"


def claim_body(kit, ext="A2010", qty=None, uom="joints", basis="CUMULATIVE", pct=None, days_ago=0, text=None, **kw):
    b = {"event_date": str(TODAY - timedelta(days=days_ago)), "raw_text": text or f"Progress on {ext}: {qty if qty is not None else pct}", "activity_uid": str(kit.uid(ext)) if ext else None}
    if qty is not None:
        b["quantities"] = [{"qty": qty, "uom": uom, "basis": basis}]
    if pct is not None:
        b["claimed_pct"] = pct
    b.update(kw)
    return b


def file_claim(kit, api, ext="A2010", qty=None, who=None, expect=201, headers=None, **kw):
    r = api.post(url(kit, "/claims"), who or kit.world.se, json=claim_body(kit, ext, qty, **kw), headers=headers or {})
    assert r.status_code == expect, r.text
    return r.json()


def decide(kit, api, claim_id, who=None, expect=201, **body):
    body.setdefault("action", "APPROVE")
    r = api.post(url(kit, f"/claims/{claim_id}/decision"), who or kit.world.sup, json=body)
    assert r.status_code == expect, r.text
    return r.json()


def summary(kit, api, who=None, **params):
    r = api.get(url(kit, "/dashboard/summary"), who or kit.world.pm, params={"as_of": "2100-01-01", **params})
    assert r.status_code == 200, r.text
    return r.json()


def activity_pct(kit, api, ext, who=None):
    r = api.get(url(kit, "/dashboard/activities"), who or kit.world.pm, params={"as_of": "2100-01-01", "limit": 200})
    assert r.status_code == 200, r.text
    return {a["external_activity_id"]: a["physical_pct"] for a in r.json()["items"]}[ext]


def jpeg() -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (16, 16), (90, 120, 150)).save(b, "JPEG")
    return b.getvalue()


def upload(kit, api, name, body, kind="DAILY_REPORT", who=None, expect=201):
    r = api.post(url(kit, "/documents"), who or kit.world.se, files={"file": (name, body, "application/octet-stream")}, data={"kind": kind})
    assert r.status_code == expect, (name, r.text)
    return r.json()
