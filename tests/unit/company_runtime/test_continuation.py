import asyncio
import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, message_to_dict
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from octop.infra.company_runtime.runtime import EnterpriseRuntime
from octop.infra.errors import OctopError
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.history.continuation import continue_dashboard_thread
from octop.infra.history.projection import message_inputs
from octop.infra.history.service import HistoryArchive
from octop.infra.history.store import HistoryStore
from octop.infra.users.expert_access import ExpertAccess
from tests.unit.company_runtime.test_projects import IDENTITY, register
from tests.unit.company_runtime.test_runtime import acquire, install_agent


def history(server, *, count=4):
    messages = [
        (HumanMessage if i % 2 == 0 else AIMessage)(content=f"history-{i}", id=f"m{i}")
        for i in range(count)
    ]
    server.services.thread_message_repo.replace_all("thread1", message_inputs(messages))
    return messages


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["projection", "archive", "checkpoint"])
@pytest.mark.parametrize("target", ["expert", "replacement"])
async def test_continue_full_owned_history_across_experts_and_storage(
    company, tmp_path, storage, target
):
    server, client, _, owner = company
    install_agent(server)
    server.services.agent_repo.create(agent_id="replacement", user_id=owner, name="New expert")
    messages = history(server, count=1004)
    archive = None
    if storage == "archive":
        archive = HistoryArchive(
            HistoryStore(tmp_path / "archive.db", identity="test"),
            server.services.thread_message_repo,
            server.services.trajectory_event_repo,
            enabled=True,
        )
        server.app_runtime.history_archive = archive
        turn = archive.begin("expert", "thread1")
        extra = [
            HumanMessage(content="archived question", id="u"),
            AIMessage(content="answer", id="a"),
        ]
        archive.save_messages(turn, [message_to_dict(m) for m in extra])
        archive.finish(turn["id"], "complete")
        messages += extra
    elif storage == "checkpoint":
        server.services.thread_message_repo.mark_projection("thread1", "pending")
        server.app_runtime.agent_registry.get_agent("expert").aget_history = AsyncMock(
            return_value=messages
        )
    try:
        await register(client)
        before = server.services.thread_repo.get("thread1")
        body = {
            **IDENTITY,
            "source_thread_id": "thread1",
            "thread_id": "continued",
            "agent_id": target,
        }
        result = await client.request("projects/project-a/continue", body)
        assert result["copied_messages"] == len(messages)
        assert result == await client.request("projects/project-a/continue", body)
        rows, more = server.services.thread_message_repo.page("continued", limit=2000)
        assert not more
        assert [json.loads(row.message_json)["data"]["content"] for row in rows] == [
            m.content for m in messages
        ]
        checkpoint = server.services.company_runtime_repo.checkpoint("continued", "", None)
        kind, data = checkpoint["checkpoint"]
        state = JsonPlusSerializer(pickle_fallback=False).loads_typed(
            (kind, base64.b64decode(data))
        )
        assert [m.content for m in state["channel_values"]["messages"]] == [
            m.content for m in messages
        ]
        assert checkpoint["parent_config"] is None
        assert set(state["channel_values"]) == {"messages"}
        assert server.services.thread_repo.get("thread1") == before
        assert server.services.company_runtime_repo.binding("thread1") is None
        assert server.services.company_runtime_repo.binding("continued")["agent_id"] == target
    finally:
        if archive:
            archive.store.close()
        await client.close()


@pytest.mark.asyncio
async def test_revoked_source_can_continue_only_with_current_assignment(company):
    server, client, _, owner = company
    install_agent(server)
    history(server)
    admin = server.services.user_repo.create(username="admin", role="admin")
    server.services.agent_repo.create(agent_id="source", user_id=admin, name="Current")
    access = ExpertAccess(server.services)
    access.configure_source(admin, admin, 0)
    access.save(admin, owner, ["source"], "assigned_only", 0, 1)
    target = access.repo.reserve(owner, "source")["instance_agent_id"]
    server.services.agent_repo.create(agent_id=target, user_id=owner, name="Current")
    try:
        await register(client)
        body = {
            **IDENTITY,
            "source_thread_id": "thread1",
            "thread_id": "continued",
            "agent_id": "expert",
        }
        denied = await client.http.post("projects/project-a/continue", json=body)
        assert denied.status_code == 403
        assert server.services.thread_repo.get("continued") is None
        result = await client.request("projects/project-a/continue", {**body, "agent_id": target})
        assert result["agent_id"] == target and result["copied_messages"] == 4
        with pytest.raises(OctopError):
            access.assert_use(owner, "expert")
        assert server.services.thread_repo.get("thread1").agent_id == "expert"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_continue_rejects_foreign_history_wrong_installation_and_running_task(company):
    server, client, _, owner = company
    install_agent(server)
    history(server)
    other = server.services.user_repo.create(username="other", role="user")
    server.services.agent_repo.create(agent_id="foreign", user_id=other, name="Foreign")
    server.services.thread_repo.insert(
        thread_id="foreign",
        agent_id="foreign",
        user_id=other,
        channel_type="dashboard",
        session_key="f",
        last_active=0,
    )
    try:
        await register(client)
        body = {
            **IDENTITY,
            "source_thread_id": "thread1",
            "thread_id": "denied",
            "agent_id": "expert",
        }
        for override in (
            {"source_thread_id": "foreign"},
            {"agent_id": "foreign"},
            {"installation_token": "z" * 32},
        ):
            response = await client.http.post(
                "projects/project-a/continue", json={**body, **override}
            )
            assert response.status_code == 403
        assert (
            await client.http.post(
                "projects/project-a/continue", json=body, headers={"Origin": "http://company.test"}
            )
        ).status_code == 403
        await acquire(client, thread="thread2")
        body["source_thread_id"] = "thread2"
        response = await client.http.post("projects/project-a/continue", json=body)
        assert response.status_code == 403
        assert server.services.thread_repo.get("denied") is None
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_tool", [False, True])
async def test_continued_context_reaches_next_local_model_turn(company, tmp_path, pending_tool):
    server, client, _, owner = company
    install_agent(server)
    messages = history(server)
    if pending_tool:
        messages.append(
            AIMessage(
                content="",
                id="pending",
                tool_calls=[
                    {
                        "id": "old-write",
                        "name": "write_file",
                        "args": {"file_path": "/must-not-replay.txt", "content": "old command"},
                    }
                ],
            )
        )
        server.services.thread_message_repo.replace_all("thread1", message_inputs(messages))
    server.services.agent_repo.create(agent_id="replacement", user_id=owner, name="New")
    seen = []

    class Model:
        def bind_tools(self, tools):
            return self

        async def astream(self, messages):
            seen.extend(messages)
            yield AIMessageChunk(content="continued with history", id="next-answer")

    server.app_runtime.agent_registry.get_agent("replacement").model_factory.get_chat_model = (
        lambda ref: Model()
    )
    runtime = EnterpriseRuntime(lambda event: None)
    runtime.client = client
    runtime.project_capable = True
    runtime.state = tmp_path / "runtime"
    runtime.workspace = tmp_path / "unused"
    runtime.installation_token = "i" * 32
    runtime.tools = {"ffmpeg": "test", "openpyxl": "test", "python-docx": "test", "Pillow": "test"}
    runtime.tool_path = str(Path(sys.executable).parent)
    root = tmp_path / "work"
    root.mkdir()
    try:
        await register(client)
        await client.request(
            "projects/project-a/continue",
            {
                **IDENTITY,
                "source_thread_id": "thread1",
                "thread_id": "continued",
                "agent_id": "replacement",
            },
        )
        await runtime.start(
            {
                "task_id": "continue-task",
                "thread_id": "continued",
                "agent_id": "replacement",
                "project_id": "project-a",
                "project_root": str(root),
                "text": "continue please",
            }
        )
        await asyncio.wait_for(runtime.work["continued"], 30)
        assert runtime.tasks["continued"]["status"] == "completed"
        assert not (root / "must-not-replay.txt").exists()
        assert [m.content for m in seen if m.type in {"human", "ai"}] == [
            m.content for m in messages
        ] + ["continue please"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_web_continuation_seeds_target_checkpoint_and_preserves_source(company):
    server, client, _, owner = company
    install_agent(server)
    history(server)
    server.services.agent_repo.create(agent_id="replacement", user_id=owner, name="New")
    graph = SimpleNamespace(aupdate_state=AsyncMock())
    server.app_runtime.agent_registry.get_agent("replacement").graph = graph
    server.app_runtime.gateway.thread_registry = ThreadRegistry(
        session_repo=server.services.session_repo, thread_repo=server.services.thread_repo
    )
    try:
        result = await continue_dashboard_thread(server, owner, "thread1", "replacement")
        assert result["agent_id"] == "replacement" and result["copied_messages"] == 4
        graph.aupdate_state.assert_awaited_once()
        assert (
            graph.aupdate_state.call_args.args[0]["configurable"]["thread_id"]
            == result["thread_id"]
        )
        assert server.services.thread_repo.get("thread1").agent_id == "expert"
        rows, more = server.services.thread_message_repo.page(result["thread_id"], limit=100)
        assert len(rows) == 4 and not more
    finally:
        await client.close()
