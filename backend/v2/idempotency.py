"""Idempotency-Key support for create endpoints. The first request with a key runs; a retry with the same key and the same body gets the
SAME response back (header Idempotent-Replay: true); the same key with a different body is refused; a concurrent duplicate that arrives while
the first is still running gets 409 (retry shortly). Failures are not cached, so a client can fix the request and retry with the same key.
Keys are per user and per operation, bound to a hash of the request body, and expire after 24 hours."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Callable, Optional, Tuple

from fastapi.encoders import jsonable_encoder

from .db import tx
from .errors import ApiError

KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
STALE_AFTER_S = 120


def run(user_id, project_id, scope: str, key: Optional[str], payload: Any, fn: Callable[[], Any]) -> Tuple[Any, bool]:
    """returns (body, replayed)"""
    if key is None:
        return fn(), False
    if not KEY_RE.match(key):
        raise ApiError(422, "BAD_IDEMPOTENCY_KEY", "Idempotency-Key must be 8-128 characters of letters, digits, '_', '.', ':' or '-'")
    h = hashlib.sha256(json.dumps(jsonable_encoder(payload), sort_keys=True, default=str).encode()).hexdigest()
    reservation = None
    with tx(user_id) as c:
        c.execute("delete from api_idempotency where user_id = %s and scope = %s and idem_key = %s and created_at < now() - interval '24 hours'", (user_id, scope, key))
        row = c.execute("insert into api_idempotency (project_id, user_id, scope, idem_key, request_hash) values (%s,%s,%s,%s,%s) on conflict (user_id, scope, idem_key) do nothing "
                        "returning idempotency_id", (project_id, user_id, scope, key, h)).fetchone()
        if row is not None:
            reservation = row["idempotency_id"]
        else:
            ex = c.execute("select * from api_idempotency where user_id = %s and scope = %s and idem_key = %s for update", (user_id, scope, key)).fetchone()
            if ex["request_hash"] != h or ex["project_id"] != project_id:
                raise ApiError(422, "IDEMPOTENCY_KEY_REUSED", "That Idempotency-Key was already used for a different request")
            if ex["state"] == "DONE":
                return ex["response_body"], True
            took = c.execute("update api_idempotency set created_at = now() where idempotency_id = %s and created_at < now() - make_interval(secs => %s) returning idempotency_id",
                             (ex["idempotency_id"], STALE_AFTER_S)).fetchone()
            if took is None:
                raise ApiError(409, "IDEMPOTENCY_IN_PROGRESS", "The first request with this key is still running; retry in a moment")
            reservation = took["idempotency_id"]
    try:
        body = jsonable_encoder(fn())
    except BaseException:
        with tx(user_id) as c:
            c.execute("delete from api_idempotency where idempotency_id = %s and state = 'IN_PROGRESS'", (reservation,))
        raise
    with tx(user_id) as c:
        c.execute("update api_idempotency set state = 'DONE', response_status = 201, response_body = %s::jsonb, completed_at = now() where idempotency_id = %s",
                  (json.dumps(body), reservation))
    return body, False
