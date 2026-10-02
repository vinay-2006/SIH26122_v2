from __future__ import annotations

from typing import Any, Optional

import psycopg.errors as pge
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


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
    return str(getattr(getattr(e, "diag", None), "message_primary", None) or e).split("\n")[0]
