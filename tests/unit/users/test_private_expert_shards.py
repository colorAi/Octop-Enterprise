"""Employee creation must consume published definitions across worker shards."""

from unittest.mock import AsyncMock

import pytest
from tests.unit.users.test_employee_resources import prepare, save, selection
from tests.unit.users.test_employee_resources import resources as resources

from octop.infra.agents.manager import AgentManager
from octop.infra.execution.service import ExecutionService
from octop.infra.execution.shards import RuntimeShards
from octop.infra.users.source_publication import SourcePublication


@pytest.mark.asyncio
async def test_assigned_expert_creation_on_worker2_never_opens_admin_workspace(
    resources, monkeypatch
):
    e = resources
    await prepare(e)
    uid = e.employees[0]
    await save(e, [selection(e, uid, ["source"], [])])
    await e.agents["source"].workspace.aupload_many(
        [("SOUL.md", b"Published definition"), ("USER.md", b"Administrator private data")]
    )
    e.service.publication = SourcePublication(e.service)
    await e.service.publication.refresh()
    # An uncommitted draft must not leak into a newly assigned instance.
    await e.agents["source"].workspace.aupload_many([("SOUL.md", b"Unpublished draft")])
    repo = e.services.execution_repo
    repo.configure_topology({"worker": "http://worker", "worker2": "http://worker2"})
    for node in ("worker", "worker2"):
        repo.register(node, "boot", "worker", container_id=node)
    assert repo.worker_for(e.admin) == "worker"
    assert repo.worker_for(uid) == "worker2"
    registry = AgentManager(repos=e.services.repos, paths=e.services.paths)
    e.server.execution = ExecutionService(repo, node="worker2", incarnation="boot", role="worker")
    registry.employee_resources = e.service
    e.server.app_runtime.agent_registry = registry
    registry.runtime_shards = RuntimeShards(e.server)
    monkeypatch.setattr(registry, "_start_agent", AsyncMock(return_value=None))

    instance = await e.service.access.instantiate(registry, "source", uid)
    registry._start_agent.assert_not_awaited()
    assert registry.runtime_last_used == {}
    assert registry.is_bootstrapped_for_display(instance.agent_id)
    # A server execution materializes the published definition on demand.
    await e.service.initialize_workspace(instance.agent_id)
    workspace = registry.workspace_for_agent(instance.agent_id)
    assert await workspace.aread_text("SOUL.md") == "Published definition"
    assert await workspace.aread_text("USER.md") is None
    assert await workspace.aread_text(".octop/.bootstrapped")
    assert instance.user_id == uid
    assert e.service.access.repo.instance(instance.agent_id)["state"] == "ready"
    again = await e.service.access.instantiate(registry, "source", uid)
    assert again.agent_id == instance.agent_id
    registry._start_agent.assert_not_awaited()
