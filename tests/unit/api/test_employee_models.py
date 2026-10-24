from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from tests.unit.users.test_model_access import model_env as model_env

from octop.api.app import _install_exception_handlers
from octop.api.deps import current_user
from octop.api.routers import providers, users
from octop.api.routers.chat.models import ChatTurnBody
from octop.api.routers.chat.turn import prepare_dashboard_turn
from octop.infra.agents.providers.store import ProviderStore
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.client_config import config_revision, read_config_state
from octop.infra.users.identity import User
from octop.infra.users.manager import UserManager
from octop.infra.utils.bounded_io import BoundedIO


@pytest.fixture
async def models_api(model_env, monkeypatch):
    e = model_env
    actor = {"user": User(e.admin, "admin", "admin", None)}
    server = SimpleNamespace(
        _started=True,
        services=e.services,
        management_io=BoundedIO(),
        user_manager=UserManager(e.services),
        app_runtime=None,
    )
    app = FastAPI()
    app.state.octop_server = server
    app.include_router(users.router, prefix="/api/users")
    app.include_router(providers.router, prefix="/api/providers")
    app.dependency_overrides[current_user] = lambda: actor["user"]
    _install_exception_handlers(app)
    notify = AsyncMock()
    monkeypatch.setattr("octop.api.routers.client_config.notify_config_changed", notify)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield e, client, actor, notify
    await server.management_io.drain()


async def test_model_assignment_admin_only_validates_and_notifies(models_api):
    e, client, actor, notify = models_api
    url = f"/api/users/{e.employee}/models"
    for uid, permissions in [(e.employee, []), (e.manager, ["users"])]:
        actor["user"] = User(uid, "employee", "user", None, permissions=permissions)
        assert (await client.get(url)).status_code == 403
        assert (
            await client.put(url, json={"model_refs": [], "expected_revision": "0"})
        ).status_code == 403
    actor["user"] = User(e.admin, "admin", "admin", None)
    initial = (await client.get(url)).json()
    assert initial["model_refs"] == [] and initial["revision"] == "0"
    assert {item["ref"] for item in initial["items"]} == {"alpha/chat", "alpha/vision", "beta/chat"}
    assert "secret-sentinel" not in str(initial)
    release, policies, expert = read_config_state(e.services, e.employee)
    before = config_revision("user", [], policies, release, expert)
    result = await client.put(url, json={"model_refs": ["beta/chat"], "expected_revision": "0"})
    assert result.status_code == 200, result.text
    assert result.json()["model_refs"] == ["beta/chat"]
    notify.assert_awaited_once()
    release, policies, expert = read_config_state(e.services, e.employee)
    assert before != config_revision("user", [], policies, release, expert)
    stale = await client.put(url, json={"model_refs": [], "expected_revision": "0"})
    assert stale.status_code == 409
    assert e.access.get(e.employee)["model_refs"] == ["beta/chat"]
    spec = client._transport.app.openapi()
    assert spec["paths"]["/api/users/{user_id}/models"]["put"]["summary"]


async def test_employee_model_picker_and_default_expose_only_grants(models_api):
    e, client, actor, _ = models_api
    assert len((await client.get("/api/providers/resolved")).json()) == 3
    actor["user"] = User(e.employee, "employee", "user", None)
    assert (await client.get("/api/providers/resolved")).json() == []
    assert (await client.get("/api/providers/active-model")).json() == {
        "provider_name": "",
        "model": "",
    }
    e.access.set(e.employee, ["beta/chat"], "0", actor="admin")
    listed = (await client.get("/api/providers/resolved")).json()
    assert [(row["provider_name"], row["model"]) for row in listed] == [("beta", "chat")]
    assert (await client.get("/api/providers/active-model")).json() == {
        "provider_name": "beta",
        "model": "chat",
    }


async def test_user_edit_saves_model_access_and_rejects_invalid_grants_before_other_writes(
    models_api,
):
    e, client, actor, _ = models_api
    url = f"/api/users/{e.employee}"
    invalid = await client.patch(
        url,
        json={
            "display_name": "wrong",
            "model_access": {"model_refs": ["alpha/disabled"], "expected_revision": "0"},
        },
    )
    assert invalid.status_code == 403
    assert e.services.user_repo.get(e.employee).display_name is None
    saved = await client.patch(
        url,
        json={
            "display_name": "Employee",
            "model_access": {"model_refs": ["alpha/chat"], "expected_revision": "0"},
        },
    )
    assert saved.status_code == 200, saved.text
    assert e.access.get(e.employee)["model_refs"] == ["alpha/chat"]
    actor["user"] = User(e.manager, "manager", "user", None, permissions=["users"])
    forbidden = await client.patch(
        url,
        json={
            "model_access": {
                "model_refs": [],
                "expected_revision": saved.json().get("revision", "0"),
            }
        },
    )
    assert forbidden.status_code == 403


async def test_dashboard_turn_rejects_unassigned_models_before_composer_write(
    model_env, monkeypatch
):
    e = model_env
    monkeypatch.setattr(
        "octop.api.routers.chat.turn.require_agent_row",
        lambda *a, **kw: SimpleNamespace(default_model="alpha/chat"),
    )
    monkeypatch.setattr(
        "octop.infra.users.expert_access.ExpertAccess",
        lambda _: SimpleNamespace(
            assert_use=Mock(),
            assigned_only=lambda _: False,
        ),
    )
    resolve_thread = AsyncMock(return_value=("thread", "session"))
    monkeypatch.setattr("octop.api.routers.chat.turn.resolve_thread_id", resolve_thread)
    threads = SimpleNamespace(update_composer=Mock())
    server = SimpleNamespace(
        services=e.services,
        app_runtime=SimpleNamespace(
            agent_registry=SimpleNamespace(providers=ProviderStore(e.services.provider_repo)),
            gateway=SimpleNamespace(thread_registry=threads),
        ),
    )
    user = User(e.employee, "employee", "user", None)
    with pytest.raises(OctopError) as exc:
        await prepare_dashboard_turn(
            server, agent_id="expert", user=user, turn=ChatTurnBody(text="Hi")
        )
    assert exc.value.code == ErrorCode.MODEL_NOT_ASSIGNED
    resolve_thread.assert_not_awaited()
    e.access.set(e.employee, ["beta/chat"], "0", actor="admin")
    with pytest.raises(OctopError):
        await prepare_dashboard_turn(
            server,
            agent_id="expert",
            user=user,
            turn=ChatTurnBody(text="Hi", default_model="alpha/chat"),
        )
    threads.update_composer.assert_not_called()
    await prepare_dashboard_turn(
        server,
        agent_id="expert",
        user=user,
        turn=ChatTurnBody(text="Hi", default_model="beta/chat"),
    )
    threads.update_composer.assert_called_once_with("thread", model_ref="beta/chat")
