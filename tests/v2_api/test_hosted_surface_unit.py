"""What an unauthenticated visitor can learn from a HOSTED API: that it is up, nothing else (no route map). No database."""
import pytest
from fastapi.testclient import TestClient

from backend.v2.app import create_app


def client(monkeypatch, hosted, docs=None):
    for k, v in (("V2_ALLOW_HOSTED", "1" if hosted else None), ("V2_ENABLE_DOCS", docs)):
        monkeypatch.delenv(k, raising=False) if v is None else monkeypatch.setenv(k, v)
    return TestClient(create_app())


def test_a_hosted_api_does_not_publish_its_route_map(monkeypatch):
    c = client(monkeypatch, hosted=True)
    assert [c.get(p).status_code for p in ("/docs", "/redoc", "/openapi.json")] == [404, 404, 404]


def test_local_use_keeps_the_interactive_docs_and_hosted_can_opt_back_in(monkeypatch):
    assert client(monkeypatch, hosted=False).get("/openapi.json").status_code == 200
    assert client(monkeypatch, hosted=True, docs="1").get("/openapi.json").status_code == 200
