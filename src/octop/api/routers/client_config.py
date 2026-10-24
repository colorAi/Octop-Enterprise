"""Company configuration notifications, separate from upstream self-update."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from octop.api.common.reads import management_read
from octop.api.deps import current_user, get_server, require_admin
from octop.infra.users.client_config import config_revision, publish_release, read_config_state
from octop.infra.users.permissions import effective_permissions

router = APIRouter(prefix="/client-config", tags=["client-config"])


class PublishBody(BaseModel):
    message: str = Field(default="", max_length=500)


async def notify_config_changed(server: Any, user_id: int | None = None) -> None:
    runtime = getattr(server, "app_runtime", None)
    if runtime is None:
        return
    hub = runtime.gateway.ws_hub
    user_ids = (
        [user_id]
        if user_id is not None
        else [u.id for u in await management_read(server, server.user_manager.list)]
    )
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
    resources = getattr(server, "employee_resources", None)
    release, policies, expert_access = await management_read(
        server, read_config_state, server.services, user.id, resources
    )
    resource_update_pending = False
    if expert_access and resources is not None and not user.is_admin:
        state = expert_access["selection"]
        if state and state["mode"] == "assigned_only":
            revision = await resources.revision(user.id)
            # Reconciliation may have revoked assignments while checking membership.
            release, policies, expert_access = await management_read(
                server, read_config_state, server.services, user.id, resources
            )
            assert expert_access is not None
            expert_access["content_revision"] = revision
            resource_update_pending = (
                await management_read(
                    server,
                    server.services.settings_repo.get,
                    f"employee_resources.applied.{user.id}",
                )
                != revision
            )
    return {
        "user_id": user.id,
        "role": user.role,
        "permissions": permissions,
        "expert_access_mode": (
            expert_access["selection"]["mode"]
            if expert_access and expert_access["selection"] and not user.is_admin
            else "legacy"
        ),
        "revision": config_revision(user.role, permissions, policies, release, expert_access),
        "message": release.get("message", ""),
        "published_at": release.get("published_at"),
        "resource_update_pending": resource_update_pending,
    }


@router.post("/apply", summary="Apply the employee's latest assigned experts and skills")
async def apply_config(
    response: Response,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    resources = getattr(server, "employee_resources", None)
    if resources is not None and not user.is_admin:
        revision = await resources.revision(user.id)
        await resources.refresh_user(user.id)
        if revision != await resources.revision(user.id):
            from octop.infra.users.expert_access import denied

            raise denied("employee_resources_changed", 409)
        await management_read(
            server,
            server.services.settings_repo.set,
            f"employee_resources.applied.{user.id}",
            revision,
        )
    return await status(response, user, server)


@router.post("/publish")
async def publish(
    body: PublishBody,
    actor: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    release = await management_read(
        server, publish_release, server.services.settings_repo, body.message.strip()
    )
    await management_read(
        server,
        server.services.audit_repo.write,
        actor=actor.username,
        action="client_config.publish",
        target=release["id"],
    )
    await notify_config_changed(server)
    return release
