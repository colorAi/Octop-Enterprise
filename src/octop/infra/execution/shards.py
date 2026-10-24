"""Stable user shards, runtime activity pins and bounded idle reclamation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def worker_topology() -> dict[str, str]:
    raw = os.environ.get("OCTOP_WORKER_URLS") or '{"runtime-worker":"http://worker:8088"}'
    value = json.loads(raw)
    if not isinstance(value, dict) or not 1 <= len(value) <= 16:
        raise ValueError("OCTOP_WORKER_URLS must map node IDs to fixed HTTP origins")
    for node, origin in value.items():
        if not isinstance(origin, str):
            raise ValueError("OCTOP_WORKER_URLS must map node IDs to fixed HTTP origins")
        parsed = urlsplit(origin)
        if (
            not node
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("OCTOP_WORKER_URLS must map node IDs to fixed HTTP origins")
    normalized = {key: value[key].rstrip("/") for key in sorted(value)}
    if len(set(normalized.values())) != len(value):
        raise ValueError("workers must have distinct origins")
    return normalized


class RuntimeShards:
    def __init__(self, server: Any) -> None:
        self.server = server
        self.service = server.execution
        self.repo = self.service.repo
        self.node, self.incarnation = self.service.node, self.service.incarnation
        self.workers = self.repo.topology()
        if self.node not in self.workers:
            raise ValueError("worker node is absent from the initialized topology")
        from octop.infra.execution.profiles import profile

        self.service.capacity = max(
            1, (profile().running + len(self.workers) - 1) // len(self.workers)
        )
        self.primary = self.node == sorted(self.workers)[0]
        self.idle_seconds = float(os.environ.get("OCTOP_RUNTIME_IDLE_SECONDS", "300"))
        if not 1 <= self.idle_seconds <= 86400:
            raise ValueError("OCTOP_RUNTIME_IDLE_SECONDS must be between 1 and 86400")
        self._gate = asyncio.Lock()
        self._pins: Counter[int | None] = Counter()
        self._agent_pins: Counter[str] = Counter()
        self._task: asyncio.Task[None] | None = None
        self._revision = ""
        self._plugins_revision: str | None = None

    def owns_user(self, user: int | None) -> bool:
        return bool(
            self.repo.worker_for(user) == self.node
            and self.repo.node_ready(self.node, self.incarnation)
        )

    def assert_user(self, user: int | None) -> None:
        self.repo.own_user(int(user or 0), self.node, self.incarnation)

    @asynccontextmanager
    async def activity(self, user: int | None, agent: str | None = None) -> AsyncIterator[None]:
        pins: Any = self._agent_pins if agent is not None else self._pins
        key = agent if agent is not None else user
        async with self._gate:
            pins[key] += 1
        try:
            yield
        finally:
            pins[key] -= 1
            if not pins[key]:
                del pins[key]

    async def retry_sandbox[T](
        self, operation: Callable[[], Awaitable[T]], *, timeout: float = 15
    ) -> T:
        """Retry admission after verified idle reclamation, outside registry locks."""
        from octop.infra.db.repos.execution import ExecutionConflict

        deadline = time.monotonic() + timeout
        while True:
            try:
                return await operation()
            except ExecutionConflict as exc:
                if str(exc) != "sandbox_memory_exhausted" or time.monotonic() >= deadline:
                    raise
            await self.reap()
            # Give other workers and finishing requests a chance to release RAM.
            await asyncio.sleep(0.1)

    async def start(self) -> None:
        self._revision = await self.service.io.run(self.repo.runtime_revision)
        self._plugins_revision = await self.service.io.run(
            self.server.services.settings_repo.get, "execution.plugins_revision"
        )
        self._task = asyncio.create_task(self._sweep(), name="runtime-idle-reaper")

    async def refresh_configuration(self) -> None:
        registry = self.server.app_runtime.agent_registry
        plugins_revision = await self.service.io.run(
            self.server.services.settings_repo.get, "execution.plugins_revision"
        )
        plugins_changed = plugins_revision != self._plugins_revision
        if plugins_changed:
            await self.service.io.run(self.server.plugin_manager.load_installed, install_deps=False)
            self._plugins_revision = plugins_revision
        revision = await self.service.io.run(self.repo.runtime_revision)
        if revision != self._revision or plugins_changed:
            await registry.on_provider_changed()
            registry.invalidate_mcp_tool_cache()
            if registry.harness_manager is not None:
                registry.harness_manager.set_security_policy(registry.security.harness_policy())
            self._revision = revision

    async def reap(self) -> list[str]:
        registry = self.server.app_runtime.agent_registry
        removed = []
        pressure = await self.service.io.run(self.repo.sandbox_pressure)
        for agent, used in sorted(registry.runtime_last_used.items(), key=lambda item: item[1]):
            age = 0 if pressure else self.idle_seconds
            if time.monotonic() - used < age:
                continue
            async with self._gate:
                row = await self.service.io.run(registry.get_row, agent)
                if (
                    row is None
                    or self._pins.get(row.user_id)
                    or self._pins.get(None)
                    or self._agent_pins.get(agent)
                ):
                    continue
                if await registry.evict_idle(agent, before=time.monotonic() - age):
                    removed.append(agent)
            if pressure:
                pressure = await self.service.io.run(self.repo.sandbox_pressure)
        return removed

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(min(5, self.idle_seconds))
            try:
                # Never mutate a runtime after losing its database fence.
                if not await self.service.io.run(self.repo.node_ready, self.node, self.incarnation):
                    return
                registry = self.server.app_runtime.agent_registry
                # A configuration reload can also need a new sandbox. Reclaim
                # before retrying it so a budget rejection cannot starve the reaper.
                await self.reap()
                await self.refresh_configuration()
                for agent in tuple(registry._runtime_config_dirty):
                    await registry.reload(agent)
            except Exception:
                logger.exception("runtime refresh/reclamation failed; will retry")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
