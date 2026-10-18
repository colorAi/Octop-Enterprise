import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.api.common.agent import assert_agent_access_row
from octop.infra.backend.isolation import host_mcp, isolated_config, isolated_spec
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
