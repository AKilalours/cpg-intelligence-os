"""Fail early, in plain words, when a script runs in the wrong environment.

Example of what this prevents: running a script with conda's base Python,
which has pyyaml but not psycopg. Before, that produced a traceback deep in
an import. Now it prints which Python is running and the one command to fix it.
Standard library only, so it can run before any dependency is installed.
"""

from __future__ import annotations

import importlib.util
import sys

from cpgos.config import REPO_ROOT

PROJECT_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

# Import name -> pip package name, for the message.
REQUIRED = {"psycopg": "psycopg[binary]", "yaml": "pyyaml", "duckdb": "duckdb", "pydantic": "pydantic"}


class EnvironmentNotReady(SystemExit):
    pass


def missing_packages(modules: dict[str, str] = REQUIRED) -> list[str]:
    return [pkg for mod, pkg in modules.items() if importlib.util.find_spec(mod) is None]


def require_packages(modules: dict[str, str] = REQUIRED) -> None:
    missing = missing_packages(modules)
    if not missing:
        return
    in_project_env = sys.prefix == str(REPO_ROOT / ".venv")
    lines = [
        f"This command needs packages that the running Python doesn't have: {', '.join(missing)}.",
        f"Running Python: {sys.executable}",
    ]
    if "conda" in sys.prefix.lower() or "anaconda" in sys.prefix.lower():
        lines.append("That is a conda environment, not this project's environment.")
    if in_project_env:
        lines.append("Fix: make setup   (reinstalls the project's packages)")
    else:
        lines.append("Fix: make setup   then run commands through make (e.g. make load-m5),")
        lines.append(f"     or call the project's Python directly: {PROJECT_PYTHON}")
    raise EnvironmentNotReady("\n".join(lines))
