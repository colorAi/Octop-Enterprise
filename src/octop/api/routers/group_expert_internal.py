"""Reserved protocol surface. Normal user JWTs and proxy paths cannot enter."""

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import get_server
from octop.api.routers.group_expert_models import (
    ExpertAdmission,
    ExpertCallMetadata,
    ExpertCapabilities,
    ExpertControlReceipt,
    ExpertDeliveryResult,
    GroupExpertResponse,
)
from octop.infra.errors import ErrorCode, OctopError

router = APIRouter()


async def service(request: Request, server: Any = Depends(get_server)) -> Any:
    groups = getattr(server, "group_experts", None)
    if groups is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "group experts unavailable", status=503)
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 65536:
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "request too large", status=413)
    request._body = bytes(raw)
    groups.bridge.verify_group_request(
        request.headers.get("authorization", "").removeprefix("Bearer "),
        request.method,
        request.url.path + ("?" + request.url.query if request.url.query else ""),
        bytes(raw),
    )
    return groups


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    control_seq: int = Field(gt=0)
    kind: Literal["dispatch", "cancel"]


@router.get(
    "/capabilities",
    response_model=GroupExpertResponse[ExpertCapabilities],
    summary="Check deployment and Worker protocol capability",
)
async def capabilities(groups: Any = Depends(service)) -> dict[str, Any]:
    return {"data": await groups.io.run(groups.repo.capabilities)}


@router.post(
    "/calls/{call}/register",
    response_model=GroupExpertResponse[ExpertAdmission],
    summary="Register a Go call idempotently without a personal thread",
)
async def register(call: str, groups: Any = Depends(service)) -> dict[str, Any]:
    return {"data": await groups.register(call)}


@router.get(
    "/calls/{call}",
    response_model=GroupExpertResponse[ExpertCallMetadata | None],
    summary="Read call metadata without question or answer text",
)
async def metadata(call: str, groups: Any = Depends(service)) -> dict[str, Any]:
    return {"data": await groups.io.run(groups.repo.get, call)}


@router.post(
    "/calls/{call}/delivery",
    response_model=GroupExpertResponse[ExpertDeliveryResult],
    summary="Redeliver the original result after authorization checks",
)
async def delivery(call: str, groups: Any = Depends(service)) -> dict[str, Any]:
    row = await groups.io.run(groups.repo.result_obligation, call)
    if row is None:
        return {"data": {"saved": False}}
    if row["acknowledged_at"] is None:
        await groups.deliver(row)
        row = await groups.io.run(groups.repo.result_obligation, call)
    return {"data": {"saved": True, "receipt": json.loads(row["receipt"])}}


@router.post(
    "/calls/{call}/control",
    response_model=GroupExpertResponse[ExpertControlReceipt],
    summary="Persist dispatch permission or irreversible cancellation",
)
async def control(call: str, body: Control, groups: Any = Depends(service)) -> dict[str, Any]:
    return {
        "data": await groups.io.run(
            groups.repo.control, call, sequence=body.control_seq, kind=body.kind
        )
    }
