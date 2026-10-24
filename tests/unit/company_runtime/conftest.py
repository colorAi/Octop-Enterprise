import hashlib
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from octop.api.app import _install_exception_handlers
from octop.api.middleware.jwt_auth import install
from octop.api.routers.company_runtime import router
from octop.config import OctopConfig
from octop.infra.company_runtime.client import CompanyClient
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def company(tmp_path):
    db = SqlitePool(tmp_path / "company.db")
    run_migrations(db)
    services = build_shared_services(
        paths=PathLayout(tmp_path), config=OctopConfig(company_runtime_enabled=True), db=db
    )
    owner = services.user_repo.create(username="employee", role="user")
    services.user_policy_repo.set(
        owner, "model_access", json.dumps({"model_refs": ["test/model"], "revision": "fixture"})
    )
    services.agent_repo.create(agent_id="expert", user_id=owner, name="Test Expert")
    for thread in ("thread1", "thread2", "thread3"):
        services.thread_repo.insert(
            thread_id=thread,
            agent_id="expert",
            user_id=owner,
            channel_type="dashboard",
            session_key=thread,
            last_active=0,
        )
    credentials = ["octop-device.test1", "octop-device.test2"]
    for i, credential in enumerate(credentials):
        now = int(time.time())
        services.device_repo.issue_code(f"code{i}", owner, f"session{i}", now + 100, now + 3600)
        services.device_repo.pair(
            f"code{i}",
            f"device{i}",
            hashlib.sha256(credential.encode()).hexdigest(),
            "Test",
            "darwin",
            2,
            now,
        )
    server = SimpleNamespace(_started=True, services=services)
    app = FastAPI()
    app.state.octop_server = server
    app.include_router(router, prefix="/api")
    _install_exception_handlers(app)
    install(app, server)
    client = CompanyClient(
        "http://company.test", credentials[0], transport=httpx.ASGITransport(app=app)
    )
    yield server, client, credentials, owner
    db.close()
