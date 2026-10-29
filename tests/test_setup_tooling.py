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


def _fake_bin(tmp_path, node_version):
    """A PATH where node/npm/npx are fakes; npm records whether it was called."""
    bin_dir = tmp_path / f"bin_{node_version}"
    bin_dir.mkdir()
    (bin_dir / "node").write_text(f"#!/bin/sh\necho v{node_version}\n")
    (bin_dir / "npm").write_text(f"#!/bin/sh\ntouch {tmp_path}/npm_was_called\n")
    (bin_dir / "npx").write_text("#!/bin/sh\necho next-dev-started\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    return bin_dir


def _free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def _serve_web(tmp_path, path_dirs, home):
    env = {**os.environ, "PATH": ":".join([*map(str, path_dirs), "/usr/bin", "/bin"]),
           "HOME": str(home), "CPGOS_WEB_PORT": str(_free_port())}
    env.pop("NVM_DIR", None)
    return subprocess.run(["bash", str(config.REPO_ROOT / "scripts" / "serve.sh"), "web"],
                          capture_output=True, text=True, env=env, timeout=30)


def test_old_node_without_nvm_stops_before_npm_install(tmp_path):
    # Regression: Node 18 ran npm install for Next 16, then failed inside Next.
    r = _serve_web(tmp_path, [_fake_bin(tmp_path, "18.20.8")], home=tmp_path)
    assert r.returncode == 1
    assert "needs Node.js 20.9 or newer; found v18.20.8" in r.stderr and "nvm install" in r.stderr
    assert not (tmp_path / "npm_was_called").exists()


def test_old_node_with_nvm_switches_automatically(tmp_path):
    old, new = _fake_bin(tmp_path, "18.20.8"), _fake_bin(tmp_path, "22.11.0")
    nvm_dir = tmp_path / ".nvm"
    nvm_dir.mkdir()
    # Fake nvm: `nvm use` puts the Node 22 bin first on PATH, as real nvm does.
    (nvm_dir / "nvm.sh").write_text(f'nvm() {{ [ "$1" = use ] && export PATH="{new}:$PATH"; return 0; }}\n')
    r = _serve_web(tmp_path, [old], home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Switching to Node 22 with nvm" in r.stdout and "Using Node v22.11.0" in r.stdout
    assert "next-dev-started" in r.stdout


def test_makefile_never_depends_on_the_executable_bit():
    # Regression: an in-place edit dropped serve.sh's executable bit and
    # `make web` failed with "Permission denied".
    makefile = (config.REPO_ROOT / "Makefile").read_text()
    calls = [line.strip() for line in makefile.splitlines() if "scripts/" in line and ".sh" in line]
    assert calls, "expected the Makefile to call shell scripts"
    assert all(c.startswith("bash scripts/") for c in calls), calls


def test_shell_scripts_are_tracked_as_executable():
    r = subprocess.run(["git", "ls-files", "-s", "scripts"], capture_output=True, text=True,
                       cwd=config.REPO_ROOT)
    if r.returncode != 0:
        pytest.skip("not a git checkout")
    modes = {line.split()[-1]: line.split()[0] for line in r.stdout.splitlines() if line.endswith(".sh")}
    assert modes and all(m == "100755" for m in modes.values()), modes


def test_servers_listen_on_this_machine_only():
    # Regression: next dev listened on every interface, so the UI was reachable
    # from the local network (e.g. http://192.168.1.168:3100).
    serve = (config.REPO_ROOT / "scripts" / "serve.sh").read_text()
    assert "next dev -H 127.0.0.1" in serve
    assert "--host" not in serve  # uvicorn defaults to 127.0.0.1; never widen it here
