"""Publish source mutations before acknowledging a successful HTTP save."""

from __future__ import annotations

import re
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from octop.infra.errors import OctopError


class SourcePublicationMiddleware:
    def __init__(self, app: ASGIApp, *, server: Any) -> None:
        self.app, self.server = app, server

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        resources = getattr(self.server, "employee_resources", None)
        publisher = resources.publication if resources is not None else None
        path = scope.get("path", "")
        user = scope.get("state", {}).get("octop_user")
        # Only management writes can alter a source. Chat/approval/upload flows
        # are not wrapped; source-agent tools use the same domain gate directly.
        selected = (
            path.startswith("/api/experts/")
            or path.startswith("/api/plugins/")
            or (
                bool(re.fullmatch(r"/api/agents(?:/[^/]+)?", path))
                or bool(
                    re.match(
                        r"/api/agents/[^/]+/(?:workspace|skills|initialize|bootstrap|clone)(?:/|$)",
                        path,
                    )
                )
                or path.startswith("/api/skill-packages")
                or path == "/api/admin/expert-assignments/source"
            )
        )
        if (
            scope["type"] != "http"
            or scope.get("method") not in {"POST", "PUT", "PATCH", "DELETE"}
            or publisher is None
            or not selected
            or user is None
        ):
            await self.app(scope, receive, send)
            return
        source = await publisher._source()
        assert resources is not None
        agent_match = re.match(r"/api/(?:plugins/)?agents/([^/]+)(?:/|$)", path)
        if agent_match:
            row = await resources._read(resources.services.agent_repo.get, agent_match[1])
            if row is not None and row.user_id != source:
                await self.app(scope, receive, send)
                return
        if (
            user.role != "admin"
            and user.id != source
            and not path.startswith("/api/skill-packages")
        ):
            await self.app(scope, receive, send)
            return
        messages: list[Message] = []
        size = 0
        status = 200

        async def collect(message: Message) -> None:
            nonlocal size, status
            if message["type"] == "http.response.start":
                status = message["status"]
            size += len(message.get("body", b""))
            if size > 2 * 1024 * 1024:
                raise ValueError("source_save_response_too_large")
            messages.append(message)

        class RejectedSave(Exception):
            pass

        try:
            async with publisher.mutation():
                await self.app(scope, receive, collect)
                if status >= 400:
                    raise RejectedSave()
        except RejectedSave:
            pass
        except Exception as exc:
            response = JSONResponse(
                status_code=exc.status if isinstance(exc, OctopError) else 503,
                content=exc.to_envelope()
                if isinstance(exc, OctopError)
                else {"detail": "resource_publication_failed"},
            )
            await response(scope, receive, send)
            return
        for message in messages:
            await send(message)
