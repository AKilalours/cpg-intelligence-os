"""`make doctor`: check every prerequisite and print the exact fix for each.

Standard library only (psycopg and yaml are used only if installed), so it
runs with any Python 3.10+, including before `make setup`.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass

from cpgos import config
from cpgos.config import REPO_ROOT
from cpgos.preflight import REQUIRED


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    critical: bool = True


def check_python() -> Check:
    v = sys.version_info
    ok = v >= (3, 10)
    return Check("Python 3.10+", ok, f"{v.major}.{v.minor} at {sys.executable}",
                 "install Python 3.10 or newer, then: make setup")


def check_project_env() -> Check:
    venv = REPO_ROOT / ".venv" / "bin" / "python"
    return Check("Project environment (.venv)", venv.exists(), str(venv) if venv.exists() else "not created",
                 "make setup")


def check_packages() -> Check:
    missing = [pkg for mod, pkg in REQUIRED.items() if importlib.util.find_spec(mod) is None]
    return Check("Python packages", not missing,
                 "all installed" if not missing else f"missing in {sys.executable}: {', '.join(missing)}",
                 "make setup  (and run commands through make, not plain python)")


def check_env_file() -> Check:
    url = config.get("CPGOS_DATABASE_URL")
    has_file = config.ENV_FILE.exists()
    ok = url is not None
    detail = ("from environment" if "CPGOS_DATABASE_URL" in os.environ
              else ".env" if has_file and ok else "not set")
    return Check("Database address (CPGOS_DATABASE_URL)", ok, detail, "make setup  (creates .env from .env.example)")


def _run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def check_docker() -> Check:
    if shutil.which("docker") is None:
        return Check("Docker engine", False, "docker CLI not found",
                     "brew install colima docker docker-compose, then: make db")
    r = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    if r is not None and r.returncode == 0:
        return Check("Docker engine", True, f"running (server {r.stdout.strip()})")
    engine = "Colima" if shutil.which("colima") else "Docker Desktop"
    return Check("Docker engine", False, f"not running ({engine} is installed but stopped)",
                 "make db  (starts it for you)")


def check_postgres() -> Check:
    url = config.get("CPGOS_DATABASE_URL")
    if url is None:
        return Check("Postgres reachable", False, "no database address", "make setup")
    if importlib.util.find_spec("psycopg") is None:
        return Check("Postgres reachable", False, "can't test: psycopg not installed", "make setup")
    import psycopg
    try:
        with psycopg.connect(url, connect_timeout=3) as c:
            n = c.execute("SELECT count(*) FROM pg_tables WHERE tablename = 'schema_migration'").fetchone()[0]
            applied = c.execute("SELECT count(*) FROM schema_migration").fetchone()[0] if n else 0
    except Exception as exc:  # noqa: BLE001 - report any connection failure plainly
        return Check("Postgres reachable", False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]}",
                     "make db")
    total = len(list((REPO_ROOT / "db" / "migrations").glob("*.sql")))
    if applied < total:
        return Check("Postgres reachable", False, f"connected; {applied}/{total} migrations applied", "make migrate")
    return Check("Postgres reachable", True, f"connected; {applied}/{total} migrations applied")


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _port_owner(port: int) -> str:
    r = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fc"], timeout=5)
    if r is None or r.returncode != 0:
        return ""
    names = [line[1:] for line in r.stdout.splitlines() if line.startswith("c")]
    return names[0] if names else ""


def check_port(label: str, env_key: str, default: int) -> Check:
    """Informational: a busy port is fine if it's our own server already running."""
    port = int(config.get(env_key, str(default)))
    if not port_in_use(port):
        return Check(f"{label} port {port}", True, "free", critical=False)
    owner = _port_owner(port)
    return Check(f"{label} port {port}", False, f"in use{f' by {owner!r}' if owner else ''}",
                 f"fine if that's this project's {label.lower()}; otherwise stop it or set {env_key} in .env",
                 critical=False)


def check_datasets() -> list[Check]:
    if importlib.util.find_spec("yaml") is None:
        return [Check("Datasets", False, "can't read registry: pyyaml not installed", "make setup", critical=False)]
    from cpgos.datasets.registry import ACCEPT_PATH, load_registry
    import yaml
    accepted = (yaml.safe_load(ACCEPT_PATH.read_text()) or {}) if ACCEPT_PATH.exists() else {}
    out = []
    for key, spec in load_registry().items():
        if spec.method == "bundled":
            continue
        files = spec.raw.get("files") or []
        present = spec.raw_dir.exists() and (all((spec.raw_dir / f).exists() for f in files) if files
                                             else any(spec.raw_dir.iterdir()))
        lic = accepted.get(key) is True
        detail = f"terms {'accepted' if lic else 'not accepted'}, files {'present' if present else 'missing'}"
        fix = "" if lic and present else (
            f"read {spec.landing_page}, add '{key}: true' to data/license_acceptance.yaml" if not lic
            else f"make download DATASET={key}" if spec.method in {"http", "kaggle"}
            else f"get files from {spec.landing_page} into data/raw/{key}/")
        out.append(Check(f"Dataset {key}", lic and present, detail, fix, critical=False))
    return out


def run_checks() -> list[Check]:
    return [check_python(), check_project_env(), check_packages(), check_env_file(),
            check_docker(), check_postgres(),
            check_port("API", "CPGOS_API_PORT", 8100), check_port("UI", "CPGOS_WEB_PORT", 3100),
            *check_datasets()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check this project's prerequisites.")
    ap.add_argument("--core", action="store_true", help="skip dataset checks")
    args = ap.parse_args(argv)
    checks = run_checks()
    if args.core:
        checks = [c for c in checks if c.critical]
    width = max(len(c.name) for c in checks)
    for c in checks:
        mark = "ok " if c.ok else ("FIX" if c.critical else "-- ")
        line = f"[{mark}] {c.name.ljust(width)}  {c.detail}"
        if not c.ok and c.fix:
            line += f"\n      {' ' * width}  -> {c.fix}"
        print(line)
    blocking = [c for c in checks if c.critical and not c.ok]
    print("\nReady." if not blocking else f"\n{len(blocking)} blocking issue(s). Fix them top to bottom.")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
