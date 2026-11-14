import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from octop.infra.db import migrate
from octop.infra.db.pool import PostgresPool, SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.company_projects import CompanyProjectRepo
from octop.infra.db.repos.company_runtime import CompanyRuntimeRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from tests.unit.company_runtime.test_runtime import acquire, install_agent

IDENTITY = {"installation_token": "i" * 32}


async def register(client, project="project-a", name="项目 A"):
    return await client.request("projects", {**IDENTITY, "project_id": project, "name": name})


@pytest.mark.asyncio
async def test_project_catalog_is_account_scoped_and_does_not_disclose_installation(company):
    server, client, credentials, owner = company
    try:
        first = await register(client)
        assert first["name"] == "项目 A"
        assert set(first) == {"project_id", "name", "created_at", "updated_at"}
        response = await client.http.patch(
            "projects", json={**IDENTITY, "project_id": "project-a", "name": "重命名"}
        )
        assert response.status_code == 200
        assert (await register(client))["name"] == "重命名"
        for p in ("project-b", "project-c"):
            await register(client, p)
        page = await client.request("projects/query", {**IDENTITY, "limit": 2})
        rest = await client.request("projects/query", {**IDENTITY, "limit": 2, "offset": 2})
        assert page["has_more"] and not rest["has_more"]
        assert len({row["project_id"] for row in page["items"] + rest["items"]}) == 3
        other_installation = {"installation_token": "other" * 8}
        response = await client.http.post(
            "projects/query",
            json=other_installation,
            headers={"X-Octop-Device-Credential": credentials[1]},
        )
        assert len(response.json()["items"]) == 3
        registered = await client.http.post(
            "projects",
            json={**other_installation, "project_id": "project-a", "name": "local name"},
            headers={"X-Octop-Device-Credential": credentials[1]},
        )
        assert registered.json()["name"] == "重命名"
        other = server.services.user_repo.create(username="other", role="user")
        with server.services.db.transaction() as conn:
            conn.execute(
                "UPDATE desktop_devices SET owner_user_id=? WHERE device_id='device1'", (other,)
            )
        response = await client.http.post(
            "projects/query", json=IDENTITY, headers={"X-Octop-Device-Credential": credentials[1]}
        )
        assert response.status_code == 200 and response.json()["items"] == []
        response = await client.http.post(
            "projects",
            json={**IDENTITY, "project_id": "project-a", "name": "hijack"},
            headers={"X-Octop-Device-Credential": credentials[1]},
        )
        assert response.status_code == 403
        assert "installation" not in str(page)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_project_threads_are_atomic_idempotent_and_cross_expert(company):
    server, client, _, owner = company
    try:
        await register(client)
        await register(client, "project-b")
        server.services.agent_repo.create(agent_id="writer", user_id=owner, name="Writer")
        params = {**IDENTITY, "thread_id": "project-thread", "agent_id": "expert"}
        created = await client.request("projects/project-a/threads", params)
        assert created == await client.request("projects/project-a/threads", params)
        await client.request(
            "projects/project-a/threads",
            {**params, "thread_id": "writer-thread", "agent_id": "writer"},
        )
        page = await client.request("projects/project-a/threads/query", {**IDENTITY, "limit": 1})
        rest = await client.request(
            "projects/project-a/threads/query", {**IDENTITY, "limit": 1, "offset": 1}
        )
        assert page["has_more"] and not rest["has_more"]
        assert {x["agent_id"] for x in page["items"] + rest["items"]} == {"expert", "writer"}
        state = server.services.company_runtime_repo.thread_state("project-thread")
        assert state["project_id"] == "project-a" and state["execution_location"] == "desktop"
        assert state["local_task_id"] is None
        assert server.services.thread_message_repo.projection_status("project-thread") == "ready"
        for path, body in [
            ("projects/project-b/threads", params),
            ("projects/project-a/threads", {**params, "agent_id": "writer"}),
            ("projects/project-a/threads", {**params, "thread_id": "thread1"}),
        ]:
            assert (await client.http.post(path, json=body)).status_code == 409
        bad = await client.http.post(
            "projects/missing/threads", json={**params, "thread_id": "not-created"}
        )
        assert bad.status_code == 403
        assert server.services.thread_repo.get("not-created") is None
        other_installation_page = await client.request(
            "projects/project-a/threads/query", {"installation_token": "other" * 8}
        )
        assert {x["thread_id"] for x in other_installation_page["items"]} == {
            "project-thread",
            "writer-thread",
        }
        # New project conversations cannot be executed by a legacy client.
        install_agent(server)
        denied = await client.http.post(
            "tasks",
            json={
                **params,
                "task_id": "legacy-attempt",
                "text": "write",
                "recovery_token": "r" * 32,
            },
        )
        assert denied.status_code == 409
        assert denied.json()["error"]["details"]["reason"] == "project_runtime_required"
        assert server.services.company_runtime_repo.task("legacy-attempt") is None
        assert (await acquire(client))["status"] == "running"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_project_routes_require_native_identity_and_validate_payloads(company):
    server, client, _, owner = company
    try:
        body = {**IDENTITY, "project_id": "project-a", "name": "A"}
        assert (
            await client.http.post("projects", json=body, headers={"Origin": "http://company.test"})
        ).status_code == 403
        assert (
            await client.http.post("projects", json={**body, "path": "/arbitrary"})
        ).status_code == 422
        assert (await client.http.post("projects", json={**body, "name": "  "})).status_code == 422
        assert (
            await client.http.post("projects/query", json={**IDENTITY, "limit": 101})
        ).status_code == 422
        assert (
            await client.http.post("projects/query", json={**IDENTITY, "offset": -1})
        ).status_code == 422
        await register(client)
        other = server.services.user_repo.create(username="other", role="user")
        server.services.agent_repo.create(agent_id="foreign", user_id=other, name="Other")
        for agent in ("foreign", "missing"):
            response = await client.http.post(
                "projects/project-a/threads",
                json={**IDENTITY, "thread_id": "denied", "agent_id": agent},
            )
            assert response.status_code == 403
        assert server.services.thread_repo.get("denied") is None
        await client.request(
            "projects/project-a/threads", {**IDENTITY, "thread_id": "bound", "agent_id": "expert"}
        )
        server.services.thread_repo.delete("bound")
        assert not (await client.request("projects/project-a/threads/query", IDENTITY))["items"]
    finally:
        await client.close()


def test_project_migration_preserves_legacy_records(tmp_path, monkeypatch):
    db = SqlitePool(tmp_path / "upgrade.db")
    discover = migrate._discover
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                migrate,
                "_discover",
                lambda dialect="sqlite": [(v, p) for v, p in discover(dialect) if v <= 30],
            )
            migrate.run_migrations(db)
        owner = UserRepo(db).create(username="legacy", role="user")
        AgentRepo(db).create(agent_id="legacy", user_id=owner, name="Legacy")
        ThreadRepo(db).insert(
            thread_id="old",
            agent_id="legacy",
            user_id=owner,
            channel_type="dashboard",
            session_key="old",
            last_active=0,
        )
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO company_runtime_threads VALUES(?,?,?,?,?,?,?)",
                ("old", owner, "legacy", "device", "old", hashlib.sha256(b"old").hexdigest(), 123),
            )
        before = CompanyRuntimeRepo(db).binding("old")
        migrate.run_migrations(db)
        migrate.run_migrations(db)
        assert CompanyRuntimeRepo(db).binding("old") == before
        with db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM company_projects").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM company_project_threads").fetchone()[0] == 0
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 32
        assert any(v == 31 for v, _ in discover("postgresql"))
    finally:
        db.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_atomic_project_creation_and_account_cleanup(backend, tmp_path):
    if backend == "postgresql":
        conninfo = os.environ.get("OCTOP_TEST_PROJECTS_DATABASE_URL")
        if not conninfo:
            pytest.skip("set OCTOP_TEST_PROJECTS_DATABASE_URL to a dedicated test database")
        db = PostgresPool(conninfo)
    else:
        db = SqlitePool(tmp_path / "concurrent.db")
    suffix = uuid.uuid4().hex
    try:
        migrate.run_migrations(db)
        migrate.run_migrations(db)
        owner = UserRepo(db).create(username="projects-" + suffix, role="user")
        aid = "expert-" + suffix
        pid = "project-" + suffix
        tid = "thread-" + suffix
        AgentRepo(db).create(agent_id=aid, user_id=owner, name="Expert")
        repo = CompanyProjectRepo(db)

        def create(_):
            repo.register(pid, owner, "installation", "Project")
            return repo.create_thread(
                pid,
                owner,
                "installation",
                thread_id=tid,
                agent_id=aid,
                device_id="session",
                title="New",
            )

        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(create, range(4)))
        assert all(r == results[0] for r in results)
        assert len(repo.threads(pid, owner, 25, 0)) == 1
        UserRepo(db).delete(owner)
        assert repo.list_projects(owner, 25, 0) == []
        assert ThreadRepo(db).get(tid) is None
        assert CompanyRuntimeRepo(db).binding(tid) is None
        with db.connect() as conn:
            assert not conn.execute(
                "SELECT 1 FROM company_project_threads WHERE thread_id=?", (tid,)
            ).fetchone()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_project_leases_serialize_experts_and_adopt_only_original_installation(company):
    server, client, _, owner = company
    install_agent(server)
    server.services.agent_repo.create(agent_id="writer", user_id=owner, name="Writer")
    try:
        for pid, tid, aid in [
            ("project-a", "pa1", "expert"),
            ("project-a", "pa2", "writer"),
            ("project-b", "pb1", "expert"),
        ]:
            await register(client, pid)
            await client.request(
                f"projects/{pid}/threads", {**IDENTITY, "thread_id": tid, "agent_id": aid}
            )
        body = {
            **IDENTITY,
            "task_id": "pa-task",
            "thread_id": "pa1",
            "agent_id": "expert",
            "project_id": "project-a",
            "text": "work",
            "recovery_token": "r" * 32,
        }
        assert (await client.request("tasks", body))["status"] == "running"
        assert (await client.request("tasks", body))["status"] == "running"
        denied = await client.http.post(
            "tasks", json={**body, "task_id": "pa-other", "thread_id": "pa2", "agent_id": "writer"}
        )
        assert denied.status_code == 409
        assert denied.json()["error"]["details"]["reason"] == "project_already_running"
        assert (
            await client.request(
                "tasks",
                {**body, "task_id": "pb-task", "thread_id": "pb1", "project_id": "project-b"},
            )
        )["status"] == "running"
        assert (
            await client.http.post(
                "tasks", json={**body, "task_id": "wrong", "project_id": "project-b"}
            )
        ).status_code == 409
        await acquire(client, "old-task", "thread1")
        await register(client, "legacy-project")
        before = server.services.company_runtime_repo.binding("thread1")
        assert (
            await client.http.post(
                "projects/legacy-project/adopt",
                json={"installation_token": "z" * 32, "thread_id": "thread1"},
            )
        ).status_code == 409
        for _ in range(2):
            await client.request(
                "projects/legacy-project/adopt", {**IDENTITY, "thread_id": "thread1"}
            )
        assert server.services.company_runtime_repo.binding("thread1") == before
        assert (
            await client.http.post(
                "projects/project-b/adopt", json={**IDENTITY, "thread_id": "thread1"}
            )
        ).status_code == 409
        server.services.thread_repo.delete("thread1")
        await client.request("projects/legacy-project/adopt", {**IDENTITY, "thread_id": "thread1"})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_project_runtime_shared_files_isolated_history_and_fork(company, tmp_path):
    import asyncio
    import json
    import sys
    from pathlib import Path

    from langchain_core.messages import AIMessageChunk, ToolMessage

    from octop.infra.company_runtime.client import RemoteCheckpointSaver
    from octop.infra.company_runtime.runtime import EnterpriseRuntime

    server, client, _, owner = company
    install_agent(server)
    server.services.agent_repo.create(agent_id="reader", user_id=owner, name="Reader")
    workspace = tmp_path / "中文 shared folder"
    workspace.mkdir()
    (workspace / "employee-not-output.txt").write_text("private work")
    other = tmp_path / "Project B"
    other.mkdir()
    events = []
    runtime = EnterpriseRuntime(events.append)
    runtime.client = client
    runtime.project_capable = True
    runtime.state = tmp_path / "runtime"
    runtime.workspace = tmp_path / "unused-global"
    runtime.installation_token = "i" * 32
    runtime.tools = {"ffmpeg": "test", "openpyxl": "test", "python-docx": "test", "Pillow": "test"}
    runtime.tool_path = str(Path(sys.executable).parent)
    try:
        await register(client)
        for tid, aid in [("writer-project", "expert"), ("reader-project", "reader")]:
            await client.request(
                "projects/project-a/threads", {**IDENTITY, "thread_id": tid, "agent_id": aid}
            )
        await runtime.start(
            {
                "task_id": "shared-write",
                "thread_id": "writer-project",
                "agent_id": "expert",
                "project_id": "project-a",
                "project_root": str(workspace),
                "text": "write report",
            }
        )
        await asyncio.wait_for(runtime.work["writer-project"], 30)
        assert runtime.tasks["writer-project"]["status"] == "completed", events[-1]
        assert (workspace / "report.txt").read_text() == "local report 42"
        assert not (workspace / "writer-project").exists()
        assert [Path(f["path"]).name for f in events[-1]["artifacts"]] == ["report.txt"]
        # Changing the default while the process remains alive cannot retarget it.
        runtime.workspace = other

        class ReaderModel:
            def bind_tools(self, tools):
                return self

            async def astream(self, messages):
                tools = [m for m in messages if isinstance(m, ToolMessage)]
                if tools:
                    assert "local report 42" in str(tools[-1].content)
                    yield AIMessageChunk(content="Read the shared report", id="reader-answer")
                else:
                    assert not any("write report" in str(m.content) for m in messages)
                    yield AIMessageChunk(
                        content="",
                        id="read-request",
                        tool_call_chunks=[
                            {
                                "name": "read_file",
                                "args": json.dumps({"file_path": "/report.txt"}),
                                "id": "read-shared",
                                "index": 0,
                            }
                        ],
                    )

        agent = server.app_runtime.agent_registry.get_agent("reader")
        agent.model_factory.get_chat_model = lambda ref: ReaderModel()
        await runtime.start(
            {
                "task_id": "shared-read",
                "thread_id": "reader-project",
                "agent_id": "reader",
                "project_id": "project-a",
                "project_root": str(workspace),
                "text": "read the existing report",
            }
        )
        await asyncio.wait_for(runtime.work["reader-project"], 30)
        assert runtime.tasks["reader-project"]["status"] == "completed", events[-1]
        assert events[-1]["artifacts"] == []
        assert not list(other.iterdir())
        assert (
            server.services.company_runtime_repo.thread_state("reader-project")["project_id"]
            == "project-a"
        )
        # A native fork keeps project/expert identity and seeds a fresh checkpoint.
        body = {
            **IDENTITY,
            "source_thread_id": "reader-project",
            "thread_id": "forked-project",
            "assistant_turns_from_end": 1,
        }
        forked = await client.request("projects/project-a/fork", body)
        assert forked["project_id"] == "project-a" and forked["agent_id"] == "reader"
        assert forked == await client.request("projects/project-a/fork", body)
        assert (
            server.services.company_runtime_repo.binding("forked-project")["installation_hash"]
            == hashlib.sha256(b"i" * 32).hexdigest()
        )
        checkpoint = server.services.company_runtime_repo.checkpoint("forked-project", "", None)
        assert checkpoint is not None
        await runtime.start(
            {
                "task_id": "fork-read",
                "thread_id": "forked-project",
                "agent_id": "reader",
                "project_id": "project-a",
                "project_root": str(workspace),
                "text": "continue",
            }
        )
        await asyncio.wait_for(runtime.work["forked-project"], 30)
        assert runtime.tasks["forked-project"]["status"] == "completed", events[-1]
        saver = RemoteCheckpointSaver(client, "fork-read", "forked-project")
        cp = await saver.aget_tuple({"configurable": {"thread_id": "forked-project"}})
        assert cp is not None
        assert not list(workspace.rglob("*.sqlite"))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_project_runtime_blocks_overlapping_roots_and_unknown_tasks(tmp_path):
    from unittest.mock import AsyncMock

    from octop.infra.company_runtime.runtime import EnterpriseRuntime

    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    runtime = EnterpriseRuntime(lambda event: None)
    runtime.client = AsyncMock()
    runtime.project_capable = True
    runtime.state = tmp_path / "state"
    runtime.tools = {"ffmpeg": "test"}
    runtime.tasks["old"] = {"project_path": str(root), "status": "unknown"}
    with pytest.raises(RuntimeError, match="project_already_running"):
        await runtime.start(
            {
                "task_id": "new-task",
                "thread_id": "new",
                "agent_id": "expert",
                "project_id": "nested",
                "project_root": str(child),
                "text": "do not run",
            }
        )
    runtime.client.request.assert_not_called()
    assert not runtime.state.exists()


@pytest.mark.asyncio
async def test_relocation_requires_new_native_revision_and_settled_previous_task(tmp_path):
    from unittest.mock import AsyncMock

    from octop.infra.company_runtime.runtime import EnterpriseRuntime

    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    runtime = EnterpriseRuntime(lambda event: None)
    runtime.client = AsyncMock()
    runtime.state = tmp_path / "state"
    runtime.project_capable = True
    runtime.tools = {"ffmpeg": "test"}
    runtime.tasks["chat"] = {
        "project_path": str(old),
        "project_revision": 1,
        "status": "completed",
        "sync_state": "synced",
    }
    params = {
        "project_id": "project",
        "project_root": str(new),
        "task_id": "relocate",
        "thread_id": "chat",
        "agent_id": "expert",
        "text": "continue",
    }
    with pytest.raises(RuntimeError, match="原工作目录"):
        await runtime.start(params)
    runtime.client.request.assert_not_called()
    runtime.client.request.side_effect = RuntimeError("admission reached")
    with pytest.raises(RuntimeError, match="admission reached"):
        await runtime.start({**params, "project_revision": 2})
    assert runtime.tasks["chat"]["project_path"] == str(new)
    assert runtime.tasks["chat"]["project_revision"] == 2
