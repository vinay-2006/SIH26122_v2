"""Repo-wide pytest configuration.

DB SAFETY GUARD (see backend/testing/guard.py and backend/testing/classification.py):
tests that can reach a database refuse to run unless SETUAI_ALLOW_DB_TESTS=1,
SETUAI_TEST_ENV=integration and DATABASE_URL (from the process env) points at an
isolated integration database. Otherwise DATABASE_URL is blanked for the whole
session so DB-free tests cannot reach any database, including the shared dev DB.
"""
import os
from pathlib import Path

import pytest

from backend.testing import guard as _guard
from backend.testing.classification import E2E, INTEGRATION, classify

_ROOT = Path(__file__).resolve().parent
_GUARD_PROBLEMS = _guard.check_env(dict(os.environ), str(_ROOT / ".env"))
if _GUARD_PROBLEMS:
    # Must run before backend.shared.db is imported (it calls load_dotenv, which never
    # overrides an existing variable, even an empty one).
    os.environ["DATABASE_URL"] = ""

# Tests must be hermetic: never inherit a developer's local .env credentials. Supabase settings
# would switch auth from the offline path the tests assume to live JWKS verification (401 -> 403/200),
# and LLM keys would let unit tests call real providers. load_dotenv() never overrides an existing
# variable (even an empty one), so blanking here wins. Tests opt in with monkeypatch.setenv.
for _var in (
    "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_JWKS_URL", "SUPABASE_JWT_SECRET", "LLM_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY",
):
    os.environ[_var] = ""
if os.environ.get("SETUAI_ALLOW_LIVE_LLM") == "1":
    # Explicit opt-in: restore ONLY the LLM settings from .env so live-provider tests can run (synthetic data only).
    from dotenv import dotenv_values
    for _k, _v in dotenv_values(_ROOT / ".env").items():
        if _k.startswith(("LLM_", "GROQ_")) and _v:
            os.environ[_k] = _v
os.environ["AUTH_DEV_MODE"] = "false"  # bool setting: must parse, so "false" rather than ""


def pytest_configure(config):
    for name, doc in (
        ("db_read", "only SELECTs against a real Postgres"),
        ("db_write", "writes rows to a real Postgres"),
        ("db_destructive", "may delete/alter data it did not create"),
        ("integration", "crosses phase boundaries / isolation / RLS"),
        ("e2e", "full workflow through API or browser"),
        ("unclassified", "not in backend/testing/classification.py (treated as db_write)"),
    ):
        config.addinivalue_line("markers", f"{name}: {doc}")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    for item in items:
        try:
            rel = Path(str(item.fspath)).resolve().relative_to(_ROOT).as_posix()
        except ValueError:
            rel = str(item.fspath)
        cls = classify(rel, item.name.split("[")[0])
        if cls == "unclassified":
            item.add_marker(pytest.mark.unclassified)
            cls = "db_write"
        if cls != "db_free":
            item.add_marker(getattr(pytest.mark, cls))
        if rel in INTEGRATION:
            item.add_marker(pytest.mark.integration)
        if rel in E2E:
            item.add_marker(pytest.mark.e2e)


def pytest_collection_finish(session):
    needs_db = [i for i in session.items if any(i.get_closest_marker(m) for m in ("db_read", "db_write", "db_destructive", "unclassified"))]
    if not needs_db:
        return
    if _GUARD_PROBLEMS:
        names = sorted({Path(str(i.fspath)).name for i in needs_db})
        raise pytest.UsageError(
            "REFUSING TO RUN database tests (%d tests in %d files, e.g. %s):\n  - %s\n"
            "Run DB-free tests with:  pytest -m 'not db_read and not db_write and not db_destructive'\n"
            "Run DB tests only against the isolated local DB:  see docs/V7_PRE_E2E_HARDENING_REPORT.md"
            % (len(needs_db), len(names), ", ".join(names[:3]), "\n  - ".join(_GUARD_PROBLEMS))
        )
    import psycopg
    import psycopg.rows
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row) as conn:
        problem = _guard.check_db_marker(conn)
    if problem:
        raise pytest.UsageError("REFUSING TO RUN database tests: " + problem)


@pytest.fixture(autouse=True)
def _no_extraction_fallback_by_default(monkeypatch):
    """The demo .env enables EXTRACTION_FALLBACK=rules. Tests assert the strict contract
    (extraction failures raise) unless a test opts in with monkeypatch.setenv."""
    monkeypatch.delenv("EXTRACTION_FALLBACK", raising=False)
