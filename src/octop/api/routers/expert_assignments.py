"""Actual administrators assign existing experts; employees never submit source definitions."""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from octop.api.common.reads import management_read
from octop.api.deps import get_server, require_admin
from octop.api.routers.client_config import notify_config_changed
from octop.api.routers.communication import request_session
from octop.infra.errors import OctopError
from octop.infra.users.expert_access import ExpertAccess, denied
from octop.infra.users.resource_directory import ResourceDirectory
from octop.infra.utils.asyncio import offload

router = APIRouter()
logger = logging.getLogger(__name__)


class SourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_user_id: int = Field(gt=0, description="Explicitly selected administrator account.")
    expected_revision: int = Field(ge=0)


class AssignmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expert_ids: list[str] = Field(
        max_length=500, description="Complete replacement; [] revokes all."
    )
    mode: Literal["legacy", "assigned_only"] = "assigned_only"
    expected_revision: int = Field(ge=0)
    expected_source_revision: int = Field(ge=0)


class AssignmentState(BaseModel):
    id: int
    mode: Literal["legacy", "assigned_only"]
    revision: int
    expert_ids: list[str]


class SourceState(BaseModel):
    source_user_id: int | None
    revision: int


class Account(BaseModel):
    id: int
    username: str
    display_name: str | None


class Candidate(BaseModel):
    agent_id: str
    name: str
    description: str | None
    skill_ids: list[str] = Field(default_factory=list)
    available: bool = True


class SkillCandidate(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    category: str
    kind: str
    source_name: str
    expert_ids: list[str]


class ResourcePool(BaseModel):
    scope_id: str
    revision: int
    expert_ids: list[str]
    skill_ids: list[str]


class Employee(AssignmentState, Account):
    disabled: bool
    communication_user_id: str | None = None
    skill_ids: list[str] = Field(default_factory=list)
    scope_id: str = "unassigned"
    membership_revision: int = 0
    membership_status: Literal["current", "changed", "pending", "unavailable"] = "current"


class Overview(BaseModel):
    source: SourceState
    administrators: list[Account]
    experts: list[Candidate]
    employees: list[Employee]
    skills: list[SkillCandidate] = Field(default_factory=list)
    defaults: list[str] = Field(default_factory=list)
    pools: list[ResourcePool] = Field(default_factory=list)


class Catalog(BaseModel):
    source: SourceState
    experts: list[Candidate]
    skills: list[SkillCandidate]
    defaults: list[str]


@router.get(
    "/directory",
    response_model=Overview,
    summary="Read saved employee assignments without scanning resource files",
    description="Memberships are a read-only batch projection. Saves and execution recheck "
    "the current authority; use the employee detail endpoint before opening an editor.",
)
async def directory(
    response: Response, actor: Any = Depends(require_admin()), server: Any = Depends(get_server)
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    reader = ResourceDirectory(server)
    data = await management_read(server, reader.snapshot, actor.id)
    return await reader.memberships(actor.id, data)


@router.get(
    "/catalog", response_model=Catalog, summary="Read expert and skill candidates separately"
)
async def catalog(
    response: Response, actor: Any = Depends(require_admin()), server: Any = Depends(get_server)
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    access = ExpertAccess(server.services)
    await management_read(server, access.require_admin, actor.id)
    source = await management_read(server, access.repo.source)
    resources = getattr(server, "employee_resources", None)
    if resources is None:
        data = await management_read(server, ResourceDirectory(server).snapshot, actor.id)
        return {"source": data["source"], "experts": data["experts"], "skills": [], "defaults": []}
    data = resources.public_catalog(await resources.catalog())
    if source != await management_read(server, access.repo.source):
        raise denied("policy_revision_conflict", 409)
    return {"source": source, **data}


@router.get(
    "/users/{user_id}",
    response_model=Employee,
    summary="Reconcile one employee with the current department before editing",
)
async def employee_detail(
    user_id: int,
    response: Response,
    actor: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    reader = ResourceDirectory(server)
    await management_read(server, reader.employee, actor.id, user_id)
    resources = getattr(server, "employee_resources", None)
    if resources is not None:
        await resources.synchronize(user_id)
    return await management_read(server, reader.employee, actor.id, user_id)


@router.get("", response_model=Overview, summary="Read administrator expert assignments")
async def overview(
    response: Response, actor: Any = Depends(require_admin()), server: Any = Depends(get_server)
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    access = ExpertAccess(server.services)
    await offload(access.require_admin, actor.id)
    source = await offload(access.repo.source)
    accounts = await offload(server.services.user_repo.list, include_disabled=True)
    administrator_ids = {u.id for u in accounts if u.role == "admin" and not u.disabled}
    experts = []
    if source["source_user_id"] in administrator_ids:
        experts = [
            {"agent_id": r.agent_id, "name": r.name, "description": r.description}
            for r in await offload(
                server.services.agent_repo.list_by_user, source["source_user_id"]
            )
            if r.enabled and r.kind == "expert"
        ]
    bridge = getattr(server, "communication", None)
    resources = getattr(server, "employee_resources", None)
    catalog = await resources.catalog() if resources is not None else None
    if catalog is not None:
        experts = catalog["experts"]
    employees = []
    for user in accounts:
        if user.role == "admin":
            continue
        state = await offload(access.repo.state, user.id)
        if resources is not None:
            await resources.synchronize(user.id)
            state = await offload(resources.state, user.id)
        employees.append(
            {
                **(state or {}),
                "username": user.username,
                "display_name": user.display_name,
                "disabled": bool(user.disabled),
                "communication_user_id": bridge.mapped_id(user.id)
                if bridge and bridge.enabled
                else None,
            }
        )
    return {
        "source": source,
        "administrators": [
            {"id": u.id, "username": u.username, "display_name": u.display_name}
            for u in accounts
            if u.id in administrator_ids
        ],
        "experts": experts,
        "employees": employees,
        "skills": resources.public_catalog(catalog)["skills"] if resources is not None else [],
        "defaults": catalog["defaults"] if catalog else [],
        "pools": await offload(resources.repo.pools) if resources is not None else [],
    }


@router.put("/source", response_model=SourceState, summary="Select the expert source administrator")
async def source(
    body: SourceBody, actor: Any = Depends(require_admin()), server: Any = Depends(get_server)
) -> dict[str, Any]:
    state = await offload(
        ExpertAccess(server.services).configure_source,
        actor.id,
        body.source_user_id,
        body.expected_revision,
    )
    await notify_config_changed(server)
    return dict(state)


@router.put(
    "/users/{user_id}",
    response_model=AssignmentState,
    summary="Replace an employee's allowed experts",
    description="Requires the current source and employee revisions. Empty selection revokes all. "
    "Legacy accounts change mode only when explicitly saved; personal history is retained.",
)
async def save(
    user_id: int,
    body: AssignmentBody,
    actor: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    access = ExpertAccess(server.services)
    resources = getattr(server, "employee_resources", None)
    if resources is not None:
        membership = await resources.synchronize(user_id)
        state = (
            await resources.save_users(
                actor.id,
                {
                    "expected_source_revision": body.expected_source_revision,
                    "employees": [
                        {
                            "id": user_id,
                            "mode": body.mode,
                            "expert_ids": body.expert_ids,
                            "skill_ids": await offload(resources.repo.skills, user_id)
                            if body.mode == "assigned_only"
                            else [],
                            "expected_revision": body.expected_revision,
                            "scope_id": membership["scope_id"],
                            "membership_revision": membership["revision"],
                        }
                    ],
                },
            )
        )[0]
    else:
        state = await offload(
            access.save,
            actor.id,
            user_id,
            body.expert_ids,
            body.mode,
            body.expected_revision,
            body.expected_source_revision,
        )
    runtime = getattr(server, "app_runtime", None)
    processor = getattr(getattr(runtime, "gateway", None), "processor", None)
    if processor is not None:
        for row in await offload(server.services.agent_repo.list_by_user, user_id):
            try:
                await offload(access.assert_use, user_id, row.agent_id)
            except OctopError:
                for thread in await offload(
                    server.services.thread_repo.list_by_agent_user,
                    agent_id=row.agent_id,
                    user_id=user_id,
                    limit=10000,
                ):
                    try:
                        processor.cancel_agent_stream(row.agent_id, thread.thread_id)
                    except Exception:
                        logger.warning(
                            "expert assignment saved; live cancellation failed", exc_info=True
                        )
    await notify_config_changed(server, user_id)
    return dict(state)


class PoolBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expert_ids: list[str] = Field(max_length=500)
    skill_ids: list[str] = Field(max_length=1000)
    expected_revision: int = Field(ge=0)
    expected_source_revision: int = Field(ge=0)


class EmployeeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(gt=0)
    mode: Literal["legacy", "assigned_only"] = "assigned_only"
    expert_ids: list[str] = Field(max_length=500)
    skill_ids: list[str] = Field(max_length=1000)
    expected_revision: int = Field(ge=0)
    scope_id: str = Field(min_length=1, max_length=200)
    membership_revision: int = Field(ge=0)


class BatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employees: list[EmployeeSelection] = Field(min_length=1, max_length=200)
    expected_source_revision: int = Field(ge=0)


class DefaultsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_ids: list[str] = Field(max_length=1000)
    expected_source_revision: int = Field(ge=0)


class BasicBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department_id: str | None
    job_title_id: str | None
    membership_revision: int = Field(ge=0)
    expected_revision: int = Field(ge=0)
    expected_source_revision: int = Field(ge=0)


@router.put("/departments/{scope_id}", summary="Set the department's assignable experts and skills")
async def save_pool(
    scope_id: str,
    body: PoolBody,
    request: Request,
    actor: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    session, _ = request_session(request, server)
    result = await server.employee_resources.save_pool(
        actor.id, scope_id, body.model_dump(), session=session
    )
    await notify_config_changed(server)
    return dict(result)


@router.put("/employees", summary="Atomically replace resources for employees in one department")
async def save_employees(
    body: BatchBody, actor: Any = Depends(require_admin()), server: Any = Depends(get_server)
) -> dict[str, Any]:
    employees = await server.employee_resources.save_users(actor.id, body.model_dump())
    await notify_config_changed(server)
    return {"employees": employees}


@router.put("/defaults", summary="Set system default skills from installed administrator resources")
async def save_defaults(
    body: DefaultsBody, actor: Any = Depends(require_admin()), server: Any = Depends(get_server)
) -> dict[str, Any]:
    result = await server.employee_resources.save_defaults(actor.id, body.model_dump())
    await notify_config_changed(server)
    return dict(result)


@router.put("/users/{user_id}/basic", summary="Save an employee's department and job title")
async def save_basic(
    user_id: int,
    body: BasicBody,
    request: Request,
    actor: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    session, _ = request_session(request, server)
    state = await server.employee_resources.basic(actor.id, user_id, session, body.model_dump())
    await notify_config_changed(server)
    return dict(state)
