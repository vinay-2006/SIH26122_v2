"""The seeded four-project database is built ONCE per module (about ten seconds) and then only read, except by the tests that are marked as destructive
and run last. Everything runs against the isolated local setuai_v2_* test database; the module empties it before and after."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "schedule_import"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2_domain"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2_api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2api import SECRET, _truncate, token  # noqa: E402


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DB_V2_URL", os.environ["DATABASE_URL"])
    mp.setenv("SUPABASE_JWT_SECRET", SECRET)
    mp.setenv("V2_EVIDENCE_DIR", str(tmp_path_factory.mktemp("seed_evidence")))
    for k in ("SUPABASE_URL", "V2_JWT_ISSUER", "V2_JWKS_URL", "SUPABASE_JWKS_URL", "V2_ALLOW_HOSTED"):
        mp.delenv(k, raising=False)
    from backend.v2 import db as v2db, jwt_verify, storage
    storage.set_store(None)
    jwt_verify.reset_verifier()
    v2db.close_pool()
    _truncate()
    from backend.v2.seed import runner
    rep = runner.seed()
    yield SimpleNamespace(report=rep, runner=runner)
    v2db.close_pool()
    jwt_verify.reset_verifier()
    storage.set_store(None)
    _truncate()
    mp.undo()


@pytest.fixture(scope="module")
def http(seeded):
    from fastapi.testclient import TestClient
    from backend.v2.app import create_app
    from v2api import Api
    with TestClient(create_app()) as c:
        yield Api(c)
