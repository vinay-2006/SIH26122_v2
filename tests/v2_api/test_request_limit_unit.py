"""The request-size ceiling as a pure ASGI layer (no database)."""
import asyncio

import pytest

from backend.v2.request_limit import RequestSizeLimit, configured


async def call(app_cls_limit, headers, chunks):
    sent, ran = [], []

    async def inner(scope, receive, send):
        ran.append(1)
        body = b""
        while True:
            m = await receive()
            if m["type"] == "http.disconnect":
                raise RuntimeError("client went away")
            body += m.get("body", b"")
            if not m.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok:%d" % len(body)})

    queue = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(m):
        sent.append(m)

    await RequestSizeLimit(inner, app_cls_limit)({"type": "http", "method": "POST", "headers": headers}, receive, send)
    return sent, ran


def test_declared_oversize_is_refused_without_running_the_route():
    sent, ran = asyncio.run(call(1000, [(b"content-length", b"5000")], [b"x" * 5000]))
    assert sent[0]["status"] == 413 and not ran and b"REQUEST_TOO_LARGE" in sent[1]["body"]


def test_undeclared_oversize_is_counted_while_streaming_and_refused():
    sent, ran = asyncio.run(call(1000, [], [b"x" * 600, b"x" * 600]))
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]


def test_small_bodies_pass_untouched():
    sent, _ = asyncio.run(call(1000, [(b"content-length", b"10")], [b"x" * 10]))
    assert sent[0]["status"] == 200 and sent[1]["body"] == b"ok:10"


def test_configuration(monkeypatch):
    monkeypatch.delenv("V2_MAX_REQUEST_BYTES", raising=False)
    assert configured() is None
    monkeypatch.setenv("V2_MAX_REQUEST_BYTES", "4400000")
    assert configured() == 4400000
    for bad in ("abc", "10"):
        monkeypatch.setenv("V2_MAX_REQUEST_BYTES", bad)
        with pytest.raises(RuntimeError):
            configured()
