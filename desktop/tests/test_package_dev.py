"""Exercise the development launcher with a real reload supervisor and local HTTP server."""

import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Bash development launcher")
SOURCE = Path(__file__).resolve().parents[1] / "package-dev.sh"


def executable(path, code):
    path.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(code)} "$@"\n')
    path.chmod(0o755)


def wait_for(check, log, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail(f"Development process did not become ready:\n{log.read_text()}")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def development(tmp_path):
    root = tmp_path / "source with spaces"
    (root / "desktop/src").mkdir(parents=True)
    (root / "src/octop").mkdir(parents=True)
    script = root / "desktop/package-dev.sh"
    shutil.copy2(SOURCE, script)
    probe = root / "src/octop/reload_probe.py"
    probe.write_text("revision = 1\n")
    home = tmp_path / "state"
    home.mkdir()
    config = home / "config.json"
    config.write_text(json.dumps({"bind_host": "192.0.2.1", "port": 9231}))
    original_config = config.read_bytes()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for tool in ("go", "node", "npm"):
        executable(binaries / tool, "pass")
    # Dependency installation is outside this process-lifecycle check. Run the
    # actual watchfiles supervisor using the same environment as pytest.
    executable(
        binaries / "uv",
        """
import os, sys
if sys.argv[1:] == ['sync', '--locked']:
    sys.exit(0)
assert sys.argv[1:4] == ['run', '--locked', 'watchfiles'], sys.argv
os.execv(sys.executable, [sys.executable, '-m', 'watchfiles', *sys.argv[4:]])
""",
    )
    executable(
        binaries / "octop",
        """
import json, os, signal, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from octop.config import load_config
import octop.cli.commands.run as command

home = Path(os.environ['OCTOP_HOME'])
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/api/health' else 404)
        self.end_headers()
    def log_message(self, *args):
        pass

def serve(**options):
    config = load_config(home / 'config.json')
    server = HTTPServer((options['host'], options['port']), Handler)
    record = dict(options, pid=os.getpid(), device=config.device_channel_enabled,
                  runtime=config.company_runtime_enabled)
    (home / f'backend-{os.getpid()}.json').write_text(json.dumps(record))
    def stop(*args):
        raise SystemExit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()

command._run_uvicorn = serve
assert sys.argv[1] == 'run', sys.argv
command.run.main(args=sys.argv[2:], standalone_mode=False)
""",
    )
    executable(
        binaries / "wails3",
        """
import json, os, sys, time
from pathlib import Path
assert sys.argv[1:] == ['dev'], sys.argv
home = Path(os.environ['OCTOP_HOME'])
record = {'pid': os.getpid(), 'url': os.environ['OCTOP_DESKTOP_URL'],
          'company': os.environ['OCTOP_COMPANY_URL'],
          'runtime_zip': os.environ.get('OCTOP_DESKTOP_PORTABLE_ZIP')}
(home / 'wails.json').write_text(json.dumps(record))
while not (home / 'quit').exists():
    time.sleep(0.05)
sys.exit(int((home / 'quit').read_text()))
""",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, OCTOP_HOME=str(home), OCTOP_PORT=str(port))
    for key in (
        "OCTOP_DEVICE_CHANNEL_ENABLED",
        "OCTOP_COMPANY_RUNTIME_ENABLED",
        "OCTOP_DESKTOP_URL",
        "OCTOP_COMPANY_URL",
        "OCTOP_DESKTOP_PORTABLE_ZIP",
    ):
        env.pop(key, None)
    env["PATH"] = str(binaries) + os.pathsep + env["PATH"]
    log = tmp_path / "launch.log"
    processes = []

    def start(**overrides):
        with log.open("w") as output:
            process = subprocess.Popen(
                ["bash", str(script)],
                cwd=root,
                env=dict(env, **overrides),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        processes.append(process)
        return process

    yield root, home, port, probe, log, start
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    assert config.read_bytes() == original_config


@pytest.mark.parametrize("exit_code", [0, 7])
def test_reload_flags_and_cleanup_when_shell_exits(development, exit_code):
    root, home, port, probe, log, start = development
    payload = root / "company tools.zip"
    payload.write_bytes(b"fixture: native validation is tested in Go")
    overrides = {"OCTOP_DESKTOP_PORTABLE_ZIP": payload.name}
    if exit_code:
        overrides.update(
            OCTOP_DEVICE_CHANNEL_ENABLED="false",
            OCTOP_COMPANY_RUNTIME_ENABLED="0",
            OCTOP_DESKTOP_URL="http://existing.test",
            OCTOP_COMPANY_URL="http://company.test",
        )
    process = start(**overrides)
    wait_for(lambda: (home / "wails.json").exists(), log)
    frontend = json.loads((home / "wails.json").read_text())
    assert frontend["company"] == overrides.get("OCTOP_COMPANY_URL", f"http://127.0.0.1:{port}")
    assert frontend["url"] == overrides.get("OCTOP_DESKTOP_URL", f"http://127.0.0.1:{port}")
    assert frontend["runtime_zip"] == str(payload)
    first = json.loads(next(home.glob("backend-*.json")).read_text())
    assert first["device"] is (exit_code == 0)
    assert first["runtime"] is (exit_code == 0)
    assert first["host"] == "127.0.0.1" and first["port"] == port
    # Edit Python source: prove a new backend process serves the same port.
    probe.write_text("revision = 2\n")
    wait_for(lambda: len(list(home.glob("backend-*.json"))) == 2, log)
    assert not alive(first["pid"])
    (home / "quit").write_text(str(exit_code))
    assert process.wait(timeout=15) == exit_code, log.read_text()
    for record in home.glob("*.json"):
        pid = json.loads(record.read_text()).get("pid")
        if pid:
            assert not alive(pid), f"Child process {pid} survived launcher exit"


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_signal_stops_both_supervisors(development, sig):
    _, home, _, _, log, start = development
    process = start()
    wait_for(lambda: (home / "wails.json").exists(), log)
    # Signal only the launcher, as IDE stop actions do, not its whole group.
    process.send_signal(sig)
    assert process.wait(timeout=15) == 128 + sig, log.read_text()
    for record in home.glob("*.json"):
        pid = json.loads(record.read_text()).get("pid")
        if pid:
            assert not alive(pid), f"Child process {pid} survived signal {sig}"


def test_missing_runtime_archive_fails_before_starting_services(development):
    _, home, _, _, log, start = development
    process = start(OCTOP_DESKTOP_PORTABLE_ZIP="missing.zip")
    assert process.wait(timeout=5) == 1
    assert "readable company runtime ZIP" in log.read_text()
    assert not list(home.glob("backend-*.json"))
    assert not (home / "wails.json").exists()


@pytest.mark.skipif(shutil.which("wails3") is None, reason="Requires the pinned Wails task runner")
def test_frontend_dependency_cache_is_shared_and_invalidated(tmp_path):
    root = tmp_path / "source"
    source = SOURCE.parent / "src"
    for name in (
        "Taskfile.yml",
        "build/Taskfile.yml",
        "build/darwin/Taskfile.yml",
        "build/windows/Taskfile.yml",
        "build/linux/Taskfile.yml",
    ):
        target = root / "desktop/src" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    dashboard = root / "dashboard"
    dashboard.mkdir()
    (dashboard / "package.json").write_text("{}")
    lock = dashboard / "package-lock.json"
    lock.write_text("{}")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    executable(
        binaries / "npm",
        """
from pathlib import Path
dashboard = Path('../../dashboard')
with (dashboard / 'install-count').open('a') as record:
    record.write('installed\\n')
(dashboard / 'node_modules').mkdir(exist_ok=True)
(dashboard / 'node_modules/.package-lock.json').write_text('{}')
""",
    )
    env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"])

    def install(task):
        result = subprocess.run(
            ["wails3", "task", task],
            cwd=root / "desktop/src",
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return len((dashboard / "install-count").read_text().splitlines())

    common = "common:install:frontend:deps"
    platform = "darwin:common:install:frontend:deps"
    assert install(common) == 1
    assert install(common) == 1
    assert install(platform) == 1
    lock.write_text('{"changed": true}')
    assert install(platform) == 2
    assert install(common) == 2
    shutil.rmtree(dashboard / "node_modules")
    assert install(common) == 3
