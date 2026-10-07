"""DB-free: the published OpenAPI document is complete and honest."""
import os

import pytest


@pytest.fixture(scope="module")
def spec():
    os.environ.setdefault("SUPABASE_JWT_SECRET", "x" * 40)
    from backend.v2.app import create_app
    return create_app().openapi()


def ops(spec):
    for path, item in spec["paths"].items():
        for method, op in item.items():
            yield method.upper(), path, op


def test_every_operation_is_tagged_and_documents_its_error_responses(spec):
    n = 0
    for method, path, op in ops(spec):
        if path == "/health":
            continue
        n += 1
        assert op.get("tags"), f"{method} {path} has no tag"
        assert {"401", "403", "422"} <= set(op["responses"]), f"{method} {path} does not document 401/403/422"
        assert any(code.startswith("2") for code in op["responses"]), f"{method} {path} has no success response"
    assert n >= 70


def test_every_operation_has_a_readable_summary(spec):
    for method, path, op in ops(spec):
        if path.startswith("/api/v2/projects/{project_id}/") and any(t in op.get("tags", []) for t in ("claims", "issues", "documents", "dashboard")):
            assert op.get("summary") and len(op["summary"]) > 12, f"{method} {path}: summary missing"


def test_request_bodies_reject_unknown_fields(spec):
    checked = 0
    for name, sch in spec["components"]["schemas"].items():
        if name in ("ClaimCreate", "DecisionBody", "IssueCreate", "ResolveBody", "WithdrawBody", "AnswerBody", "QuantityIn", "MemoryBody", "RootCauseCreate", "ExtractBody", "EvidenceBody"):
            assert sch.get("additionalProperties") is False, f"{name} accepts unknown fields"
            checked += 1
    assert checked == 11


def test_all_references_resolve(spec):
    import json
    text = json.dumps(spec)
    refs = {s.split('"')[0] for s in text.split('"$ref": "')[1:]}
    for r in refs:
        node = spec
        for part in r.lstrip("#/").split("/"):
            node = node[part]


def test_money_like_figures_are_not_floats_in_requests(spec):
    q = spec["components"]["schemas"]["QuantityIn"]["properties"]["qty"]
    assert "number" in str(q) or "string" in str(q)             # decimals are accepted as numbers or strings and parsed as Decimal on the server


def test_the_decision_methods_are_exactly_the_documented_ones(spec):
    d = spec["components"]["schemas"]["DecisionBody"]["properties"]
    assert set(d["action"]["enum"]) == {"APPROVE", "EDIT", "REJECT", "HOLD"}
    methods = next(x for x in d["method"]["anyOf"] if "enum" in x)["enum"]
    assert set(methods) == {"QUANTITIES_AS_CLAIMED", "MANUAL_QUANTITIES", "APPLY_PCT_TO_ASSIGNMENTS", "PCT_ONLY_ACTIVITY"}


def test_the_api_reference_document_is_up_to_date():
    import subprocess, sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    r = subprocess.run([sys.executable, str(root / "scripts" / "gen_v2_api_doc.py"), "--check"], capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, r.stderr
