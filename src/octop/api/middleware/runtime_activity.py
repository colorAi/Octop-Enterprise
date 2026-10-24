"""Pin workspace users until HTTP bodies or interactive shell sessions finish."""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from typing import Any

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from octop.api.common.execution import execution_error_response
from octop.api.common.runtime_routing import runtime_agent, runtime_owner
from octop.api.deps import resolve_user_from_token
from octop.infra.db.repos.execution import ExecutionConflict
from octop.infra.errors import OctopError


class RuntimeActivityMiddleware:
    def __init__(self, app: ASGIApp, *, server: Any) -> None:
        self.app, self.server = app, server

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        shards = getattr(self.server, "runtime_shards", None)
        path = scope.get("path", "")
        if (
            shards is None
            or scope["type"] not in {"http", "websocket"}
            or path.startswith("/api/health")
        ):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # Chat is pinned by durable runs. Long-lived notification/device
            # sockets alone must not keep every user's runtime resident.
            if not any(
                x in path
                for x in (
                    "terminal/ws",
                    "browser-stream/ws",
                    "desktop-stream/ws",
                    "mobile-stream/ws",
                    "shell/ws",
                )
            ):
                await self.app(scope, receive, send)
                return
            from starlette.websockets import WebSocket

            socket = WebSocket(scope, receive, send)
            agent_id = runtime_agent(path, socket.query_params)
            raw = socket.query_params.get("token")
            if not raw:
                await self.app(scope, receive, send)
                return
            try:
                user = await self.server.management_io.run(
                    resolve_user_from_token, self.server, raw
                )
                owner = await runtime_owner(self.server, path, user, socket.query_params)
                await self.server.management_io.run(shards.assert_user, owner)
            except OctopError:
                await socket.close(code=4001)
                return
            except ExecutionConflict:
                await socket.close(code=1013)
                return
        else:
            request = Request(scope, receive)
            agent_id = runtime_agent(path, request.query_params)
            user = scope.get("state", {}).get("octop_user")
            try:
                owner = await runtime_owner(
                    self.server, path, user, request.query_params, headers=request.headers
                )
                await self.server.management_io.run(shards.assert_user, owner)
            except OctopError as exc:
                from starlette.responses import JSONResponse

                await JSONResponse(status_code=exc.status, content=exc.to_envelope())(
                    scope, receive, send
                )
                return
            except ExecutionConflict as exc:
                await (await execution_error_response(request, exc))(scope, receive, send)
                return

        async def forward(message: Message) -> None:
            if (
                message["type"] == "http.response.start"
                and 200 <= message["status"] < 300
                and scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
                and path.startswith("/api/plugins/")
                and not path.startswith("/api/plugins/agents/")
            ):
                # Plugin code/config lives on the shared app volume. Publish a
                # revision only after a successful managed mutation or reload.
                await self.server.management_io.run(
                    self.server.services.settings_repo.set,
                    "execution.plugins_revision",
                    uuid.uuid4().hex,
                )
            await send(message)

        # Private creation pins the actual employee instance in the domain layer;
        # the URL identifies an administrator's source, not that runtime.
        async with (
            nullcontext()
            if path.endswith("/private-instance")
            else shards.activity(owner, agent_id)
        ):
            await self.app(scope, receive, forward)
