"""Assigned expert portraits and welcome cards come from published display data."""

import json
from unittest.mock import Mock

import httpx
import pytest
from tests.unit.users.test_employee_resources import resources as resources
from tests.unit.users.test_managed_resource_sync import managed

from octop.api.deps import current_user
from octop.api.routers import agents
from octop.api.routers.chat import routes
from octop.infra.errors import OctopError
from octop.infra.users.identity import User
from octop.infra.users.source_publication import SourcePublication


@pytest.mark.parametrize("uploaded", [False, True])
async def test_cold_expert_display_matches_source_and_checks_revocation(
    resources, monkeypatch, uploaded
):
    e = resources
    uid, aid, app = await managed(e)
    workspace = e.agents["source"].workspace
    manifest = {
        "welcome_message": {"zh": "专属欢迎语", "en": "Expert welcome"},
        "quick_prompts": [
            {
                "title": {"zh": "原来的卡片", "en": "Original card"},
                "prompt": {"zh": "分析示例", "en": "Analyze example"},
                "icon_name": "code",
                "color": "#123456",
            }
        ],
        "task_examples": {"zh": ["原来的示例"], "en": ["Original example"]},
        "private_admin_data": "must not be published",
    }
    await workspace.aupload_many([(".octop/manifest.json", json.dumps(manifest).encode())])
    image = b"\x89PNG\r\n\x1a\n" + b"avatar-content"
    if uploaded:
        await workspace.aupload_many([(".octop/avatar.png", image)])
    e.services.agent_repo.update_config(
        "source",
        icon_url="/api/agents/source/avatar"
        if uploaded
        else "/experts/avatars/ai-coding-coach.svg",
        template_name="ai-coding-coach",
        icon_name="code",
        color="#123456",
    )
    e.service.publication = SourcePublication(e.service)
    await e.service.publication.refresh()
    published = await e.service.definition("source")
    assert "private_admin_data" not in published["presentation"]
    app.include_router(agents.router, prefix="/api/agents")
    app.include_router(routes.router, prefix="/api")
    registry = e.server.app_runtime.agent_registry
    e.server.expert_catalog = None
    registry.is_bootstrapped_for_display = lambda _: True
    registry.is_bootstrapped = lambda _: True
    # Every display endpoint must work without the source/employee runtime.
    unavailable = Mock(side_effect=AssertionError("display must not start a sandbox"))
    monkeypatch.setattr(registry, "workspace_for_agent", unavailable)
    monkeypatch.setattr(registry, "get_agent", unavailable)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        listing = await client.get("/api/agents")
        assert listing.status_code == 200, listing.text
        profile = listing.json()[0]
        assert profile["template_name"] == "ai-coding-coach"
        assert profile["icon_name"] == "code"
        expected_icon = (
            f"/api/agents/{aid}/avatar?v=" if uploaded else "/experts/avatars/ai-coding-coach.svg"
        )
        assert profile["icon_url"].startswith(expected_icon)
        detail = await client.get(f"/api/agents/{aid}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["icon_url"] == profile["icon_url"]
        welcome = await client.get(f"/api/agents/{aid}/chat/welcome")
        assert welcome.status_code == 200, welcome.text
        assert welcome.json()["welcome_message"] == manifest["welcome_message"]
        assert welcome.json()["quick_prompts"][0]["title"] == manifest["quick_prompts"][0]["title"]
        assert welcome.json()["task_examples"]["zh"][0] == "原来的示例"
        if uploaded:
            for identity in (aid, "source"):
                avatar = await client.get(f"/api/agents/{identity}/avatar")
                assert avatar.status_code == 200, avatar.text
                assert avatar.content == image
        with e.pool.transaction() as conn:
            conn.execute("DELETE FROM expert_assignments WHERE user_id=?", (uid,))
        assert (await client.get(f"/api/agents/{aid}/avatar")).status_code == 403
        assert (await client.get(f"/api/agents/{aid}/chat/welcome")).status_code == 403
    unavailable.assert_not_called()


async def test_admin_can_inspect_employee_presentation_but_cannot_execute(resources, monkeypatch):
    e = resources
    _, aid, app = await managed(e)
    image = b"\x89PNG\r\n\x1a\n" + b"administrator-preview"
    await e.agents["source"].workspace.aupload_many([(".octop/avatar.png", image)])
    e.service.publication = SourcePublication(e.service)
    await e.service.publication.refresh()
    app.include_router(agents.router, prefix="/api/agents")
    app.include_router(routes.router, prefix="/api")
    e.server.expert_catalog = None
    registry = e.server.app_runtime.agent_registry
    registry.is_bootstrapped_for_display = lambda _: True
    registry.is_bootstrapped = lambda _: True
    unavailable = Mock(side_effect=AssertionError("display must not start a sandbox"))
    monkeypatch.setattr(registry, "workspace_for_agent", unavailable)
    monkeypatch.setattr(registry, "get_agent", unavailable)
    app.dependency_overrides[current_user] = lambda: User(e.admin, "administrator", "admin", None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        root = f"/api/agents/{aid}"
        for path in (root, root + "/chat/welcome", root + "/avatar"):
            response = await client.get(path)
            assert response.status_code == 200, response.text
        assert response.content == image
        with pytest.raises(OctopError, match="resource_not_assigned"):
            e.service.access.assert_use(e.admin, aid)

        other = e.employees[1]
        app.dependency_overrides[current_user] = lambda: User(other, "employee-1", "user", None)
        for path in (root, root + "/chat/welcome", root + "/avatar"):
            assert (await client.get(path)).status_code == 403

        app.dependency_overrides[current_user] = lambda: User(
            e.admin, "administrator", "admin", None
        )
        with e.pool.transaction() as conn:
            conn.execute("UPDATE users SET disabled=1 WHERE id=?", (e.admin,))
        for path in (root, root + "/chat/welcome", root + "/avatar"):
            assert (await client.get(path)).status_code == 403
    unavailable.assert_not_called()
