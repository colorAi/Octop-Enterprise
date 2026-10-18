"""Integration tests for GET /api/agents/{agent_id}/terminal/context."""

from __future__ import annotations

from typing import Any

import httpx
import pytest


@pytest.fixture
async def env(env_terminal):
    yield env_terminal


@pytest.mark.anyio
async def test_context_returns_expected_fields(env: Any) -> None:
    _srv, app, tok, agent_id = env
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
    ) as c:
        resp = await c.get(
            f"/api/agents/{agent_id}/terminal/context",
            headers={"Authorization": f"Bearer {tok}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "os",
        "shell",
        "hostname",
        "username",
        "workspace_dir",
        "agent_id",
        "agent_name",
        "terminal_supported",
        "terminal_unsupported_reason",
    ):
        assert key in data, f"missing field: {key}"
    assert data["agent_id"] == agent_id
    assert isinstance(data["terminal_supported"], bool)


@pytest.mark.anyio
async def test_context_requires_auth(env: Any) -> None:
    _srv, app, _tok, agent_id = env
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
    ) as c:
        resp = await c.get(f"/api/agents/{agent_id}/terminal/context")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_context_unknown_agent(env: Any) -> None:
    _srv, app, tok, _aid = env
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
    ) as c:
        resp = await c.get(
            "/api/agents/nonexistent/terminal/context",
            headers={"Authorization": f"Bearer {tok}"},
        )
    assert resp.status_code == 404
