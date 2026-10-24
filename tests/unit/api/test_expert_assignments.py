import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from deepagents.backends.local_shell import LocalShellBackend
from fastapi import FastAPI
from octop_harness.backends.workspace import BackendWorkspace

from octop.api.app import _install_exception_handlers
from octop.api.deps import current_user, get_server
from octop.api.routers import agents, expert_assignments
from octop.config import OctopConfig
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.users.employee_resources import EmployeeResources
from octop.infra.users.expert_access import ExpertAccess
from octop.infra.users.identity import User
from octop.infra.utils.bounded_io import BoundedIO
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def assignment_api(tmp_path):
    db = SqlitePool(tmp_path / "api.db")
    run_migrations(db)
    services = build_shared_services(paths=PathLayout(tmp_path), config=OctopConfig(), db=db)
    admin_id = services.user_repo.create(username="admin", role="admin")
    employee_id = services.user_repo.create(username="employee", role="user", permissions=["users"])
    access = ExpertAccess(services)
    services.agent_repo.create(
        agent_id="source",
        user_id=admin_id,
        name="Company expert",
        system_prompt="PRIVATE ADMIN PROMPT",
        config_json='{"backend":{"root_dir":"/private/admin"}}',
    )
    services.agent_repo.create(agent_id="old", user_id=employee_id, name="Old personal expert")
    root = tmp_path / "source"
    root.mkdir()
    source_ws = BackendWorkspace(LocalShellBackend(root_dir=str(root), virtual_mode=False), root)
    registry = SimpleNamespace(
        get_row=services.agent_repo.get,
        list_agents=services.agent_repo.list_by_user,
        list_rows=services.agent_repo.list_all,
        is_bootstrapped=lambda aid: True,
        is_bootstrapped_for_display=lambda aid: True,
        workspace_for_agent=lambda aid: source_ws,
        get_config=lambda aid: {},
        _shared_instance_lock=asyncio.Lock(),
    )
    created_workspaces = {}

    async def create(spec, *, workspace_initializer=None):
        trusted = access.assert_create(spec.user_id, spec.agent_id)
        assert trusted, "Only internal assignment creation should reach this fake registry"
        services.agent_repo.create(
            agent_id=spec.agent_id,
            user_id=spec.user_id,
            name=spec.name,
            system_prompt=spec.system_prompt,
        )
        row = services.agent_repo.get(spec.agent_id)
        target = tmp_path / spec.agent_id
        target.mkdir()
        workspace = BackendWorkspace(
            LocalShellBackend(root_dir=str(target), virtual_mode=False), target
        )
        await workspace_initializer(row, workspace)
        created_workspaces[spec.agent_id] = workspace
        return row

    registry.create = AsyncMock(side_effect=create)
    admin = User(admin_id, "admin", "admin", None)
    employee = User(employee_id, "employee", "user", None, permissions=["users"])
    actor = {"user": admin}
    server = SimpleNamespace(
        management_io=BoundedIO(),
        services=services,
        app_runtime=SimpleNamespace(
            agent_registry=registry,
            gateway=SimpleNamespace(ws_hub=SimpleNamespace(push_to_user=AsyncMock())),
        ),
        user_manager=SimpleNamespace(list=lambda: [admin, employee]),
    )
    app = FastAPI()
    app.include_router(expert_assignments.router, prefix="/api/admin/expert-assignments")
    app.include_router(agents.router, prefix="/api/agents")
    app.dependency_overrides[get_server] = lambda: server
    app.dependency_overrides[current_user] = lambda: actor["user"]
    _install_exception_handlers(app)
    yield SimpleNamespace(
        app=app,
        server=server,
        actor=actor,
        admin=admin,
        employee=employee,
        source_ws=source_ws,
        workspaces=created_workspaces,
        registry=registry,
        access=access,
    )
    db.close()


@pytest.mark.asyncio
async def test_assignments_keep_owned_legacy_history_visible_without_execution(assignment_api):
    env = assignment_api
    env.access.configure_source(env.admin.id, env.admin.id, 0)
    env.access.save(env.admin.id, env.employee.id, ["source"], "assigned_only", 0, 1)
    env.server.services.thread_repo.insert(
        thread_id="old-chat",
        agent_id="old",
        user_id=env.employee.id,
        channel_type="dashboard",
        session_key="legacy",
        last_active=123,
    )
    env.actor["user"] = env.employee
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/agents")
        detail = await client.get("/api/agents/old")
    assert response.status_code == 200
    payloads = {p["agent_id"]: p for p in response.json()}
    assert set(payloads) == {"old", "source"}
    assert payloads["old"]["history_only"] is True
    assert payloads["old"]["requires_private_instance"] is False
    assert detail.status_code == 200
    assert detail.json()["history_only"] is True
    from octop.infra.errors import OctopError

    with pytest.raises(OctopError, match="resource_not_assigned"):
        env.access.assert_use(env.employee.id, "old")
    assert env.server.services.thread_repo.get("old-chat").agent_id == "old"


@pytest.mark.asyncio
async def test_resource_directory_does_not_scan_or_reconcile_and_detail_checks_authority(
    assignment_api,
):
    env = assignment_api
    env.access.configure_source(env.admin.id, env.admin.id, 0)
    env.access.save(env.admin.id, env.employee.id, ["source"], "assigned_only", 0, 1)
    resources = EmployeeResources(env.server)
    env.server.employee_resources = resources
    resources.repo.reconcile(env.employee.id, "A", 1, True)
    before = resources.state(env.employee.id)
    batch = AsyncMock(
        return_value={
            env.employee.id: {
                "found": True,
                "enabled": True,
                "department_id": "B",
                "revision": 2,
            }
        }
    )
    single = AsyncMock(return_value={"enabled": True, "department_id": "B", "revision": 2})
    env.server.communication = SimpleNamespace(
        enabled=True,
        mapped_id=lambda uid: f"native:{uid}",
        resource_memberships=batch,
        resource_membership=single,
    )
    resources.catalog = AsyncMock(side_effect=AssertionError("directory must not scan"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/admin/expert-assignments/directory")
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        row = response.json()["employees"][0]
        assert row["expert_ids"] == ["source"] and row["scope_id"] == "B"
        assert row["membership_status"] == "changed"
        assert resources.state(env.employee.id) == before
        batch.assert_awaited_once_with(env.admin.id, [env.employee.id])
        single.assert_not_awaited()
        resources.catalog.assert_not_awaited()
        assert "PRIVATE ADMIN PROMPT" not in response.text and "password_hash" not in response.text

        detail = await client.get(f"/api/admin/expert-assignments/users/{env.employee.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["expert_ids"] == []
        assert detail.json()["revision"] == before["revision"] + 1
        assert detail.json()["membership_status"] == "current"
        single.assert_awaited_once_with(env.employee.id)

        env.actor["user"] = env.employee
        for path in ("/directory", "/catalog", f"/users/{env.employee.id}"):
            assert (await client.get("/api/admin/expert-assignments" + path)).status_code == 403
        assert batch.await_count == 1 and single.await_count == 1
    await resources.drain()


@pytest.mark.asyncio
async def test_slow_catalog_does_not_delay_directory_and_source_change_rejects_result(
    assignment_api,
):
    env = assignment_api
    env.access.configure_source(env.admin.id, env.admin.id, 0)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow():
        started.set()
        await release.wait()
        return {"experts": [], "skills": [], "defaults": []}

    env.server.employee_resources = SimpleNamespace(
        catalog=AsyncMock(side_effect=slow),
        public_catalog=lambda data: data,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://test"
    ) as client:
        catalog = asyncio.create_task(client.get("/api/admin/expert-assignments/catalog"))
        try:
            await asyncio.wait_for(started.wait(), 2)
            response = await asyncio.wait_for(
                client.get("/api/admin/expert-assignments/directory"), 1
            )
            assert response.status_code == 200
            assert not catalog.done()
            env.access.repo.set_source(env.admin.id, 1, "admin")
        finally:
            release.set()
        assert (await catalog).status_code == 409
    schema = env.app.openapi()
    assert schema["paths"]["/api/admin/expert-assignments/catalog"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]["$ref"].endswith("Catalog")


@pytest.mark.asyncio
async def test_admin_assigns_private_expert_without_publishing_and_employee_gets_private_copy(
    assignment_api,
):
    env = assignment_api
    await env.source_ws.aupload_many(
        [
            ("SOUL.md", b"Expert definition"),
            ("USER.md", b"Administrator personal data"),
            ("MEMORY.md", b"Administrator memory"),
            (".env", b"SECRET=example"),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://test"
    ) as client:
        source = await client.put(
            "/api/admin/expert-assignments/source",
            json={
                "source_user_id": env.admin.id,
                "expected_revision": 0,
            },
        )
        assert source.status_code == 200
        before = (await client.get("/api/admin/expert-assignments")).json()
        assert before["experts"][0]["agent_id"] == "source"
        response = await client.put(
            f"/api/admin/expert-assignments/users/{env.employee.id}",
            json={
                "expert_ids": ["source"],
                "expected_revision": 0,
                "expected_source_revision": 1,
            },
        )
        assert response.status_code == 200
        env.actor["user"] = env.employee
        listed = await client.get("/api/agents")
        assert [r["agent_id"] for r in listed.json()] == ["source"]
        assert "PRIVATE ADMIN PROMPT" not in listed.text
        assert "/private/admin" not in listed.text
        assert listed.json()[0]["requires_private_instance"]
        first = await client.post("/api/agents/source/private-instance")
        assert first.status_code == 200, first.text
        instance = first.json()["agent_id"]
        again = await client.post("/api/agents/source/private-instance")
        assert again.json()["agent_id"] == instance
        assert env.registry.create.await_count == 1
        copied = env.workspaces[instance]
        assert await copied.aread_text("SOUL.md") == "Expert definition"
        for private in ("USER.md", "MEMORY.md", ".env"):
            assert await copied.aread_text(private) is None
        assert env.server.services.agent_repo.get("source").is_shared == 0
        assert env.server.services.agent_repo.get(instance).user_id == env.employee.id
        assert (await client.get("/api/agents")).json()[0]["agent_id"] == instance
        assert (await client.get("/api/agents?scope=all")).status_code == 403
        assert (await client.post("/api/agents", json={"name": "Bypass"})).status_code == 403


@pytest.mark.asyncio
async def test_module_permission_cannot_self_assign_and_invalid_shapes_do_not_clear_selection(
    assignment_api,
):
    env = assignment_api
    env.access.configure_source(env.admin.id, env.admin.id, 0)
    env.access.save(env.admin.id, env.employee.id, ["source"], "assigned_only", 0, 1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://test"
    ) as client:
        env.actor["user"] = env.employee
        assert (await client.get("/api/admin/expert-assignments")).status_code == 403
        target = f"/api/admin/expert-assignments/users/{env.employee.id}"
        body = {"expert_ids": [], "expected_revision": 1, "expected_source_revision": 1}
        assert (await client.put(target, json=body)).status_code == 403
        env.actor["user"] = env.admin
        for invalid in (
            {"expected_revision": 1, "expected_source_revision": 1},
            {**body, "expert_ids": None},
            {**body, "source_user_id": env.employee.id},
        ):
            assert (await client.put(target, json=invalid)).status_code == 422
        assert env.access.repo.state(env.employee.id)["expert_ids"] == ["source"]
        assert (await client.put(target, json={**body, "expected_revision": 0})).status_code == 409
        assert (await client.put(target, json=body)).status_code == 200
        env.actor["user"] = env.employee
        assert (await client.get("/api/agents")).json() == []
        assert (await client.post("/api/agents/source/private-instance")).status_code == 403


@pytest.mark.asyncio
async def test_managed_initialization_excludes_disabled_skills_and_package_mounts(assignment_api):
    env = assignment_api
    await env.source_ws.aupload_many(
        [
            ("SOUL.md", b"Company definition"),
            ("skills/admin-disabled/SKILL.md", b"Private disabled skill"),
            ("skills/skill-manager/SKILL.md", b"Management-only skill"),
            ("agents/private.md", b"Private administrator agent"),
        ]
    )
    env.registry.get_config = lambda aid: {
        "skills_disabled": ["admin-disabled"],
        "skill_package_ids": ["private-package"],
    }
    env.access.configure_source(env.admin.id, env.admin.id, 0)
    env.access.save(env.admin.id, env.employee.id, ["source"], "assigned_only", 0, 1)
    row = await env.access.instantiate(env.registry, "source", env.employee.id)
    target = env.workspaces[row.agent_id]
    assert await target.aread_text("SOUL.md") == "Company definition"
    for path in (
        "skills/admin-disabled/SKILL.md",
        "skills/skill-manager/SKILL.md",
        "agents/private.md",
    ):
        assert await target.aread_text(path) is None
    assert env.registry.create.call_args.args[0].skill_package_ids == []
