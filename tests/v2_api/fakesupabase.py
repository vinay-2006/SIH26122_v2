"""An in-memory stand-in for the Supabase Storage REST API, used as the urllib opener of SupabaseEvidenceStore (no network)."""
import io
import json
import urllib.error


class FakeStorageAPI:
    def __init__(self, key="service-key-never-leaked", public=False, fail=None):
        self.key, self.public, self.fail = key, public, fail
        self.objects = {}
        self.calls = []

    def __call__(self, req, timeout=None):
        path = req.full_url.split("/storage/v1", 1)[1]
        self.calls.append((req.get_method(), path, {k.lower(): v for k, v in req.header_items()}))
        hdr = self.calls[-1][2]
        if self.fail:
            raise urllib.error.HTTPError(req.full_url, self.fail, "x", {}, io.BytesIO(b"secret body"))
        if hdr.get("authorization") != f"Bearer {self.key}" or hdr.get("apikey") != self.key:
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b"{}"))
        m = req.get_method()
        body = b""
        if m == "POST" and path.startswith("/object/") and not path.startswith("/object/authenticated/"):
            name = path[len("/object/"):]
            if name in self.objects and hdr.get("x-upsert") != "true":
                raise urllib.error.HTTPError(req.full_url, 409, "exists", {}, io.BytesIO(b"{}"))
            self.objects[name] = req.data
        elif m == "GET" and path.startswith("/object/info/authenticated/"):
            name = path[len("/object/info/authenticated/"):]
            if name not in self.objects:
                raise urllib.error.HTTPError(req.full_url, 404, "missing", {}, io.BytesIO(b"{}"))
            body = json.dumps({"name": name}).encode()
        elif m == "GET" and path.startswith("/object/authenticated/"):
            name = path[len("/object/authenticated/"):]
            if name not in self.objects:
                raise urllib.error.HTTPError(req.full_url, 404, "missing", {}, io.BytesIO(b"{}"))
            body = self.objects[name]
        elif m == "DELETE" and path.startswith("/object/"):
            self.objects.pop(path[len("/object/"):], None)
        elif m == "GET" and path.startswith("/bucket/"):
            body = json.dumps({"id": path.split("/")[-1], "public": self.public}).encode()
        else:
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b"{}"))
        resp = io.BytesIO(body)
        resp.__enter__ = lambda s=resp: s
        resp.__exit__ = lambda *a: False
        return resp
