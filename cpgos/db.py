"""Database connection and a small, explicit migration runner.

Why not a framework: migrations here are plain SQL files applied in order.
Each applied file's SHA-256 is recorded. If someone edits a migration that
already ran, the runner refuses to continue instead of silently drifting.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"


class MigrationDriftError(RuntimeError):
    """An already-applied migration file was modified."""


def database_url() -> str:
    url = os.environ.get("CPGOS_DATABASE_URL")
    if not url:
        raise RuntimeError("Set CPGOS_DATABASE_URL, e.g. postgresql://cpgos:...@localhost:5432/cpgos")
    return url


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or database_url())


def code_version() -> str:
    """Git SHA of the running code, recorded on every ingestion run."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parent,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parent,
        ).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unversioned"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(conn: psycopg.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations in filename order. Returns names applied."""
    with conn.transaction():
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migration (
                   name TEXT PRIMARY KEY,
                   sha256 CHAR(64) NOT NULL,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )
    applied = dict(conn.execute("SELECT name, sha256 FROM schema_migration").fetchall())

    newly_applied: list[str] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        digest = _sha256(path)
        if path.name in applied:
            if applied[path.name] != digest:
                raise MigrationDriftError(
                    f"{path.name} was edited after it was applied. "
                    "Write a new migration instead of changing history."
                )
            continue
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO schema_migration (name, sha256) VALUES (%s, %s)",
                (path.name, digest),
            )
        newly_applied.append(path.name)
    return newly_applied


if __name__ == "__main__":
    with connect() as c:
        done = migrate(c)
        print("applied:", done or "nothing (up to date)")
