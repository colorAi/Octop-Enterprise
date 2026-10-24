"""HTTP behaviour for the local ONNX embedding service admin endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

MODEL = "BAAI/bge-small-zh-v1.5"


@pytest.mark.parametrize("locale", ["zh", "en"])
@pytest.mark.parametrize(
    ("method", "path", "body", "status"),
    [
        ("POST", "/api/onnx-models/download", {"model": MODEL}, 503),
        ("PUT", "/api/onnx-models/config", {"model": MODEL, "enabled": True}, 503),
        ("POST", "/api/knowledge-bases/onnx-download", {"model": MODEL}, 409),
        ("POST", "/api/onnx-models/test", {"model": MODEL}, 200),
        ("POST", "/api/knowledge-bases/onnx-test", {"model": MODEL}, 200),
    ],
)
async def test_dependency_permission_error_is_localized(
    env: Any,
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
    method: str,
    path: str,
    body: dict[str, Any],
    status: int,
) -> None:
    from octop.i18n import tr
    from octop.infra.agents.providers import onnx_service
    from octop.infra.utils.runtime_packages import PackageInstallError

    client, _server, auth = env
    monkeypatch.setattr(onnx_service, "local_embedding_deps_available", lambda: False)

    def fail_install(*_args: Any, **_kwargs: Any) -> None:
        raise PackageInstallError("permission_denied")

    monkeypatch.setattr(onnx_service, "install_packages", fail_install)
    response = await client.request(
        method, path, headers={**auth, "Accept-Language": locale}, json=body
    )
    assert response.status_code == status, response.text
    payload = response.json()
    if isinstance(payload.get("error"), dict):
        detail = payload["error"]["details"]["reason"]
    else:
        detail = payload.get("detail") or payload["error"]
    assert detail == tr("runtime_packages.permission_denied", locale)
    assert "pip/uv unavailable" not in response.text


def _seed_cache(home: Path, model: str) -> None:
    """Lay down a cache that looks like a finished fastembed download."""
    from octop.infra.agents.providers.onnx_service import model_cache_dir

    snap = model_cache_dir(model) / "snapshots" / "abc123"
    (snap / "onnx").mkdir(parents=True)
    (snap / "onnx" / "model_optimized.onnx").write_bytes(b"onnx")


async def test_enable_without_weights_is_rejected(
    env: Any, tmp_octop_home: Path, monkeypatch: Any
) -> None:
    """Enabling a model that was never downloaded must not persist enabled=True."""
    client, _server, auth = env
    from octop.infra.agents.providers import onnx_service

    monkeypatch.setattr(onnx_service, "ensure_local_embedding_deps", _ready)

    resp = await client.put(
        "/api/onnx-models/config",
        headers=auth,
        json={"enabled": True, "model": MODEL, "download_if_missing": False},
    )
    assert resp.status_code == 409, resp.text
    assert "not downloaded" in resp.json()["detail"]

    status = await client.get("/api/onnx-models/status", headers=auth)
    assert status.json()["enabled"] is False


async def test_enable_succeeds_once_weights_are_present(
    env: Any, tmp_octop_home: Path, monkeypatch: Any
) -> None:
    client, _server, auth = env
    from octop.infra.agents.providers import onnx_service

    monkeypatch.setattr(onnx_service, "ensure_local_embedding_deps", _ready)
    monkeypatch.setattr(onnx_service, "local_embedding_deps_available", lambda: True)
    _seed_cache(tmp_octop_home, MODEL)

    resp = await client.put(
        "/api/onnx-models/config",
        headers=auth,
        json={"enabled": True, "model": MODEL, "download_if_missing": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["downloaded"] is True
    assert body["ready"] is True


async def test_activate_reports_downloaded_nested_weights(
    env: Any, tmp_octop_home: Path, monkeypatch: Any
) -> None:
    """Regression: nested per-tower weights used to fail activation."""
    client, _server, auth = env
    from octop.infra.agents.providers import onnx_service

    monkeypatch.setattr(onnx_service, "ensure_local_embedding_deps", _ready)
    monkeypatch.setattr(onnx_service, "local_embedding_deps_available", lambda: True)

    model = "jinaai/jina-clip-v1"
    catalog = await client.get("/api/onnx-models/catalog", headers=auth)
    if model not in {item["id"] for item in catalog.json()}:
        import pytest

        pytest.skip("fastembed not installed; jina-clip-v1 absent from catalog")

    snap = onnx_service.model_cache_dir(model) / "snapshots" / "abc123"
    (snap / "onnx").mkdir(parents=True)
    (snap / "onnx" / "text_model.onnx").write_bytes(b"onnx")

    resp = await client.post(
        "/api/knowledge-bases/onnx-activate", headers=auth, json={"model": model}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["downloaded"] is True


def _ready(*_args: Any, **_kwargs: Any) -> str:
    return "ready"


async def test_knowledge_onnx_test_uses_knowledge_settings_permission(
    env: Any, tmp_octop_home: Path, monkeypatch: Any
) -> None:
    """The settings drawer's check must work for a knowledge-settings admin.

    ``/onnx-models/test`` is gated on ``onnx_models``, which that role need not
    hold, so the knowledge router carries its own probe.
    """
    from tests.support.auth import create_user

    client, _server, admin_auth = env
    from octop.infra.agents.providers import onnx_service

    monkeypatch.setattr(onnx_service, "ensure_local_embedding_deps", _ready)
    monkeypatch.setattr(onnx_service, "local_embedding_deps_available", lambda: True)
    monkeypatch.setattr(onnx_service, "embed_texts", lambda _m, _t: [[0.0] * 512])
    _seed_cache(tmp_octop_home, MODEL)

    kb_admin = await create_user(
        client,
        admin_auth,
        username="kb_probe_admin",
        permissions=["knowledge_bases", "knowledge_settings"],
    )

    denied = await client.post("/api/onnx-models/test", headers=kb_admin, json={"model": MODEL})
    assert denied.status_code == 403, denied.text

    allowed = await client.post(
        "/api/knowledge-bases/onnx-test", headers=kb_admin, json={"model": MODEL}
    )
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert body["ok"] is True
    assert body["dim"] == 512


async def test_knowledge_onnx_test_reports_missing_weights(
    env: Any, tmp_octop_home: Path, monkeypatch: Any
) -> None:
    """A model with no weights yields a local message, never a remote error."""
    client, _server, auth = env
    from octop.infra.agents.providers import onnx_service

    monkeypatch.setattr(onnx_service, "ensure_local_embedding_deps", _ready)

    resp = await client.post("/api/knowledge-bases/onnx-test", headers=auth, json={"model": MODEL})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is False
    assert "not downloaded" in resp.json()["error"]
