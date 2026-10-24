"""One-time initialization and the lightweight web process lifecycle."""

from __future__ import annotations

import logging
import os
import secrets
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from octop.config import load_config
from octop.infra.communication.bridge import CommunicationBridge
from octop.infra.db.factory import open_database
from octop.infra.db.migrate import (
    _current_version,
    _max_discovered_version,
    initialize_checkpoints,
    run_migrations,
)
from octop.infra.db.pool import PostgresPool
from octop.infra.db.services import build_shared_services
from octop.infra.execution.service import ExecutionService
from octop.infra.users.manager import UserManager
from octop.infra.utils.bounded_io import BoundedIO
from octop.infra.utils.paths import PathLayout


def require_initialized(db: Any) -> None:
    if db.dialect != "postgresql":
        raise ValueError("web/worker roles require PostgreSQL")
    if _current_version(db) != _max_discovered_version(db.dialect):
        raise ValueError("run octop run --role init with this build before starting web/worker")
    from octop.infra.db.services import RepoBundle

    repos = RepoBundle.from_pool(db)
    if (
        repos.execution_repo.mode() != "distributed-v1"
        or repos.settings_repo.get("execution.checkpoints_initialized") != "1"
        or repos.settings_repo.get("execution.sandbox_budget_initialized") != "1"
    ):
        raise ValueError("distributed execution has not been initialized")


def initialize(home: Path) -> None:
    paths = PathLayout(home)
    paths.ensure_root()
    config = load_config(paths.config)
    if not config.database.is_postgresql:
        raise ValueError("role initialization requires PostgreSQL")
    db = open_database(config, paths)
    try:
        services = build_shared_services(db=db, paths=paths, config=config)
        if _current_version(db) >= 29 and any(
            node["role"] == "worker" and node["state"] != "stopped"
            for node in services.execution_repo.nodes()
        ):
            raise ValueError(
                "stop the existing worker and record its stop proof before initialization"
            )
        run_migrations(db)
        assert isinstance(db, PostgresPool)
        initialize_checkpoints(db, config.database.postgresql_conninfo())
        # Never change an existing secret or any provider credentials.
        if services.secret_repo.get("jwt") is None:
            services.secret_repo.get_or_create("jwt", lambda: secrets.token_bytes(32))
        from octop.infra.agents.plugins.manager import PluginManager

        plugins = PluginManager(plugins_dir=paths.plugins_dir, config_path=paths.config)
        plugins.seed_bundled()
        plugins.load_installed(install_deps=True)
        from octop.infra.execution.profiles import memory_bytes, model_concurrency, profile

        limits = profile()
        from octop.infra.execution.shards import worker_topology

        services.execution_repo.configure_topology(worker_topology())
        services.execution_repo.configure(
            running=limits.running, models=model_concurrency(), queued=limits.queued, user_queued=8
        )
        services.execution_repo.configure_sandbox_budget(
            total=memory_bytes(
                os.environ.get("OCTOP_SANDBOX_TOTAL_MEMORY", str(limits.sandbox_total_memory))
            ),
            sandbox=memory_bytes(
                os.environ.get("OCTOP_SANDBOX_MEMORY", str(limits.sandbox_memory))
            ),
        )
        services.settings_repo.set("execution.mode", "distributed-v1")
        services.settings_repo.set("execution.checkpoints_initialized", "1")
        services.settings_repo.set("execution.sandbox_budget_initialized", "1")
    finally:
        db.close()


class WebProcess:
    """Metadata and auth only; no agent, scheduler, plugin or device runtime."""

    role = "web"

    def __init__(self, home: Path, *, node: str, incarnation: str | None = None) -> None:
        self.paths = PathLayout(home)
        self.config = load_config(self.paths.config)
        self.services = build_shared_services(
            db=open_database(self.config, self.paths), paths=self.paths, config=self.config
        )
        try:
            require_initialized(self.services.db)
            if self.services.secret_repo.get("jwt") is None:
                raise ValueError("JWT secret missing; run the one-time init role")
        except BaseException:
            self.services.db.close()
            raise
        self.management_io = BoundedIO(workers=2, capacity=24)
        self.user_manager = UserManager(self.services)
        self.communication = CommunicationBridge(self.services)
        self.app_runtime = SimpleNamespace(user_manager=self.user_manager)
        self.execution = ExecutionService(
            self.services.execution_repo,
            node=node,
            incarnation=incarnation or uuid.uuid4().hex,
            role="web",
            conninfo=self.config.database.postgresql_conninfo(),
        )
        self._started = False
        self._started_at = int(time.time())

    @property
    def database_bound(self) -> bool:
        return self._started

    async def start(self) -> None:
        logging.basicConfig(level=logging.INFO)
        await self.execution.start()
        # Same-origin IM transport belongs to the lightweight edge. Only the
        # primary worker runs background identity delivery/reconciliation.
        await self.communication.start(background=False)
        self._started = True

    async def stop(self) -> None:
        await self.communication.stop()
        await self.execution.close()
        await self.management_io.drain()
        self.services.db.close()
        self._started = False
