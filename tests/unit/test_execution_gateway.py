from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, ToolMessage

from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.execution.gateway import GatewayExecution
from octop.infra.execution.service import ExecutionService
from tests.unit.test_execution_service import setup as setup


async def test_sandbox_preflight_denial_has_failed_receipt_and_releases_thread(setup):
    from octop.infra.db.repos.execution import ExecutionConflict
    from octop.infra.db.repos.users import UserRepo
    from tests.unit.db.test_execution import submit

    pool, repo, user = setup
    registry = SimpleNamespace(
        start=AsyncMock(side_effect=ExecutionConflict("sandbox_memory_exhausted")),
        cancel_stream=Mock(),
        _octop_harness_or_none=lambda _: None,
        call=AsyncMock(),
    )
    server = SimpleNamespace(
        services=SimpleNamespace(user_repo=UserRepo(pool)),
        employee_resources=SimpleNamespace(authorize=AsyncMock()),
        app_runtime=SimpleNamespace(
            agent_registry=registry, gateway=SimpleNamespace(processor=SimpleNamespace())
        ),
    )
    service = ExecutionService(repo, node="worker", incarnation="boot1", role="worker")
    GatewayExecution(server, service)
    run = submit(repo, user)
    await service.start()
    try:
        events = [event async for event in service.replay(run["run_id"], user=user)]
        assert repo.get(run["run_id"])["state"] == "failed"
        assert repo.active("one") is None
        frames = [frame for event in events for frame in event["payload"].get("frames", [])]
        assert frames[0]["code"] == "SANDBOX_MEMORY_EXHAUSTED"
        assert frames[0]["retry_after"] == 5
        from octop_gateway.models import MessageEvent

        assert MessageEvent.model_validate(frames[0]).error == frames[0]["message"]
        registry.call.assert_not_called()
    finally:
        await service.close()


def test_steer_continuation_gets_a_new_execution_key():
    from octop_gateway.models import InboundMessage, TextContent

    from octop.infra.gateway.ws.ws_channel import _continuation_inbound

    original = InboundMessage(
        channel_id="dashboard",
        channel_type="dashboard",
        tenant_id="expert",
        content=[TextContent(text="first")],
        metadata={"client_request_id": "original", "thread_id": "one"},
    )
    continuation = _continuation_inbound(original, text="continue")
    assert continuation.metadata["client_request_id"] != "original"
    assert continuation.metadata["thread_id"] == "one"
    assert original.metadata["client_request_id"] == "original"


async def test_model_budget_settles_usage_and_keeps_unknown_requests_reserved(setup):
    from octop.infra.agents.middleware.execution_budget import ExecutionBudgetMiddleware
    from octop.infra.execution.service import ActiveExecution, ExecutionConflict, current_execution
    from tests.unit.db.test_execution import submit

    _, repo, user = setup
    repo.configure(running=2, models=1, queued=8, user_queued=4)
    submit(repo, user)
    run = repo.claim("worker", "boot1")
    service = ExecutionService(repo, node="worker", incarnation="boot1", role="worker")
    token = current_execution.set(ActiveExecution(service, run))
    middleware = ExecutionBudgetMiddleware()
    request = ModelRequest(model=SimpleNamespace(model_name="test"), messages=[], tools=[])
    response = SimpleNamespace(
        result=[
            AIMessage(
                content="ok",
                usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            )
        ]
    )
    success = AsyncMock(return_value=response)
    failure = AsyncMock(side_effect=OSError("response lost"))
    try:
        assert await middleware.awrap_model_call(request, success) is response
        assert success.call_args.args[0].model_settings["max_tokens"] == 8192
        assert repo.reconciliation(run["run_id"])["models"] == []
        with pytest.raises(OSError, match="response lost"):
            await middleware.awrap_model_call(request, failure)
        assert repo.reconciliation(run["run_id"])["models"][0]["state"] == "unknown"
        with pytest.raises(ExecutionConflict, match="model_capacity_exceeded"):
            await middleware.awrap_model_call(request, failure)
        assert failure.await_count == 1
        # A retry middleware returning a fallback answer must not hide the lost
        # provider result and make its reservation impossible to reconcile.
        with pytest.raises(ExecutionConflict, match="model_result_unconfirmed"):
            repo.transition(*ActiveExecution(service, run).fence, "completed")
        repo.transition(*ActiveExecution(service, run).fence, "unknown")
        assert repo.active("one")["state"] == "unknown"
    finally:
        current_execution.reset(token)
        await service.io.drain()


async def test_durable_call_preserves_tool_messages_and_deduplicates(setup):
    pool, repo, _ = setup
    calls = []

    async def call(agent, request):
        calls.append(request)
        assert request["messages"][0].tool_calls[0]["id"] == "tool-call"
        assert request["messages"][1].tool_call_id == "tool-call"
        assert request["messages"][1].artifact == {"file": "result.txt"}
        return {"ok": True}

    registry = SimpleNamespace(
        call=call, cancel_stream=Mock(), _octop_harness_or_none=lambda _: object()
    )
    server = SimpleNamespace(
        services=SimpleNamespace(thread_repo=ThreadRepo(pool)),
        employee_resources=SimpleNamespace(authorize=AsyncMock()),
        app_runtime=SimpleNamespace(
            agent_registry=registry, gateway=SimpleNamespace(processor=SimpleNamespace())
        ),
    )
    service = ExecutionService(repo, node="worker", incarnation="boot1", role="worker")
    adapter = GatewayExecution(server, service)
    request = {
        "thread_id": "one",
        "client_request_id": "same-call",
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "write_file", "args": {"path": "result.txt"}, "id": "tool-call"}
                ],
            ),
            ToolMessage(content="saved", tool_call_id="tool-call", artifact={"file": "result.txt"}),
        ],
    }
    await service.start()
    try:
        first = [frame async for frame in adapter.manager_stream("expert", request, call=True)]
        second = [frame async for frame in adapter.manager_stream("expert", request, call=True)]
        assert first == second == [{"type": "execution.result", "result": {"ok": True}}]
        assert len(calls) == 1
    finally:
        await service.close()


async def test_claimed_turn_waits_for_idle_sandbox_reclamation_before_any_model(setup):
    from octop.infra.db.repos.execution import ExecutionConflict

    pool, repo, _ = setup
    start = AsyncMock(side_effect=[ExecutionConflict("sandbox_memory_exhausted"), None])
    call = AsyncMock(return_value={"ok": True})
    reap = AsyncMock(return_value=["old-idle-expert"])
    registry = SimpleNamespace(
        start=start,
        call=call,
        cancel_stream=Mock(),
        runtime_shards=SimpleNamespace(reap=reap),
    )
    server = SimpleNamespace(
        services=SimpleNamespace(thread_repo=ThreadRepo(pool)),
        employee_resources=SimpleNamespace(authorize=AsyncMock()),
        app_runtime=SimpleNamespace(
            agent_registry=registry, gateway=SimpleNamespace(processor=SimpleNamespace())
        ),
    )
    service = ExecutionService(repo, node="worker", incarnation="boot1", role="worker")
    adapter = GatewayExecution(server, service)
    await service.start()
    try:
        frames = [
            frame
            async for frame in adapter.manager_stream(
                "expert",
                {"thread_id": "one", "messages": [], "client_request_id": "capacity-wait"},
                call=True,
            )
        ]
        assert frames == [{"type": "execution.result", "result": {"ok": True}}]
        assert start.await_count == 2 and reap.await_count == 1
        assert call.await_count == 1
        assert repo.active("one") is None
    finally:
        await service.close()
