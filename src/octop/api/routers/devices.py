"""Company desktop pairing and narrow device WebSocket transport."""

from __future__ import annotations

from contextlib import suppress
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from octop.api.deps import _decode, current_user, extract_raw_token, get_server
from octop.infra.auth.tokens import session_id
from octop.infra.devices.manager import DeviceManager
from octop.infra.devices.protocol import DirectoryGrant, ProbeResult
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/devices")


class PairingCode(BaseModel):
    code: str
    expires_at: int
    credential_expires_at: int


class DeviceInfo(BaseModel):
    device_id: str
    name: str
    platform: Literal["windows", "darwin"]
    protocol_version: int
    expires_at: int
    created_at: int
    last_seen_at: int
    revoked_at: int | None
    online: bool
    capabilities: list[str]
    grants: list[DirectoryGrant] = Field(default_factory=list)


class DeviceList(BaseModel):
    enabled: bool
    devices: list[DeviceInfo]


class ProbeResponse(BaseModel):
    call_id: str
    status: Literal["completed", "cancelled", "timed_out", "device_disconnected"]
    result: ProbeResult | None = None


def _manager(server: OctopServer) -> DeviceManager:
    if server.devices is None:
        raise OctopError(ErrorCode.NOT_FOUND, "device channel disabled", status=404)
    return server.devices


@router.get("", response_model=DeviceList, summary="List this employee's desktop devices")
async def list_devices(
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DeviceList:
    manager = server.devices
    return DeviceList(
        enabled=manager is not None,
        devices=[DeviceInfo(**row) for row in await manager.list_devices(user.id)]
        if manager
        else [],
    )


@router.post(
    "/pairing",
    response_model=PairingCode,
    summary="Create a two-minute, single-use pairing code",
    description="Requires an employee login over the deployment's HTTP or HTTPS entry. "
    "Replaces any previous pending code. "
    "Device credentials expire with this login or after eight hours, whichever comes first.",
)
async def pairing(
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> PairingCode:
    manager = _manager(server)
    raw = extract_raw_token(authorization=request.headers.get("authorization"))
    if not raw:
        raise OctopError(ErrorCode.AUTH_FAILED, "Authorization header required")
    claims = _decode(server, raw)
    response.headers["Cache-Control"] = "no-store"
    return PairingCode(
        **await manager.issue_pairing(user.id, session_id(raw, claims), int(claims["exp"]))
    )


@router.delete("/{device_id}", status_code=204, summary="Revoke an owned desktop device")
async def revoke(
    device_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    await _manager(server).revoke(user.id, device_id)
    return Response(status_code=204)


@router.post(
    "/{device_id}/probe", response_model=ProbeResponse, summary="Probe an owned desktop channel"
)
async def probe(
    device_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> ProbeResponse:
    return ProbeResponse(**await _manager(server).probe(user.id, device_id))


@router.websocket("/ws")
async def device_channel(websocket: WebSocket) -> None:
    server = websocket.app.state.octop_server
    if server.devices is None:
        await websocket.close(code=4404)
        return
    # Browser scripts cannot use this channel. Credentials are first-frame only, never URLs.
    if websocket.headers.get("origin"):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    try:
        await server.devices.serve(websocket)
    except WebSocketDisconnect:
        return
    except OctopError as exc:
        with suppress(RuntimeError):
            await websocket.close(code=4001, reason=str(exc.details.get("reason", "device_denied")))
    except (ValidationError, ValueError, KeyError):
        with suppress(RuntimeError):
            await websocket.close(code=4002, reason="invalid_device_frame")
    except TimeoutError:
        with suppress(RuntimeError):
            await websocket.close(code=4008, reason="device_timeout")
