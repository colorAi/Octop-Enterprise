"""Ownership polling must not interrupt a task's terminal cleanup."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from langchain_core.messages import ToolMessage

from octop.infra.company_runtime import runtime as runtime_module

from .test_resilience import run_turn, runtime_for
from .test_runtime import ToolModel, install_agent


@pytest_asyncio.fixture
async def resource_closers(monkeypatch):
    """Observe real cleanup and release SQLite even if a regression assertion fails."""
    closers = []
    for name, method in (
        ("ProjectBackend", "shutdown"),
        ("HarnessAgent", "aclose"),
        ("LocalCheckpointSaver", "close"),
    ):
        factory = getattr(runtime_module, name)

        def capture(*args, _factory=factory, _method=method, **kwargs):
            resource = _factory(*args, **kwargs)
            close = AsyncMock(wraps=getattr(resource, _method))
            monkeypatch.setattr(resource, _method, close)
            closers.append(close)
            return resource

        monkeypatch.setattr(runtime_module, name, capture)
    yield closers
    for close in closers:
        if not close.await_count:
            await close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["complete", "fail", "cancel", "close"])
async def test_pending_status_poll_cannot_interrupt_finalization(
    company, tmp_path, monkeypatch, resource_closers, action
):
    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    polling = asyncio.Event()
    send = client.http.send

    async def hold_poll(request, *args, **kwargs):
        current = asyncio.current_task()
        if (
            request.method == "GET"
            and request.url.path == "/api/company-runtime/tasks/task1"
            and current is not None
            and current.get_coro().__name__ == "watch"
        ):
            polling.set()
            await asyncio.Event().wait()
        return await send(request, *args, **kwargs)

    class FinishDuringPolling(ToolModel):
        async def astream(self, messages):
            if any(isinstance(message, ToolMessage) for message in messages):
                await asyncio.wait_for(polling.wait(), 10)
                if action == "fail":
                    raise RuntimeError("model failed during status poll")
                if action in {"cancel", "close"}:
                    await asyncio.Event().wait()
            async for chunk in super().astream(messages):
                yield chunk

    monkeypatch.setattr(client.http, "send", hold_poll)
    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: FinishDuringPolling()
    )
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
        await asyncio.wait_for(polling.wait(), 10)
        if action == "cancel":
            assert await runtime.cancel({"thread_id": "thread1"}) == {"status": "cancel_requested"}
        elif action == "close":
            await asyncio.wait_for(runtime.close(), 10)
        await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), 10)
        expected = {"complete": "completed", "fail": "failed"}.get(action, "cancelled")
        assert (runtime.workspace / "thread1/report.txt").read_text() == "local report 42"
        assert "thread1" not in runtime.work
        assert not worker.cancelled()
        done = [event for event in events if event.get("event") == "done"]
        assert len(done) == 1
        assert done[0]["status"] == expected
        assert done[0]["sync_state"] == "synced"
        receipt = json.loads((runtime.state / "tasks/task1/task.json").read_text())
        assert receipt["status"] == receipt["reported_status"] == expected
        assert receipt["sync_state"] == "synced"
        assert server.services.company_runtime_repo.task("task1")["status"] == expected
        assert len(resource_closers) == 3
        for close in resource_closers:
            close.assert_awaited_once()
        if action != "close":
            # The server lease and local conversation must both accept the next turn.
            install_agent(server)
            await runtime.start(
                {
                    "task_id": "task2",
                    "thread_id": "thread1",
                    "agent_id": "expert",
                    "text": "Continue",
                }
            )
            await asyncio.wait_for(runtime.work["thread1"], 10)
            assert runtime.tasks["thread1"]["status"] == "completed"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_final_sync_keeps_task_running_until_done(company, tmp_path, monkeypatch):
    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    flush = runtime_module.flush_checkpoints

    async def hold_sync(*args):
        entered.set()
        await release.wait()
        await flush(*args)

    monkeypatch.setattr(runtime_module, "flush_checkpoints", hold_sync)
    turn = asyncio.create_task(run_turn(runtime))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert runtime.status()["active_tasks"][0]["status"] == "running"
        assert "thread1" in runtime.work
        assert not any(event.get("event") == "done" for event in events)
        assert await runtime.cancel({"thread_id": "thread1"}) == {"status": "completed"}
        release.set()
        await asyncio.wait_for(turn, 10)
        assert runtime.tasks["thread1"]["status"] == "completed"
        assert len([event for event in events if event.get("event") == "done"]) == 1
    finally:
        release.set()
        await asyncio.gather(turn, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "method"),
    [
        (runtime_module.ProjectBackend, "shutdown"),
        (runtime_module.HarnessAgent, "aclose"),
        (runtime_module.LocalCheckpointSaver, "close"),
    ],
    ids=["backend", "agent", "saver"],
)
async def test_cleanup_failure_closes_other_resources_and_releases_task(
    company, tmp_path, monkeypatch, resource_closers, factory, method, caplog
):
    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    original_close = getattr(factory, method)

    async def fail_close(self):
        # Release the real fixture resource, then reproduce a close error.
        await original_close(self)
        raise RuntimeError(f"cleanup failed: {method}")

    monkeypatch.setattr(factory, method, fail_close)
    try:
        await run_turn(runtime)
        assert "thread1" not in runtime.work
        assert (runtime.workspace / "thread1/report.txt").read_text() == "local report 42"
        assert len(resource_closers) == 3
        for close in resource_closers:
            close.assert_awaited_once()
        done = [event for event in events if event.get("event") == "done"]
        assert len(done) == 1
        assert done[0]["status"] == "unknown"
        assert done[0]["sync_state"] == "synced"
        assert done[0]["error"] == f"cleanup failed: {method}"
        assert f"cleanup failed: {method}" in caplog.text
        receipt = json.loads((runtime.state / "tasks/task1/task.json").read_text())
        assert receipt["status"] == receipt["reported_status"] == "unknown"
        assert server.services.company_runtime_repo.task("task1")["status"] == "unknown"
        with pytest.raises(RuntimeError, match="上次任务结果待核对"):
            await runtime.start(
                {
                    "task_id": "task2",
                    "thread_id": "thread1",
                    "agent_id": "expert",
                    "text": "Continue",
                }
            )
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before_sync", "after_sync", "persistent"])
async def test_finalization_write_failure_emits_done_and_recovers_without_replay(
    company, tmp_path, monkeypatch, resource_closers, stage, caplog
):
    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    atomic_json = runtime_module.atomic_json
    failures = []
    retry_gate = asyncio.Event()

    async def pause_retry(*args):
        await retry_gate.wait()

    def fail_receipt(path, value):
        if path.name == "task.json" and value.get("reported_status"):
            matches = (
                stage == "persistent"
                or (stage == "before_sync" and value.get("sync_state") != "synced")
                or (stage == "after_sync" and value.get("sync_state") == "synced")
            )
            if matches and (not failures or stage == "persistent"):
                failures.append(path)
                raise OSError("task receipt write failed")
        atomic_json(path, value)

    monkeypatch.setattr(runtime_module, "atomic_json", fail_receipt)
    monkeypatch.setattr(runtime, "retry_sync", pause_retry)
    try:
        await run_turn(runtime)
        assert failures
        assert not runtime.work
        assert "task1" in runtime.sync_work
        for close in resource_closers:
            close.assert_awaited_once()
        done = [event for event in events if event.get("event") == "done"]
        assert len(done) == 1
        assert done[0]["status"] == "completed"
        assert done[0]["sync_state"] == "pending"
        assert done[0]["error"] == "task receipt write failed"
        assert "task receipt write failed" in caplog.text
        output = runtime.workspace / "thread1/report.txt"
        written_at = output.stat().st_mtime_ns
        monkeypatch.setattr(runtime_module, "atomic_json", atomic_json)
        await runtime.recover(runtime.tasks["thread1"], runtime.state / "tasks/task1")
        assert runtime.tasks["thread1"]["sync_state"] == "synced"
        assert server.services.company_runtime_repo.task("task1")["status"] == "completed"
        assert output.stat().st_mtime_ns == written_at
        await run_turn(runtime, task_id="task2")
        assert runtime.tasks["thread1"]["status"] == "completed"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_pending_sync_retries_local_receipt_write_errors(tmp_path, monkeypatch):
    runtime = runtime_module.EnterpriseRuntime(lambda event: None)
    task = {"task_id": "task1", "sync_state": "pending"}
    attempts = 0

    async def recover(saved, directory):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary local receipt write failure")
        saved["sync_state"] = "synced"

    monkeypatch.setattr(runtime, "recover", recover)
    await asyncio.wait_for(runtime.retry_sync(task, tmp_path), 10)
    assert attempts == 2
    assert task["sync_state"] == "synced"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["cancel_requested", 401, 403, 404])
async def test_status_poll_still_stops_execution_when_ownership_is_lost(
    company, tmp_path, monkeypatch, resource_closers, response
):
    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    model_started = asyncio.Event()
    send = client.http.send

    class WaitingModel(ToolModel):
        async def astream(self, messages):
            model_started.set()
            await asyncio.Event().wait()
            async for chunk in super().astream(messages):
                yield chunk

    async def revoke_during_poll(request, *args, **kwargs):
        if request.method == "GET" and request.url.path == "/api/company-runtime/tasks/task1":
            await asyncio.wait_for(model_started.wait(), 10)
            if response == "cancel_requested":
                server.services.company_runtime_repo.transition("task1", response)
            else:
                return httpx.Response(response, json={}, request=request)
        return await send(request, *args, **kwargs)

    monkeypatch.setattr(client.http, "send", revoke_during_poll)
    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: WaitingModel()
    )
    try:
        await runtime.start(
            {
                "task_id": "task1",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": "Create report",
            }
        )
        await asyncio.wait_for(runtime.work["thread1"], 15)
        assert "thread1" not in runtime.work
        assert not (runtime.workspace / "thread1/report.txt").exists()
        done = [event for event in events if event.get("event") == "done"]
        assert len(done) == 1
        assert done[0]["status"] == "cancelled"
        assert done[0]["sync_state"] == "synced"
        assert server.services.company_runtime_repo.task("task1")["status"] == "cancelled"
        for close in resource_closers:
            close.assert_awaited_once()
    finally:
        await runtime.close()
