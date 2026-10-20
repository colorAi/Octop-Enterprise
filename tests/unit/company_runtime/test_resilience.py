"""Execution must survive synchronization faults without replaying local effects."""

import asyncio
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from octop.infra.company_runtime.runtime import EnterpriseRuntime

from .test_runtime import ToolModel, acquire, install_agent


def runtime_for(client, tmp_path):
    events = []
    runtime = EnterpriseRuntime(events.append)
    runtime.client = client
    runtime.workspace = tmp_path / "workspace"
    runtime.workspace.mkdir()
    runtime.state = tmp_path / "state"
    runtime.authorized = True
    runtime.installation_token = "i" * 32
    runtime.tools = dict.fromkeys(("ffmpeg", "openpyxl", "python-docx", "Pillow"), "test")
    runtime.tool_path = str(Path(sys.executable).parent)
    return runtime, events


async def run_turn(runtime, **options):
    await runtime.start(
        {
            "task_id": "task1",
            "thread_id": "thread1",
            "agent_id": "expert",
            "text": "Create report",
            **options,
        }
    )
    await asyncio.wait_for(runtime.work["thread1"], 15)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "close"])
async def test_final_sync_cannot_be_cancelled_out_of_cleanup(
    company, tmp_path, monkeypatch, action
):
    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    entered_sync = asyncio.Event()
    send = client.http.send

    async def hold_checkpoint(request, *args, **kwargs):
        if request.url.path.endswith("/checkpoint-batch"):
            entered_sync.set()
            await asyncio.Event().wait()
        return await send(request, *args, **kwargs)

    monkeypatch.setattr(client.http, "send", hold_checkpoint)
    try:
        await runtime.start(
            {
                "task_id": "task1",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": "Create report",
            }
        )
        worker = runtime.work["thread1"]
        await asyncio.wait_for(entered_sync.wait(), 10)
        assert (runtime.workspace / "thread1/report.txt").is_file()
        if action == "close":
            await asyncio.wait_for(runtime.close(), 10)
        else:
            for _ in range(2):
                assert await runtime.cancel({"thread_id": "thread1"}) == {"status": "completed"}
        await asyncio.wait_for(worker, 10)
        assert "thread1" not in runtime.work
        done = [event for event in events if event.get("event") == "done"]
        assert len(done) == 1
        assert done[0]["status"] == "completed"
        assert done[0]["sync_state"] == "pending"
        assert runtime.tasks["thread1"]["status"] == "completed"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_lost_start_response_survives_restart_and_releases_server_lease(
    company, tmp_path, monkeypatch
):
    server, client, _, _ = company
    install_agent(server)
    runtime, _ = runtime_for(client, tmp_path)
    send = client.http.send
    dropped = False

    async def drop_after_commit(request, *args, **kwargs):
        nonlocal dropped
        response = await send(request, *args, **kwargs)
        if request.url.path == "/api/company-runtime/tasks" and not dropped:
            dropped = True
            assert response.status_code == 200
            raise httpx.ReadError("accepted task response lost", request=request)
        return response

    monkeypatch.setattr(client.http, "send", drop_after_commit)
    restored = EnterpriseRuntime(lambda event: None)
    try:
        with pytest.raises(httpx.ReadError):
            await runtime.start(
                {
                    "task_id": "task1",
                    "thread_id": "thread1",
                    "agent_id": "expert",
                    "text": "Create report",
                }
            )
        assert server.services.company_runtime_repo.task("task1")["status"] == "running"
        saved = json.loads((runtime.state / "tasks/task1/task.json").read_text())
        assert saved["status"] == "unknown" and saved["recovery_token"]
        assert not runtime.work
        restored.client, restored.state, restored.workspace = (
            client,
            runtime.state,
            runtime.workspace,
        )
        restored.installation_token = runtime.installation_token
        restored.authorized, restored.tools = True, runtime.tools
        await restored.restore_tasks()
        assert restored.tasks["thread1"]["status"] == "unknown"
        assert await restored.reconcile(
            {"thread_id": "thread1", "task_id": "task1", "resolution": "cancelled"}
        ) == {"status": "cancelled"}
        assert server.services.company_runtime_repo.task("task1")["status"] == "cancelled"
        assert not (runtime.workspace / "thread1/report.txt").exists()
        await run_turn(restored, task_id="task2")
        assert restored.tasks["thread1"]["status"] == "completed"
    finally:
        runtime.client = None
        await runtime.close()
        await restored.close()


@pytest.mark.asyncio
async def test_rejected_acquisition_does_not_leave_an_unknown_task(company, tmp_path):
    from octop.infra.company_runtime.client import CompanyServiceError

    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    try:
        with pytest.raises(CompanyServiceError):
            await runtime.start(
                {
                    "task_id": "rejected",
                    "thread_id": "thread1",
                    "agent_id": "missing",
                    "text": "Hello",
                }
            )
        assert runtime.tasks["thread1"]["status"] == "failed"
        assert runtime.tasks["thread1"]["sync_state"] == "synced"
        await run_turn(runtime)
        assert events[-1]["status"] == "completed", events[-1]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_graph_nodes_use_local_checkpoints_and_one_batch(company, tmp_path, monkeypatch):
    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    calls = Counter()
    send = client.http.send

    async def record(request, *args, **kwargs):
        calls[request.url.path] += 1
        return await send(request, *args, **kwargs)

    monkeypatch.setattr(client.http, "send", record)
    try:
        await run_turn(runtime)
        assert events[-1]["status"] == "completed"
        assert calls["/api/company-runtime/tasks/task1/checkpoint/get"] == 1
        assert calls["/api/company-runtime/tasks/task1/checkpoint-batch"] == 1
        assert calls["/api/company-runtime/tasks/task1/checkpoint/put"] == 0
        assert calls["/api/company-runtime/tasks/task1/checkpoint/writes"] == 0
        assert sum(calls.values()) <= 12, calls
        assert not list((runtime.state / "tasks/task1/checkpoints").glob("*.json"))
        assert server.services.company_runtime_repo.checkpoint("thread1", "", None)
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_path", ["/checkpoint-batch", "/status", "/events"])
async def test_completed_execution_survives_sync_failure(
    company, tmp_path, monkeypatch, failed_path
):
    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    send = client.http.send
    failed = False

    async def interrupt(request, *args, **kwargs):
        nonlocal failed
        if not failed and request.url.path.endswith(failed_path):
            failed = True
            raise httpx.ReadError("temporary synchronization failure", request=request)
        return await send(request, *args, **kwargs)

    monkeypatch.setattr(client.http, "send", interrupt)
    try:
        await run_turn(runtime)
        assert failed
        assert events[-1]["status"] == "completed"
        assert events[-1]["sync_state"] == "pending"
        output = runtime.workspace / "thread1/report.txt"
        assert output.read_text() == "local report 42"
        directory = runtime.state / "tasks/task1"
        await runtime.recover(runtime.tasks["thread1"], directory)
        assert runtime.tasks["thread1"]["sync_state"] == "synced"
        assert (await client.request("tasks/task1"))["status"] == "completed"
        receipts = list((runtime.state / "threads/thread1/operations").glob("*.json"))
        assert len(receipts) == 1
        assert json.loads(receipts[0].read_text())["status"] == "completed"
        assert not list((directory / "checkpoints").glob("*.json"))
        assert not list((directory / "pending").glob("*.json"))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_restart_uploads_saved_checkpoints_without_restarting_the_graph(
    company, tmp_path, monkeypatch
):
    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    original = client.http.send
    interrupted = False

    async def disconnect(request, *args, **kwargs):
        nonlocal interrupted
        if not interrupted and request.url.path.endswith("/checkpoint-batch"):
            interrupted = True
            raise httpx.ReadError("offline at completion", request=request)
        return await original(request, *args, **kwargs)

    monkeypatch.setattr(client.http, "send", disconnect)
    await run_turn(runtime)
    output = runtime.workspace / "thread1/report.txt"
    written_at = output.stat().st_mtime_ns
    assert events[-1]["sync_state"] == "pending"
    runtime.client = None
    await runtime.close()
    restored = EnterpriseRuntime(lambda event: None)
    restored.client, restored.state, restored.workspace = client, runtime.state, runtime.workspace
    restored.installation_token = "i" * 32
    monkeypatch.setattr(restored, "run", AsyncMock(side_effect=AssertionError("must not replay")))
    try:
        await restored.restore_tasks()
        assert restored.tasks["thread1"]["status"] == "completed"
        assert restored.tasks["thread1"]["sync_state"] == "synced"
        assert output.stat().st_mtime_ns == written_at
        assert not restored.work
        assert not restored.run.called
        assert server.services.company_runtime_repo.checkpoint("thread1", "", None)
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_temporary_model_timeout_retries_without_replaying_tools(company, tmp_path):
    server, client, _, _ = company
    install_agent(server)
    calls = 0

    class BusyModel(ToolModel):
        async def astream(self, messages):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("temporarily busy")
            async for chunk in super().astream(messages):
                yield chunk

    model = BusyModel()
    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: model
    )
    runtime, events = runtime_for(client, tmp_path)
    try:
        await run_turn(runtime)
        assert events[-1]["status"] == "completed", events[-1]
        assert calls == 3
        assert (runtime.workspace / "thread1/report.txt").is_file()
        assert len(list((runtime.state / "threads/thread1/operations").glob("*.json"))) == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_partial_model_output_is_not_retried(company, tmp_path):
    from langchain_core.messages import AIMessageChunk

    server, client, _, _ = company
    install_agent(server)
    calls = 0

    class PartialModel(ToolModel):
        async def astream(self, messages):
            nonlocal calls
            calls += 1
            yield AIMessageChunk(content="Partial answer", id="partial")
            raise TimeoutError("stream disconnected after output")

    model = PartialModel()
    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: model
    )
    runtime, events = runtime_for(client, tmp_path)
    try:
        await run_turn(runtime)
        assert calls == 1
        assert events[-1]["status"] == "failed"
        assert "company_model_timeout" in events[-1]["error"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_plan_mode_cannot_write_an_ordinary_project_file(company, tmp_path):
    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    try:
        await run_turn(runtime, conversation_mode="plan")
        assert events[-1]["status"] == "completed", events[-1]
        assert not (runtime.workspace / "thread1/report.txt").exists()
        task = server.services.company_runtime_repo.task("task1")
        assert json.loads(task["request_json"])["conversation_mode"] == "plan"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_reasoning_and_expert_sampling_reach_the_company_model(company, tmp_path):
    server, client, _, _ = company
    install_agent(server)
    server.services.agent_repo.update_config(
        "expert", config_json=json.dumps({"temperature": 0.2, "max_tokens": 2048})
    )
    server.services.provider_repo.create(
        name="test",
        kind="openai",
        base_url="http://model.test/v1",
        api_key="fixture",
        models_json=json.dumps(
            [
                {
                    "id": "model",
                    "reasoning_config": {"adapter": "thinking", "efforts": ["low", "high"]},
                }
            ]
        ),
    )
    received = []

    class ConfiguredModel(ToolModel):
        extra_body = None

        def model_copy(self, *, update):
            received.append(update)
            model = copy.copy(self)
            for key, value in update.items():
                setattr(model, key, value)
            return model

        def bind(self, **kwargs):
            received.append(kwargs)
            return self

    model = ConfiguredModel()
    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: model
    )
    runtime, events = runtime_for(client, tmp_path)
    try:
        await run_turn(runtime, reasoning_mode="enabled", reasoning_effort="high")
        assert events[-1]["status"] == "completed", events[-1]
        assert any(
            item.get("temperature") == 0.2 and item.get("max_tokens") == 2048 for item in received
        )
        assert any(
            item.get("extra_body", {}).get("thinking") == {"type": "enabled"} for item in received
        ), received
        assert any(
            item.get("extra_body", {}).get("reasoning_effort") == "high" for item in received
        ), received
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_checkpoint_batches_are_atomic_scoped_and_idempotent(company):
    server, client, credentials, _ = company
    install_agent(server)
    await acquire(client)
    operation = {
        "action": "put",
        "namespace": "",
        "checkpoint_id": "cp1",
        "payload": {"fixture": 1},
    }
    body = {"operations": [operation]}
    assert await client.request("tasks/task1/checkpoint-batch", body) == {"saved": 1}
    assert await client.request("tasks/task1/checkpoint-batch", body) == {"saved": 1}
    response = await client.http.post(
        "tasks/task1/checkpoint-batch",
        json=body,
        headers={"X-Octop-Device-Credential": credentials[1]},
    )
    assert response.status_code == 403
    conflict = {
        "operations": [
            {**operation, "checkpoint_id": "cp2"},
            {**operation, "payload": {"fixture": 2}},
        ]
    }
    response = await client.http.post("tasks/task1/checkpoint-batch", json=conflict)
    assert response.status_code == 409
    assert server.services.company_runtime_repo.checkpoint("thread1", "", "cp2") is None
    await client.request("tasks/task1/status", {"status": "completed"})
    assert (await client.http.post("tasks/task1/checkpoint-batch", json=body)).status_code == 409
    await client.close()
