#!/usr/bin/env python3
"""Disposable Docker isolation smoke test; mounts no production data."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path

import docker

from octop.infra.utils.paths import PathLayout


def main() -> None:
    module_spec = importlib.util.spec_from_file_location(
        "sandbox_under_test", "/check/isolation.py"
    )
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    sys.modules["octop.infra.backend.isolation"] = module
    client = docker.from_env()
    prefix = "verify_" + uuid.uuid4().hex[:12]
    paths = PathLayout(Path("/data/.octop"))
    backends = []
    specs = []
    checks = []
    try:
        for suffix in ("a", "b"):
            aid = prefix + suffix
            spec = module.isolated_spec(paths, aid, paths.agents_dir / aid)
            specs.append(spec)
            backends.append(module.create_isolated_backend(spec))
        a, b = backends
        assert all(
            not result.error for result in a.upload_files([("secret.txt", b"employee-a-private")])
        )
        assert a.execute("cat secret.txt").output.strip() == "employee-a-private"
        assert a.execute("printf 'updated' > secret.txt").exit_code == 0
        checks.append("workspace uploads and commands run as the unprivileged owner")
        assert b.execute("cat " + specs[0]["workspace_path"] + "/secret.txt").exit_code != 0
        checks.append("another employee's workspace is inaccessible")
        assert (
            a.execute(
                "test ! -e /data/.octop/octop.db && test ! -e /run/secrets/im_identity && test ! -e /var/run/docker.sock"
            ).exit_code
            == 0
        )
        assert (
            a.execute(
                'test -z "$OCTOP_DATABASE_URL$OCTOP_IM_IDENTITY_KEY$SANDBOX_TEST_CONTROL_SECRET"'
            ).exit_code
            == 0
        )
        checks.append("no control database, signing keys, host environment or Docker socket")
        assert a.execute("touch /app/isolation-write-check").exit_code != 0
        link_result = a.execute("ln -s /data/.octop/octop.db leaked && test ! -r leaked")
        assert link_result.exit_code == 0, str(link_result) + str(
            a.execute(
                'python3 -c \'import os; print(os.getcwd(),os.getuid(),os.getgid(),os.stat(".")); open("created.txt","w").write("ok")\''
            )
        )
        checks.append("read-only root filesystem and symlink escape blocked")
        cfg = a._container.attrs["HostConfig"]
        assert (
            cfg["NetworkMode"] == "none" and cfg["CapDrop"] == ["ALL"] and cfg["PidsLimit"] == 128
        )
        assert not cfg["Privileged"] and cfg["Memory"] == 1073741824
        assert a._container.attrs["Config"]["Healthcheck"]["Test"] == ["NONE"]
        checks.append("network disabled, capabilities dropped, resource limits applied")
        a.close()
        again = module.create_isolated_backend(specs[0])
        backends.append(again)
        assert again.execute("cat secret.txt").output.strip() == "updated"
        checks.append("private content survives backend restart")
        managed = module.create_isolated_backend(specs[0], managed_lifecycle=True)
        backends.append(managed)
        # The harness closes old agents while their replacement config and
        # workspace middleware still reference the manager-owned backend.
        from octop_harness.agent import HarnessAgent

        retiring_agent = object.__new__(HarnessAgent)
        retiring_agent._backend = managed
        retiring_agent._close_backend()
        assert managed.execute("cat secret.txt").output.strip() == "updated"
        managed.release()
        try:
            managed.execute("cat secret.txt")
        except RuntimeError as exc:
            assert "closed" in str(exc)
        else:
            raise AssertionError("manager release must detach its backend")
        checks.append("harness reload preserves a borrowed workspace; manager release closes it")
        migration_spec = importlib.util.spec_from_file_location(
            "migration_under_test", "/check/migrate_isolated_workspaces.py"
        )
        migration = importlib.util.module_from_spec(migration_spec)
        migration_spec.loader.exec_module(migration)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / ".octop").mkdir()
            (source / ".octop" / "manifest.json").write_text('{"name":"test"}')
            (source / ".octop" / "memory.sqlite").write_text("runtime stays private")
            (source / "script.py").write_text("print('test')")
            (source / "shortcut").symlink_to("script.py")
            hashes = migration.copy_workspace(b, source, migration.content_entries(source))
            result = b._container.exec_run(
                ["python3", "-c", migration.VERIFY_SCRIPT, specs[1]["workspace_path"]]
            )
            assert result.exit_code == 0 and json.loads(result.output) == hashes
            assert ".octop/memory.sqlite" not in hashes
        checks.append("migration verifies file hashes and symlinks, excludes runtime databases")
        print(json.dumps({"passed": checks}, ensure_ascii=False, indent=2))
    finally:
        for backend in backends:
            backend.release()
        for spec in specs:
            for container in client.containers.list(
                all=True, filters={"label": "octop.agent.id=" + spec["agent_id"]}
            ):
                container.remove(force=True)
            with suppress(docker.errors.NotFound):
                client.volumes.get(spec["volume"]).remove()


if __name__ == "__main__":
    main()
