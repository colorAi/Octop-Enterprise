"""Voice provider aliases must enforce the same credential boundary."""

import pytest

from tests.support.auth import create_user


@pytest.mark.parametrize("permissions", [[], ["providers"], ["voice"]])
async def test_voice_credentials_require_voice_permission(env, permissions):
    client, server, admin = env
    sentinel = "sk-voice-access-regression-only"
    created = await client.post(
        "/api/admin/voice/providers",
        headers=admin,
        json={"name": "voice-test", "kind": "openai", "capability": "tts", "api_key": sentinel},
    )
    assert created.status_code == 201
    employee = await create_user(client, admin, username="employee", permissions=permissions)
    allowed = "voice" in permissions
    for path in ("/api/voice/providers", "/api/admin/voice/providers"):
        anonymous = await client.get(path)
        assert anonymous.status_code == 401
        assert sentinel not in anonymous.text
        response = await client.get(path, headers=employee)
        assert response.status_code == (200 if allowed else 403)
        assert (sentinel in response.text) is allowed
        assert (await client.get(path, headers=admin)).status_code == 200
    active = await client.get("/api/voice/active", headers=employee)
    assert active.status_code == 200
    assert sentinel not in active.text
    pid = created.json()["id"]
    patched = await client.patch(
        f"/api/admin/voice/providers/{pid}", headers=admin, json={"note": "new note"}
    )
    assert patched.status_code == 200
    assert server.services.voice_provider_repo.get(pid).api_key == sentinel
