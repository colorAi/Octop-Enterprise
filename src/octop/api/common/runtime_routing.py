"""Resolve the runtime scope of an authenticated transport request."""

from __future__ import annotations

import re
from typing import Any


def runtime_agent(path: str, query: Any) -> str | None:
    match = re.match(r"^/api/(?:plugins/)?agents/([^/]+)(?:/|$)", path)
    return match[1] if match else query.get("agent_id")


async def runtime_owner(
    server: Any, path: str, user: Any, query: Any, *, headers: Any = None
) -> int | None:
    if path.startswith("/api/company-runtime/"):
        from octop.infra.company_runtime.service import authenticate_company_device

        headers = headers or {}
        identity = await server.management_io.run(
            authenticate_company_device,
            server.services,
            headers.get("x-octop-device-credential", ""),
            headers.get("origin"),
        )
        return int(identity["owner_user_id"])
    owner = user.id if user is not None else None
    agent_id = runtime_agent(path, query)
    if agent_id and not path.endswith(("/private-instance", "/read")) and user is not None:
        row = await server.management_io.run(server.services.agent_repo.get, agent_id)
        if row is not None:
            owner = row.user_id
    if not re.match(r"^/api/(?:plugins/)?agents/", path) and path.startswith(
        (
            "/api/admin/",
            "/api/providers",
            "/api/plugins",
            "/api/skill-packages",
            "/api/experts",
            "/api/settings",
        )
    ):
        owner = None
    return owner
