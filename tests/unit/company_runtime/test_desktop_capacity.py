"""Desktop users share company services, not resident server tool sandboxes."""

import asyncio
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessageChunk

from octop.infra.agents.manager import AgentManager
from octop.infra.company_runtime.service import CompanyRuntimeService
from octop.infra.errors import OctopError
from octop.infra.execution.service import ExecutionService
from octop.infra.execution.shards import RuntimeShards
from octop.infra.users.employee_resources import EmployeeResources
from octop.infra.users.source_publication import SourcePublication
from octop.infra.utils.bounded_io import BoundedIO
from tests.unit.users.test_employee_resources import prepare, save, selection
from tests.unit.users.test_employee_resources import resources as resources


@pytest.mark.asyncio
@pytest.mark.parametrize("employee_count,model_limit", [(30, 4), (100, 0)])
async def test_employees_use_six_experts_without_server_sandboxes(
    resources, employee_count, model_limit
):
    e = resources
    for i in range(employee_count - len(e.employees)):
        uid = e.services.user_repo.create(username=f"capacity-{i}", role="user")
        e.employees.append(uid)
        e.memberships[uid] = {"department_id": "A", "revision": 0, "enabled": True}
    for uid in e.employees:
        e.services.user_policy_repo.set(
            uid,
            "model_access",
            json.dumps({"model_refs": ["test/model"], "revision": "fixture"}),
        )
    sources = ["source", *(f"source-{i}" for i in range(1, 6))]
    for aid in sources[1:]:
        e.services.agent_repo.create(agent_id=aid, user_id=e.admin, name=aid)
        e.add_agent(aid)
    await prepare(e)
    e.service.repo.save_pool(
        "A", sources, [e.extra], 1, e.service.access.repo.source()["revision"], "administrator"
    )
    await save(e, [selection(e, uid, sources, [e.extra]) for uid in e.employees])
    e.service.publication = SourcePublication(e.service)
    await e.service.publication.refresh()
    repo = e.services.execution_repo
    repo.configure(running=4, models=model_limit, queued=256, user_queued=8)
    repo.configure_sandbox_budget(total=1536 * 1024**2, sandbox=384 * 1024**2)
    repo.configure_topology({"worker": "http://worker", "worker2": "http://worker2"})
    servers = {}
    active_models = peak_models = 0
    started = asyncio.Queue()
    release = asyncio.Event()

    class Model:
        def bind(self, **kwargs):
            return self

        async def astream(self, messages):
            nonlocal active_models, peak_models
            active_models += 1
            peak_models = max(peak_models, active_models)
            try:
                if model_limit:
                    await asyncio.sleep(0.03)
                else:
                    started.put_nowait(True)
                    await release.wait()
                yield AIMessageChunk(
                    content="done",
                    usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                )
            finally:
                active_models -= 1

    factory = SimpleNamespace(list_refs=lambda: ["test/model"], get_chat_model=lambda _: Model())
    for node in ("worker", "worker2"):
        repo.register(node, "boot", "worker", container_id=node)
        registry = AgentManager(repos=e.services.repos, paths=e.services.paths)
        server = SimpleNamespace(
            role="worker",
            execution=ExecutionService(repo, node=node, incarnation="boot", role="worker"),
            services=e.services,
            communication=e.server.communication,
            management_io=BoundedIO(),
            app_runtime=SimpleNamespace(
                agent_registry=registry,
                gateway=SimpleNamespace(ws_hub=SimpleNamespace(is_turn_active=lambda _: False)),
            ),
        )
        registry.runtime_shards = RuntimeShards(server)
        registry.employee_resources = server.employee_resources = EmployeeResources(server)
        server.employee_resources.publication = SourcePublication(server.employee_resources)
        registry._start_agent = AsyncMock(side_effect=AssertionError("server runtime started"))
        registry._backend_workspace_for_row = Mock(side_effect=AssertionError("tool workspace"))
        registry.get_agent = Mock(side_effect=AssertionError("resident harness required"))
        registry.company_model_factory = lambda: factory
        registry.resolve_fallback_model_ref = lambda: "test/model"
        registry.desktop_context_workspace = AsyncMock(
            return_value=SimpleNamespace(aread_text=AsyncMock(return_value=None))
        )
        servers[node] = server

    async def browse(uid):
        server = servers[repo.worker_for(uid)]
        resources = server.employee_resources
        registry = server.app_runtime.agent_registry
        instances = []
        for source in sources:
            row = await resources.access.instantiate(registry, source, uid)
            assert row.last_state == "running"
            assert registry.is_bootstrapped_for_display(row.agent_id)
            skills = await resources.skill_summaries(uid, row.agent_id)
            assert "slides" in {item["slug"] for item in skills}
            assert "skill-manager" not in {item["slug"] for item in skills}
            instances.append(row.agent_id)
        return uid, instances

    async def batches(operations):
        results = []
        for offset in range(0, len(operations), 30):
            results.extend(await asyncio.gather(*operations[offset : offset + 30]))
        return results

    opened = await batches([browse(uid) for uid in e.employees])
    assert len({aid for _, ids in opened for aid in ids}) == employee_count * 6
    assert repo.sandbox_budget()["used_bytes"] == 0
    # Applying an administrator update refreshes cold metadata without loading
    # any of the employee's six tool workspaces or resident harnesses.
    e.services.agent_repo.update_config("source", welcome_message="Updated welcome")
    await e.service.publication.refresh()
    uid, instances = opened[0]
    await servers[repo.worker_for(uid)].employee_resources.refresh_user(uid)
    assert e.services.agent_repo.get(instances[0]).welcome_message == "Updated welcome"
    assert repo.sandbox_budget()["used_bytes"] == 0

    async def begin(uid, instances):
        server = servers[repo.worker_for(uid)]
        device = f"device-{uid}"
        now = int(time.time())
        e.services.device_repo.issue_code(device, uid, device, now + 100, now + 3600)
        e.services.device_repo.pair(
            device, device, hashlib.sha256(device.encode()).hexdigest(), "Desktop", "darwin", 2, now
        )
        thread = f"thread-{uid}"
        e.services.thread_repo.insert(
            thread_id=thread,
            agent_id=instances[0],
            user_id=uid,
            channel_type="dashboard",
            session_key=thread,
            last_active=0,
        )
        svc = CompanyRuntimeService(server, {"owner_user_id": uid, "device_id": device})
        task = await svc.start(
            {
                "task_id": f"task-{uid}",
                "thread_id": thread,
                "agent_id": instances[0],
                "text": "test",
                "installation_token": "i" * 32,
                "recovery_token": "r" * 32,
            }
        )
        assert task["expert"]["models"] == ["test/model"]
        return svc, task["task_id"]

    tasks = await batches([begin(uid, ids) for uid, ids in opened])
    assert repo.sandbox_budget()["used_bytes"] == 0

    async def generate(svc, task):
        return [chunk async for chunk in svc.model(task, {"model": "test/model", "messages": []})]

    if model_limit:
        replies = await asyncio.gather(*(generate(svc, task) for svc, task in tasks))
    else:
        # Keep every provider stream open while admitting the next request.
        # This measures in-flight model concurrency, not a metadata I/O burst.
        jobs = []
        try:
            for svc, task in tasks:
                jobs.append(asyncio.create_task(generate(svc, task)))
                await asyncio.wait_for(started.get(), timeout=15)
            assert peak_models == employee_count
        finally:
            release.set()
            replies = await asyncio.gather(*jobs)
    assert all(replies)
    assert 1 < peak_models <= (model_limit or employee_count)
    # Personal memory persists without a harness or an idle database connection.
    svc, task = tasks[0]
    body = {"assertion": "Prefers concise reports", "operation_id": "remember-1"}
    saved = await svc.memory(task, "remember", body)
    assert await svc.memory(task, "remember", body) == saved
    assert await svc.memory(task, "search", {"query": "concise"})
    other, other_task = tasks[1]
    assert "Prefers concise reports" not in str(
        await other.memory(other_task, "search", {"query": "concise"})
    )
    uid, instances = opened[0]
    state = e.service.state(uid)
    e.service.access.save(
        e.admin,
        uid,
        [],
        "assigned_only",
        state["revision"],
        e.service.access.repo.source()["revision"],
    )
    with pytest.raises(OctopError, match="resource_not_assigned"):
        await svc.expert(instances[0])
    with pytest.raises(OctopError, match="resource_not_assigned"):
        await generate(svc, task)
    assert repo.sandbox_budget()["used_bytes"] == 0
    for server in servers.values():
        registry = server.app_runtime.agent_registry
        registry._start_agent.assert_not_awaited()
        registry._backend_workspace_for_row.assert_not_called()
        registry.get_agent.assert_not_called()
        await server.execution.close()
