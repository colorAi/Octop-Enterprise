"""Owner-scoped task status, replay and durable cancellation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from octop.api.common.reads import management_read
from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.execution.service import ExecutionConflict

router = APIRouter()


async def _owned(server: Any, user: Any, run_id: str) -> tuple[Any, dict[str, Any]]:
    repo = server.services.execution_repo
    run = await management_read(server, repo.get, run_id, user=user.id)
    if run is None:
        raise OctopError(ErrorCode.NOT_FOUND, "Execution not found")
    return repo, run


@router.get("/{run_id}", summary="Read the current user's durable execution status")
async def status(
    run_id: str, user: Any = Depends(current_user), server: Any = Depends(get_server)
) -> dict[str, Any]:
    _, run = await _owned(server, user, run_id)
    return {
        key: run[key]
        for key in (
            "run_id",
            "thread_id",
            "agent_id",
            "state",
            "event_seq",
            "approval_id",
            "created_at",
            "updated_at",
        )
    }


@router.get("/{run_id}/events", summary="Replay committed execution events after a cursor")
async def events(
    run_id: str,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    repo, run = await _owned(server, user, run_id)
    rows = await management_read(
        server, repo.events, run_id, user=user.id, after=after, limit=limit
    )
    return {"events": rows, "cursor": rows[-1]["seq"] if rows else after, "state": run["state"]}


class CancelBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)


@router.post("/{run_id}/cancel", summary="Persist cancellation; acceptance is not proof of stop")
async def cancel(
    run_id: str,
    body: CancelBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    repo, _ = await _owned(server, user, run_id)
    try:
        await management_read(
            server,
            repo.command,
            run_id,
            user=user.id,
            key=body.request_id,
            kind="cancel",
            payload={},
        )
    except ExecutionConflict as exc:
        raise OctopError(
            ErrorCode.FORBIDDEN, str(exc), status=409, details={"reason": str(exc)}
        ) from exc
    return {"run_id": run_id, "requested": True}
