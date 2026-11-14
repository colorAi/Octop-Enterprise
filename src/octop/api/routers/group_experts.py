"""Python owns scope/grants; Go owns membership, bindings and message visibility."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import current_user, get_server
from octop.api.routers.communication import request_session
from octop.api.routers.group_expert_models import (
    ExpertCandidates,
    ExpertEnableResult,
    ExpertInvitationResult,
    ExpertListing,
    ExpertRemovalResult,
    ExpertScopeResult,
    ExpertSyncStatus,
    GroupExpertResponse,
)
from octop.infra.db.repos.execution import ExecutionConflict
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User
from octop.infra.users.permissions import user_has_permission

router = APIRouter()


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_agent_id: str = Field(min_length=1, max_length=160)
    source_row_id: int = Field(gt=0)


class Invitation(Identity):
    request_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=0)


class Removal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=100)
    generation: int = Field(gt=0)


class ScopeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=0)
    sources: list[Identity] = Field(max_length=500)


class Enable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=100)
    enabled: bool


async def access(
    request: Request, server: Any, user: User, group: str
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    service = getattr(server, "group_experts", None)
    if service is None or server.role not in {"web", "worker"}:
        raise ExecutionConflict("group_expert_protocol_unavailable")
    organization = "/organization/" in request.url.path
    if organization and (not user.is_admin or not user_has_permission(user, "departments")):
        raise OctopError(ErrorCode.FORBIDDEN, "department administration required")
    session, _ = request_session(request, server)
    await service.bridge.ready(user.id, session, "departments" if organization else "messaging")
    actor = {"actor_id": service.bridge.mapped_id(user.id), "organization": organization}
    info = await service.bridge.group_request(
        "POST", f"/groups/{quote(group, safe='')}/inspect", actor, actor=user.id
    )
    return service, info, actor


@router.get(
    "/groups/{group}/experts",
    response_model=GroupExpertResponse[ExpertListing],
    summary="List group expert bindings and permitted sources",
)
@router.get(
    "/organization/groups/{group}/experts",
    response_model=GroupExpertResponse[ExpertListing],
    summary="Inspect experts as an enterprise administrator",
    description="Requires administrator role and departments permission; does not grant message history access.",
)
async def listing(
    group: str,
    request: Request,
    user: User = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service, info, actor = await access(request, server, user, group)
    if not actor["organization"]:
        await service.reconcile_user(user.id)
        await service.flush(conversation_id=group)
    scope = await service.io.run(service.repo.scope, group)
    sources = await service.io.run(service.repo.available_sources)
    allowed = json.loads(scope["sources_json"]) if scope else []
    if actor["organization"]:
        if info["department_id"] is not None:
            pool = await service.io.run(
                service.services.employee_resource_repo.pool, info["department_id"]
            )
            sources = [v for v in sources if v["source_agent_id"] in pool["expert_ids"]]
    else:
        sources = [
            v
            for v in sources
            if {"source_agent_id": v["source_agent_id"], "source_row_id": v["source_row_id"]}
            in allowed
        ]
    candidates = []
    for source in sources:
        can_call = await service.io.run(
            service.repo.can_call,
            user=user.id,
            conversation_id=group,
            source=source["source_agent_id"],
            source_row=source["source_row_id"],
        )
        candidates.append(
            {k: v for k, v in source.items() if k != "authorization_versions"}
            | {"can_call": can_call}
        )
    for binding in info["items"]:
        binding["can_call"] = binding["state"] == "active" and await service.io.run(
            service.repo.can_call,
            user=user.id,
            conversation_id=group,
            source=binding["source_agent_id"],
            source_row=binding["source_row_id"],
            binding_scope_revision=binding["scope_revision"],
        )
    return {
        "data": {
            **{k: v for k, v in info.items() if k != "sources"},
            "candidates": candidates,
            "scope_sources": allowed,
            "scope_revision": scope["revision"] if scope else 0,
            "syncing": await service.io.run(service.repo.syncing, conversation_id=group),
            "ready": info.get("ready", False),
            "can_configure": actor["organization"],
        }
    }


@router.get(
    "/groups/{group}/experts/candidates",
    response_model=GroupExpertResponse[ExpertCandidates],
    summary="List experts the current member may mention",
)
async def candidates(
    group: str,
    request: Request,
    user: User = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    value = (await listing(group, request, user, server))["data"]
    return {
        "data": {
            "items": [
                v
                for v in value["items"]
                if value["ready"] and value["experts_enabled"] and v["can_call"]
            ]
        }
    }


@router.post(
    "/groups/{group}/experts",
    response_model=GroupExpertResponse[ExpertInvitationResult],
    summary="Invite one expert as group owner or administrator",
)
@router.post(
    "/organization/groups/{group}/experts",
    response_model=GroupExpertResponse[ExpertInvitationResult],
    summary="Invite an expert and add its source to the group scope",
)
async def invite(
    group: str,
    body: Invitation,
    request: Request,
    user: User = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service, info, actor = await access(request, server, user, group)
    if not info["can_manage"]:
        raise OctopError(ErrorCode.FORBIDDEN, "group administration required")
    value = await service.io.run(
        service.repo.prepare_invitation,
        actor=user.id,
        request_id=body.request_id,
        conversation_id=group,
        department_id=info["department_id"],
        source=body.source_agent_id,
        source_row=body.source_row_id,
        revision=body.revision,
        organization=actor["organization"],
    )
    await service.flush(
        conversation_id=group, source=body.source_agent_id, source_row=body.source_row_id
    )
    if await service.io.run(
        service.repo.syncing,
        conversation_id=group,
        source=body.source_agent_id,
        source_row=body.source_row_id,
    ):
        raise ExecutionConflict("group_expert_authorization_syncing")
    await service.runtime()
    result = await service.bridge.group_request(
        "POST",
        f"/groups/{quote(group, safe='')}/bindings",
        {**value, **actor, "request_id": body.request_id},
        actor=user.id,
    )
    return {"data": result}


@router.delete(
    "/groups/{group}/experts/{binding}",
    response_model=GroupExpertResponse[ExpertRemovalResult],
    summary="Remove the specified expert binding generation",
)
@router.delete(
    "/organization/groups/{group}/experts/{binding}",
    response_model=GroupExpertResponse[ExpertRemovalResult],
    summary="Remove an expert as an enterprise administrator",
)
async def remove(
    group: str,
    binding: str,
    body: Removal,
    request: Request,
    user: User = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service, _, actor = await access(request, server, user, group)
    return {
        "data": await service.bridge.group_request(
            "DELETE",
            f"/groups/{quote(group, safe='')}/bindings/{quote(binding, safe='')}",
            {**body.model_dump(), **actor},
            actor=user.id,
        )
    }


@router.put(
    "/organization/groups/{group}/expert-scope",
    response_model=GroupExpertResponse[ExpertScopeResult],
    summary="Replace the permitted group expert sources",
)
async def scope_update(
    group: str,
    body: ScopeUpdate,
    request: Request,
    user: User = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service, info, _ = await access(request, server, user, group)
    result = await service.io.run(
        service.repo.save_scope,
        actor=user.id,
        request_id=body.request_id,
        conversation_id=group,
        department_id=info["department_id"],
        sources=[v.model_dump() for v in body.sources],
        expected_revision=body.revision,
    )
    await service.flush()
    return {
        "data": {
            "revision": result["revision"],
            "syncing": await service.io.run(service.repo.syncing),
        }
    }


@router.put(
    "/organization/groups/{group}/experts-enabled",
    response_model=GroupExpertResponse[ExpertEnableResult],
    summary="Enable or disable group expert admission",
)
async def enable(
    group: str,
    body: Enable,
    request: Request,
    user: User = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service, _, actor = await access(request, server, user, group)
    if body.enabled and not (await service.runtime())["ready"]:
        raise ExecutionConflict("group_expert_protocol_unavailable")
    await service.flush()
    return {
        "data": await service.bridge.group_request(
            "PUT",
            f"/groups/{quote(group, safe='')}/enabled",
            {**body.model_dump(), **actor},
            actor=user.id,
        )
    }


@router.get(
    "/organization/expert-authorization-status",
    response_model=GroupExpertResponse[ExpertSyncStatus],
    summary="Check whether group expert authorization changes are confirmed",
)
async def authorization_status(
    request: Request, user: User = Depends(current_user), server: Any = Depends(get_server)
) -> dict[str, Any]:
    if not user.is_admin:
        raise OctopError(ErrorCode.FORBIDDEN, "administrator required")
    service = getattr(server, "group_experts", None)
    return {"data": {"syncing": bool(service and await service.io.run(service.repo.syncing))}}
