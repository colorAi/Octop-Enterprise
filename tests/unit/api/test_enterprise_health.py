from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from octop.api.deps import current_user, get_server
from octop.api.routers.health import router


@pytest.mark.asyncio
async def test_readiness_reports_failure_without_details(monkeypatch):
    from octop.api.app import _install_exception_handlers

    app = FastAPI()
    _install_exception_handlers(app)
    app.include_router(router, prefix="/api/health")
    schema = app.openapi()
    assert schema["paths"]["/api/health/storage"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/EnterpriseStorageResponse")
    app.dependency_overrides[get_server] = lambda: SimpleNamespace()
    probe = AsyncMock(return_value={"ok": False, "checks": {"database": False}})
    monkeypatch.setattr("octop.api.routers.health.readiness", probe)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health/ready")
        assert response.status_code == 503
        assert response.json() == {"ok": False, "checks": {"database": False}}
        # The public health prefix must not make capacity data public.
        response = await client.get("/api/health/storage")
        assert response.status_code in {401, 403}
        app.dependency_overrides[current_user] = lambda: SimpleNamespace(is_admin=False)
        assert (await client.get("/api/health/storage")).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "database,communication,sandbox",
    [(True, 200, True), (False, 200, True), (True, 503, True), (True, 200, False)],
)
async def test_readiness_aggregates_enabled_services(monkeypatch, database, communication, sandbox):
    from contextlib import nullcontext

    from octop.infra import diagnostics

    connection = SimpleNamespace(execute=lambda sql: SimpleNamespace(fetchone=lambda: [1]))
    server = SimpleNamespace(
        database_bound=database,
        services=SimpleNamespace(db=SimpleNamespace(connect=lambda: nullcontext(connection))),
        communication=SimpleNamespace(enabled=True, url="http://communication.test"),
    )
    monkeypatch.setenv("OCTOP_AGENT_ISOLATION", "docker")

    def probe_sandbox():
        if not sandbox:
            raise RuntimeError("private infrastructure error must not be returned")
        return True

    monkeypatch.setattr(diagnostics, "_sandbox_ready", probe_sandbox)
    client_type = httpx.AsyncClient

    def handler(request):
        assert str(request.url) == "http://communication.test/healthz"
        assert "Authorization" not in request.headers
        return httpx.Response(communication)

    monkeypatch.setattr(
        diagnostics.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
    )
    result = await diagnostics.readiness(server)
    assert result == {
        "ok": database and communication == 200 and sandbox,
        "checks": {"database": database, "communication": communication == 200, "sandbox": sandbox},
    }
