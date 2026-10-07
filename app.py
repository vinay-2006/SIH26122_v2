"""Vercel entrypoint for the ANVYRA API (FastAPI, ASGI). Vercel looks for `app` in this file. The same application as `uvicorn backend.v2.app:app`."""
from backend.v2.app import app  # noqa: F401
