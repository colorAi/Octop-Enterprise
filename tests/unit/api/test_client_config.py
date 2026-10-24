from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from octop.api.deps import current_user, get_server
from octop.api.routers import client_config, users
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.user_policies import UserPolicyRow
from octop.infra.errors import OctopError
from octop.infra.gateway.ws import WebSocketHub
from octop.infra.users.client_config import config_revision, publish_release, read_release
from octop.infra.users.identity import User
from octop.infra.utils.bounded_io import BoundedIO


@pytest.fixture
def environment(tmp_path):
    pool = SqlitePool(tmp_path / "settings.sqlite")
    with pool.connect() as conn:
        conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
    settings = SettingsRepo(pool)
    employee = User(2, "employee", "user", None, permissions=["browser"])
    admin = User(1, "admin", "admin", None)
    policy = UserPolicyRow(1, "p1", 2, "workspace_root_dir", True, "/private/project", 1, 1)
    hub = WebSocketHub()
    frames = {1: [], 2: [], 3: []}
    for uid in frames:

        async def send(frame, uid=uid):
            frames[uid].append(frame)

        hub.register(str(uid), send, user_id=uid)
    manager = SimpleNamespace(list=lambda: [admin, employee])
    server = SimpleNamespace(
        management_io=BoundedIO(),
        app_runtime=SimpleNamespace(gateway=SimpleNamespace(ws_hub=hub)),
        user_manager=manager,
        services=SimpleNamespace(
            settings_repo=settings,
            user_policy_repo=SimpleNamespace(
                list_for_user=lambda uid: [policy] if uid == 2 else []
            ),
            audit_repo=Mock(),
        ),
    )
    app = FastAPI()
    app.include_router(client_config.router, prefix="/api")
    app.dependency_overrides[get_server] = lambda: server
    app.dependency_overrides[current_user] = lambda: employee
    app.add_exception_handler(
        OctopError,
        lambda request, exc: JSONResponse({"error": exc.code.value}, status_code=exc.status),
    )
    yield SimpleNamespace(
        app=app,
        server=server,
        employee=employee,
        admin=admin,
        settings=settings,
        frames=frames,
        policy=policy,
    )
    pool.close()


@pytest.mark.asyncio
async def test_slow_config_database_read_leaves_the_event_loop_responsive(environment):
    env = environment
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original = env.server.services.user_policy_repo.list_for_user

    def slow(uid):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return original(uid)

    env.server.services.user_policy_repo.list_for_user = slow
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://test"
    ) as client:
        request = asyncio.create_task(client.get("/api/client-config/status"))
        try:
            await asyncio.wait_for(started.wait(), 1)
            denied = await asyncio.wait_for(
                client.post("/api/client-config/publish", json={"message": "denied"}), 1
            )
            assert denied.status_code == 403
            assert not request.done()
        finally:
            release.set()
        assert (await request).status_code == 200


@pytest.mark.asyncio
async def test_employee_can_read_own_revision_without_update_permission(environment):
    env = environment
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/client-config/status")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        status = response.json()
        assert status["user_id"] == 2 and status["permissions"] == ["browser"]
        assert len(status["revision"]) == 64
        assert "/private/project" not in response.text
        denied = await client.post("/api/client-config/publish", json={"message": "not allowed"})
        assert denied.status_code == 403
        assert read_release(env.settings) == {}
        assert not any(env.frames.values())


@pytest.mark.asyncio
async def test_admin_publish_persists_notice_and_notifies_active_users(environment):
    env = environment
    env.app.dependency_overrides[current_user] = lambda: env.admin
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://test"
    ) as client:
        before = (await client.get("/api/client-config/status")).json()["revision"]
        response = await client.post(
            "/api/client-config/publish", json={"message": " New model access "}
        )
        assert response.status_code == 200
        assert response.json()["message"] == "New model access"
        assert read_release(env.settings) == response.json()
        after = (await client.get("/api/client-config/status")).json()
        assert after["revision"] != before and after["message"] == "New model access"
        assert env.frames[1] == env.frames[2] == [{"type": "client_config_changed"}]
        assert not env.frames[3]
        assert (
            env.server.services.audit_repo.write.call_args.kwargs["action"]
            == "client_config.publish"
        )
        invalid = await client.post("/api/client-config/publish", json={"message": "x" * 501})
        assert invalid.status_code == 422


def test_permission_policy_and_offline_release_changes_have_stable_revisions(environment):
    env = environment
    revision = config_revision("user", ["browser", "terminal"], [env.policy], {})
    assert revision == config_revision("user", ["terminal", "browser", "browser"], [env.policy], {})
    assert revision != config_revision("user", ["browser"], [env.policy], {})
    assert revision != config_revision(
        "user", ["browser", "terminal"], [replace(env.policy, value="/other")], {}
    )
    release = publish_release(env.settings, "Model settings")
    assert release == read_release(env.settings)
    assert revision != config_revision(
        "user", ["browser", "terminal"], [env.policy], read_release(env.settings)
    )
    assert publish_release(env.settings, "Model settings")["id"] != release["id"]


@pytest.mark.asyncio
async def test_admin_permission_edit_automatically_notifies_only_target(environment, monkeypatch):
    env = environment
    row = SimpleNamespace(id=2, username="employee", role="user")
    env.server.user_manager.get_row = lambda uid: row
    env.server.user_manager.set_permissions = AsyncMock()
    monkeypatch.setattr(users, "_row_to_dict", lambda *args: {"id": 2})
    await users.patch_user(
        2, users.UserPatchBody(permissions=["browser", "users"]), env.admin, env.server
    )
    env.server.user_manager.set_permissions.assert_awaited_once_with(
        "employee", ["browser", "users"]
    )
    assert env.frames[2] == [{"type": "client_config_changed"}]
    assert not env.frames[1] and not env.frames[3]
