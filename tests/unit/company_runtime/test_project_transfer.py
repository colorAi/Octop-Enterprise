import asyncio
import json
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.checkpoint.base import id as checkpoint_ids

from octop.infra.company_runtime.client import CompanyClient, CompanyServiceError
from octop.infra.company_runtime.runtime import EnterpriseRuntime

from .test_projects import IDENTITY, register
from .test_runtime import acquire, install_agent

DESTINATION = {"installation_token": "destination-installation-token-00001"}
pytestmark = pytest.mark.parametrize("company", ["sqlite", "postgresql"], indirect=True)


def new_device(client, credential):
    return CompanyClient("http://company.test", credential, transport=client.http._transport)


async def create_thread(client, thread, project="project-a", identity=IDENTITY):
    await client.request(
        f"projects/{project}/threads", {**identity, "thread_id": thread, "agent_id": "expert"}
    )


async def test_same_account_syncs_current_threads_without_reassigning_either_device(company):
    server, source, credentials, owner = company
    target = new_device(source, credentials[1])
    install_agent(server)
    try:
        await register(source)
        await create_thread(source, "a-first")
        await create_thread(source, "a-late")
        # The destination can start a new project before attaching any old one.
        await target.request(
            "projects", {**DESTINATION, "project_id": "new-project", "name": "New"}
        )
        catalog = await target.request("projects/query", DESTINATION)
        assert {p["project_id"] for p in catalog["items"]} == {"project-a", "new-project"}
        assert any(
            p["project_id"] == "new-project"
            for p in (await source.request("projects/query", IDENTITY))["items"]
        )
        await create_thread(target, "new-chat", "new-project", DESTINATION)

        request = {
            "task_id": "source-task",
            "thread_id": "a-first",
            "agent_id": "expert",
            "project_id": "project-a",
            "text": "work",
            "recovery_token": "r" * 32,
        }
        assert (await source.request("tasks", {**request, **IDENTITY}))["status"] == "running"
        query = "projects/project-a/threads/query"
        first = await target.request(query, {**DESTINATION, "after_thread_id": "", "limit": 1})
        assert first["has_more"] and first["items"][0]["thread_id"] == "a-first"
        last = await target.request(
            query, {**DESTINATION, "after_thread_id": "a-first", "limit": 1}
        )
        assert not last["has_more"] and last["items"][0]["thread_id"] == "a-late"
        repo = server.services.company_runtime_repo
        assert repo.binding("a-first")["device_id"] == "device0"

        target_task = {**request, **DESTINATION, "task_id": "target-task"}
        with pytest.raises(CompanyServiceError, match="thread_already_running"):
            await target.request("tasks", target_task)
        # Different conversations use the corresponding machine's authorized copy.
        await target.request(
            "tasks", {**target_task, "task_id": "other-task", "thread_id": "a-late"}
        )
        await source.request("tasks/source-task/status", {"status": "completed"})
        await target.request("tasks/other-task/status", {"status": "completed"})
        assert (await target.request("tasks", target_task))["status"] == "running"
        assert repo.binding("a-first")["device_id"] == "device1"

        with pytest.raises(CompanyServiceError, match="recovery_not_owned"):
            await target.request(
                "tasks/source-task/recover",
                {**DESTINATION, "recovery_token": request["recovery_token"]},
            )
        await source.request(
            "tasks/source-task/recover", {**IDENTITY, "recovery_token": request["recovery_token"]}
        )
        assert repo.binding("a-first")["device_id"] == "device1"

        await target.request("tasks/target-task/status", {"status": "completed"})
        assert (await source.request("tasks", {**request, **IDENTITY, "task_id": "source-again"}))[
            "status"
        ] == "running"
        assert (await source.request(query, IDENTITY))["items"]
        await create_thread(target, "a-created-on-target", identity=DESTINATION)
        assert await source.request(
            "projects/project-a/adopt", {**IDENTITY, "thread_id": "a-created-on-target"}
        ) == {"project_id": "project-a", "thread_id": "a-created-on-target"}

        other = server.services.user_repo.create(username="other", role="user")
        assert other != owner
        with server.services.db.transaction() as conn:
            conn.execute(
                "UPDATE desktop_devices SET owner_user_id=? WHERE device_id='device1'", (other,)
            )
        with pytest.raises(CompanyServiceError, match="project_not_owned"):
            await target.request(query, DESTINATION)
    finally:
        await target.close()
        await source.close()


async def test_cross_device_turns_keep_replies_with_skewed_clocks(company, tmp_path, monkeypatch):
    server, client, credentials, _ = company
    target_client = new_device(client, credentials[1])
    install_agent(server)
    thread = "project-thread"
    source_root = tmp_path / "old" / "project"
    target_root = tmp_path / "new" / "project"
    source_root.mkdir(parents=True)
    target_root.mkdir(parents=True)
    (source_root / "report.txt").write_text("source copy")
    (target_root / "report.txt").write_text("destination copy")
    seen = {}

    class ReplyModel:
        def bind_tools(self, tools):
            return self

        async def astream(self, messages):
            question = next(m for m in reversed(messages) if m.type == "human")
            key = question.id.removesuffix("_user")
            seen[key] = [m.id for m in messages]
            if key == "target-first":
                tools = [m for m in messages if isinstance(m, ToolMessage)]
                if not tools:
                    yield AIMessageChunk(
                        content="",
                        id="read-command",
                        tool_call_chunks=[
                            {
                                "name": "read_file",
                                "args": json.dumps({"file_path": str(source_root / "report.txt")}),
                                "id": "read-old-path",
                                "index": 0,
                            }
                        ],
                    )
                    return
                assert "destination copy" in str(tools[-1].content)
            yield AIMessageChunk(content=f"reply to {key}", id=f"reply-{key}")

    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: ReplyModel()
    )

    def runtime(runtime_client, identity, device):
        worker = EnterpriseRuntime(lambda event: None)
        worker.client = runtime_client
        worker.project_capable = True
        worker.installation_token = identity["installation_token"]
        worker.state = tmp_path / device / "runtime"
        worker.workspace = tmp_path / device / "unused-default"
        worker.tools = {
            "ffmpeg": "test",
            "openpyxl": "test",
            "python-docx": "test",
            "Pillow": "test",
        }
        worker.tool_path = str(Path(sys.executable).parent)
        return worker

    source = runtime(client, IDENTITY, "old")
    target = runtime(target_client, DESTINATION, "new")

    async def turn(worker, key, root, previous=()):
        await worker.start(
            {
                "task_id": key,
                "thread_id": thread,
                "agent_id": "expert",
                "project_id": "project-a",
                "project_root": str(root),
                "project_previous_roots": list(previous),
                "text": key,
            }
        )
        await asyncio.wait_for(worker.work[thread], 30)
        assert worker.tasks[thread]["status"] == "completed", worker.tasks[thread]
        assert worker.tasks[thread]["sync_state"] == "synced"

    def head():
        row = server.services.company_runtime_repo.checkpoint(thread, "", None)
        return row["config"]["configurable"]["checkpoint_id"]

    try:
        await register(client)
        await create_thread(client, thread)
        with monkeypatch.context() as source_clock:
            ahead = checkpoint_ids.uuid6().time + 5 * 60 * 10_000_000
            source_clock.setattr(checkpoint_ids, "_last_v6_timestamp", ahead)
            await turn(source, "source-first", source_root)
        old_head = head()
        # Existing databases initially resolve a legacy checkpoint without a head row.
        with server.services.db.transaction() as conn:
            conn.execute(
                "DELETE FROM company_runtime_checkpoint_heads WHERE thread_id=?", (thread,)
            )
        assert head() == old_head
        assert (await target_client.request("projects/project-a/threads/query", DESTINATION))[
            "items"
        ][0]["thread_id"] == thread
        await turn(target, "target-first", target_root, [str(source_root)])
        assert head() < old_head
        await turn(target, "target-second", target_root, [str(source_root)])
        assert "reply-source-first" in seen["target-first"]
        assert "reply-target-first" in seen["target-second"]
        await turn(source, "source-again", source_root)
        assert "reply-target-second" in seen["source-again"]
        with server.services.db.connect() as conn:
            ids = [
                row[0]
                for row in conn.execute(
                    "SELECT message_id FROM thread_messages WHERE thread_id=? ORDER BY seq",
                    (thread,),
                ).fetchall()
            ]
        assert [value for value in ids if value.startswith("reply-")] == [
            "reply-source-first",
            "reply-target-first",
            "reply-target-second",
            "reply-source-again",
        ]
    finally:
        await target.close()
        await source.close()


async def test_replayed_checkpoint_does_not_rewind_commit_order(company):
    server, client, _, _ = company
    install_agent(server)
    try:
        await acquire(client)
        path = "tasks/task1/checkpoint-batch"
        old = {"action": "put", "checkpoint_id": "z-old", "payload": {"value": "older"}}
        new = {"action": "put", "checkpoint_id": "a-new", "payload": {"value": "newer"}}
        await client.request(path, {"operations": [old, new]})
        await client.request(path, {"operations": [old]})
        assert (await client.request("tasks/task1/checkpoint/get", {}))["value"] == "newer"
        assert (await client.request("tasks/task1/checkpoint/get", {"checkpoint_id": "z-old"}))[
            "value"
        ] == "older"
    finally:
        await client.close()
