"""Authenticated, same-origin adapters to the internal communication service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any, cast

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketState
from websockets.exceptions import WebSocketException

from octop.api.deps import _decode, current_user, get_server
from octop.api.routers.communication_models import BODY_MODELS, Envelope
from octop.infra.auth.tokens import session_id
from octop.infra.communication.bridge import CommunicationBridge
from octop.infra.communication.contracts import ROUTES
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()


class RealtimeTicket(BaseModel):
    ticket: str = Field(description="One-use credential valid for at most 30 seconds")
    expires_at: int = Field(description="Ticket expiry as Unix seconds")


def bridge(server: OctopServer) -> CommunicationBridge:
    value = getattr(server, "communication", None)
    if value is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "communication is disabled", status=503)
    return cast(CommunicationBridge, value)


def request_session(request: Request, server: OctopServer) -> tuple[str, int]:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise OctopError(ErrorCode.AUTH_FAILED, "Bearer credentials required")
    raw = header[7:].strip()
    claims = _decode(server, raw)
    return session_id(raw, claims), int(claims["exp"])


@router.post(
    "/realtime-ticket", response_model=RealtimeTicket, summary="Issue one-use realtime ticket"
)
async def realtime_ticket(
    request: Request, user: User = Depends(current_user), server: OctopServer = Depends(get_server)
) -> dict[str, Any]:
    session, expires = request_session(request, server)
    return await bridge(server).issue_ticket(user.id, session, expires)


@router.websocket("/realtime")
async def realtime(websocket: WebSocket) -> None:
    raw_ticket = websocket.query_params.get("ticket", "")
    # Uvicorn logs this same scope on acceptance and rejection. Remove the one-use secret first.
    websocket.scope["query_string"] = b""
    server = websocket.app.state.octop_server
    # Exact public origin: no caller-supplied forwarded identity or origin headers.
    expected = f"{'https' if websocket.url.scheme == 'wss' else 'http'}://{websocket.headers.get('host', '')}"
    if websocket.headers.get("origin") != expected:
        await websocket.close(code=1008)
        return
    try:
        value = await bridge(server).consume_ticket(raw_ticket)
    except OctopError:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        await bridge(server).realtime(value, websocket)
    except (OctopError, OSError, ValueError, WebSocketException):
        pass
    finally:
        if (
            websocket.application_state == WebSocketState.CONNECTED
            and websocket.client_state == WebSocketState.CONNECTED
        ):
            # The transport may finish before its disconnect frame is consumed.
            with suppress(WebSocketDisconnect, RuntimeError):
                await websocket.close(code=1008)


async def forward(
    request: Request, user: User = Depends(current_user), server: OctopServer = Depends(get_server)
) -> StreamingResponse:
    session, _ = request_session(request, server)
    path = request.url.path.removeprefix("/api/im")
    limit = 50 << 20 if path.endswith("/content") else 64 << 10

    async def bounded_body() -> AsyncIterator[bytes]:
        count = 0
        async for chunk in request.stream():
            count += len(chunk)
            if count > limit:
                raise OctopError(
                    ErrorCode.SLASH_BAD_ARGS, "communication request too large", status=413
                )
            yield chunk

    response = await bridge(server).proxy(
        method=request.method,
        path=path,
        user_id=user.id,
        session=session,
        query=list(request.query_params.multi_items()),
        content=bounded_body() if request.method not in {"GET", "HEAD"} else None,
        content_type=request.headers.get("content-type", "application/json"),
    )

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()

    headers = {
        k: v
        for k, v in response.headers.items()
        if k.lower()
        in {"content-type", "content-length", "content-disposition", "retry-after", "x-request-id"}
    }
    headers["Cache-Control"] = "no-store"
    headers["X-Content-Type-Options"] = "nosniff"
    return StreamingResponse(body(), status_code=response.status_code, headers=headers)


for route in ROUTES:
    extra: dict[str, Any] = {}
    if route.method not in {"GET", "HEAD"}:
        model = BODY_MODELS.get(route.path)
        if model is not None:
            extra["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": model.model_json_schema()}},
            }
    if route.path.endswith("/content"):
        extra["requestBody"] = {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    responses: dict[int | str, dict[str, Any]] = {
        200: {"model": Envelope},
        201: {"model": Envelope},
    }
    if route.method == "GET" and route.path == "/media/{id}":
        responses = {
            200: {
                "description": "Private visible image bytes",
                "content": {
                    mime: {"schema": {"type": "string", "format": "binary"}}
                    for mime in ("image/png", "image/jpeg", "image/gif", "image/webp")
                },
            }
        }
    router.add_api_route(
        route.path,
        forward,
        methods=[route.method],
        summary=route.summary,
        description="Requires Octop Bearer JWT and the "
        + route.permission
        + " permission. Preserves the communication request_id/data/error envelope; media endpoints stream bytes. See services/communication/api/contract.md for request schemas.",
        response_class=StreamingResponse,
        responses=responses,
        openapi_extra=extra,
    )
