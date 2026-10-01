"""Local demo login: real HS256 JWTs, only against the isolated DB and only with an explicit opt-in."""
import pytest
from fastapi.testclient import TestClient

import backend.main  # noqa: F401
from backend.main import app

pytestmark = []


def test_local_login_is_404_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("SETUAI_LOCAL_DEMO_AUTH", raising=False)
    r = TestClient(app).post("/api/v1/auth/local-login", json={"email": "pm@nfu.setuai.test", "password": "Demo123456!"})
    assert r.status_code == 404


def test_local_login_stays_disabled_for_a_shared_looking_database(monkeypatch):
    monkeypatch.setenv("SETUAI_LOCAL_DEMO_AUTH", "1")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("SETUAI_ALLOW_DB_TESTS", "1")
    monkeypatch.setenv("SETUAI_TEST_ENV", "integration")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres")
    r = TestClient(app).post("/api/v1/auth/local-login", json={"email": "pm@nfu.setuai.test", "password": "Demo123456!"})
    assert r.status_code == 404
