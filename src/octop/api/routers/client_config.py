"""Company configuration notifications, separate from upstream self-update."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server, require_admin
from octop.infra.users.client_config import config_revision, publish_release, read_release
from octop.infra.users.permissions import effective_permissions

router = APIRouter(prefix="/client-config", tags=["client-config"])


class PublishBody(BaseModel):
    message: str = Field(default="", max_length=500)


async def notify_config_changed(server: Any, user_id: int | None = None) -> None:
    runtime = getattr(server, "app_runtime", None)
    if runtime is None:
        return
    hub = runtime.gateway.ws_hub
    user_ids = [user_id] if user_id is not None else [u.id for u in server.user_manager.list()]
    await asyncio.gather(
        *(hub.push_to_user(uid, {"type": "client_config_changed"}) for uid in user_ids)
    )


@router.get("/status")
async def status(
    response: Response,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    permissions = effective_permissions(user)
    release = read_release(server.services.settings_repo)
    policies = server.services.user_policy_repo.list_for_user(user.id)
    return {
        "user_id": user.id,
        "role": user.role,
        "permissions": permissions,
        "revision": config_revision(user.role, permissions, policies, release),
        "message": release.get("message", ""),
        "published_at": release.get("published_at"),
    }


@router.post("/publish")
async def publish(
    body: PublishBody,
    actor: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    release = publish_release(server.services.settings_repo, body.message.strip())
    server.services.audit_repo.write(
        actor=actor.username,
        action="client_config.publish",
        target=release["id"],
    )
    await notify_config_changed(server)
    return release
