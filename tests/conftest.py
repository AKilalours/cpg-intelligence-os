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

from cpgos import config
from cpgos.db import migrate

def _admin_url() -> str:
    """Where tests create throwaway databases.

    Priority: CPGOS_TEST_ADMIN_URL, else the project database server from
    CPGOS_DATABASE_URL (.env) with the maintenance database `postgres`.
    Tests never touch the project database itself, only temporary ones.
    """
    explicit = config.get("CPGOS_TEST_ADMIN_URL")
    if explicit:
        return explicit
    url = config.get("CPGOS_DATABASE_URL")
    if url:
        base, _, query = url.partition("?")
        return base.rsplit("/", 1)[0] + "/postgres" + (f"?{query}" if query else "")
    return "postgresql://postgres@/postgres?host=/tmp"


ADMIN_URL = _admin_url()
FIXTURES = Path(__file__).parent / "fixtures"


def _url_for(dbname: str) -> str:
    base, _, query = ADMIN_URL.partition("?")
    base = base.rsplit("/", 1)[0]
    return f"{base}/{dbname}" + (f"?{query}" if query else "")


@pytest.fixture(autouse=True)
def _isolate_processed_data(tmp_path, monkeypatch):
    """No test may write into the real data/processed folder."""
    import cpgos.ingest.m5 as m5mod
    monkeypatch.setattr(m5mod, "PROCESSED_ROOT", tmp_path / "isolated_processed" / "m5")


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
