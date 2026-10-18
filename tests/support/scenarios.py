"""Multi-step bootstrap scenarios for integration tests."""

from __future__ import annotations

from typing import Any

import httpx

from octop.infra.server import OctopServer
from tests.support.auth import create_provider, create_user, resolve_user_id


async def bootstrap_boundary_env(
    client: httpx.AsyncClient,
    srv: OctopServer,
    admin_auth: dict[str, str],
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, Any],
]:
    """Admin + alice + bob + shared provider + alice's agent."""
    shared = await create_provider(
        client,
        admin_auth,
        name="shared-openai",
        api_key="k",
    )
    alice_auth = await create_user(client, admin_auth, username="alice")
    bob_auth = await create_user(client, admin_auth, username="bob")
    alice_id = await resolve_user_id(client, admin_auth, "alice")
    bob_id = await resolve_user_id(client, admin_auth, "bob")

    r = await client.post(
        "/api/agents",
        headers=admin_auth,
        json={
            "name": "alices-bot",
            "config": {
                "providers": ["shared-openai"],
                "default_model": "openai:gpt-4o-mini",
            },
        },
    )
    r.raise_for_status()
    row = r.json()
    alice_agent_id = row["agent_id"]

    ctx = {
        "alice_id": alice_id,
        "bob_id": bob_id,
        "alice_agent_id": alice_agent_id,
        "shared_pid": shared["id"],
    }
    return alice_auth, bob_auth, ctx
