"""Regressions for managed resource boundaries and employee configuration updates."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from tests.unit.users.test_employee_resources import prepare, save, selection
from tests.unit.users.test_employee_resources import resources as resources

from octop.api.app import _install_exception_handlers
from octop.api.deps import current_user, get_server
from octop.api.routers import client_config, skills, subagents, workspace
from octop.infra.agents.middleware.expert_access import ExpertAccessMiddleware
from octop.infra.communication.bridge import CommunicationBridge
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User
from octop.infra.users.model_access import ModelAccess


def configure_desktop_model(e, uid, aid):
    e.services.provider_repo.create(
        name="test",
        kind="openai",
        base_url="https://model.invalid/v1",
        api_key="fake",
        models_json='[{"id":"chat"}]',
    )
    ModelAccess(e.services).set(uid, ["test/chat"], "0", actor="administrator")
    agent = e.agents[aid]
    agent.model_factory = SimpleNamespace(
        list_refs=lambda: ["test/chat"],
        get_chat_model=lambda _: SimpleNamespace(profile={}),
    )
    agent.config.pick_default_model_ref = lambda: "test/chat"
    e.server.app_runtime.agent_registry.resolve_fallback_model_ref = lambda: "test/chat"


async def managed(e):
    await prepare(e)
    uid = e.employees[0]
    await save(e, [selection(e, uid, ["source"], [])])
    aid = e.service.access.repo.reserve(uid, "source")["instance_agent_id"]
    e.services.agent_repo.create(agent_id=aid, user_id=uid, name="Employee instance")
    e.add_agent(aid)
    e.service.access.repo.finish_instance(aid, ready=True)
    registry = e.server.app_runtime.agent_registry
    registry.get_row = e.services.agent_repo.get
    registry.workspace_for_agent = lambda agent_id: e.agents[agent_id].workspace
    registry.get_config = lambda _: {}

    async def sync(agent_id, fields, runtime_settings):
        e.services.agent_repo.update_config(agent_id, **fields, skill_package_ids="[]")

    registry.sync_managed_definition = AsyncMock(side_effect=sync)
    e.server.paths = e.services.paths
    e.server.employee_resources = e.service
    user = User(uid, "employee-0", "user", None, permissions=[])
    app = FastAPI()
    for router in (skills.router, subagents.router, workspace.router, client_config.router):
        app.include_router(router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_server] = lambda: e.server
    _install_exception_handlers(app)
    return uid, aid, app


@pytest.mark.asyncio
async def test_employee_cannot_create_enable_edit_or_mount_managed_skills(resources):
    e = resources
    uid, aid, app = await managed(e)
    await e.service.prepare(aid)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        root = f"/api/agents/{aid}"
        for method, path, body in [
            ("POST", "/skills", {"name": "unassigned", "content": "---\nname: unassigned\n---\n"}),
            ("PUT", "/skills/pdf", {"content": "changed"}),
            ("POST", "/skills/pdf/enable", None),
            ("POST", "/skills/pdf/disable", None),
            ("DELETE", "/skills/pdf", None),
            ("PUT", "/skill-packages", {"package_ids": [e.extra.split(":")[1]]}),
        ]:
            response = await c.request(method, root + path, json=body)
            assert response.status_code == 403, response.text
        assert not (e.workspaces[aid] / "skills/unassigned").exists()
        assert (await c.get(root + "/skill-packages")).status_code == 403
        assert (await c.get(root + "/subagents")).json() == []
        assert (
            await c.post(root + "/subagents/install", json={"slug": "unassigned"})
        ).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "SOUL.md",
        "./SOUL.md",
        "docs/../SOUL.md",
        "skills/pdf/script.py",
        ".octop/skills/pdf/SKILL.md",
        ".octop/.bootstrapped",
    ],
)
async def test_managed_definition_writes_are_blocked_but_personal_files_work(resources, path):
    e = resources
    uid, aid, app = await managed(e)
    await e.service.prepare(aid)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        url = f"/api/agents/{aid}/workspace/file"
        response = await c.put(
            url, params={"path": path, "from_workspace": "true"}, json={"content": "changed"}
        )
        assert response.status_code == 403, response.text
        response = await c.put(
            url,
            params={"path": "notes.txt", "from_workspace": "true"},
            json={"content": "employee output"},
        )
        assert response.status_code == 200, response.text
        response = await c.put(
            url,
            params={"path": "MEMORY.md", "from_workspace": "true"},
            json={"content": "employee memory"},
        )
        assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_source_skill_revocation_blocks_next_call_and_removes_old_files(resources):
    e = resources
    uid, aid, app = await managed(e)
    await e.service.prepare(aid)
    revision = await e.service.revision(uid)
    await e.agents["source"].workspace.adelete("skills/pdf")
    assert revision != await e.service.revision(uid)
    guard = ExpertAccessMiddleware(e.services, aid, lambda: e.service)
    handler = AsyncMock()
    with pytest.raises(OctopError, match="employee_resources_changed"):
        await guard.awrap_tool_call(object(), handler)
    handler.assert_not_awaited()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        root = f"/api/agents/{aid}"
        assert (await c.get(root + "/skills/pdf")).status_code == 403
        assert (
            await c.get(
                root + "/workspace/file",
                params={"path": "skills/pdf/SKILL.md", "from_workspace": "true"},
            )
        ).status_code == 403
    await e.service.prepare(aid)
    assert not (e.workspaces[aid] / "skills/pdf").exists()


@pytest.mark.asyncio
async def test_preparation_reenforces_allowlist_even_when_source_version_is_unchanged(resources):
    e = resources
    uid, aid, app = await managed(e)
    await e.service.prepare(aid)
    await e.agents[aid].workspace.aupload_many(
        [("skills/unassigned/SKILL.md", b"---\nname: unassigned\n---\n")]
    )
    await e.service.prepare(aid)
    assert "unassigned" in e.agents[aid].disabled
    guard = ExpertAccessMiddleware(e.services, aid, lambda: e.service)
    with pytest.raises(OctopError, match="resource_not_assigned"):
        await guard.awrap_tool_call(
            SimpleNamespace(
                tool_call={"name": "read_file", "args": {"file_path": "skills/unassigned/SKILL.md"}}
            ),
            AsyncMock(),
        )


@pytest.mark.asyncio
async def test_content_updates_change_notice_revision_and_apply_preserves_employee_data(resources):
    e = resources
    uid, aid, app = await managed(e)
    await e.agents[aid].workspace.aupload_many(
        [
            ("USER.md", b"employee profile"),
            ("MEMORY.md", b"employee memory"),
            ("report.txt", b"employee output"),
        ]
    )
    e.services.thread_repo.insert(
        thread_id="history",
        agent_id=aid,
        user_id=uid,
        channel_type="dashboard",
        session_key="history",
        last_active=0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        first = await c.post("/api/client-config/apply")
        assert first.status_code == 200, first.text
        assert first.json()["resource_update_pending"] is False
        previous = first.json()["revision"]
        for update in ("prompt", "definition", "script"):
            if update == "prompt":
                e.services.agent_repo.update_config(
                    "source", system_prompt="Updated company prompt", name="Renamed expert"
                )
            elif update == "definition":
                await e.agents["source"].workspace.aupload_many(
                    [("SOUL.md", b"Updated company definition")]
                )
            else:
                await e.agents["source"].workspace.aupload_many(
                    [("skills/pdf/script.py", b'print("new version")')]
                )
            current = await c.get("/api/client-config/status")
            assert current.status_code == 200, current.text
            assert current.json()["revision"] != previous
            assert current.json()["resource_update_pending"] is True
            assert "Updated company prompt" not in current.text
            applied = await c.post("/api/client-config/apply")
            assert applied.status_code == 200, applied.text
            assert applied.json()["resource_update_pending"] is False
            previous = applied.json()["revision"]
        assert e.services.agent_repo.get(aid).system_prompt == "Updated company prompt"
        assert e.services.agent_repo.get(aid).name == "Renamed expert"
        assert (e.workspaces[aid] / "SOUL.md").read_text() == "Updated company definition"
        assert (e.workspaces[aid] / "skills/pdf/script.py").read_text() == 'print("new version")'
        assert (e.workspaces[aid] / "USER.md").read_text() == "employee profile"
        assert (e.workspaces[aid] / "MEMORY.md").read_text() == "employee memory"
        assert (e.workspaces[aid] / "report.txt").read_text() == "employee output"
        assert e.services.thread_repo.get("history") is not None


@pytest.mark.asyncio
async def test_refresh_does_not_rebuild_an_expert_with_an_active_invocation(resources):
    e = resources
    uid, aid, app = await managed(e)
    await e.service.prepare(aid)
    e.server.app_runtime.agent_registry._active_invocations[aid] = 1
    e.services.agent_repo.update_config("source", system_prompt="New prompt")
    with pytest.raises(OctopError, match="client_config_busy"):
        await e.service.refresh_user(uid)
    assert e.services.agent_repo.get(aid).system_prompt != "New prompt"


@pytest.mark.asyncio
async def test_config_apply_can_recover_after_an_incomplete_first_open(resources):
    e = resources
    await prepare(e)
    uid = e.employees[0]
    await save(e, [selection(e, uid, ["source"], [])])
    mapping = e.service.access.repo.reserve(uid, "source")
    assert e.services.agent_repo.get(mapping["instance_agent_id"]) is None
    await e.service.refresh_user(uid)
    assert e.service.access.repo.instance(mapping["instance_agent_id"])["state"] == "pending"


@pytest.mark.asyncio
async def test_config_apply_does_not_load_every_idle_assigned_expert(resources, monkeypatch):
    e = resources
    uid, aid, _ = await managed(e)
    registry = e.server.app_runtime.agent_registry
    registry.runtime_shards = object()
    registry._octop_harness_or_none = lambda _: None
    prepare_instance = AsyncMock()
    monkeypatch.setattr(e.service, "prepare", prepare_instance)
    e.services.agent_repo.update_config(
        "source", name="Renamed expert", system_prompt="New prompt", welcome_message="Welcome"
    )
    await e.service.refresh_user(uid)
    prepare_instance.assert_not_awaited()
    row = e.services.agent_repo.get(aid)
    assert (row.name, row.system_prompt, row.welcome_message) == (
        "Renamed expert",
        "New prompt",
        "Welcome",
    )
    assert e.service.access.repo.instance(aid)["state"] == "ready"


@pytest.mark.asyncio
async def test_basic_paths_pass_the_real_bridge_and_partial_save_requires_refresh(resources):
    e = resources
    await prepare(e)
    uid = e.employees[0]
    e.service.repo.save_pool("B", [], [], 0, 2, "administrator")
    bridge = CommunicationBridge(e.services)
    bridge._instance = "review"
    bridge._http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": {"updated": True}})
        )
    )
    bridge.ready = AsyncMock(return_value={"version": 1, "effective_permissions": ["departments"]})
    bridge._key = b"k" * 32
    bridge.url = "http://organization"
    try:
        for suffix in ("department", "job-title"):
            result = await bridge.organization_request(
                e.admin,
                "session",
                "PUT",
                f"/organization/users/{bridge.mapped_id(uid)}/{suffix}",
                {},
            )
            assert result == {"updated": True}
    finally:
        await bridge._http.aclose()
    original = e.server.communication.organization_request

    async def fail_title(actor, session, method, path, body):
        if path.endswith("/job-title"):
            raise OctopError(ErrorCode.NOT_FOUND, "title removed", status=404)
        return await original(actor, session, method, path, body)

    e.server.communication.organization_request = fail_title
    with pytest.raises(OctopError) as error:
        await e.service.basic(
            e.admin,
            uid,
            "session",
            {
                "department_id": "B",
                "job_title_id": "removed",
                "membership_revision": 0,
                "expected_revision": 0,
                "expected_source_revision": 3,
            },
        )
    assert e.service.state(uid)["scope_id"] == "B"
    assert error.value.details == {"reason": "basic_settings_partial", "refresh_required": True}


@pytest.mark.asyncio
async def test_real_harness_applies_updated_prompt_before_the_next_invocation(
    resources, monkeypatch
):
    import json

    from octop.infra.agents.manager import AgentManager

    e = resources
    await prepare(e)
    uid = e.employees[0]
    await save(e, [selection(e, uid, ["source"], [])])
    e.services.provider_repo.create(
        name="review-model",
        kind="openai",
        base_url="https://example.invalid/v1",
        api_key="test",
        models_json=json.dumps([{"id": "test-model", "name": "Test", "enabled": True}]),
    )
    e.services.agent_repo.update_config(
        "source",
        system_prompt="Original company prompt",
        config_json=json.dumps({"memory": {"memory_enabled": False}}),
    )
    manager = AgentManager(repos=e.services.repos, paths=e.services.paths)
    build = manager._build_harness_config

    def without_memory(row):
        config = build(row)
        config.memory_enabled = False
        return config

    monkeypatch.setattr(manager, "_build_harness_config", without_memory)
    try:
        await manager.boot()
        e.server.app_runtime.agent_registry = manager
        manager.employee_resources = e.service
        source = manager.get_agent("source").workspace
        await source.aupload_many(
            [
                ("SOUL.md", b"Original definition"),
                (
                    "skills/pdf/SKILL.md",
                    b"---\nname: PDF\ndescription: Read PDF\n---\nPDF instructions",
                ),
                ("skills/pdf/script.py", b"original script"),
            ]
        )
        legacy = str(source.workspace_dir / "skills/legacy")
        await source.aupload_many(
            [
                (legacy + "/SKILL.md", b"---\nname: Legacy\ndescription: Legacy skill\n---\n"),
                (legacy + "/script.py", b"legacy script"),
            ]
        )
        row = await e.service.access.instantiate(manager, "source", uid)
        await e.service.prepare(row.agent_id)
        assert manager.get_agent(row.agent_id).config.system_prompt == "Original company prompt"
        target = manager.get_agent(row.agent_id).workspace
        assert await target.aread_text("skills/pdf/script.py") == "original script"
        assert await target.aread_text("skills/legacy/script.py") == "legacy script"
        await target.aupload_many(
            [("MEMORY.md", b"Employee memory"), ("report.txt", b"Employee report")]
        )
        e.services.agent_repo.update_config(
            "source",
            system_prompt="Updated company prompt",
            config_json=json.dumps(
                {"memory": {"memory_enabled": False}, "temperature": 0.25, "max_iters": 20}
            ),
        )
        await source.aupload_many(
            [("SOUL.md", b"Updated definition"), ("skills/pdf/script.py", b"updated script")]
        )
        async with manager._track_invocation(row.agent_id):
            current = manager.get_agent(row.agent_id)
            assert current.config.system_prompt == "Updated company prompt"
            assert manager.get_config(row.agent_id)["temperature"] == 0.25
            assert manager.get_config(row.agent_id)["max_iters"] == 20
            assert await current.workspace.aread_text("SOUL.md") == "Updated definition"
            assert await current.workspace.aread_text("skills/pdf/script.py") == "updated script"
            assert await current.workspace.aread_text("MEMORY.md") == "Employee memory"
            assert await current.workspace.aread_text("report.txt") == "Employee report"
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_alternate_workspace_mutations_and_manager_mutators_are_protected(resources):
    from octop.infra.agents.manager import AgentManager

    e = resources
    uid, aid, app = await managed(e)
    await e.service.prepare(aid)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        root = f"/api/agents/{aid}/workspace"
        for method, suffix, params, body in [
            ("DELETE", "/file", {"path": "SOUL.md"}, None),
            ("POST", "/move", {"path": "notes.txt"}, {"destination": "SOUL.md"}),
            ("POST", "/move", {"path": "skills"}, {"destination": "copied-skills"}),
        ]:
            response = await c.request(method, root + suffix, params=params, json=body)
            assert response.status_code == 403, response.text
        response = await c.post(
            root + "/upload",
            params={"path": "SOUL.md", "from_workspace": "true"},
            files={"file": ("SOUL.md", b"replacement")},
        )
        assert response.status_code == 403, response.text
        assert (await c.get(root + "/archive")).status_code == 403
        assert (
            await c.post(root + "/archive", files={"file": ("test.zip", b"archive")})
        ).status_code == 403
    manager = AgentManager(repos=e.services.repos, paths=e.services.paths)
    for method, arg in [
        (manager.persist_skills_disabled, set()),
        (manager.persist_tools_disabled, set()),
        (manager.persist_plugin_tools_config, {}),
        (manager.persist_skill_package_ids, []),
        (manager.apply_persona_mbti, "INTJ"),
    ]:
        with pytest.raises(OctopError, match="managed_resource_read_only"):
            await method(aid, arg)


@pytest.mark.asyncio
async def test_desktop_exports_new_content_and_cancels_tasks_bound_to_old_content(resources):
    import json

    from octop.infra.company_runtime.service import CompanyRuntimeService

    e = resources
    uid, aid, app = await managed(e)
    configure_desktop_model(e, uid, aid)
    service = CompanyRuntimeService(e.server, {"owner_user_id": uid, "device_id": "device0"})
    first = await service.expert(aid)
    e.services.thread_repo.insert(
        thread_id="desktop-thread",
        agent_id=aid,
        user_id=uid,
        channel_type="dashboard",
        session_key="desktop-thread",
        last_active=0,
    )
    with e.pool.transaction() as conn:
        conn.execute(
            "INSERT INTO company_runtime_threads VALUES (?,?,?,?,?,?,?)",
            ("desktop-thread", uid, aid, "device0", "project", "hash", 0),
        )
        conn.execute(
            "INSERT INTO company_runtime_tasks(task_id,thread_id,device_id,status,request_json,expert_json,recovery_hash,started_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "old-task",
                "desktop-thread",
                "device0",
                "running",
                "{}",
                json.dumps(first),
                "hash",
                0,
                0,
            ),
        )
    e.services.agent_repo.update_config("source", system_prompt="Desktop prompt v2")
    await e.agents["source"].workspace.aupload_many(
        [("skills/pdf/script.py", b"desktop script v2")]
    )
    assert (await service.task("old-task"))["status"] == "cancel_requested"
    with pytest.raises(OctopError):
        await service.task("old-task", active=True)
    fresh = await service.expert(aid)
    assert "Desktop prompt v2" in fresh["instructions"]
    assert fresh["resource_revision"] != first["resource_revision"]
    assert fresh["version"] != first["version"]
    old_script = next(a for a in first["skills"] if a["path"] == "skills/pdf/script.py")
    new_script = next(a for a in fresh["skills"] if a["path"] == "skills/pdf/script.py")
    assert old_script["sha256"] != new_script["sha256"]


@pytest.mark.asyncio
async def test_desktop_rejects_an_export_if_source_changes_between_definition_and_skills(
    resources, monkeypatch
):
    from octop.infra.company_runtime.service import CompanyRuntimeService

    e = resources
    uid, aid, app = await managed(e)
    configure_desktop_model(e, uid, aid)
    snapshot = e.service.snapshot
    calls = 0

    async def changing_snapshot(*args):
        nonlocal calls
        value = await snapshot(*args)
        calls += 1
        if calls == 2:
            e.services.agent_repo.update_config("source", system_prompt="Changed during export")
        return value

    monkeypatch.setattr(e.service, "snapshot", changing_snapshot)
    service = CompanyRuntimeService(e.server, {"owner_user_id": uid, "device_id": "device0"})
    with pytest.raises(OctopError, match="employee_resources_changed"):
        await service.expert(aid)
