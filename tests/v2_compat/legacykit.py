"""legacy-contract test client (uniquely named: several test folders have a conftest.py)"""


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
