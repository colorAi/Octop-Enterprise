import json
import os
from unittest.mock import AsyncMock

import pytest

from octop.infra.company_runtime.client import CompanyServiceError
from octop.infra.company_runtime.local import atomic_json
from octop.infra.company_runtime.runtime import EnterpriseRuntime

from .test_runtime import acquire, install_agent


def saved_runtime(client, tmp_path, *, task_id="task1", status="unknown"):
    runtime = EnterpriseRuntime(lambda event: None)
    runtime.client = client
    runtime.installation_token = "i" * 32
    runtime.state = tmp_path / "state"
    runtime.workspace = tmp_path / "projects"
    runtime.workspace.mkdir(exist_ok=True)
    task = {
        "task_id": task_id,
        "thread_id": "thread1",
        "status": status,
        "reported_status": status,
        "recovery_token": "x" * 32,
    }
    runtime.tasks["thread1"] = task
    directory = runtime.state / "tasks" / task_id
    atomic_json(directory / "task.json", task)
    return runtime, task, directory


@pytest.mark.asyncio
async def test_missing_task_can_be_acknowledged_without_deleting_local_output(company, tmp_path):
    _, client, _, _ = company
    runtime, task, directory = saved_runtime(client, tmp_path)
    result = runtime.workspace / "report.txt"
    result.write_text("existing output")
    pending = directory / "pending" / "00000001.json"
    atomic_json(pending, {"event_id": "task1_1", "seq": 1, "payload": {"kind": "note"}})
    await runtime.restore_tasks()
    assert runtime.status()["active_tasks"][0]["recovery_state"] == "missing"
    assert runtime.status()["active_tasks"][0]["status"] == "unknown"
    assert not runtime.work
    assert await runtime.reconcile(
        {"thread_id": "thread1", "task_id": "task1", "resolution": "failed"}
    ) == {"status": "failed"}
    assert result.read_text() == "existing output"
    assert pending.exists()
    saved = json.loads((directory / "task.json").read_text())
    assert saved["acknowledged_at"] and saved["recovery_state"] == "missing"
    client.request = AsyncMock(side_effect=AssertionError("must not recreate missing task"))
    await runtime.restore_tasks()
    assert runtime.tasks["thread1"]["status"] == "failed"
    assert not client.request.called
    await runtime.close()


@pytest.mark.asyncio
async def test_server_completed_receipt_wins_over_local_unknown(company, tmp_path):
    server, client, _, _ = company
    install_agent(server)
    await acquire(client)
    await client.request("tasks/task1/status", {"status": "completed"})
    runtime, task, directory = saved_runtime(client, tmp_path)
    assert await runtime.reconcile(
        {"thread_id": "thread1", "task_id": "task1", "resolution": "failed"}
    ) == {"status": "completed"}
    assert (await client.request("tasks/task1"))["status"] == "completed"
    assert json.loads((directory / "task.json").read_text())["status"] == "completed"
    await runtime.close()


@pytest.mark.asyncio
async def test_ownership_and_network_errors_do_not_acknowledge_task(company, tmp_path):
    server, client, _, _ = company
    install_agent(server)
    await acquire(client)
    runtime, task, directory = saved_runtime(client, tmp_path)
    task["recovery_token"] = "wrong" * 8
    with pytest.raises(CompanyServiceError, match="recovery_not_owned"):
        await runtime.reconcile({"thread_id": "thread1", "resolution": "failed"})
    assert "recovery_state" not in task and task["status"] == "unknown"
    client.request = AsyncMock(side_effect=CompanyServiceError("company_service_503", 503))
    with pytest.raises(CompanyServiceError):
        await runtime.reconcile({"thread_id": "thread1", "resolution": "failed"})
    assert "recovery_state" not in task and task["status"] == "unknown"
    assert "acknowledged_at" not in json.loads((directory / "task.json").read_text())
    await runtime.close()


@pytest.mark.asyncio
async def test_restore_uses_stable_task_order_and_rejects_stale_selection(company, tmp_path):
    _, client, _, _ = company
    runtime, latest, latest_dir = saved_runtime(client, tmp_path, task_id="a-latest")
    latest["started_at_ns"] = 200
    atomic_json(latest_dir / "task.json", latest)
    old_dir = runtime.state / "tasks" / "z-old"
    atomic_json(
        old_dir / "task.json",
        {**latest, "task_id": "z-old", "started_at_ns": 100, "status": "completed"},
    )
    await runtime.restore_tasks()
    assert runtime.tasks["thread1"]["task_id"] == "a-latest"
    os.utime(old_dir / "task.json", None)
    await runtime.restore_tasks()
    assert runtime.tasks["thread1"]["task_id"] == "a-latest"
    with pytest.raises(ValueError, match="task_selection_changed"):
        await runtime.reconcile(
            {"thread_id": "thread1", "task_id": "z-old", "resolution": "failed"}
        )
    assert runtime.tasks["thread1"]["status"] == "unknown"
    await runtime.close()
