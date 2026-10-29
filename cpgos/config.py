"""Settings from the environment, with a project-level .env file as fallback.

Why: the database address used to live only in a shell `export`. Open a new
terminal tab and it was gone, and every command failed in a confusing way.
Now `.env` (gitignored, created from .env.example by `make setup`) holds it.
Real environment variables always win over .env, so CI and overrides work.
Standard library only, so it works even before dependencies are installed.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()  # allow trailing comments on unquoted values
        if key:
            values[key] = value
    return values


def get(key: str, default: str | None = None, env_file: Path = ENV_FILE) -> str | None:
    if key in os.environ:
        return os.environ[key]
    return read_env_file(env_file).get(key, default)
