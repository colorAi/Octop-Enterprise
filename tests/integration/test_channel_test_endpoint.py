"""tests/integration/test_channel_test_endpoint.py — channel probe."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch


async def test_channel_test_ok(env_with_channel: Any) -> None:
    c, srv, auth, agent_id, cid = env_with_channel
    with patch.object(
        srv.app_runtime.gateway, "probe_channel", new=AsyncMock(return_value={"ok": True})
    ):
        r = await c.post(
            f"/api/agents/{agent_id}/channels/{cid}/test",
            headers=auth,
        )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


async def test_channel_test_start_fails(env_with_channel: Any) -> None:
    c, srv, auth, agent_id, cid = env_with_channel
    with patch.object(
        srv.app_runtime.gateway,
        "probe_channel",
        new=AsyncMock(return_value={"ok": False, "error": "connect refused"}),
    ):
        r = await c.post(
            f"/api/agents/{agent_id}/channels/{cid}/test",
            headers=auth,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "connect refused" in body["error"]


async def test_channel_test_missing_channel(env_with_channel: Any) -> None:
    c, _srv, auth, agent_id, _cid = env_with_channel
    r = await c.post(
        f"/api/agents/{agent_id}/channels/01HXX/test",
        headers=auth,
    )
    assert r.status_code == 404
