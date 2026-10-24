"""tests/integration/test_providers_api.py"""

from __future__ import annotations

import pytest

from tests.support.auth import create_user, seed_openai_provider


async def test_list_providers(env):
    """GET /providers returns provider records to an administrator."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "openai", "kind": "openai", "api_key": "k"},
    )
    assert r.status_code == 201

    r = await c.get("/api/providers", headers=auth)
    assert r.status_code == 200
    assert any(p["name"] == "openai" for p in r.json())


@pytest.mark.parametrize("permissions", [[], ["users"], ["providers"]])
async def test_provider_credentials_require_model_management_permission(env, permissions):
    client, server, admin = env
    sentinel = "sk-provider-access-regression-only"
    await seed_openai_provider(client, admin, api_key=sentinel)
    employee = await create_user(client, admin, username="employee", permissions=permissions)
    allowed = "providers" in permissions
    for path in ("/api/providers", "/api/admin/providers"):
        anonymous = await client.get(path)
        assert anonymous.status_code == 401
        assert sentinel not in anonymous.text
        response = await client.get(path, headers=employee)
        assert response.status_code == (200 if allowed else 403)
        assert (sentinel in response.text) is allowed
    # Read access and a metadata-only edit must not change stored credentials.
    provider = next(p for p in server.services.provider_repo.list_all() if p.name == "openai")
    patched = await client.patch(
        f"/api/admin/providers/{provider.id}", headers=admin, json={"note": "new note"}
    )
    assert patched.status_code == 200
    assert server.services.provider_repo.get(provider.id).api_key == sentinel


async def test_employee_model_metadata_and_config_status_exclude_credentials(env):
    client, server, admin = env
    sentinel = "sk-provider-metadata-regression-only"
    await seed_openai_provider(client, admin, api_key=sentinel)
    employee = await create_user(client, admin, username="employee", permissions=[])
    for method, path in (
        ("GET", "/api/providers/resolved"),
        ("GET", "/api/providers/active-model"),
        ("GET", "/api/client-config/status"),
        ("POST", "/api/client-config/apply"),
    ):
        response = await client.request(method, path, headers=employee)
        assert response.status_code == 200
        assert sentinel not in response.text
        assert "api_key" not in response.text
        if path.endswith("/resolved"):
            assert response.json() == []
    user = server.services.user_repo.get_by_username("employee")
    policy = (await client.get(f"/api/users/{user.id}/models", headers=admin)).json()
    response = await client.put(
        f"/api/users/{user.id}/models",
        headers=admin,
        json={"model_refs": ["openai/gpt-4o"], "expected_revision": policy["revision"]},
    )
    assert response.status_code == 200
    response = await client.get("/api/providers/resolved", headers=employee)
    assert [(model["provider_name"], model["model"]) for model in response.json()] == [
        ("openai", "gpt-4o")
    ]
    assert sentinel not in response.text


async def test_delegated_provider_manager_can_edit_and_loses_read_access_on_revocation(env):
    client, server, admin = env
    employee = await create_user(client, admin, username="model-manager", permissions=["providers"])
    created = await client.post(
        "/api/admin/providers",
        headers=employee,
        json={"name": "delegated", "kind": "openai", "api_key": "sk-delegated-test-only"},
    )
    assert created.status_code == 201
    patched = await client.patch(
        f"/api/admin/providers/{created.json()['id']}", headers=employee, json={"note": "managed"}
    )
    assert patched.status_code == 200
    assert patched.json()["api_key"] == "sk-delegated-test-only"
    row = server.services.user_repo.get_by_username("model-manager")
    revoked = await client.patch(f"/api/users/{row.id}", headers=admin, json={"permissions": []})
    assert revoked.status_code == 200
    for path in ("/api/providers", "/api/admin/providers"):
        assert (await client.get(path, headers=employee)).status_code == 403


async def test_admin_creates_provider(env):
    """POST /admin/providers creates a provider."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "openai", "kind": "openai", "api_key": "k"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "openai"
    assert "user_id" not in body


async def test_regular_user_cannot_create_provider(env):
    """POST /admin/providers is admin-only."""
    c, _, admin_auth = env
    await c.post(
        "/api/users",
        headers=admin_auth,
        json={"username": "regular", "password": "TestPass12", "role": "user"},
    )
    tok = (
        await c.post("/api/auth/login", json={"username": "regular", "password": "TestPass12"})
    ).json()["access_token"]
    user_auth = {"Authorization": f"Bearer {tok}"}

    r = await c.post(
        "/api/admin/providers",
        headers=user_auth,
        json={"name": "should-fail", "kind": "openai"},
    )
    assert r.status_code == 403


async def test_admin_delete_provider(env):
    """DELETE /admin/providers/{id} removes the provider."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "to-delete", "kind": "openai"},
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    r = await c.delete(f"/api/admin/providers/{pid}", headers=auth)
    assert r.status_code in (200, 204)

    r = await c.get("/api/admin/providers", headers=auth)
    ids = [p["id"] for p in r.json()]
    assert pid not in ids


async def test_admin_cannot_delete_local_runtime_provider(env):
    """DELETE /admin/providers/{id} keeps ONNX / Ollama local rows."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "ONNX (Local)", "kind": "openai", "api_key": "onnx"},
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    r = await c.delete(f"/api/admin/providers/{pid}", headers=auth)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PROVIDER_LOCAL_PROTECTED"

    r = await c.get("/api/admin/providers", headers=auth)
    ids = [p["id"] for p in r.json()]
    assert pid in ids


async def test_admin_can_delete_custom_provider_on_local_url(env):
    """Custom OpenAI-compat rows stay deletable even on Ollama-like URLs."""
    c, _, auth = env
    for name, base_url in (
        ("my-local-llm", "http://127.0.0.1:8000/v1"),
        ("new-api", "http://192.168.1.10:3000/v1"),
        ("company-ollama", "http://127.0.0.1:11434"),
        ("docker-ollama", "http://ollama:11434"),
    ):
        r = await c.post(
            "/api/admin/providers",
            headers=auth,
            json={
                "name": name,
                "kind": "openai",
                "api_key": "sk-local",
                "base_url": base_url,
                "models": [{"id": "custom-qwen", "name": "custom-qwen", "enabled": True}],
            },
        )
        assert r.status_code == 201, name
        pid = r.json()["id"]

        r = await c.delete(f"/api/admin/providers/{pid}", headers=auth)
        assert r.status_code in (200, 204), name

        r = await c.get("/api/admin/providers", headers=auth)
        ids = [p["id"] for p in r.json()]
        assert pid not in ids, name


async def test_admin_cannot_delete_ollama_runtime_by_identity(env):
    """Onboard Ollama is protected by placeholder key / exact name, not URL."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={
            "name": "Ollama (Local)",
            "kind": "openai",
            "api_key": "ollama",
            "base_url": "http://127.0.0.1:11434",
        },
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    r = await c.delete(f"/api/admin/providers/{pid}", headers=auth)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PROVIDER_LOCAL_PROTECTED"


async def test_admin_stream_usage_defaults_and_patch(env):
    """Custom providers default stream_usage off; presets stay on until patched."""
    c, _, auth = env
    custom = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "mindie-arm", "kind": "openai", "api_key": "k"},
    )
    assert custom.status_code == 201
    assert custom.json()["stream_usage"] is False

    preset = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "DeepSeek", "kind": "openai", "api_key": "k"},
    )
    assert preset.status_code == 201
    assert preset.json()["stream_usage"] is True

    patched = await c.patch(
        f"/api/admin/providers/{preset.json()['id']}",
        headers=auth,
        json={"stream_usage": False},
    )
    assert patched.status_code == 200
    assert patched.json()["stream_usage"] is False
