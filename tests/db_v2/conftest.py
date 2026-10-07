import os

import psycopg
import psycopg.rows
import pytest

import v2world


@pytest.fixture
def conn():
    """Fresh connection; every change (including fixtures' data) is rolled back after the test."""
    c = psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def w(conn):
    return v2world.build_world(conn)
