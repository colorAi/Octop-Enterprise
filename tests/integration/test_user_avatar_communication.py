"""Account portraits remain usable by ordinary company contacts and chats."""

from __future__ import annotations

import pytest

from tests.integration.test_auth_flow import _PNG


async def test_portrait_changes_publish_revisions_from_account_and_admin(env):
    c, srv, auth = env
    user = (await c.get("/api/auth/me", headers=auth)).json()
    uid = user["id"]
    identities = srv.services.communication_identity_repo

    async def upload(path):
        before = identities.current(uid)["version"]
        response = await c.post(
            path, headers=auth, files={"file": ("avatar.png", _PNG, "image/png")}
        )
        assert response.status_code == 201, response.text
        assert identities.current(uid)["version"] > before
        assert response.json()["avatar_url"]

    for path in ("/api/auth/me/avatar", f"/api/users/{uid}/avatar"):
        await upload(path)
        before = identities.current(uid)["version"]
        assert (await c.delete(path, headers=auth)).status_code == 204
        assert identities.current(uid)["version"] > before
        assert (await c.get(f"/api/users/{uid}/avatar", headers=auth)).status_code == 404

    # Choosing the same preset still removes a previously uploaded portrait.
    for path in ("/api/auth/me", f"/api/users/{uid}"):
        await upload("/api/auth/me/avatar")
        before = identities.current(uid)["version"]
        response = await c.patch(path, headers=auth, json={"avatar_icon": None})
        assert response.status_code == 200, response.text
        assert response.json()["avatar_url"] is None
        assert identities.current(uid)["version"] > before


@pytest.mark.parametrize(
    "permissions,expected", [(["contacts"], 200), (["messaging"], 200), ([], 403)]
)
async def test_colleague_portrait_permissions_do_not_grant_user_management(
    env, permissions, expected
):
    c, srv, auth = env
    owner = (await c.get("/api/auth/me", headers=auth)).json()["id"]
    assert (
        await c.post(
            "/api/auth/me/avatar", headers=auth, files={"file": ("avatar.png", _PNG, "image/png")}
        )
    ).status_code == 201
    response = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "colleague",
            "password": "TestPass12",
            "role": "user",
            "permissions": permissions,
        },
    )
    assert response.status_code == 201, response.text
    token = (
        await c.post("/api/auth/login", json={"username": "colleague", "password": "TestPass12"})
    ).json()["access_token"]
    colleague = {"Authorization": f"Bearer {token}"}
    portrait = await c.get(f"/api/users/{owner}/avatar", headers=colleague)
    assert portrait.status_code == expected, portrait.text
    if expected == 200:
        assert portrait.content == _PNG
    assert (await c.get("/api/users", headers=colleague)).status_code == 403
    assert (await c.delete(f"/api/users/{owner}/avatar", headers=colleague)).status_code == 403
