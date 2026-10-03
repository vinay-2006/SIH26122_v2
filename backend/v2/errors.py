from __future__ import annotations

from typing import Any, Optional

import psycopg.errors as pge
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("setuai.v2")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Optional[Any] = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def forbidden(message: str, code: str = "FORBIDDEN") -> ApiError:
    return ApiError(403, code, message)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return JSONResponse(status_code=e.status, content={"error": {"code": e.code, "message": e.message, "details": e.details}})

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"), "problem": err.get("msg"), "type": err.get("type")} for err in e.errors()][:25]
        return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "message": "The request is not valid", "details": details}})

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, e: Exception):
        rid = uuid.uuid4().hex[:12]
        log.exception("unhandled error %s on %s %s", rid, request.method, request.url.path)           # the traceback stays in the server log
        return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred", "details": {"request_id": rid}}})

    @app.exception_handler(pge.InsufficientPrivilege)
    async def _priv(_: Request, e):
        return JSONResponse(status_code=403, content={"error": {"code": "DB_FORBIDDEN", "message": _msg(e), "details": None}})

    @app.exception_handler(pge.UniqueViolation)
    async def _uniq(_: Request, e):
        return JSONResponse(status_code=409, content={"error": {"code": "CONFLICT", "message": _msg(e), "details": None}})

    @app.exception_handler(pge.CheckViolation)
    async def _check(_: Request, e):
        return JSONResponse(status_code=409, content={"error": {"code": "RULE_VIOLATION", "message": _msg(e), "details": None}})

    @app.exception_handler(pge.ForeignKeyViolation)
    async def _fk(_: Request, e):
        return JSONResponse(status_code=409, content={"error": {"code": "REFERENCE_VIOLATION", "message": _msg(e), "details": None}})

    @app.exception_handler(pge.InvalidTextRepresentation)
    async def _bad(_: Request, e):
        return JSONResponse(status_code=422, content={"error": {"code": "BAD_VALUE", "message": _msg(e), "details": None}})


def _msg(e) -> str:
    """User-facing text for a database rule violation. A message the schema's own triggers raise (RAISE EXCEPTION '...') is written for people and is shown;
    anything that names a table, column or constraint is internal and is replaced by a generic sentence (the detail goes to the server log)."""
    d = getattr(e, "diag", None)
    primary = str(getattr(d, "message_primary", None) or e).split("\n")[0]
    if d is not None and (getattr(d, "constraint_name", None) or getattr(d, "table_name", None) or getattr(d, "column_name", None)):
        log.warning("database rule violation (hidden from the client): %s", primary)
        return "The request conflicts with an existing record or a data rule"
    return primary
