"""Liveness probe (no auth)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from octop.api.deps import get_server, require_admin
from octop.infra.diagnostics import enterprise_storage, readiness

router = APIRouter()


@router.get("", summary="Health check")
async def health(server: Any = Depends(get_server)) -> dict[str, Any]:
    """Liveness probe: database reachability, user count, and loaded agents. No auth required."""
    bound = server.database_bound
    users = server.user_manager.count() if server.user_manager is not None else 0
    agents = (
        len(server.app_runtime.agent_registry.list_rows()) if server.app_runtime is not None else 0
    )
    return {
        "ok": True,
        "started_at": server._started_at,
        "db": bound,
        "users_loaded": users,
        "agents_running": agents,
    }


class ReadinessResponse(BaseModel):
    ok: bool
    checks: dict[str, bool]


@router.get(
    "/ready",
    summary="Readiness of the database and enabled enterprise services",
    response_model=ReadinessResponse,
)
async def ready(response: Response, server: Any = Depends(get_server)) -> dict[str, Any]:
    result = await readiness(server)
    response.status_code = 200 if result["ok"] else 503
    return result


class PersistentRows(BaseModel):
    rows: int
    payload_characters: int


class EnterpriseStorageResponse(BaseModel):
    tasks: PersistentRows
    events: PersistentRows
    checkpoints: PersistentRows
    writes: PersistentRows
    operations: PersistentRows
    task_statuses: dict[str, int]
    skill_cache: dict[str, int]
    identity_delivery: dict[str, int]
    devices: dict[str, int]
    automatic_deletion_enabled: bool


@router.get(
    "/storage",
    summary="Enterprise storage and delivery backlog",
    response_model=EnterpriseStorageResponse,
    description="Administrator-only, read-only counts. No credentials or message contents are returned; no data is deleted.",
    dependencies=[Depends(require_admin())],
)
async def storage(server: Any = Depends(get_server)) -> dict[str, Any]:
    return await enterprise_storage(server)
