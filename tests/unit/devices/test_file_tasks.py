import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import SystemMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import ValidationError

from octop.api.routers.chat.models import ChatTurnBody, UserTurnWsFrame
from octop.infra.agents.middleware.device_tools import DeviceToolsMiddleware
from octop.infra.devices.protocol import DeviceSelection, DirectoryGrant
from octop.infra.devices.tasks import CONFIG_KEY, stamp_device_task
from octop.infra.errors import OctopError
from octop.infra.users.model_access import ModelAccess
from tests.unit.devices.test_channel import Socket, finish


async def connect_files(manager, owner):
    code = await manager.issue_pairing(owner, "session-alice", int(time.time()) + 600)
    grant = DirectoryGrant(id="a" * 32, name="Work", version=1, writable=True, allow_company=True)
    socket = Socket()
    await socket.send(
        {
            "type": "pair",
            "credential": code["code"],
            "name": "Desktop",
            "platform": "windows",
            "protocol_version": 2,
            "capabilities": ["device_status", "local_files"],
            "grants": [grant.model_dump()],
        }
    )
    runner = asyncio.create_task(manager.serve(socket))
    ready = await socket.receive()
    selection = DeviceSelection(
        mode="auto", device_id=ready["device_id"], grant_id=grant.id, grant_version=1
    )
    return socket, runner, selection


async def bind(manager, owner, selection):
    token = await manager.bind_task(
        owner=owner,
        agent_id="private",
        thread_id="thread",
        session_key="session",
        selection=selection,
    )
    return manager.tasks[token]


async def test_retried_turn_reuses_device_authority_but_changed_selection_conflicts(stack):
    server, alice, _, _ = stack
    manager = server.devices
    _, runner, selection = await connect_files(manager, alice)
    kwargs = {
        "owner": alice,
        "agent_id": "private",
        "thread_id": "thread",
        "session_key": "session",
        "request_id": "request",
    }
    try:
        token = await manager.bind_task(**kwargs, selection=selection)
        assert await manager.bind_task(**kwargs, selection=selection) == token
        with pytest.raises(OctopError, match="identity_conflict"):
            await manager.bind_task(
                **kwargs, selection=selection.model_copy(update={"grant_version": 2})
            )
        assert len(manager.tasks) == 1
    finally:
        await finish(runner)


async def test_durable_tool_receipt_survives_lost_in_memory_future(stack):
    from octop.infra.execution.service import ActiveExecution, ExecutionService, current_execution

    server, alice, _, _ = stack
    manager = server.devices
    repo = server.services.execution_repo
    repo.register("worker", "boot", "worker")
    service = ExecutionService(repo, node="worker", incarnation="boot", role="worker")
    manager.execution = service
    server.services.agent_repo.create(agent_id="private", user_id=alice, name="Private")
    server.services.thread_repo.insert(
        thread_id="thread",
        agent_id="private",
        user_id=alice,
        channel_type="dashboard",
        session_key="session",
    )
    repo.submit(
        user=alice,
        agent="private",
        thread="thread",
        operation="chat",
        key="tool-request",
        request={},
    )
    run = repo.claim("worker", "boot")
    socket, runner, selection = await connect_files(manager, alice)
    token = current_execution.set(ActiveExecution(service, run))
    try:
        task = await bind(manager, alice, selection)
        conn = manager.connections[selection.device_id]
        pending = asyncio.create_task(
            manager._call(
                conn,
                alice,
                "local_write_file",
                {"path": "result.txt", "content": "test"},
                timeout=0.05,
                task=task,
            )
        )
        call = await socket.receive()
        assert (await pending)["status"] == "timed_out"
        assert call["call_id"] not in conn.pending
        assert (await socket.receive())["type"] == "cancel"
        await socket.send(
            {
                "type": "result",
                "call_id": call["call_id"],
                "result": {"kind": "local_operation", "status": "completed", "path": "result.txt"},
            }
        )
        await socket.send({"type": "heartbeat"})
        assert (await socket.receive())["type"] == "heartbeat_ack"
        operation = repo.device_operation(selection.device_id, conn.generation, call["call_id"])
        assert operation["state"] == "completed"
        assert json.loads(operation["result_json"])["result"]["status"] == "completed"
    finally:
        current_execution.reset(token)
        await finish(runner)
        await service.io.drain()


async def test_file_context_owner_consent_version_and_revocation(stack):
    server, alice, bob, _ = stack
    manager = server.devices
    socket, runner, selection = await connect_files(manager, alice)
    try:
        for owner, chosen in [
            (bob, selection),
            (alice, selection.model_copy(update={"grant_version": 2})),
            (alice, selection.model_copy(update={"grant_id": "b" * 32})),
        ]:
            with pytest.raises(OctopError):
                await bind(manager, owner, chosen)
        task = await bind(manager, alice, selection)
        pending = asyncio.create_task(
            manager.execute(task, "local_read_file", {"path": "input.csv"})
        )
        call = await socket.receive()
        assert (call["agent_id"], call["thread_id"], call["task_id"], call["grant_id"]) == (
            "private",
            "thread",
            task.token,
            selection.grant_id,
        )
        await socket.send(
            {
                "type": "result",
                "call_id": call["call_id"],
                "result": {"kind": "local_text", "status": "completed", "text": "bounded data"},
            }
        )
        result = await pending
        assert result["execution_location"] == "local"
        assert result["result"]["text"] == "bounded data"
        await socket.send({"type": "grants", "grants": []})
        await socket.send({"type": "heartbeat"})
        await socket.receive()
        with pytest.raises(OctopError, match="directory_permission_revoked"):
            await manager.execute(task, "local_read_file", {"path": "input.csv"})
        assert (await manager.list_devices(alice))[0]["grants"] == []
    finally:
        await finish(runner)


async def test_file_write_disconnect_never_replayed(stack):
    server, alice, _, _ = stack
    manager = server.devices
    socket, runner, selection = await connect_files(manager, alice)
    task = await bind(manager, alice, selection)
    pending = asyncio.create_task(
        manager.execute(task, "local_write_file", {"path": "report.md", "content": "report"})
    )
    call = await socket.receive()
    await finish(runner)
    result = await pending
    assert result["status"] == "device_disconnected"
    assert result["call_id"] == call["call_id"]
    assert socket.outgoing.empty()


async def test_task_middleware_visibility_and_execution_guards(stack, monkeypatch):
    server, alice, _, _ = stack
    socket, runner, selection = await connect_files(server.devices, alice)
    try:
        task = await bind(server.devices, alice, selection)
        cfg = {
            CONFIG_KEY: task.token,
            "user": str(alice),
            "agent_id": "private",
            "thread_id": "thread",
            "session_key": "session",
            "conversation_mode": "craft",
        }
        monkeypatch.setattr(
            "octop.infra.agents.middleware.device_tools.get_config", lambda: {"configurable": cfg}
        )
        middleware = DeviceToolsMiddleware(
            lambda: server.devices, lambda: server.services.device_repo
        )
        allowed, _ = await middleware._allowed()
        assert "local_write_file" in allowed and "search_knowledge" in allowed
        assert not {"shell", "task", "ask_agent", "web_fetch", "write_file"} & allowed
        cfg["conversation_mode"] = "ask"
        assert "local_write_file" not in (await middleware._allowed())[0]
        cfg["user"] = "999"
        assert (await middleware._allowed())[0] == set()
        cfg["user"] = str(alice)
        cfg["agent_id"] = "other"
        assert (await middleware._allowed())[0] == set()
        cfg["agent_id"] = "private"
        request = ModelRequest(
            model=SimpleNamespace(),
            messages=[],
            system_message=SystemMessage(content="existing"),
            tools=[*middleware.tools, {"name": "shell"}, {"name": "search_knowledge"}],
        )
        model_handler = AsyncMock(return_value="response")
        await middleware.awrap_model_call(request, model_handler)
        names = {
            t.name if hasattr(t, "name") else t["name"]
            for t in model_handler.call_args.args[0].tools
        }
        assert (
            "local_read_file" in names and "shell" not in names and "local_write_file" not in names
        )
        handler = AsyncMock()
        denied = await middleware.awrap_tool_call(
            ToolCallRequest(
                tool_call={"name": "shell", "args": {}, "id": "call", "type": "tool_call"},
                tool=None,
                state={},
                runtime=SimpleNamespace(),
            ),
            handler,
        )
        assert denied.status == "error"
        handler.assert_not_called()
        tool = next(t for t in middleware.tools if t.name == "local_write_file")
        result = json.loads(await tool.ainvoke({"path": "report.md", "content": "x"}))
        assert result["error"] == "read_only_conversation"
        assert socket.outgoing.empty()
    finally:
        await finish(runner)


async def test_context_clear_and_existing_history_guard(stack, monkeypatch):
    server, alice, _, _ = stack
    svc = server.services
    # The persistent boundary survives disconnect/restart and does not need a device manager.
    svc.agent_repo.create(agent_id="private", user_id=alice, name="Private")
    svc.thread_repo.insert(
        thread_id="thread",
        agent_id="private",
        user_id=alice,
        channel_type="dashboard",
        session_key="session",
    )
    svc.device_repo.mark_file_thread("thread")
    middleware = DeviceToolsMiddleware(lambda: None, lambda: svc.device_repo)
    cfg = {"thread_id": "thread"}
    monkeypatch.setattr(
        "octop.infra.agents.middleware.device_tools.get_config", lambda: {"configurable": cfg}
    )
    assert (await middleware._allowed())[0] == {"search_knowledge"}
    cfg["thread_id"] = "fresh"
    assert (await middleware._allowed())[0] is None
    request = {"configurable": {CONFIG_KEY: "old"}}
    stamp_device_task(request, {})
    assert request["configurable"][CONFIG_KEY] is None


def test_ws_device_selection_is_validated_and_preserved():
    device = {"mode": "local", "device_id": "a" * 32, "grant_id": "b" * 32, "grant_version": 1}
    turn = UserTurnWsFrame.model_validate(
        {"type": "user_turn", "text": "read", "device": device}
    ).to_turn_body()
    assert turn.device.model_dump() == device
    for changed in [{"mode": "shell"}, {"owner_user_id": 9}, {"grant_version": 0}]:
        with pytest.raises(ValidationError):
            ChatTurnBody.from_ws_payload({"text": "read", "device": {**device, **changed}})


async def test_hitl_resumes_only_the_original_task_scope(stack, monkeypatch):
    from octop.infra.devices.tasks import effective_device_config, resumed_device_task

    server, alice, _, _ = stack
    socket, runner, selection = await connect_files(server.devices, alice)
    try:
        task = await bind(server.devices, alice, selection)
        original = {
            CONFIG_KEY: task.token,
            "user": str(alice),
            "agent_id": "private",
            "thread_id": "thread",
            "session_key": "session",
            "conversation_mode": "craft",
        }
        monkeypatch.setattr(
            "octop.infra.agents.middleware.device_tools.get_config",
            lambda: {"configurable": {"thread_id": "thread"}},
        )
        middleware = DeviceToolsMiddleware(
            lambda: server.devices, lambda: server.services.device_repo
        )
        with resumed_device_task(original):
            assert "local_write_file" in (await middleware._allowed())[0]
            assert CONFIG_KEY not in effective_device_config({"thread_id": "other"})
            await server.devices.revoke(alice, selection.device_id)
            assert "local_write_file" not in (await middleware._allowed())[0]
        assert CONFIG_KEY not in effective_device_config({"thread_id": "thread"})
    finally:
        await finish(runner)


async def test_real_agent_graph_reads_device_and_preserves_company_boundary(stack):
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from pydantic import Field

    class PlannedModel(BaseChatModel):
        calls: int = 0
        visible: list[str] = Field(default_factory=list)

        @property
        def _llm_type(self):
            return "device-integration-test"

        def bind_tools(self, tools, **kwargs):
            self.visible = [tool.name if hasattr(tool, "name") else tool["name"] for tool in tools]
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            answer = (
                AIMessage(
                    content="read",
                    tool_calls=[
                        {
                            "name": "local_read_file",
                            "args": {"path": "sheet.csv"},
                            "id": "read-call",
                            "type": "tool_call",
                        }
                    ],
                )
                if self.calls == 1
                else AIMessage(content="Read complete")
            )
            return ChatResult(generations=[ChatGeneration(message=answer)])

    server, alice, _, _ = stack
    socket, runner, selection = await connect_files(server.devices, alice)
    try:
        task = await bind(server.devices, alice, selection)
        middleware = DeviceToolsMiddleware(
            lambda: server.devices, lambda: server.services.device_repo
        )
        model = PlannedModel()
        from octop.infra.agents.middleware.device_tools import DeviceToolGuardMiddleware

        graph = create_agent(
            model=model, middleware=[DeviceToolGuardMiddleware(middleware), middleware]
        )
        config = {
            "configurable": {
                CONFIG_KEY: task.token,
                "user": str(alice),
                "agent_id": "private",
                "thread_id": "thread",
                "session_key": "session",
                "conversation_mode": "craft",
            }
        }
        execution = asyncio.create_task(
            graph.ainvoke({"messages": [HumanMessage(content="Read sheet.csv")]}, config=config)
        )
        call = await socket.receive()
        assert call["tool"] == "local_read_file"
        await socket.send(
            {
                "type": "result",
                "call_id": call["call_id"],
                "result": {"kind": "local_text", "status": "completed", "text": "name,value\na,10"},
            }
        )
        result = await asyncio.wait_for(execution, 5)
        outputs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert json.loads(outputs[0].content)["execution_location"] == "local"
        assert "local_read_file" in model.visible
        assert result["messages"][-1].content == "Read complete"
    finally:
        await finish(runner)


async def test_chat_preparation_binds_authenticated_owner_and_private_expert(stack, monkeypatch):
    from octop.api.routers.chat.turn import build_dashboard_inbound, prepare_dashboard_turn

    server, alice, bob, _ = stack
    svc = server.services
    svc.provider_repo.create(
        name="test",
        kind="openai",
        base_url="https://model.invalid/v1",
        api_key="fake",
        models_json='[{"id":"chat"}]',
    )
    ModelAccess(svc).set(alice, ["test/chat"], "0", actor="bob")
    user = server.user_manager.get_by_id(alice)
    svc.agent_repo.create(agent_id="private", user_id=alice, name="Private")
    svc.thread_repo.insert(
        thread_id="thread",
        agent_id="private",
        user_id=alice,
        channel_type="dashboard",
        session_key="session",
    )
    registry = SimpleNamespace(get_thread=svc.thread_repo.get)
    server.app_runtime = SimpleNamespace(
        gateway=SimpleNamespace(thread_registry=registry), agent_registry=SimpleNamespace()
    )
    row = SimpleNamespace(user_id=alice, is_shared=0, kind="expert", default_model=None)
    prefix = "octop.api.routers.chat.turn."
    monkeypatch.setattr(prefix + "require_agent_row", lambda *args, **kwargs: row)
    monkeypatch.setattr(prefix + "resolve_thread_id", AsyncMock(return_value=("thread", "session")))
    mcp = AsyncMock(return_value=[])
    monkeypatch.setattr(prefix + "validate_chat_mcp_servers", mcp)
    monkeypatch.setattr(prefix + "validate_chat_skills", AsyncMock(return_value=[]))
    socket, runner, selection = await connect_files(server.devices, alice)
    try:
        turn = ChatTurnBody(
            text="read input.csv", device=selection, mcp_servers=["external"], skills=["script"]
        )
        prepared = await prepare_dashboard_turn(server, agent_id="private", user=user, turn=turn)
        assert prepared.device_task in server.devices.tasks
        assert svc.device_repo.is_file_thread("thread")
        assert mcp.call_args.kwargs["names"] == []
        inbound = build_dashboard_inbound(
            agent_id="private", user_id=alice, prepared=prepared, turn=turn, ws_connection_id="ws"
        )
        assert inbound.metadata[CONFIG_KEY] == prepared.device_task
        assert inbound.metadata["mcp_servers"] == []
        row.user_id = bob
        with pytest.raises(OctopError):
            await prepare_dashboard_turn(server, agent_id="private", user=user, turn=turn)
        row.user_id, row.kind = alice, "team"
        with pytest.raises(OctopError):
            await prepare_dashboard_turn(server, agent_id="private", user=user, turn=turn)
    finally:
        await finish(runner)


async def test_failed_file_operation_is_not_reported_as_completed(stack):
    server, alice, _, _ = stack
    socket, runner, selection = await connect_files(server.devices, alice)
    try:
        task = await bind(server.devices, alice, selection)
        execution = asyncio.create_task(
            server.devices.execute(task, "local_write_file", {"path": "report.md", "content": "x"})
        )
        call = await socket.receive()
        await socket.send(
            {
                "type": "result",
                "call_id": call["call_id"],
                "result": {
                    "kind": "local_error",
                    "status": "failed",
                    "error": "output_exists_or_not_writable",
                },
            }
        )
        assert (await execution)["status"] == "failed"
    finally:
        await finish(runner)
