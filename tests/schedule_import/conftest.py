"""Fixtures live in v2api.py (a uniquely named module, so two conftest.py files never collide on the module name `conftest`)."""
from v2api import api, clean_db, client, client_500, world  # noqa: F401
