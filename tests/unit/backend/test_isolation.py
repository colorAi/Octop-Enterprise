import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from octop.api.common.agent import assert_agent_access_row
from octop.infra.backend.isolation import (
    create_isolated_backend,
    host_mcp,
    isolated_config,
    isolated_spec,
)
from octop.infra.errors import OctopError
from octop.infra.utils.paths import PathLayout


def test_cannot_select_host_paths_or_credentials(monkeypatch):
    monkeypatch.setenv("OCTOP_AGENT_ISOLATION", "docker")
    cfg = {
        "backend": {"type": "local_shell", "root_dir": "/"},
        "skills": ["/secret"],
        "acp": {"tool_enabled": True},
        "system_files_path": "../other",
        "memory": {"backend": {"type": "sqlite", "db_path": "/other/memory.sqlite"}},
    }
    safe = isolated_config(cfg)
    assert not {"backend", "skills", "acp", "system_files_path"}.intersection(safe)
    assert safe["memory"]["backend"] == {"type": "sqlite"}
    assert cfg["memory"]["backend"]["db_path"] == "/other/memory.sqlite"
    assert host_mcp({"command": "sh"})
    assert host_mcp({"transport": "stdio"})
    assert not host_mcp({"transport": "streamable_http", "url": "https://example.test/mcp"})


def test_canonical_workspaces_have_distinct_persistent_volumes(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOP_SANDBOX_IMAGE", "test:immutable")
    paths = PathLayout(tmp_path.resolve())
    a = isolated_spec(paths, "a", paths.agents_dir / "a")
    b = isolated_spec(paths, "b", paths.agents_dir / "b")
    assert a["volume"] != b["volume"]
    assert a == isolated_spec(paths, "a", paths.agents_dir / "a")
    assert (
        isolated_spec(paths, "a", paths.agents_dir / "a", instance_key="1:2")["volume"]
        != isolated_spec(paths, "a", paths.agents_dir / "a", instance_key="2:3")["volume"]
    )
    with pytest.raises(OctopError):
        isolated_spec(paths, "a", paths.agents_dir / "b")


@pytest.mark.skipif(os.name != "posix", reason="symlink semantics")
def test_symlink_workspace_is_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OCTOP_SANDBOX_IMAGE", "test:immutable")
    paths = PathLayout(tmp_path.resolve())
    paths.agents_dir.mkdir()
    (paths.agents_dir / "b").mkdir()
    (paths.agents_dir / "a").symlink_to(paths.agents_dir / "b")
    with pytest.raises(OctopError):
        isolated_spec(paths, "a", paths.agents_dir / "a")


def test_shared_expert_runtime_requires_private_instance(monkeypatch):
    monkeypatch.setenv("OCTOP_AGENT_ISOLATION", "docker")
    row = SimpleNamespace(user_id=1, is_shared=1)
    with pytest.raises(OctopError):
        assert_agent_access_row(row, SimpleNamespace(id=2, is_admin=False))
    assert_agent_access_row(row, SimpleNamespace(id=1, is_admin=False))


@pytest.fixture
def existing_sandbox(monkeypatch):
    from octop_harness.backends.docker_sandbox import DockerSandbox

    monkeypatch.setattr(DockerSandbox, "__init__", lambda self, **kwargs: None)
    backend = create_isolated_backend(
        {
            "image": "test:image",
            "agent_id": "demo",
            "workspace_path": "/workspace",
            "volume": "workspace_volume",
        },
        package_mounts={"/Users/example/skills": "/data/skills"},
    )
    options = backend._octop_run_options
    container = SimpleNamespace(
        reload=lambda: None,
        attrs={
            "Config": {"Labels": dict(options["labels"]), "User": options["user"]},
            "HostConfig": {
                "ReadonlyRootfs": True,
                "NetworkMode": "none",
                "Memory": options["mem_limit"],
                "MemorySwap": options["memswap_limit"],
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Binds": [
                    "workspace_volume:/workspace:rw,nocopy",
                    "/Users/example/skills:/data/skills:ro",
                ],
            },
        },
    )
    backend._client = SimpleNamespace(info=Mock(return_value={"OperatingSystem": "Docker Desktop"}))
    monkeypatch.setattr(DockerSandbox, "_get_named_container", lambda self, name: container)
    return backend, container


def test_identical_binds_do_not_query_docker_info(existing_sandbox):
    backend, container = existing_sandbox
    assert backend._get_named_container("test") is container
    backend._client.info.assert_not_called()


@pytest.mark.parametrize("docker_desktop", [True, False])
def test_host_mnt_alias_is_accepted_only_on_docker_desktop(existing_sandbox, docker_desktop):
    backend, container = existing_sandbox
    backend._client.info.return_value = {
        "OperatingSystem": "Docker Desktop" if docker_desktop else "Ubuntu 24.04"
    }
    container.attrs["HostConfig"]["Binds"][1] = "/host_mnt/Users/example/skills:/data/skills:ro"
    if docker_desktop:
        assert backend._get_named_container("test") is container
    else:
        with pytest.raises(RuntimeError, match="different isolation policy"):
            backend._get_named_container("test")


@pytest.mark.parametrize(
    "bind",
    [
        "/host_mnt/Users/other/skills:/data/skills:ro",
        "/host_mnt/Users/example/skills:/other:ro",
        "/host_mnt/Users/example/skills:/data/skills:rw",
        "/host_mnt/Users/example/skills/../skills:/data/skills:ro",
        "/host_mnt/workspace_volume:/workspace:rw,nocopy",
    ],
)
def test_desktop_alias_does_not_accept_different_mounts(existing_sandbox, bind):
    backend, container = existing_sandbox
    container.attrs["HostConfig"]["Binds"][1] = bind
    with pytest.raises(RuntimeError, match="different isolation policy"):
        backend._get_named_container("test")


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("Config", "User", "0:0"),
        ("Config", "Labels", {"octop.isolation.policy": "different"}),
        ("HostConfig", "NetworkMode", "host"),
        ("HostConfig", "Privileged", True),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "CapDrop", []),
        ("HostConfig", "SecurityOpt", []),
    ],
)
def test_desktop_alias_does_not_relax_isolation(existing_sandbox, section, key, value):
    backend, container = existing_sandbox
    container.attrs["HostConfig"]["Binds"][1] = "/host_mnt/Users/example/skills:/data/skills:ro"
    container.attrs[section][key] = value
    with pytest.raises(RuntimeError, match="different isolation policy"):
        backend._get_named_container("test")
