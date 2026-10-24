"""Bounded readiness probes and read-only enterprise storage diagnostics."""

from __future__ import annotations

import os
from typing import Any, cast

import httpx

from octop.infra.backend.isolation import isolation_enabled
from octop.infra.company_runtime.maintenance import storage_summary
from octop.infra.utils.asyncio import offload


def _database_ready(server: Any) -> bool:
    if not server.database_bound:
        return False
    try:
        with server.services.db.connect() as conn:
            return bool(conn.execute("SELECT 1").fetchone()[0] == 1)
    except Exception:
        return False


def _sandbox_ready() -> bool:
    import docker

    client = cast(Any, docker).from_env(timeout=3)
    try:
        client.ping()
        client.images.get(os.environ.get("OCTOP_SANDBOX_IMAGE", ""))
        return True
    finally:
        client.close()


async def readiness(server: Any) -> dict[str, Any]:
    checks = {"database": await offload(_database_ready, server)}
    execution = getattr(server, "execution", None)
    if execution is not None:
        try:
            checks["execution"] = execution._accepting and await execution.io.run(
                execution.repo.runtime_ready
            )
        except Exception:
            checks["execution"] = False
    bridge = getattr(server, "communication", None)
    if os.environ.get("OCTOP_IM_URL") or (bridge is not None and bridge.enabled):
        checks["communication"] = False
        if bridge is not None and bridge.enabled:
            try:
                async with httpx.AsyncClient(
                    timeout=3, follow_redirects=False, trust_env=False
                ) as client:
                    response = await client.get(bridge.url + "/healthz")
                checks["communication"] = response.status_code == 200
            except httpx.HTTPError:
                pass
    if isolation_enabled():
        try:
            checks["sandbox"] = await offload(_sandbox_ready)
        except Exception:
            checks["sandbox"] = False
    return {"ok": all(checks.values()), "checks": checks}


async def enterprise_storage(server: Any) -> dict[str, Any]:
    return await offload(storage_summary, server.services.db, server.services.paths.root)
