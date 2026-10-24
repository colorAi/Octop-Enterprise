from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from octop.config import OctopConfig
from octop.infra.communication.bridge import CommunicationBridge, sign_credential
from octop.infra.communication.contracts import match_route
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.errors import OctopError
from octop.infra.users.profile_avatar import delete_profile_avatar, profile_avatar_url
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def bridge(tmp_path: Path):
    pool = SqlitePool(tmp_path / "test.db")
    run_migrations(pool)
    services = build_shared_services(db=pool, paths=PathLayout(tmp_path), config=OctopConfig())
    value = CommunicationBridge(services)
    value.enabled = True
    value.url = "http://internal-im"
    value._instance = "test-instance"
    value._key = b"x" * 32
    yield value
    pool.close()


def test_whitelist_and_credential_signature():
    assert match_route("GET", "/contacts").permission == "contacts"
    assert match_route("GET", "/presence").permission == "contacts"
    assert match_route("POST", "/groups").permission == "groups_create"
    assert match_route("POST", "/organization/company-group").permission == "departments"
    for method, path in [
        ("POST", "/groups/arbitrary/unknown"),
        ("GET", "/../internal/identities"),
        ("PUT", "/users/1"),
        ("GET", "//example.com"),
        ("GET", "/media/http://bad"),
    ]:
        assert match_route(method, path) is None
    assert sign_credential(b"x" * 32, {"sub": "a"}) != sign_credential(b"y" * 32, {"sub": "a"})


@pytest.mark.asyncio
async def test_resource_directory_is_batched_scoped_and_uses_a_distinct_credential(bridge):
    import base64

    calls = []

    def handler(req):
        assert req.url.path == "/internal/resource-memberships"
        token = req.headers["Authorization"].split()[1].split(".")[0]
        claims = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        assert claims["purpose"] == "resource-directory"
        assert claims["sub"] == bridge.mapped_id(1)
        ids = json.loads(req.content)["user_ids"]
        calls.append(ids)
        return httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {
                            "user_id": uid,
                            "found": True,
                            "revision": 3,
                            "enabled": False,
                            "department_id": "D",
                        }
                        for uid in reversed(ids)
                    ]
                }
            },
        )

    bridge._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await bridge.resource_memberships(1, list(range(2, 404)) + [2])
        assert [len(batch) for batch in calls] == [200, 200, 2]
        assert len(result) == 402 and result[2]["revision"] == 3
        assert result[403]["enabled"] is False
        assert await bridge.resource_memberships(1, []) == {}
        assert len(calls) == 3
    finally:
        await bridge.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", ["missing", "duplicate", "foreign", "revision", "enabled"])
async def test_invalid_resource_batch_fails_closed(bridge, malformed):
    rows = [
        {
            "user_id": bridge.mapped_id(uid),
            "found": True,
            "revision": 3,
            "enabled": True,
            "department_id": None,
        }
        for uid in (2, 3)
    ]
    if malformed == "missing":
        rows.pop()
    elif malformed == "duplicate":
        rows[1] = rows[0]
    elif malformed == "foreign":
        rows[1]["user_id"] = "octop:foreign:3"
    elif malformed == "revision":
        rows[1]["revision"] = True
    else:
        rows[1]["enabled"] = "true"
    bridge._http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"data": {"items": rows}})
        )
    )
    try:
        with pytest.raises(OctopError) as error:
            await bridge.resource_memberships(1, [2, 3])
        assert error.value.status == 503
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_contacts_only_realtime_retains_messaging_permission_boundary(bridge):
    uid = bridge.services.user_repo.create(
        username="directory", role="user", permissions=["contacts"]
    )
    bridge._http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"data": {"version": "1"}})
        )
    )
    ticket = await bridge.issue_ticket(
        uid, "contacts-session", int(__import__("time").time()) + 100
    )
    assert (await bridge.consume_ticket(ticket["ticket"]))["user_id"] == uid
    with pytest.raises(OctopError):
        await bridge.authorize(uid, "contacts-session", "messaging")
    bridge.services.user_repo.set_permissions(uid, [])
    with pytest.raises(OctopError):
        await bridge.issue_ticket(uid, "contacts-session", int(__import__("time").time()) + 100)
    await bridge.stop()


@pytest.mark.asyncio
async def test_failed_delivery_retries_and_preserves_permission_boundary(bridge):
    uid = bridge.services.user_repo.create(username="a", role="user", permissions=["messaging"])
    calls = []
    fail = True

    def handler(req):
        calls.append(req)
        if fail:
            return httpx.Response(503)
        return httpx.Response(
            200, json={"data": {"version": str(json.loads(req.content)["version"])}}
        )

    bridge._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(OctopError):
        await bridge.ready(uid, "s", "messaging")
    assert bridge.repo.pending(uid, due=False)[0]["attempts"] == 1
    fail = False
    await bridge.ready(uid, "s", "messaging")
    assert bridge.repo.pending(uid, due=False) == []
    assert "password" not in calls[-1].content.decode()
    with pytest.raises(OctopError):
        await bridge.authorize(uid, "s", "contacts")
    bridge.services.user_repo.set_disabled(uid, True)
    with pytest.raises(OctopError):
        await bridge.authorize(uid, "s", "messaging")
    await bridge.stop()


@pytest.mark.asyncio
async def test_delivers_existing_uploaded_and_removed_account_portraits(bridge):
    uid = bridge.services.user_repo.create(
        username="portrait", role="user", permissions=["contacts"]
    )
    directory = bridge.services.paths.user_avatars_dir
    directory.mkdir(parents=True)
    portrait = directory / f"{uid}.png"
    portrait.write_bytes(b"existing portrait")
    os.utime(portrait, ns=(1_100_000_000, 1_100_000_000))
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        return httpx.Response(200, json={"data": {"version": str(body["version"])}})

    bridge._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await bridge.ready(uid, "s", "contacts")
    first = calls[-1]["avatar_url"]
    assert first == f"/api/users/{uid}/avatar?v=1100000000"
    # A replacement in the same second must bypass the old frontend image cache.
    os.utime(portrait, ns=(1_200_000_000, 1_200_000_000))
    bridge.repo.refresh_profile(uid)
    await bridge.ready(uid, "s", "contacts")
    assert calls[-1]["avatar_url"] == profile_avatar_url(
        directory, str(uid), f"/api/users/{uid}/avatar"
    )
    assert calls[-1]["avatar_url"] != first
    delete_profile_avatar(directory, str(uid))
    bridge.repo.refresh_profile(uid)
    await bridge.ready(uid, "s", "contacts")
    assert calls[-1]["avatar_url"] == ""
    assert calls[-1]["version"] == 3
    await bridge.stop()


@pytest.mark.asyncio
async def test_proxy_does_not_accept_external_identity_headers_or_redirects(bridge):
    uid = bridge.services.user_repo.create(username="a", role="admin")
    requests = []

    def handler(req):
        requests.append(req)
        if req.url.path.startswith("/internal/"):
            return httpx.Response(200, json={"data": {"version": "1"}})
        return httpx.Response(302, headers={"Location": "http://untrusted/"})

    bridge._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    response = await bridge.proxy(
        method="GET",
        path="/contacts",
        user_id=uid,
        session="s",
        query=[],
        content=None,
        content_type="application/json",
    )
    assert response.status_code == 302
    assert str(requests[-1].url) == "http://internal-im/api/app/v1/contacts"
    assert "X-User-Id" not in requests[-1].headers
    assert requests[-1].headers["Authorization"].startswith("Bearer ")
    await response.aclose()
    await bridge.stop()


@pytest.mark.asyncio
async def test_ticket_cross_account_and_logout(bridge):
    a = bridge.services.user_repo.create(username="a", role="user", permissions=["messaging"])
    b = bridge.services.user_repo.create(username="b", role="user", permissions=["messaging"])
    bridge._http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"data": {"version": "1"}})
        )
    )
    ticket = await bridge.issue_ticket(a, "session-a", int(__import__("time").time()) + 100)
    consumed = await bridge.consume_ticket(ticket["ticket"])
    assert consumed["user_id"] == a and consumed["user_id"] != b
    with pytest.raises(OctopError):
        await bridge.consume_ticket(ticket["ticket"])
    bridge.repo.revoke_session("session-a", int(__import__("time").time()) + 100)
    with pytest.raises(OctopError):
        await bridge.issue_ticket(a, "session-a", int(__import__("time").time()) + 100)
    await bridge.stop()
