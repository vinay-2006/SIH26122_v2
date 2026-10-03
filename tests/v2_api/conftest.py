"""HTTP-level tests of the v2 API. Reuses the committed-project fixtures (schedule_import/v2api.py, v2_domain/domainkit.py) and gives every test
its own empty evidence directory."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "schedule_import"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2_domain"))
from v2api import api, clean_db, client, client_500, world  # noqa: E402,F401
from domainkit import kit  # noqa: E402,F401


@pytest.fixture(autouse=True)
def evidence_dir(tmp_path, monkeypatch):
    from backend.v2 import storage
    d = tmp_path / "evidence"
    monkeypatch.setenv("V2_EVIDENCE_DIR", str(d))
    storage.set_store(None)
    yield d
    storage.set_store(None)
