import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "schedule_import"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2_domain"))
from v2api import api, clean_db, client, client_500, world, build_and_activate  # noqa: E402,F401
from domainkit import kit  # noqa: E402,F401


@pytest.fixture(autouse=True)
def rules_only_extraction(monkeypatch):
    """hermetic: the ORIGINAL extraction runs its deterministic rule fallback; no provider is ever contacted from a test"""
    monkeypatch.setenv("EXTRACTION_FALLBACK", "rules")


class Legacy:
    """call the legacy-contract endpoints the way the original frontend does: bearer token + X-Project-ID (+ X-Schedule-ID)"""
    def __init__(self, api, project, version=None):
        self.api, self.project, self.version = api, str(project), version

    def h(self, **extra):
        h = {"X-Project-ID": self.project, **extra}
        if self.version:
            h["X-Schedule-ID"] = str(self.version)
        return h

    def get(self, path, user, **kw):
        return self.api.get(path, user, headers=self.h(), **kw)

    def post(self, path, user, **kw):
        return self.api.post(path, user, headers=self.h(), **kw)

    def patch(self, path, user, **kw):
        return self.api.patch(path, user, headers=self.h(), **kw)


@pytest.fixture
def lg(kit, api):
    return Legacy(api, kit.project)
