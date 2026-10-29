import os
import subprocess
import sys

import pytest

from cpgos import config, doctor, preflight


def test_env_file_parsing(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# comment\nA=1\nexport B="two words"\nC=3 # trailing\nD=\'x#y\'\nnot a line\n')
    assert config.read_env_file(f) == {"A": "1", "B": "two words", "C": "3", "D": "x#y"}


def test_real_environment_overrides_env_file(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("CPGOS_X=from_file\n")
    monkeypatch.setenv("CPGOS_X", "from_env")
    assert config.get("CPGOS_X", env_file=f) == "from_env"
    monkeypatch.delenv("CPGOS_X")
    assert config.get("CPGOS_X", env_file=f) == "from_file"


def test_missing_package_message_names_interpreter_and_fix():
    with pytest.raises(preflight.EnvironmentNotReady) as e:
        preflight.require_packages({"definitely_not_a_module_xyz": "fake-pkg"})
    msg = str(e.value)
    assert "fake-pkg" in msg and sys.executable in msg and "make setup" in msg


def test_script_run_with_wrong_python_explains_instead_of_crashing(tmp_path):
    # Simulates the original failure: a Python that lacks psycopg runs load_data.py.
    shim = tmp_path / "psycopg.py"
    shim.write_text("raise ImportError('blocked for test')\n")
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    code = (
        "import importlib.util, runpy, sys\n"
        "orig = importlib.util.find_spec\n"
        "importlib.util.find_spec = lambda n, *a: None if n == 'psycopg' else orig(n, *a)\n"
        f"sys.argv = ['load_data.py', 'm5']\n"
        f"runpy.run_path({str(config.REPO_ROOT / 'scripts' / 'load_data.py')!r}, run_name='__main__')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode != 0
    assert "psycopg" in r.stderr and "make setup" in r.stderr
    assert "Traceback" not in r.stderr


def test_doctor_reports_unreachable_postgres_with_fix(monkeypatch):
    monkeypatch.setenv("CPGOS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    c = doctor.check_postgres()
    assert not c.ok and c.fix == "make db"


def test_doctor_reports_reachable_postgres(db_url, monkeypatch):
    monkeypatch.setenv("CPGOS_DATABASE_URL", db_url)
    c = doctor.check_postgres()
    assert c.ok and "migrations applied" in c.detail


def test_doctor_runs_and_returns_exit_code(capsys, monkeypatch):
    monkeypatch.setenv("CPGOS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    code = doctor.main(["--core"])
    out = capsys.readouterr().out
    assert code == 1 and "Postgres reachable" in out and "-> make db" in out


def _listener():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s, s.getsockname()[1]


def test_doctor_flags_a_busy_port(monkeypatch):
    s, port = _listener()
    try:
        monkeypatch.setenv("CPGOS_WEB_PORT", str(port))
        c = doctor.check_port("UI", "CPGOS_WEB_PORT", 3100)
        assert not c.ok and "in use" in c.detail and "CPGOS_WEB_PORT" in c.fix
    finally:
        s.close()
    assert doctor.check_port("UI", "CPGOS_WEB_PORT", 3100).ok  # free again


def test_serve_refuses_a_busy_port_instead_of_drifting():
    # Regression: Next.js moved from 3000 to 3001 silently; port 3000 was Grafana.
    s, port = _listener()
    try:
        env = {**os.environ, "CPGOS_WEB_PORT": str(port)}
        r = subprocess.run(["bash", str(config.REPO_ROOT / "scripts" / "serve.sh"), "web"],
                           capture_output=True, text=True, env=env, timeout=20)
    finally:
        s.close()
    assert r.returncode == 1
    assert f"port {port} is already in use" in r.stderr and "CPGOS_WEB_PORT" in r.stderr
