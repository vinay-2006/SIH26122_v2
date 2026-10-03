import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "schedule_import"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2_domain"))
from v2api import api, clean_db, client, client_500, world, build_and_activate  # noqa: E402,F401
from domainkit import kit  # noqa: E402,F401
from legacykit import Legacy  # noqa: E402


@pytest.fixture(autouse=True)
def rules_only_extraction(monkeypatch):
    """hermetic: the ORIGINAL extraction runs its deterministic rule fallback; no provider is ever contacted from a test"""
    monkeypatch.setenv("EXTRACTION_FALLBACK", "rules")


@pytest.fixture
def lg(kit, api):
    return Legacy(api, kit.project)
