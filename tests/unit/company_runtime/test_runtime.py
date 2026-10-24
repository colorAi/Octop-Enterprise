import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage

from octop.infra.company_runtime.client import RemoteCheckpointSaver
from octop.infra.company_runtime.local import ProjectBackend
from octop.infra.company_runtime.runtime import EnterpriseRuntime


class ToolModel:
    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            yield AIMessageChunk(
                content="Saved report locally.",
                id="model2",
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            )
        else:
            yield AIMessageChunk(
                content="",
                id="model1",
                tool_call_chunks=[
                    {
                        "name": "write_file",
                        "args": json.dumps(
                            {"file_path": "/report.txt", "content": "local report 42"}
                        ),
                        "id": "write1",
                        "index": 0,
                    }
                ],
            )


def install_agent(server):
    agent = SimpleNamespace(
        workspace=SimpleNamespace(aread_text=AsyncMock(return_value=None)),
        list_skill_summaries=AsyncMock(return_value=[]),
        model_factory=SimpleNamespace(
            list_refs=lambda: ["test/model"], get_chat_model=lambda ref: ToolModel()
        ),
        config=SimpleNamespace(pick_default_model_ref=lambda: "test/model"),
        memory=None,
    )
    server.app_runtime = SimpleNamespace(
        agent_registry=SimpleNamespace(get_agent=lambda aid: agent),
        gateway=SimpleNamespace(ws_hub=SimpleNamespace(is_turn_active=lambda thread: False)),
    )


async def acquire(client, task="task1", thread="thread1"):
    return await client.request(
        "tasks",
        {
            "task_id": task,
            "thread_id": thread,
            "agent_id": "expert",
            "text": "create report",
            "recovery_token": "x" * 32,
            "installation_token": "i" * 32,
        },
    )


@pytest.mark.asyncio
async def test_company_snapshot_excludes_provider_credentials(company):
    server, client, _, _ = company
    install_agent(server)
    sentinel = "sk-company-snapshot-regression-only"
    server.services.provider_repo.create(name="test", kind="openai", api_key=sentinel)
    agent = server.app_runtime.agent_registry.get_agent("expert")
    agent.config.api_key = sentinel
    agent.config.providers = [{"api_key": sentinel}]
    agent.model_factory.api_key = sentinel
    try:
        task = await acquire(client)
        assert task["expert"]["models"] == ["test/model"]
        assert sentinel not in json.dumps(task)
        assert "api_key" not in json.dumps(task["expert"])
        assert sentinel not in server.services.company_runtime_repo.task("task1")["expert_json"]
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["valid", "missing", "corrupt", "symlink"])
async def test_skill_download_reads_only_intact_task_pinned_objects(company, state):
    from octop.infra.company_runtime.skills import store_asset

    server, client, _, _ = company
    install_agent(server)
    try:
        task = await acquire(client)
        cache = server.services.paths.root / "company-runtime-skills"
        data = b"task-pinned asset"
        digest = store_asset(cache, data)
        task["expert"]["skills"] = [
            {"path": "skills/test/SKILL.md", "sha256": digest, "size": len(data)}
        ]
        with server.services.db.transaction() as conn:
            conn.execute(
                "UPDATE company_runtime_tasks SET expert_json=? WHERE task_id='task1'",
                (json.dumps(task["expert"]),),
            )
        target = cache / digest
        if state in {"missing", "symlink"}:
            target.chmod(0o600)
            target.unlink()
        if state == "corrupt":
            target.chmod(0o600)
            target.write_bytes(b"partial bytes")
        if state == "symlink":
            draft = cache / "writable-draft"
            draft.write_bytes(data)
            try:
                target.symlink_to(draft)
            except OSError:
                pytest.skip("symlinks unavailable")
        denied = await client.http.get("tasks/task1/skills/" + "0" * 64)
        assert denied.status_code == 403
        response = await client.http.get("tasks/task1/skills/" + digest)
        if state == "valid":
            assert response.status_code == 200 and response.content == data
        else:
            assert response.status_code == 409
            assert response.json()["error"]["details"]["reason"] == "skill_checksum_mismatch"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_identity_event_and_device_isolation(company):
    server, client, credentials, owner = company
    install_agent(server)
    first = await acquire(client)
    assert first["status"] == "running"
    assert (await acquire(client))["expert"] == first["expert"]
    with pytest.raises(RuntimeError, match="thread_already_running|409"):
        await acquire(client, "task2")
    event = {"event_id": "event1", "seq": 1, "payload": {"kind": "note", "text": "saved"}}
    assert (await client.request("tasks/task1/events", event))["accepted"]
    assert not (await client.request("tasks/task1/events", event))["accepted"]
    with pytest.raises(RuntimeError):
        await client.request("tasks/task1/events", {**event, "payload": {"other": True}})
    response = await client.http.get(
        "tasks/task1", headers={"X-Octop-Device-Credential": credentials[1]}
    )
    assert response.status_code == 403
    response = await client.http.get("capabilities", headers={"Origin": "http://company.test"})
    assert response.status_code == 403
    server.services.device_repo.revoke("device0", owner, 1)
    assert (await client.http.get("capabilities")).status_code == 401
    await client.close()


@pytest.mark.asyncio
async def test_real_harness_remote_model_tool_checkpoint_history(company, tmp_path):
    server, client, _, _ = company
    install_agent(server)
    workspace = tmp_path / "中文 work"
    workspace.mkdir()
    events = []
    runtime = EnterpriseRuntime(events.append)
    runtime.client = client
    runtime.workspace = workspace
    runtime.state = tmp_path / "state"
    runtime.authorized = True
    runtime.installation_token = "i" * 32
    runtime.tools = {"ffmpeg": "test", "openpyxl": "test", "python-docx": "test", "Pillow": "test"}
    runtime.tool_path = str(Path(sys.executable).parent)
    await runtime.start(
        {
            "task_id": "task1",
            "thread_id": "thread1",
            "agent_id": "expert",
            "text": "Write report.txt with the number 42",
        }
    )
    await asyncio.wait_for(runtime.work["thread1"], 30)
    assert runtime.tasks["thread1"]["status"] == "completed", events[-1]
    assert (workspace / "thread1" / "report.txt").read_text() == "local report 42"
    first_artifacts = events[-1]["artifacts"]
    assert len(first_artifacts) == 1
    assert not list(workspace.rglob("*.sqlite"))
    assert not list(workspace.rglob("MEMORY.md"))
    saver = RemoteCheckpointSaver(client, "task1", "thread1")
    cp = await saver.aget_tuple({"configurable": {"thread_id": "thread1"}})
    assert cp is not None
    assert server.services.thread_message_repo.head("thread1") >= 4
    # The employee keeps the same runtime while the company changes model ids.
    # A shared catalog can update before the cached expert config is rebuilt.
    registry = server.app_runtime.agent_registry
    registry.get_agent("expert").model_factory.list_refs = lambda: ["test/renamed-model"]
    registry.resolve_fallback_model_ref = lambda: "test/renamed-model"
    server.services.user_policy_repo.set(
        server.services.agent_repo.get("expert").user_id,
        "model_access",
        json.dumps({"model_refs": ["test/renamed-model"], "revision": "renamed"}),
    )
    await runtime.start(
        {
            "task_id": "task2",
            "thread_id": "thread1",
            "agent_id": "expert",
            "text": "What did you create?",
        }
    )
    await asyncio.wait_for(runtime.work["thread1"], 30)
    assert runtime.tasks["thread1"]["status"] == "completed", events[-1]
    task = server.services.company_runtime_repo.task("task2")
    assert task is not None
    snapshot = json.loads(task["expert_json"])
    assert snapshot["default_model"] == "test/renamed-model"
    assert snapshot["models"] == ["test/renamed-model"]
    assert (workspace / "thread1" / "report.txt").read_text() == "local report 42"
    assert events[-1]["artifacts"] == []
    turns = server.services.company_runtime_repo.artifact_turns("thread1")
    assert len(turns) == 1 and turns[0]["task_id"] == "task1"
    assert turns[0]["files"] == first_artifacts
    await runtime.close()


@pytest.mark.asyncio
async def test_model_failure_persists_partial_turn_before_next_user(company, tmp_path):
    server, client, _, _ = company
    install_agent(server)

    class FailingModel(ToolModel):
        async def astream(self, messages):
            if any(isinstance(m, ToolMessage) for m in messages):
                raise RuntimeError("model connection lost after tool")
            async for chunk in super().astream(messages):
                yield chunk

    agent = server.app_runtime.agent_registry.get_agent("expert")
    agent.model_factory.get_chat_model = lambda ref: FailingModel()
    events = []
    runtime = EnterpriseRuntime(events.append)
    runtime.client = client
    runtime.workspace = tmp_path / "local"
    runtime.workspace.mkdir()
    runtime.state = tmp_path / "state"
    runtime.authorized = True
    runtime.installation_token = "i" * 32
    runtime.tools = {"ffmpeg": "test"}
    await runtime.start(
        {"task_id": "task1", "thread_id": "thread1", "agent_id": "expert", "text": "create report"}
    )
    await asyncio.wait_for(runtime.work["thread1"], 30)
    assert events[-1]["status"] == "failed", events[-1]
    with server.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT role FROM thread_messages WHERE thread_id=? ORDER BY seq", ("thread1",)
        ).fetchall()
    assert [row["role"] for row in rows] == ["human", "ai", "tool"]
    assert (runtime.workspace / "thread1" / "report.txt").exists()
    agent.model_factory.get_chat_model = lambda ref: ToolModel()
    await runtime.start(
        {"task_id": "task2", "thread_id": "thread1", "agent_id": "expert", "text": "try again"}
    )
    await asyncio.wait_for(runtime.work["thread1"], 30)
    assert events[-1]["status"] == "completed", events[-1]
    with server.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT role FROM thread_messages WHERE thread_id=? ORDER BY seq", ("thread1",)
        ).fetchall()
    assert [row["role"] for row in rows] == ["human", "ai", "tool", "human", "ai"]
    await runtime.close()


@pytest.mark.asyncio
async def test_image_execute_returns_to_model_and_completes_task(company, tmp_path):
    from PIL import Image

    server, client, _, _ = company
    install_agent(server)
    workspace = tmp_path / "图片 work"
    project = workspace / "thread1"
    project.mkdir(parents=True)
    image = project / "cat_test.png"
    Image.new("RGB", (37, 19), "blue").save(image)
    command = (
        f'"{sys.executable}" -c "from PIL import Image; '
        f"im=Image.open(r'{image}'); print(im.size, im.format)\""
    )
    tool_results = []

    class ImageModel(ToolModel):
        async def astream(self, messages):
            result = next((m for m in messages if isinstance(m, ToolMessage)), None)
            if result is not None:
                tool_results.append(result.content)
                assert "(37, 19) PNG" in str(result.content)
                yield AIMessageChunk(content="Image inspected successfully.", id="image-result")
            else:
                yield AIMessageChunk(
                    content="",
                    id="inspect-image",
                    tool_call_chunks=[
                        {
                            "name": "execute",
                            "args": json.dumps({"command": command}),
                            "id": "inspect1",
                            "index": 0,
                        }
                    ],
                )

    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: ImageModel()
    )
    events = []
    runtime = EnterpriseRuntime(events.append)
    runtime.client = client
    runtime.workspace = workspace
    runtime.state = tmp_path / "state"
    runtime.authorized = True
    runtime.installation_token = "i" * 32
    runtime.tools = {"ffmpeg": "test", "openpyxl": "test", "python-docx": "test", "Pillow": "test"}
    runtime.tool_path = str(Path(sys.executable).parent)
    try:
        await runtime.start(
            {
                "task_id": "image_task",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": "Check cat_test.png size and format",
            }
        )
        await asyncio.wait_for(runtime.work["thread1"], 30)
        assert len(tool_results) == 1
        assert runtime.tasks["thread1"]["status"] == "completed", events[-1]
        assert events[-1]["event"] == "done"
        assert any("Image inspected successfully." in str(event) for event in events)
        task = server.services.company_runtime_repo.task("image_task")
        assert task["status"] == "completed"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_edit_replaces_checkpoint_and_history_without_replaying_old_receipts(
    company, tmp_path
):
    server, client, _, _ = company
    install_agent(server)
    seen = []

    class ContextModel(ToolModel):
        async def astream(self, messages):
            seen.append([m.content for m in messages if isinstance(m, HumanMessage)])
            yield AIMessageChunk(content="Reply to " + seen[-1][-1], id=f"reply-{len(seen)}")

    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: ContextModel()
    )
    runtime = EnterpriseRuntime(lambda event: None)
    runtime.client = client
    runtime.workspace = tmp_path / "work"
    runtime.workspace.mkdir()
    runtime.state = tmp_path / "state"
    runtime.authorized = True
    runtime.installation_token = "i" * 32
    runtime.tools = {"ffmpeg": "test", "openpyxl": "test", "python-docx": "test", "Pillow": "test"}
    runtime.tool_path = str(Path(sys.executable).parent)

    async def turn(task, text, edit=None):
        await runtime.start(
            {
                "task_id": task,
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": text,
                **({"edit_message_id": edit} if edit else {}),
            }
        )
        await asyncio.wait_for(runtime.work["thread1"], 10)
        assert runtime.tasks["thread1"]["status"] == "completed"

    def history():
        repo = server.services.thread_message_repo
        return [
            json.loads(r.message_json)["data"]["content"]
            for r in repo.range_rows("thread1", 0, repo.head("thread1"))
        ]

    try:
        await turn("keep", "Keep earlier context")
        await turn("old", "Old question")
        original = server.services.company_runtime_repo.events("old")
        artifact = runtime.workspace / "thread1" / "keep.txt"
        artifact.write_text("Do not delete existing files")
        await turn("edit", "Revised question", "old_user")
        assert seen[-1] == ["Keep earlier context", "Revised question"]
        assert history() == [
            "Keep earlier context",
            "Reply to Keep earlier context",
            "Revised question",
            "Reply to Revised question",
        ]
        assert artifact.read_text() == "Do not delete existing files"
        for event in original:
            assert not (await client.request("tasks/old/events", event))["accepted"]
        late = {**original[-2], "event_id": "late-old-receipt", "seq": original[-1]["seq"] + 1}
        assert (await client.request("tasks/old/events", late))["accepted"]
        assert "Old question" not in history()
        await turn("again", "Only this question", "keep_user")
        assert seen[-1] == ["Only this question"]
        assert history() == ["Only this question", "Reply to Only this question"]
        # Ordinary continuation must use the edited branch too.
        await turn("next", "Continue")
        assert seen[-1] == ["Only this question", "Continue"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_edit_rejects_active_tasks_and_foreign_message_ids_without_changing_history(company):
    server, client, _, _ = company
    install_agent(server)
    await acquire(client)
    event = {
        "event_id": "user1",
        "seq": 1,
        "payload": {
            "kind": "messages",
            "messages": [{"type": "human", "data": {"id": "question", "content": "original"}}],
        },
    }
    await client.request("tasks/task1/events", event)
    request = {
        "task_id": "edit1",
        "thread_id": "thread1",
        "agent_id": "expert",
        "text": "replacement",
        "edit_message_id": "question",
        "recovery_token": "r" * 32,
        "installation_token": "i" * 32,
    }
    with pytest.raises(RuntimeError, match="thread_already_running"):
        await client.request("tasks", request)
    assert server.services.thread_message_repo.head("thread1") == 1
    assert server.services.company_runtime_repo.checkpoint("thread1", "", None) is None
    await client.request("tasks/task1/status", {"status": "cancelled"})
    with pytest.raises(RuntimeError, match="edit_message_not_found"):
        await client.request("tasks", {**request, "thread_id": "thread2"})
    assert server.services.thread_message_repo.head("thread1") == 1
    await client.close()


@pytest.mark.asyncio
async def test_real_harness_steer_keeps_tools_and_company_history(company, tmp_path):
    server, client, _, _ = company
    install_agent(server)

    class SteeredModel(ToolModel):
        async def astream(self, messages):
            latest = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
            if latest.content == "Also explain the saved number":
                yield AIMessageChunk(content="The saved number is 42.", id="corrected")
            else:
                async for chunk in super().astream(messages):
                    yield chunk

    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: SteeredModel()
    )
    workspace = tmp_path / "steer work"
    workspace.mkdir()
    events = []
    runtime = EnterpriseRuntime(events.append)
    runtime.client = client
    runtime.workspace = workspace
    runtime.state = tmp_path / "state"
    runtime.authorized = True
    runtime.installation_token = "i" * 32
    runtime.tools = {"ffmpeg": "test", "openpyxl": "test", "python-docx": "test", "Pillow": "test"}
    runtime.tool_path = str(Path(sys.executable).parent)
    try:
        await runtime.start(
            {
                "task_id": "steer_task",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": "Write report.txt with the number 42",
            }
        )
        turn = runtime.steering.coordinator.get_state("thread1").turn_id
        reply = runtime.steering.send(
            {
                "thread_id": "thread1",
                "expected_turn_id": turn,
                "client_pending_id": "correction",
                "text": "Also explain the saved number",
            }
        )
        assert reply["type"] == "steer.queued"
        await asyncio.wait_for(runtime.work["thread1"], 30)
        assert runtime.tasks["thread1"]["status"] == "completed", events[-1]
        assert (workspace / "thread1" / "report.txt").read_text() == "local report 42"
        frames = [e["frame"] for e in events if e["event"] == "steer"]
        inserted = next(f for f in frames if f["type"] == "user")
        assert inserted["content"] == "Also explain the saved number"
        assert [f["type"] for f in frames].count("steer.drained") == 1
        assert any("The saved number is 42." in str(e) for e in events)
        saver = RemoteCheckpointSaver(client, "steer_task", "thread1")
        checkpoint = await saver.aget_tuple({"configurable": {"thread_id": "thread1"}})
        assert checkpoint is not None
        history = server.services.thread_message_repo
        rows = history.range_rows("thread1", 0, history.head("thread1"))
        assert sum(row.message_id == inserted["message_id"] for row in rows) == 1
        assert any("The saved number is 42." in row.message_json for row in rows)
        receipts = [
            json.loads(p.read_text())
            for p in (runtime.state / "threads/thread1/operations").glob("*.json")
        ]
        writes = [item for item in receipts if item["tool"] == "write_file"]
        assert len(writes) == 1 and writes[0]["status"] == "completed"
        assert not list(workspace.rglob("*.sqlite"))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_subprocess_failure_and_cancel_are_real(tmp_path):
    project = tmp_path / "项目 space"
    project.mkdir()
    backend = ProjectBackend(
        project, tmp_path / "state", lambda event: None, tool_path=str(Path(sys.executable).parent)
    )
    result = await backend.aexecute(f'"{sys.executable}" -c "import sys; print(123); sys.exit(7)"')
    assert result.exit_code == 7 and "123" in result.output
    task = asyncio.create_task(
        backend.aexecute(f'"{sys.executable}" -c "import time; time.sleep(60)"')
    )
    for _ in range(100):
        if backend.processes:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not backend.processes
    statuses = [json.loads(p.read_text()) for p in (tmp_path / "state").rglob("status.json")]
    assert any(p["status"] == "cancelled" for p in statuses)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "messages",
    [
        [{"type": "system", "data": {"content": "bad"}}],
        [
            {"type": "human", "data": {"content": "valid first"}},
            {"type": "system", "data": {"content": "bad"}},
        ],
        [{"type": "unknown", "data": {"content": "bad"}}],
        "not a list",
        [{}],
    ],
)
async def test_invalid_history_does_not_reserve_event(company, messages):
    server, client, _, _ = company
    install_agent(server)
    await acquire(client)
    event = {
        "event_id": "retry-event",
        "seq": 1,
        "payload": {"kind": "messages", "messages": messages},
    }
    response = await client.http.post("tasks/task1/events", json=event)
    assert response.status_code == 422
    with server.services.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM company_runtime_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM thread_messages").fetchone()[0] == 0
    event["payload"]["messages"] = [{"type": "human", "data": {"content": "corrected"}}]
    assert (await client.request("tasks/task1/events", event))["accepted"]
    assert not (await client.request("tasks/task1/events", event))["accepted"]


@pytest.mark.asyncio
async def test_event_and_history_roll_back_together_on_projection_failure(company):
    import sqlite3

    server, client, _, _ = company
    install_agent(server)
    await acquire(client)
    with server.services.db.transaction() as conn:
        conn.execute(
            "CREATE TRIGGER fail_history BEFORE INSERT ON thread_messages BEGIN SELECT RAISE(ABORT, 'temporary projection failure'); END"
        )
    event = {
        "event_id": "projection",
        "seq": 1,
        "payload": {
            "kind": "messages",
            "messages": [{"type": "human", "data": {"content": "hello"}}],
        },
    }
    with pytest.raises((sqlite3.DatabaseError, RuntimeError)):
        await client.request("tasks/task1/events", event)
    with server.services.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM company_runtime_events").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_history")
    assert (await client.request("tasks/task1/events", event))["accepted"]
    assert not (await client.request("tasks/task1/events", event))["accepted"]
    with server.services.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM company_runtime_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM thread_messages").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_company_backup_cache_and_preserved_tasks(company, tmp_path, monkeypatch):
    from octop.config import DatabaseConfig
    from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
    from octop.infra.company_runtime.maintenance import storage_summary
    from octop.infra.company_runtime.skills import store_asset

    server, client, _, _ = company
    install_agent(server)
    await acquire(client)
    paths = server.services.paths
    cache = paths.root / "company-runtime-skills"
    from octop.infra.db.repos.company_runtime import CompanyRuntimeRepo

    repo = CompanyRuntimeRepo(server.services.db)
    with server.services.db.connect() as conn:
        conn.execute("INSERT INTO desktop_file_threads VALUES (?)", ("thread2",))
    repo.append_event("task1", "backup-event", 1, {"kind": "note"})
    repo.put_checkpoint("thread1", "", "checkpoint1", {"checkpoint": "saved"})
    repo.put_writes("thread1", "", "checkpoint1", "write1", [{"idx": 0, "value": {"saved": True}}])
    repo.begin_operation("task1", "operation1", "hash")
    digest = store_asset(cache, b"pinned skill")
    (cache / ".pending-incomplete").write_bytes(b"incomplete")
    archive = tmp_path / "company.tar.gz"
    monkeypatch.setenv("OCTOP_AGENT_ISOLATION", "docker")
    monkeypatch.setenv("OCTOP_IM_URL", "http://communication:8090")
    create_system_backup(
        paths=paths,
        pool=server.services.db,
        db_config=DatabaseConfig(),
        agent_rows=[],
        dest=archive,
        include_chats=False,
    )
    (cache / digest).chmod(0o600)
    (cache / digest).unlink()
    result = restore_system_backup(
        archive,
        paths=paths,
        pool=server.services.db,
        db_config=DatabaseConfig(),
        restore_config=False,
    )
    assert result["company_skill_files"] == 1
    assert "sandbox_volumes" in result["external_components"]
    assert "communication_database" in result["external_components"]
    assert (cache / digest).read_bytes() == b"pinned skill"
    with server.services.db.connect() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM company_runtime_tasks").fetchone()[0] == 1
    summary = storage_summary(server.services.db, paths.root)
    assert summary["tasks"]["rows"] == 1
    for key in ("events", "checkpoints", "writes", "operations"):
        assert summary[key]["rows"] == 1
    assert summary["task_statuses"] == {"running": 1}
    assert summary["skill_cache"] == {"files": 1, "bytes": len(b"pinned skill")}
    assert summary["temporary_cache"] == {"files": 1, "bytes": len(b"incomplete")}
    assert not summary["automatic_deletion_enabled"]
    assert repo.checkpoint("thread1", "", None)["pending_writes"] == [{"saved": True}]
