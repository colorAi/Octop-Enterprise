import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from octop.api.app import _install_exception_handlers
from octop.api.middleware.jwt_auth import install
from octop.api.routers import auth, devices
from octop.infra.devices.manager import _digest


@pytest.fixture
def client(stack):
    server, _, _, _ = stack
    app = FastAPI()
    app.state.octop_server = server
    app.include_router(devices.router, prefix="/api")
    app.include_router(auth.router, prefix="/api/auth")
    _install_exception_handlers(app)
    install(app, server)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def headers(stack, user="alice"):
    return {"Authorization": "Bearer " + stack[3][user]}


def pair_frame(code, **changes):
    return {
        "type": "pair",
        "credential": code,
        "name": "My computer",
        "platform": "windows",
        "protocol_version": 1,
        "capabilities": ["device_status"],
        **changes,
    }


def test_http_auth_disabled_and_schema(client, stack):
    assert client.get("/api/devices").status_code == 401
    assert (
        client.post("http://testserver/api/devices/pairing", headers=headers(stack)).status_code
        == 200
    )
    result = client.post("/api/devices/pairing", headers=headers(stack))
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["expires_at"] <= time.time() + 120
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/devices"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    stack[0].devices = None
    assert client.get("/api/devices", headers=headers(stack)).json() == {
        "enabled": False,
        "devices": [],
    }
    assert client.post("/api/devices/pairing", headers=headers(stack)).status_code == 404


@pytest.mark.parametrize(
    "url,extra",
    [
        ("ws://testserver/api/devices/ws", {"origin": "http://testserver"}),
        ("wss://testserver/api/devices/ws", {"origin": "https://testserver"}),
    ],
)
def test_ws_rejects_browser_origins_on_both_transports(client, url, extra):
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(url, headers=extra):
        pass
    assert exc.value.code == 4403


@pytest.mark.parametrize(
    "changes",
    [
        {"protocol_version": 99},
        {"capabilities": ["shell"]},
        {"owner_user_id": 2},
        {"name": "x" * 81},
    ],
)
def test_ws_rejects_unapproved_protocol_fields(client, stack, changes):
    code = client.post("/api/devices/pairing", headers=headers(stack)).json()["code"]
    with client.websocket_connect("wss://testserver/api/devices/ws") as ws:
        ws.send_json(pair_frame(code, **changes))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4002


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_pair_probe_revoke_and_credential_audience(client, stack, scheme):
    code = client.post(f"{scheme}://testserver/api/devices/pairing", headers=headers(stack)).json()[
        "code"
    ]
    with client.websocket_connect(
        f"{scheme.replace('http', 'ws')}://testserver/api/devices/ws"
    ) as ws:
        ws.send_json(pair_frame(code))
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        device = ready["device_id"]
        credential = ready["credential"]
        assert (
            client.get(
                "/api/devices", headers={"Authorization": "Bearer " + credential}
            ).status_code
            == 401
        )
        assert client.get("/api/devices", headers=headers(stack, "bob")).json()["devices"] == []
        assert (
            client.delete(f"/api/devices/{device}", headers=headers(stack, "bob")).status_code
            == 404
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            call = executor.submit(
                client.post, f"/api/devices/{device}/probe", headers=headers(stack)
            )
            frame = ws.receive_json()
            ws.send_json(
                {
                    "type": "result",
                    "call_id": frame["call_id"],
                    "result": {"platform": "windows", "protocol_version": 1},
                }
            )
            assert call.result(timeout=3).json()["status"] == "completed"
        assert client.delete(f"/api/devices/{device}", headers=headers(stack)).status_code == 204
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert stack[0].services.device_repo.authenticate(_digest(credential), int(time.time())) is None


def test_device_list_preserves_native_directory_grants(client, stack):
    code = client.post("/api/devices/pairing", headers=headers(stack)).json()["code"]
    grant = {
        "id": "a" * 32,
        "name": "Reports",
        "version": 1,
        "writable": True,
        "allow_company": True,
    }
    with client.websocket_connect("wss://testserver/api/devices/ws") as ws:
        ws.send_json(
            pair_frame(
                code,
                protocol_version=2,
                capabilities=["device_status", "local_files"],
                grants=[grant],
            )
        )
        ws.receive_json()
        listed = client.get("/api/devices", headers=headers(stack)).json()["devices"]
        assert listed[0]["grants"] == [grant]
        assert client.get("/api/devices", headers=headers(stack, "bob")).json()["devices"] == []
        ws.send_json({"type": "grants", "grants": []})
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["type"] == "heartbeat_ack"
        assert (
            client.get("/api/devices", headers=headers(stack)).json()["devices"][0]["grants"] == []
        )


def test_logout_closes_device_and_invalidates_pending_pair_code(client, stack):
    code = client.post("/api/devices/pairing", headers=headers(stack)).json()["code"]
    with client.websocket_connect("wss://testserver/api/devices/ws") as ws:
        ws.send_json(pair_frame(code))
        ready = ws.receive_json()
        pending = client.post("/api/devices/pairing", headers=headers(stack)).json()["code"]
        assert client.post("/api/auth/logout", headers=headers(stack)).status_code == 204
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    repo = stack[0].services.device_repo
    assert repo.authenticate(_digest(ready["credential"]), int(time.time())) is None
    assert (
        repo.pair(_digest(pending), "another", "fake", "PC", "windows", 1, int(time.time())) is None
    )


def test_login_jwt_is_not_a_device_credential(client, stack):
    with client.websocket_connect("wss://testserver/api/devices/ws") as ws:
        ws.send_json(pair_frame(stack[3]["alice"], type="hello"))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_device_expiry_does_not_sign_out_the_company_account(client, stack):
    code = client.post("/api/devices/pairing", headers=headers(stack)).json()["code"]
    with client.websocket_connect("wss://testserver/api/devices/ws") as ws:
        ws.send_json(pair_frame(code))
        ready = ws.receive_json()
        with stack[0].services.db.connect() as conn:
            conn.execute("UPDATE desktop_devices SET expires_at=0")
        response = client.post(f"/api/devices/{ready['device_id']}/probe", headers=headers(stack))
        assert response.status_code == 409
        assert client.get("/api/devices", headers=headers(stack)).status_code == 200
