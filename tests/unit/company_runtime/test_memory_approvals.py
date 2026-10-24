import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from octop_memory import Memory

from octop.infra.company_runtime.approvals import CompanyApprovals
from octop.infra.company_runtime.runtime import EnterpriseRuntime
from tests.unit.company_runtime.test_runtime import acquire, install_agent


def native_runtime(client, tmp_path, events):
    runtime = EnterpriseRuntime(events.append)
    runtime.client = client
    runtime.workspace = tmp_path / "project"
    runtime.workspace.mkdir()
    runtime.state = tmp_path / "state"
    runtime.authorized = True
    runtime.installation_token = "i" * 32
    runtime.tools = dict.fromkeys(("ffmpeg", "openpyxl", "python-docx", "Pillow"), "test")
    runtime.tool_path = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    return runtime


@pytest.mark.skipif(os.name != "posix", reason="POSIX shell removal fixture")
@pytest.mark.parametrize("decision", ["approve", "reject", "cancel", "automatic"])
async def test_native_guard_pauses_before_removal_and_resumes_same_task(
    company, tmp_path, decision
):
    server, client, _, _ = company
    install_agent(server)
    events = []
    runtime = native_runtime(client, tmp_path, events)
    target = runtime.workspace / "thread1" / "disposable.txt"
    target.parent.mkdir()
    target.write_text("test-owned disposable file")

    class Model:
        def bind_tools(self, tools):
            return self

        async def astream(self, messages):
            if any(isinstance(m, ToolMessage) for m in messages):
                yield AIMessageChunk(content="finished", id="answer")
            else:
                yield AIMessageChunk(
                    content="",
                    id="command",
                    tool_call_chunks=[
                        {
                            "name": "execute",
                            "args": json.dumps({"command": "rm disposable.txt"}),
                            "id": "delete-once",
                            "index": 0,
                        }
                    ],
                )

    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = lambda _: (
        Model()
    )
    try:
        await runtime.start(
            {
                "task_id": "approval",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": "Remove the disposable fixture",
                "hitl_policy": {"mode": "allow_all" if decision == "automatic" else "ask"},
            }
        )
        work = runtime.work["thread1"]
        if decision != "automatic":
            async with asyncio.timeout(15):
                while not runtime.steering.approvals.snapshot("thread1"):
                    assert not work.done(), events[-1]
                    await asyncio.sleep(0.01)
            pending = runtime.status()["active_tasks"][0]["pending_approval"]
            assert target.exists()
            assert any(e.get("data", {}).get("type") == "hitl_required" for e in events)
            if decision == "cancel":
                await runtime.cancel({"thread_id": "thread1"})
            else:
                params = {
                    "thread_id": "thread1",
                    "pending_id": pending["pending_id"],
                    "decisions": [{"type": decision}],
                }
                with pytest.raises(ValueError, match="not_pending"):
                    runtime.steering.approvals.resolve({**params, "thread_id": "thread2"})
                runtime.steering.approvals.resolve(params)
                with pytest.raises(ValueError, match="not_pending"):
                    runtime.steering.approvals.resolve(params)
        await asyncio.wait_for(work, 15)
        assert runtime.tasks["thread1"]["status"] == (
            "cancelled" if decision == "cancel" else "completed"
        ), events[-1]
        assert target.exists() == (decision in {"reject", "cancel"})
        assert runtime.steering.approvals.snapshot("thread1") is None
    finally:
        await runtime.close()


async def test_server_memory_capture_is_idempotent_and_automatic_recall_reaches_model(
    company, tmp_path
):
    server, client, _, _ = company
    install_agent(server)
    memory = Memory(
        namespace="agent_expert",
        backend="sqlite",
        backend_config={"db_path": str(tmp_path / "company-memory.sqlite")},
    )
    server.app_runtime.agent_registry.get_agent("expert").memory = memory
    events = []
    runtime = native_runtime(client, tmp_path, events)
    seen = []

    class Model:
        async def astream(self, messages):
            seen.extend(messages)
            yield AIMessageChunk(
                content="I recalled your preference for concise reports.", id="answer"
            )

        def bind_tools(self, tools):
            return self

    try:
        await acquire(client, "seed", "thread2")
        payload = {
            "event_id": "seed-memory",
            "seq": 1,
            "payload": {
                "kind": "messages",
                "messages": [
                    {
                        "type": "human",
                        "data": {
                            "content": "I prefer concise reports with clear figures, remember my preference.",
                            "id": "old-user",
                        },
                    }
                ],
            },
        }
        await client.request("tasks/seed/events", payload)
        await client.request("tasks/seed/events", payload)
        assert len(memory.list_raw(thread_id="thread2")) == 1
        await client.request(
            "tasks/seed/memory/remember",
            {"assertion": "Prefers concise reports", "operation_id": "preference"},
        )
        server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
            lambda _: Model()
        )
        await runtime.start(
            {
                "task_id": "recall",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": "What reports do I prefer?",
            }
        )
        await asyncio.wait_for(runtime.work["thread1"], 15)
        assert runtime.tasks["thread1"]["status"] == "completed", events[-1]
        assert any(
            "<memory-context>" in str(m.content) and "concise" in str(m.content)
            for m in seen
            if isinstance(m, HumanMessage)
        )
        captured = memory.list_raw(thread_id="thread1")
        assert captured and all("<memory-context>" not in e.content for e in captured)
        assert not list(runtime.workspace.rglob("*.sqlite"))
        assert not list(runtime.workspace.rglob("MEMORY.md"))
    finally:
        await runtime.close()
        memory._backend.close()


async def test_approval_decisions_do_not_change_another_threads_policy():
    approvals = CompanyApprovals()
    request = {"action_requests": [{"name": "execute", "args": {"command": "rm fixture"}}]}
    approvals.begin("one", None)
    approvals.begin("two", None)
    first, future = approvals.prepare("one", request)
    second, _ = approvals.prepare("two", request)
    with pytest.raises(ValueError, match="decisions"):
        approvals.resolve({"thread_id": "one", "pending_id": first["pending_id"], "decisions": []})
    approvals.resolve(
        {
            "thread_id": "one",
            "pending_id": first["pending_id"],
            "decisions": [{"type": "approve"}],
            "hitl_policy": {"mode": "allow_all"},
        }
    )
    assert await future == [{"type": "approve"}]
    assert approvals.snapshot("two") == second
    assert approvals.policies["two"].mode == "ask"
    approvals.end("one")
    approvals.end("two")


async def test_parallel_tool_calls_require_independent_decisions():
    from types import SimpleNamespace

    from octop.infra.company_runtime.approvals import CompanyToolGuard

    approvals = CompanyApprovals()
    approvals.begin("thread", None)
    frames, executed = [], []
    guard = CompanyToolGuard(approvals, "thread", frames.append)

    async def handler(request):
        executed.append(request.tool_call["id"])
        return ToolMessage(content="done", tool_call_id=request.tool_call["id"])

    async def run(ident):
        return await guard.awrap_tool_call(
            SimpleNamespace(
                tool_call={
                    "id": ident,
                    "name": "execute",
                    "args": {"command": f"rm {ident}"},
                }
            ),
            handler,
        )

    first, second = asyncio.create_task(run("one")), asyncio.create_task(run("two"))
    async with asyncio.timeout(5):
        while len(frames) < 1:
            await asyncio.sleep(0)
        one = frames[-1]["data"]["request"]
        approvals.resolve(
            {
                "thread_id": "thread",
                "pending_id": one["pending_id"],
                "decisions": [{"type": "approve"}],
            }
        )
        await first
        while len(frames) < 2:
            await asyncio.sleep(0)
        two = frames[-1]["data"]["request"]
        assert one["pending_id"] != two["pending_id"]
        assert executed == ["one"]
        with pytest.raises(ValueError, match="not_pending"):
            approvals.resolve(
                {
                    "thread_id": "thread",
                    "pending_id": one["pending_id"],
                    "decisions": [{"type": "approve"}],
                }
            )
        approvals.resolve(
            {
                "thread_id": "thread",
                "pending_id": two["pending_id"],
                "decisions": [{"type": "reject"}],
            }
        )
        assert (await second).status == "error"
    assert executed == ["one"]
    approvals.end("thread")


async def test_managed_memory_reads_only_its_owned_predecessor_on_worker(company, tmp_path):
    from types import SimpleNamespace

    from octop.infra.company_runtime.service import CompanyRuntimeService
    from octop.infra.users.expert_access import ExpertAccess
    from octop.infra.utils.bounded_io import BoundedIO

    server, client, _, owner = company
    install_agent(server)
    services = server.services
    admin = services.user_repo.create(username="source-admin", role="admin")
    services.agent_repo.create(agent_id="source", user_id=admin, name="Template")
    with services.db.transaction() as conn:
        conn.execute(
            "UPDATE agents SET template_name='general' WHERE agent_id IN ('source','expert')"
        )
    access = ExpertAccess(services)
    access.configure_source(admin, admin, 0)
    access.save(admin, owner, ["source"], "assigned_only", 0, 1)
    managed = access.repo.reserve(owner, "source")["instance_agent_id"]
    services.agent_repo.create(agent_id=managed, user_id=owner, name="Managed")
    services.thread_repo.insert(
        thread_id="managed-chat",
        agent_id=managed,
        user_id=owner,
        channel_type="dashboard",
        session_key="managed",
        last_active=0,
    )
    await client.request(
        "tasks",
        {
            "task_id": "managed-task",
            "thread_id": "managed-chat",
            "agent_id": managed,
            "text": "recall",
            "installation_token": "i" * 32,
            "recovery_token": "r" * 32,
        },
    )
    for aid, fact in [
        ("expert", "Prefers concise reports"),
        ("source", "Administrator confidential reports"),
    ]:
        services.agent_repo.update_config(
            aid, config_json='{"system_files_path":".octop","memory":{"backend":{"type":"sqlite"}}}'
        )
        path = services.paths.agents_dir / aid / ".octop/memory.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        memory = Memory(
            namespace="agent_" + aid, backend="sqlite", backend_config={"db_path": str(path)}
        )
        memory.list_atoms(limit=1)
        memory.create_atom(fact, entity_name="User", entity_type="User", actor="user")
        memory._backend.close()
    server.role = "worker"
    server.management_io = BoundedIO()
    server.app_runtime.agent_registry = SimpleNamespace(
        get_config=lambda _: {},
        resolve_workspace_dir=lambda aid: services.paths.agents_dir / aid,
    )
    svc = CompanyRuntimeService(server, {"owner_user_id": owner, "device_id": "device0"})
    try:
        hits = (await svc.memory("managed-task", "search", {"query": "reports"}))["hits"]
        assert hits and all(h["path"].startswith("legacy/expert/") for h in hits)
        assert "confidential" not in json.dumps(hits)
        result = await svc.memory("managed-task", "get", {"path": hits[0]["path"]})
        assert "concise" in json.dumps(result)
        assert result["path"] == hits[0]["path"]
        assert result["source_agent_id"] == hits[0]["source_agent_id"] == "expert"
        assert result["source_scope"] == "legacy"
        recalled = await svc.memory("managed-task", "recall", {"query": "reports"})
        assert "earlier personal expert instance (expert)" in recalled["rendered"]
        assert "Earlier in this workspace" not in recalled["rendered"]
        assert "legacy/expert/atom/" in recalled["rendered"]
        from octop.infra.errors import OctopError

        with pytest.raises(OctopError, match="memory_source_not_owned"):
            await svc.memory("managed-task", "get", {"path": "legacy/source/atom/anything.md"})
    finally:
        await client.close()


async def test_raw_history_remains_searchable_without_automatic_prompt_injection(company, tmp_path):
    from octop_memory import MemoryService

    server, client, _, _ = company
    install_agent(server)
    memory = Memory(
        namespace="test_raw",
        backend="sqlite",
        backend_config={"db_path": str(tmp_path / "raw.sqlite")},
    )
    server.app_runtime.agent_registry.get_agent("expert").memory = memory
    try:
        MemoryService(memory).capture_turn(
            user="生成一张新的一天新的开始的图像，2:3比例的",
            assistant="图好了，新的一天，新的开始。",
            thread_id="old-conversation",
            session_id="old-conversation",
        )
        await acquire(client)
        query = "因为这是历史会话，我开新的会话应该就好了"
        search = await client.request("tasks/task1/memory/search", {"query": query})
        assert search["hits"]
        assert (await client.request("tasks/task1/memory/recall", {"query": query}))[
            "rendered"
        ] == ""
        hit = search["hits"][0]
        result = await client.request("tasks/task1/memory/get", {"path": hit["path"]})
        assert result["path"] == hit["path"] and result["source_scope"] == "current"
        memory.list_atoms(limit=1)
        memory.create_atom(
            "用户喜欢简洁的中文报告", entity_name="User", entity_type="User", actor="user"
        )
        assert (await client.request("tasks/task1/memory/search", {"query": "报告"}))["hits"]
        assert (
            "简洁的中文报告"
            in (await client.request("tasks/task1/memory/recall", {"query": "报告"}))["rendered"]
        )
    finally:
        memory._backend.close()
        await client.close()
