"""A request-size ceiling that answers with the API's own error shape, BEFORE any route (and therefore any database write) runs.

V2_MAX_REQUEST_BYTES unset -> no ceiling (local use: the per-file-type limits in filetypes.py apply). On Vercel the platform itself refuses bodies above 4.5 MB (HTTP 413,
FUNCTION_PAYLOAD_TOO_LARGE, before the function starts, so nothing is ever created); set this a little below that (4400000) so that requests which do reach the function
are refused here with a JSON error the web app can show. A body that announces its size is refused from Content-Length alone; a body that does not (chunked) is
counted as it streams and refused at the ceiling."""
from __future__ import annotations

import json
import os
from typing import Optional

ENV = "V2_MAX_REQUEST_BYTES"


def configured() -> Optional[int]:
    raw = (os.environ.get(ENV) or "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError as e:
        raise RuntimeError(f"{ENV} must be a whole number of bytes, got {raw!r}") from e
    if n < 1024:
        raise RuntimeError(f"{ENV} is implausibly small ({n})")
    return n


def _body(limit: int) -> bytes:
    mb = limit / (1024 * 1024)
    return json.dumps({"error": {"code": "REQUEST_TOO_LARGE", "message": f"The upload is larger than this server accepts (about {mb:.1f} MB per request). Reduce the file size and try again.",
                                 "details": {"max_bytes": limit}}}).encode()


class RequestSizeLimit:
    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        declared = next((v for k, v in scope["headers"] if k == b"content-length"), None)
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            return await self._refuse(send)
        seen, refused, started = 0, False, False

        async def counted():
            nonlocal seen, refused
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max_bytes:
                    refused = True
                    return {"type": "http.disconnect"}                     # stop the route reading; the refusal is sent below
            return msg

        async def guarded_send(msg):
            nonlocal started
            if refused and not started:
                return                                                     # swallow whatever the aborted route tried to answer
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, counted, guarded_send)
        except Exception:                                                  # the route was cut off mid-read by the refusal above; anything else is a real error
            if not refused:
                raise
        if refused and not started:
            await self._refuse(send)

    async def _refuse(self, send):
        body = _body(self.max_bytes)
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


def install(app) -> None:
    limit = configured()
    if limit:
        app.add_middleware(RequestSizeLimit, max_bytes=limit)
