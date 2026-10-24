"""Lightweight web edge: local management reads and bounded runtime forwarding.

Runtime routes retain their original authentication at the worker. No caller
identity headers or implicit retries are introduced by this transport.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from octop.api.common.execution import execution_error_response
from octop.api.common.runtime_routing import runtime_owner
from octop.api.deps import extract_raw_token, resolve_user_from_token
from octop.api.middleware.jwt_auth import install as install_jwt_auth
from octop.api.routers import communication, executions, users
from octop.infra.errors import OctopError
from octop.infra.execution.service import ExecutionConflict

_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_LOCAL_USERS = re.compile(r"^/api/users(?:/(?:page|permissions|[0-9]+))?$")


def build_web_app(
    server: Any, upstream: str, *, runtime_client: httpx.AsyncClient | None = None
) -> FastAPI:
    parsed = urlsplit(upstream)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("OCTOP_WORKER_URL must be a fixed HTTP(S) origin")
    origin = upstream.rstrip("/")
    client = runtime_client or httpx.AsyncClient(
        timeout=httpx.Timeout(120, connect=3, pool=2),
        # Streaming model calls hold a connection until completion. A fixed
        # transport pool must not override the operator's model concurrency.
        limits=httpx.Limits(max_connections=None, max_keepalive_connections=16),
        follow_redirects=False,
        trust_env=False,
    )
    app = FastAPI(title="Octop Enterprise Web", docs_url=None, openapi_url=None)
    app.state.octop_server = server
    app.state.runtime_client = client
    app.add_exception_handler(ExecutionConflict, execution_error_response)
    app.include_router(users.router, prefix="/api/users")
    app.include_router(executions.router, prefix="/api/executions")
    app.include_router(communication.router, prefix="/api/im")

    async def route(path: str, user: Any = None, query: Any = None, headers: Any = None) -> str:
        owner = await runtime_owner(server, path, user, query or {}, headers=headers)
        return await server.management_io.run(server.services.execution_repo.route, owner) or origin

    @app.exception_handler(OctopError)
    async def octop_error(_: Request, exc: OctopError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.to_envelope())

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "role": "web", "started_at": server._started_at}

    @app.get("/api/health/ready")
    async def ready() -> JSONResponse:
        try:
            ok = (
                server._started
                and server.execution._accepting
                and await server.management_io.run(
                    server.execution.repo.node_ready,
                    server.execution.node,
                    server.execution.incarnation,
                )
                and await server.management_io.run(server.execution.repo.runtime_ready)
            )
        except Exception:
            ok = False
        return JSONResponse(
            status_code=200 if ok else 503, content={"ok": ok, "checks": {"web": ok}}
        )

    @app.middleware("http")
    async def dispatch(request: Request, call_next: Any) -> Any:
        path = request.url.path
        local = (
            path in {"/api/health", "/api/health/ready"}
            or path.startswith("/api/im/")
            or path.startswith("/api/executions/")
            or (request.method == "GET" and _LOCAL_USERS.fullmatch(path))
        )
        if local:
            return await call_next(request)
        headers = _headers(request.headers.raw)
        length = request.headers.get("content-length")
        maximum = server.config.max_upload_bytes
        if length is not None and (not length.isdigit() or int(length) > maximum):
            return JSONResponse(status_code=413, content={"detail": "request_too_large"})

        async def content() -> Any:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > maximum:
                    raise ValueError("request_too_large")
                yield chunk

        try:
            selected = await route(
                path,
                getattr(request.state, "octop_user", None),
                request.query_params,
                request.headers,
            )
        except ExecutionConflict as exc:
            return await execution_error_response(request, exc)
        except OctopError as exc:
            return JSONResponse(status_code=exc.status, content=exc.to_envelope())
        url = selected + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        try:
            response = await client.send(
                client.build_request(request.method, url, headers=headers, content=content()),
                stream=True,
            )
        except (httpx.HTTPError, ValueError):
            return JSONResponse(
                status_code=503,
                content={"detail": "runtime_temporarily_unavailable"},
                headers={"Retry-After": "1"},
            )
        # Stream raw bytes: preserve encoding headers and bound buffering to a chunk.
        result = StreamingResponse(
            response.aiter_raw(),
            status_code=response.status_code,
            background=BackgroundTask(response.aclose),
        )
        result.raw_headers = _headers(response.headers.raw)
        return result

    install_jwt_auth(app, server)

    @app.websocket("/{path:path}")
    async def relay(websocket: WebSocket, path: str) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidHandshake

        first_frame = None
        accepted = False
        try:
            if path == "api/devices/ws":
                if websocket.headers.get("origin"):
                    await websocket.close(code=4403)
                    return
                await websocket.accept()
                accepted = True
                first_frame = await asyncio.wait_for(websocket.receive_text(), 10)
                if len(first_frame.encode()) > 16384:
                    raise ValueError("device_frame_too_large")
                from octop.infra.devices.protocol import Hello

                hello = Hello.model_validate(json.loads(first_frame))
                digest = hashlib.sha256(hello.credential.encode()).hexdigest()
                devices = server.services.device_repo
                if hello.type == "pair":
                    owner = await server.management_io.run(
                        devices.pairing_owner, digest, int(time.time())
                    )
                else:
                    identity = await server.management_io.run(
                        devices.authenticate, digest, int(time.time())
                    )
                    owner = identity["owner_user_id"] if identity else None
                if owner is None:
                    raise ValueError("device_denied")
                selected = (
                    await server.management_io.run(server.services.execution_repo.route, owner)
                    or origin
                )
            else:
                raw = websocket.query_params.get("token") or extract_raw_token(
                    authorization=websocket.headers.get("authorization"),
                    access_token=websocket.query_params.get("access_token"),
                )
                user = (
                    await server.management_io.run(resolve_user_from_token, server, raw)
                    if raw
                    else None
                )
                selected = await route("/" + path, user, websocket.query_params)
        except ExecutionConflict:
            await websocket.close(code=1013)
            return
        except (OctopError, ValueError, TimeoutError):
            await websocket.close(code=4001)
            return
        target = (
            selected.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/" + path
        )
        if websocket.url.query:
            target += "?" + websocket.url.query
        headers = [
            (key.decode("latin1"), value.decode("latin1"))
            for key, value in _headers(websocket.headers.raw)
            if key.lower()
            not in {
                b"host",
                b"sec-websocket-key",
                b"sec-websocket-version",
                b"sec-websocket-extensions",
                b"sec-websocket-protocol",
            }
        ]
        try:
            async with connect(
                target,
                additional_headers=headers,
                subprotocols=websocket.scope.get("subprotocols", []),
                max_size=1024 * 1024,
                max_queue=16,
                open_timeout=5,
                compression=None,
            ) as remote:
                if not accepted:
                    await websocket.accept(subprotocol=remote.subprotocol)
                if first_frame is not None:
                    await remote.send(first_frame)

                async def outward() -> None:
                    async for message in remote:
                        if isinstance(message, str):
                            await asyncio.wait_for(websocket.send_text(message), 5)
                        else:
                            await asyncio.wait_for(websocket.send_bytes(message), 5)

                async def inward() -> None:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        value = message.get("text") or message.get("bytes") or ""
                        if len(value) > 1024 * 1024:
                            await websocket.close(code=1009)
                            return
                        await remote.send(value)

                tasks = {asyncio.create_task(outward()), asyncio.create_task(inward())}
                try:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if remote.close_code is not None:
                        await websocket.close(
                            code=remote.close_code, reason=remote.close_reason or ""
                        )
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except InvalidHandshake as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            with suppress(Exception):
                await websocket.close(code=4001 if status in {401, 403} else 1013)
        except (OSError, TimeoutError, ConnectionClosed):
            pass
        finally:
            with suppress(Exception):
                await websocket.close(code=1013)

    return app


def _headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    excluded = set(_HOP)
    for name, value in headers:
        if name.lower() == b"connection":
            excluded.update(x.strip().lower() for x in value.decode("latin1").split(","))
    return [
        (name, value) for name, value in headers if name.decode("latin1").lower() not in excluded
    ]
