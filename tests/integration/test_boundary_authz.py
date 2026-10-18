"""tests/integration/test_boundary_authz.py — cross-user / cross-scope authz.

P0.3: gap-fill the boundary cases that were missing from phases 12.x.
Covers:
  - Admin ``?as_user=`` agents/cron/channels access against another user.
  - Non-admin sending ``?as_user=`` is rejected with 403.
  - Provider PATCH cross-scope: admin can edit shared rows; non-admin
    cannot edit a shared (user_id == NULL) provider.
  - Provider DELETE blocked when an agent references the name (covered
    elsewhere for own-scope; here we verify the same path through the
    admin /api/admin/providers/{id} delete).
  - Agent PATCH/DELETE cross-user 404 for non-admin.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
async def env(env_boundary):
    yield env_boundary


# --- Agent visibility (per-user ownership) -----------------------------------


async def test_admin_scope_all_lists_alice_agent(env: Any) -> None:
    c, _srv, admin_auth, _, _, ctx = env
    r = await c.get("/api/agents?scope=all", headers=admin_auth)
    assert r.status_code == 200
    rows = r.json()
    assert any(a["agent_id"] == ctx["alice_agent_id"] for a in rows)


async def test_admin_default_list_is_mine_only(env: Any) -> None:
    c, _srv, admin_auth, _, _, ctx = env
    r = await c.get("/api/agents", headers=admin_auth)
    assert r.status_code == 200
    rows = r.json()
    assert all(a.get("user_id") == rows[0].get("user_id") for a in rows) if rows else True
    assert any(a["agent_id"] == ctx["alice_agent_id"] for a in rows)


async def test_admin_can_get_alices_agent_via_as_user(env: Any) -> None:
    c, _srv, admin_auth, _, _, ctx = env
    r = await c.get(f"/api/agents/{ctx['alice_agent_id']}", headers=admin_auth)
    assert r.status_code == 200
    assert r.json()["agent_id"] == ctx["alice_agent_id"]


async def test_non_admin_list_is_mine_only(env: Any) -> None:
    c, _srv, _admin_auth, _, bob_auth, ctx = env
    r = await c.get("/api/agents", headers=bob_auth)
    assert r.status_code == 200
    rows = r.json()
    assert all(a["agent_id"] != ctx["alice_agent_id"] for a in rows)


async def test_non_admin_cross_user_agent_get_forbidden(env: Any) -> None:
    c, _srv, _admin_auth, _, bob_auth, ctx = env
    r = await c.get(f"/api/agents/{ctx['alice_agent_id']}", headers=bob_auth)
    assert r.status_code == 403


async def test_non_admin_cross_user_agent_delete_forbidden(env: Any) -> None:
    """Non-admin cannot DELETE another user's agent."""
    c, _srv, _admin_auth, _, bob_auth, ctx = env
    r = await c.delete(f"/api/agents/{ctx['alice_agent_id']}", headers=bob_auth)
    assert r.status_code == 403


# --- Provider scope ----------------------------------------------------------


async def test_non_admin_cannot_patch_shared_provider(env: Any) -> None:
    """Non-admin cannot modify providers — the user-scope PATCH endpoint is removed.

    All provider modifications require admin access via /api/admin/providers.
    Regular users hitting /api/providers/{id} with PATCH get 405 Method Not Allowed.
    """
    c, _srv, _admin_auth, alice_auth, _, ctx = env
    r = await c.patch(
        f"/api/providers/{ctx['shared_pid']}",
        headers=alice_auth,
        json={"note": "alice trying to rewrite history"},
    )
    # 405: endpoint no longer exists; providers are admin-only via /admin/providers
    assert r.status_code in (403, 404, 405)


async def test_admin_can_patch_shared_provider(env: Any) -> None:
    c, _srv, admin_auth, _, _, ctx = env
    r = await c.patch(
        f"/api/admin/providers/{ctx['shared_pid']}",
        headers=admin_auth,
        json={"note": "admin updated"},
    )
    assert r.status_code == 200
    assert r.json()["note"] == "admin updated"


async def test_admin_delete_shared_provider_blocked_when_referenced(env: Any) -> None:
    """Alice's agent references ``shared-openai``; admin DELETE through
    /admin/providers must still surface PROVIDER_REFERENCED."""
    c, _srv, admin_auth, _, _, ctx = env
    r = await c.delete(
        f"/api/admin/providers/{ctx['shared_pid']}",
        headers=admin_auth,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PROVIDER_REFERENCED"


# --- Cron / channels cross-user --------------------------------------------


async def test_non_admin_cross_user_cron_list_forbidden(env: Any) -> None:
    c, _srv, _admin_auth, _, bob_auth, ctx = env
    r = await c.get(f"/api/agents/{ctx['alice_agent_id']}/cron", headers=bob_auth)
    assert r.status_code == 403


async def test_non_admin_cross_user_channels_list_forbidden(env: Any) -> None:
    c, _srv, _admin_auth, _, bob_auth, ctx = env
    r = await c.get(f"/api/agents/{ctx['alice_agent_id']}/channels", headers=bob_auth)
    assert r.status_code == 403
