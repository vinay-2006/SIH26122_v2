"""Helpers for the seed tests (uniquely named: several test directories have their own conftest.py)."""
from types import SimpleNamespace


def person(handle):
    from backend.v2.seed.projects_spec import email, user_id
    return SimpleNamespace(id=user_id(handle), handle=handle, email=email(handle))
