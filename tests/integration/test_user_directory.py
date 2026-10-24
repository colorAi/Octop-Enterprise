"""Paged management reads must stay lightweight and preserve access boundaries."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from tests.support.auth import create_user
from tests.support.fakes import FakeHarnessAgent


async def test_directory_pagination_search_disabled_and_counts(env, monkeypatch):
    client, server, admin = env
    repo = server.services.user_repo
    first = repo.create(username="staff_a", display_name="Finance", role="user")
    second = repo.create(username="staff_b", role="user")
    repo.set_disabled(second, True)
    repo.create(username="staff-c", display_name="100% !_", role="user")
    server.services.agent_repo.create(agent_id="one", user_id=first, name="One")
    server.services.agent_repo.create(agent_id="two", user_id=first, name="Two")

    def no_probe(*args, **kwargs):
        raise AssertionError("Directory must not probe or load expert runtimes")

    registry = server.app_runtime.agent_registry
    monkeypatch.setattr(registry, "is_bootstrapped", no_probe)
    monkeypatch.setattr(registry, "get_agent", no_probe)
    response = await client.get("/api/users/page?page_size=1&search=staff_", headers=admin)
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 2
    assert page["items"][0]["id"] == first
    assert page["items"][0]["agent_count"] == 2
    assert "password_hash" not in page["items"][0]
    page2 = (
        await client.get("/api/users/page?page=2&page_size=1&search=staff_", headers=admin)
    ).json()
    assert page2["items"][0]["id"] == second
    assert page2["items"][0]["disabled"] is True
    for search, total in (("FINANCE", 1), ("100% !_", 1), ("absent", 0), ("' OR 1=1 --", 0)):
        result = (
            await client.get("/api/users/page", headers=admin, params={"search": search})
        ).json()
        assert result["total"] == total
        assert len(result["items"]) == total
    legacy = await client.get("/api/users", headers=admin)
    assert isinstance(legacy.json(), list)
    assert len(legacy.json()) == 4


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101", "search=" + "a" * 201])
async def test_directory_rejects_unbounded_parameters(env, query):
    client, _, admin = env
    assert (await client.get("/api/users/page?" + query, headers=admin)).status_code == 422


async def test_directory_and_owner_filter_permissions(env):
    client, server, admin = env
    employee = await create_user(client, admin, username="employee", permissions=[])
    manager = await create_user(client, admin, username="manager", permissions=["users"])
    owner = server.services.user_repo.get_by_username("employee").id
    server.services.agent_repo.create(agent_id="employee-expert", user_id=owner, name="Employee")
    assert (await client.get("/api/users/page")).status_code == 401
    assert (await client.get("/api/users/page", headers=employee)).status_code == 403
    assert (await client.get("/api/users/page", headers=manager)).status_code == 200
    path = f"/api/agents?scope=all&owner_user_id={owner}"
    assert (await client.get(path, headers=employee)).status_code == 403
    agents = await client.get(path, headers=manager)
    assert agents.status_code == 200
    assert [row["agent_id"] for row in agents.json()] == ["employee-expert"]
    assert (
        await client.get(f"/api/agents?owner_user_id={owner}", headers=employee)
    ).status_code == 403


async def test_slow_directory_io_does_not_block_permissions(env, monkeypatch):
    client, server, admin = env
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original = server.services.user_repo.page

    def slow(**kwargs):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return original(**kwargs)

    monkeypatch.setattr(server.services.user_repo, "page", slow)
    listing = asyncio.create_task(client.get("/api/users/page", headers=admin))
    try:
        await asyncio.wait_for(started.wait(), 2)
        response = await asyncio.wait_for(client.get("/api/users/permissions", headers=admin), 1)
        assert response.status_code == 200
        assert not listing.done()
    finally:
        release.set()
        assert (await listing).status_code == 200


async def test_agent_listing_never_probes_workspaces(env_with_provider, monkeypatch):
    client, server, admin = env_with_provider
    registry = server.app_runtime.agent_registry

    def no_probe(*args):
        raise AssertionError("List must read a lifecycle projection, not Docker/workspace")

    monkeypatch.setattr(registry, "is_bootstrapped", no_probe)
    monkeypatch.setattr(registry, "get_agent", no_probe)
    for scope in ("mine", "all"):
        response = await client.get("/api/agents", headers=admin, params={"scope": scope})
        assert response.status_code == 200
        assert response.json()
        assert all(isinstance(row["bootstrap_pending"], bool) for row in response.json())


async def test_bootstrap_display_updates_after_lifecycle_callback(env_with_provider, monkeypatch):
    client, server, admin = env_with_provider
    monkeypatch.setattr(FakeHarnessAgent, "is_bootstrapped", lambda self: False)
    created = await client.post("/api/agents", headers=admin, json={"name": "Needs onboarding"})
    assert created.status_code == 201
    agent_id = created.json()["agent_id"]
    registry = server.app_runtime.agent_registry
    agent = registry.get_agent(agent_id)
    assert registry.is_bootstrapped_for_display(agent_id) is False
    agent._config = SimpleNamespace(system_prompt="", memory=None)
    agent.on_bootstrap_complete()
    assert registry.is_bootstrapped_for_display(agent_id) is True
    # Authoritative checks still inspect the workspace rather than the display projection.
    assert registry.is_bootstrapped(agent_id) is False
    response = await client.get("/api/agents?scope=all", headers=admin)
    assert (
        next(row for row in response.json() if row["agent_id"] == agent_id)["bootstrap_pending"]
        is False
    )


async def test_directory_and_credential_openapi_docs(env):
    client, _, _ = env
    # The fixture disables public API docs; inspect the same generated schema.
    paths = client._octop_app.openapi()["paths"]
    for path in ("/api/providers", "/api/voice/providers", "/api/users/page", "/api/agents"):
        assert paths[path]["get"]["summary"]
        assert paths[path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
