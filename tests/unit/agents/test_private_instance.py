import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from octop_harness.backends.workspace import BackendWorkspace

from octop.infra.agents.manager import AgentManager


def workspace(root: Path) -> BackendWorkspace:
    root.mkdir(parents=True, exist_ok=True)
    return BackendWorkspace(LocalShellBackend(root_dir=str(root), virtual_mode=False), root)


@pytest.mark.asyncio
async def test_shared_templates_make_reusable_private_copies_without_owner_data(tmp_path):
    source_ws = workspace(tmp_path / "source")
    await source_ws.aupload_many(
        [
            ("SOUL.md", b"Expert instructions"),
            ("USER.md", b"private owner profile"),
            ("MEMORY.md", b"private memory"),
            (".env", b"SECRET=owner"),
            ("inbound/private.txt", b"private attachment"),
            (
                "skills/research/SKILL.md",
                b"---\nname: research\ndescription: research\n---\nExpert skill",
            ),
        ]
    )
    source = SimpleNamespace(
        agent_id="source",
        user_id=1,
        is_shared=1,
        kind="expert",
        name="Researcher",
        description="shared expertise",
        icon_name="bot",
        color="blue",
        welcome_message="Hello",
        system_prompt="Expert",
        default_model=None,
    )
    rows = {"source": source}
    targets = {}

    async def create(spec, *, workspace_initializer):
        row = SimpleNamespace(agent_id=spec.agent_id, user_id=spec.user_id, name=spec.name)
        target = workspace(tmp_path / spec.agent_id)
        await workspace_initializer(row, target)
        rows[spec.agent_id] = row
        targets[spec.agent_id] = target
        return row

    registry = SimpleNamespace(
        get_row=rows.get,
        get_config=lambda _id: {"skill_package_ids": []},
        workspace_for_agent=lambda _id: source_ws,
        _shared_instance_lock=asyncio.Lock(),
        list_agents=lambda uid: [r for r in rows.values() if r.user_id == uid],
        create=AsyncMock(side_effect=create),
    )
    a, again = await asyncio.gather(
        AgentManager.instantiate_shared(registry, "source", 2),
        AgentManager.instantiate_shared(registry, "source", 2),
    )
    b = await AgentManager.instantiate_shared(registry, "source", 3)
    assert a.agent_id == again.agent_id != b.agent_id
    assert registry.create.await_count == 2
    for row in (a, b):
        ws = targets[row.agent_id]
        assert await ws.aread_text("SOUL.md") == "Expert instructions"
        assert "Expert skill" in await ws.aread_text("skills/research/SKILL.md")
        assert await ws.aread_text(".octop/.bootstrapped") is not None
        for private in ("USER.md", "MEMORY.md", ".env", "inbound/private.txt"):
            assert await ws.aread_text(private) is None
