"""Verify failure-safe shutdown/restart contracts without touching a Docker engine."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "enterprise_backup", Path(__file__).resolve().parents[3] / "docker/enterprise/backup.py"
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


@pytest.mark.parametrize("fail", ["none", "dump", "manifest"])
def test_backup_resumes_services_and_marks_failures(tmp_path, monkeypatch, fail):
    data = tmp_path / "data"
    for name in ("app", "media", "secrets"):
        (data / name).mkdir(parents=True)
        (data / name / "sample").write_text(name)
    env = tmp_path / "deployment.env"
    env.write_text("private fixture")
    output = tmp_path / "backup"
    calls = []
    config = {
        "services": {
            "octop": {
                "image": "fixture:source",
                "volumes": [{"source": str(data / "app"), "target": "/data/.octop"}],
            }
        }
    }

    def read(args, **kwargs):
        if "config" in args:
            return json.dumps(config).encode()
        return "octop\ncommunication\n"

    def run(args, **kwargs):
        calls.append(args)
        if "pg_dump" in args:
            if fail == "dump" and args[-1] == "octop_im":
                raise subprocess.CalledProcessError(1, args)
            kwargs["stdout"].write(b"fixture dump")

    monkeypatch.setattr(_module.subprocess, "check_output", read)
    monkeypatch.setattr(_module.subprocess, "run", run)
    if fail == "manifest":
        write_text = Path.write_text

        def fail_manifest(path, *args, **kwargs):
            if path.name == "manifest.json":
                raise OSError("disk full")
            return write_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fail_manifest)
    if fail != "none":
        with pytest.raises((subprocess.CalledProcessError, OSError)):
            _module.backup(env, output, [], True)
    else:
        _module.backup(env, output, [], True)
    assert calls[-1][-3:] == ["start", "octop", "communication"]
    if fail == "manifest":
        return
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["complete"] is (fail == "none")
    if fail == "none":
        assert set(manifest["files"]) == {
            "octop.dump",
            "octop_im.dump",
            "files.tar.gz",
            "deployment.env",
        }


def test_backup_refuses_unknown_volume_inventory(tmp_path):
    with pytest.raises(SystemExit, match="List every sandbox"):
        _module.backup(tmp_path / "env", tmp_path / "out", [], False)
