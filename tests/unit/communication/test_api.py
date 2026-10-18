from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from octop.api.middleware.jwt_auth import install
from octop.api.routers.communication import router
from octop.config import OctopConfig
from octop.infra.auth.tokens import sign_token
from octop.infra.communication.bridge import CommunicationBridge
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.errors import OctopError
from octop.infra.users.identity import User
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def api(tmp_path: Path):
    pool = SqlitePool(tmp_path / "db")
    run_migrations(pool)
    services = build_shared_services(db=pool, config=OctopConfig(), paths=PathLayout(tmp_path))
    secret = services.secret_repo.get_or_create("jwt", lambda: b"secret" * 6)
    uid = services.user_repo.create(
        username="alice", role="user", permissions=["messaging", "contacts"]
    )
    bridge = CommunicationBridge(services)
    bridge.enabled = True
    bridge.url = "http://internal"
    bridge._instance = "installation"
    bridge._key = b"key" * 11
    calls = []

    def upstream(req):
        calls.append(req)
        data = (
            {"data": {"version": str(json.loads(req.content)["version"])}}
            if req.url.path.startswith("/internal/")
            else {"data": {"items": []}, "request_id": "upstream"}
        )
        return httpx.Response(
            200,
            stream=httpx.ByteStream(json.dumps(data).encode()),
            headers={"Content-Type": "application/json", "Cache-Control": "public"},
        )

    bridge._http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))

    def current(user_id):
        row = services.user_repo.get(user_id)
        return (
            User(row.id, row.username, row.role, row.display_name, permissions=row.permissions)
            if row and not row.disabled
            else None
        )

    server = SimpleNamespace(
        _started=True,
        services=services,
        user_manager=SimpleNamespace(get_by_id=current),
        communication=bridge,
    )
    app = FastAPI()
    app.state.octop_server = server
    app.include_router(router, prefix="/api/im")
    install(app, server)

    @app.exception_handler(OctopError)
    async def failure(request, error):
        return JSONResponse(error.to_envelope(), status_code=error.status)

    token = sign_token(secret, sub=uid, uname="alice", role="user")
    with TestClient(app) as client:
        yield client, services, bridge, calls, uid, {"Authorization": f"Bearer {token}"}
    pool.close()


def test_http_auth_status_permissions_whitelist_and_stream(api):
    client, services, bridge, calls, uid, headers = api
    assert client.get("/api/im/contacts").status_code == 401
    assert (
        client.get("/api/im/contacts", headers={"Authorization": "Bearer invalid"}).status_code
        == 401
    )
    response = client.get(
        "/api/im/contacts",
        headers={**headers, "X-User-Id": "somebody-else", "X-Miaox-Token": "forged"},
    )
    assert response.status_code == 200
    assert response.json()["request_id"] == "upstream"
    assert response.headers["Cache-Control"] == "no-store"
    assert "X-User-Id" not in calls[-1].headers and "X-Miaox-Token" not in calls[-1].headers
    assert client.get("/api/im/organization/departments", headers=headers).status_code == 403
    assert client.post("/api/im/groups", headers=headers).status_code == 403
    services.user_repo.set_disabled(uid, True)
    assert client.get("/api/im/contacts", headers=headers).status_code == 403


def test_websocket_ticket_origin_and_account_revocation(api):
    client, services, bridge, calls, uid, headers = api
    seen = []

    async def fake_realtime(ticket, browser):
        seen.append(ticket)
        assert browser.scope["query_string"] == b""
        await browser.send_json({"type": "sync.available"})

    bridge.realtime = fake_realtime
    ticket = client.post("/api/im/realtime-ticket", headers=headers).json()["ticket"]
    with client.websocket_connect(
        f"/api/im/realtime?ticket={ticket}", headers={"Origin": "http://testserver"}
    ) as ws:
        assert ws.receive_json()["type"] == "sync.available"
    assert seen[0]["user_id"] == uid
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            f"/api/im/realtime?ticket={ticket}", headers={"Origin": "http://testserver"}
        ),
    ):
        pass
    fresh = client.post("/api/im/realtime-ticket", headers=headers).json()["ticket"]
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            f"/api/im/realtime?ticket={fresh}", headers={"Origin": "http://evil.test"}
        ),
    ):
        pass
    services.user_repo.set_disabled(uid, True)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            f"/api/im/realtime?ticket={fresh}", headers={"Origin": "http://testserver"}
        ),
    ):
        pass


def test_organization_roster_and_metadata_are_not_contacts_endpoints(api):
    client, services, bridge, calls, uid, headers = api
    assert client.get("/api/im/organization/users", headers=headers).status_code == 403
    assert client.get("/api/im/organization/groups/group_test", headers=headers).status_code == 403
    services.user_repo.set_permissions(uid, ["departments"])
    assert client.get("/api/im/organization/users", headers=headers).status_code == 200
    assert client.get("/api/im/organization/groups/group_test", headers=headers).status_code == 200
    assert calls[-1].url.path == "/api/app/v1/organization/groups/group_test"


def test_client_disconnect_does_not_send_another_close(api, monkeypatch):
    from starlette.websockets import WebSocket

    client, services, bridge, calls, uid, headers = api
    closed = []
    original_close = WebSocket.close

    async def record_close(socket, *args, **kwargs):
        closed.append(socket.client_state)
        await original_close(socket, *args, **kwargs)

    async def receive_disconnect(ticket, browser):
        await browser.send_json({"type": "ready"})
        with suppress(WebSocketDisconnect):
            await browser.receive_text()

    monkeypatch.setattr(WebSocket, "close", record_close)
    bridge.realtime = receive_disconnect
    ticket = client.post("/api/im/realtime-ticket", headers=headers).json()["ticket"]
    with client.websocket_connect(
        f"/api/im/realtime?ticket={ticket}", headers={"Origin": "http://testserver"}
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
    assert closed == []


@pytest.mark.asyncio
async def test_transport_close_race_is_harmless(api):
    from unittest.mock import AsyncMock

    from starlette.websockets import WebSocketState

    from octop.api.routers.communication import realtime

    client, services, bridge, calls, uid, headers = api
    ticket = client.post("/api/im/realtime-ticket", headers=headers).json()["ticket"]
    bridge.realtime = AsyncMock()
    websocket = SimpleNamespace(
        query_params={"ticket": ticket},
        scope={"query_string": b"ticket=redacted"},
        app=SimpleNamespace(state=SimpleNamespace(octop_server=client.app.state.octop_server)),
        url=SimpleNamespace(scheme="ws"),
        headers={"origin": "http://testserver", "host": "testserver"},
        application_state=WebSocketState.CONNECTED,
        client_state=WebSocketState.CONNECTED,
        accept=AsyncMock(),
        close=AsyncMock(side_effect=RuntimeError("response already completed")),
    )
    await realtime(websocket)
    websocket.close.assert_awaited_once_with(code=1008)


def test_group_creation_grant_and_openapi_contract(api):
    client, services, bridge, calls, uid, headers = api
    body = {"request_id": "stable-create", "name": "Project", "user_ids": []}
    assert client.post("/api/im/groups", json=body, headers=headers).status_code == 403
    services.user_repo.set_permissions(uid, ["messaging", "contacts", "groups_create"])
    response = client.post("/api/im/groups", json=body, headers=headers)
    assert response.status_code == 200
    assert calls[-1].url.path == "/api/app/v1/groups"
    assert json.loads(calls[-1].content) == body
    assert (
        client.post("/api/im/organization/company-group", json={}, headers=headers).status_code
        == 403
    )
    schema = client.get("/openapi.json", headers=headers).json()
    operation = schema["paths"]["/api/im/groups"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert "request_id" in request_schema["required"]
    assert "name" in request_schema["required"]
    for path in (
        "/api/im/groups/{id}/members",
        "/api/im/groups/{id}/leave",
        "/api/im/groups/{id}/archive",
    ):
        assert path in schema["paths"]
