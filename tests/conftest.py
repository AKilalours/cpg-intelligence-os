"""Tests run against a REAL Postgres, not mocks.

A migrated template database is built once per session; each test gets a
fresh copy, so tests never leak state into each other.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

from cpgos.db import migrate

ADMIN_URL = os.environ.get("CPGOS_TEST_ADMIN_URL", "postgresql://postgres@/postgres?host=/tmp")
FIXTURES = Path(__file__).parent / "fixtures"


def _url_for(dbname: str) -> str:
    base, _, query = ADMIN_URL.partition("?")
    base = base.rsplit("/", 1)[0]
    return f"{base}/{dbname}" + (f"?{query}" if query else "")


@pytest.fixture(scope="session")
def template_db():
    name = f"cpgos_tmpl_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(_url_for(name)) as c:
        migrate(c)
    yield name
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def db_url(template_db):
    name = f"cpgos_t_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"')
    yield _url_for(name)
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def conn(db_url):
    with psycopg.connect(db_url, autocommit=True) as c:
        yield c
