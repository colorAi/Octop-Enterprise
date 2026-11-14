import hashlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from octop.api.deps import current_user, get_server
from octop.api.routers import group_expert_internal, group_experts
from octop.infra.communication.bridge import CommunicationBridge, sign_credential
from octop.infra.errors import OctopError
from octop.infra.users.identity import User
from octop.infra.utils.bounded_io import BoundedIO


@pytest.mark.asyncio
async def test_internal_protocol_rejects_user_tokens_changed_bodies_and_expired_credentials():
    bridge = CommunicationBridge(SimpleNamespace(communication_identity_repo=None))
    bridge._key = b"test-group-key-must-be-at-least-32-bytes"
    io = BoundedIO(wait=True)
    repo = SimpleNamespace(
        capabilities=lambda: {
            "protocol": 2,
            "schema": 36,
            "mode": "distributed-v1",
            "ready": False,
        },
        control=lambda call, **kwargs: {
            "call_id": call,
            "saved": True,
            "execution_state": None,
            "stopped": True,
        },
    )
    app = FastAPI()
    app.state.octop_server = SimpleNamespace(
        group_experts=SimpleNamespace(bridge=bridge, io=io, repo=repo)
    )
    app.dependency_overrides[get_server] = lambda: app.state.octop_server
    app.include_router(group_expert_internal.router, prefix="/internal/group-experts/v1")
    app.add_exception_handler(
        OctopError,
        lambda request, exc: JSONResponse(status_code=exc.status, content=exc.to_envelope()),
    )
    path = "/internal/group-experts/v1/calls/call-one/control"
    raw = b'{"control_seq":2,"kind":"cancel"}'
    now = int(time.time())
    claims = {
        "iss": "octop-im",
        "aud": "octop",
        "purpose": "group-experts-v1",
        "method": "POST",
        "path": path,
        "body_digest": hashlib.sha256(raw).hexdigest(),
        "iat": now,
        "exp": now + 15,
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for override, payload in [
                ({"purpose": "request"}, raw),
                ({"exp": now - 1}, raw),
                ({}, raw.replace(b"cancel", b"dispatch")),
            ]:
                token = sign_credential(bridge._key, {**claims, **override})
                response = await client.post(
                    path,
                    content=payload,
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                    },
                )
                assert response.status_code == 401, response.text
            response = await client.post(
                path,
                content=raw,
                headers={
                    "Authorization": "Bearer " + sign_credential(bridge._key, claims),
                    "Content-Type": "application/json",
                },
            )
            assert response.status_code == 200 and response.json()["data"]["saved"]
    finally:
        await io.drain()


@pytest.mark.asyncio
async def test_organization_metadata_requires_admin_and_does_not_grant_history(monkeypatch):
    io = BoundedIO(wait=True)
    bridge = SimpleNamespace(
        ready=AsyncMock(),
        mapped_id=lambda user: f"octop:install:{user}",
        group_request=AsyncMock(
            return_value={
                "items": [],
                "can_manage": True,
                "experts_enabled": False,
                "department_id": None,
                "status": "active",
                "group_kind": "project",
                "sources": [],
                "ready": True,
            }
        ),
    )
    repo = SimpleNamespace(
        scope=lambda group: None, available_sources=lambda: [], syncing=lambda **kwargs: False
    )
    groups = SimpleNamespace(
        bridge=bridge, repo=repo, io=io, runtime=AsyncMock(return_value={"ready": True})
    )
    app = FastAPI()
    app.state.octop_server = SimpleNamespace(role="web", group_experts=groups)
    app.dependency_overrides[get_server] = lambda: app.state.octop_server
    app.include_router(group_experts.router, prefix="/api/im")
    app.add_exception_handler(
        OctopError,
        lambda request, exc: JSONResponse(status_code=exc.status, content=exc.to_envelope()),
    )
    monkeypatch.setattr(
        group_experts, "request_session", lambda request, server: ("session", int(time.time()) + 30)
    )
    user = User(1, "manager", "user", None, permissions=["departments"])
    app.dependency_overrides[current_user] = lambda: user
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            denied = await client.get("/api/im/organization/groups/group-one/experts")
            assert denied.status_code == 403
            bridge.group_request.assert_not_awaited()
            user.role = "admin"
            allowed = await client.get("/api/im/organization/groups/group-one/experts")
            assert allowed.status_code == 200, allowed.text
            assert allowed.json()["data"]["ready"] is True
            groups.runtime.assert_not_awaited()
            bridge.group_request.assert_awaited_once_with(
                "POST",
                "/groups/group-one/inspect",
                {"actor_id": "octop:install:1", "organization": True},
                actor=1,
            )
            assert "snapshot" not in allowed.text and "messages" not in allowed.text
    finally:
        await io.drain()


def test_group_routes_document_typed_metadata_and_control_contract():
    app = FastAPI()
    app.include_router(group_experts.router, prefix="/api/im")
    app.include_router(group_expert_internal.router, prefix="/internal/group-experts/v1")
    schema = app.openapi()
    for path, methods in schema["paths"].items():
        for method in methods.values():
            assert method["summary"], path
            response = method["responses"]["200"]["content"]["application/json"]["schema"]
            assert "$ref" in response, path
    assert schema["components"]["schemas"]["Control"]["properties"]["kind"]["enum"] == [
        "dispatch",
        "cancel",
    ]
    assert "result_json" not in schema["components"]["schemas"]["ExpertCallMetadata"]["properties"]
