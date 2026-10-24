"""Composition root — wire OctopServer, FastAPI, and uvicorn for ``octop run``."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)


def _ensure_linux_bubblewrap() -> None:
    """Best-effort startup provisioning; never fail the ``octop run`` process."""
    try:
        from octop.infra.utils.bwrap import ensure_bubblewrap

        result = ensure_bubblewrap()
    except Exception:
        logger.warning("background bubblewrap provisioning failed", exc_info=True)
        return
    status = result.get("status")
    detail = result.get("detail")
    if status == "degraded":
        logger.warning("background bubblewrap provisioning degraded: %s", detail or "unknown")
    else:
        logger.info("background bubblewrap provisioning status=%s", status)


def _schedule_linux_bubblewrap_ensure() -> asyncio.Task[None] | None:
    """Run bubblewrap provisioning once in the background on Linux."""
    if not sys.platform.startswith("linux"):
        return None
    return asyncio.create_task(
        asyncio.to_thread(_ensure_linux_bubblewrap),
        name="ensure-linux-bubblewrap",
    )


async def _cancel_background_task(task: asyncio.Task[None] | None) -> None:
    """Drop a startup background task on shutdown; never raise."""
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _serve(server: Any) -> None:
    from octop.infra.utils.log_redaction import install_query_secret_filter

    install_query_secret_filter()
    await server.serve()


async def run_foreground(
    *,
    host: str | None,
    port: int | None,
    reload: bool,
    workers: int,
    log_level: str | None,
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
    role: str = "monolith",
) -> None:
    """Boot the domain server, serve the HTTP API, then shut down cleanly."""
    import uvicorn

    if role not in {"monolith", "init", "web", "worker"}:
        raise ValueError("unsupported process role")
    if role != "monolith" and (workers != 1 or reload):
        raise ValueError("distributed roles run one process per container")
    if role in {"web", "worker"}:
        from concurrent.futures import ThreadPoolExecutor

        from octop.infra.execution.profiles import profile

        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(
                max_workers=4 if role == "web" else profile().threads, thread_name_prefix="octop-io"
            )
        )

    if role in {"init", "web"}:
        from octop.infra.execution.roles import WebProcess, initialize
        from octop.infra.utils.paths import PathLayout

        if role == "init":
            await asyncio.to_thread(initialize, PathLayout.from_env().root)
            return
        if workers != 1 or reload:
            raise ValueError("web roles run one process per container")
        import uuid

        from octop.api.enterprise import build_web_app

        process = WebProcess(
            PathLayout.from_env().root,
            node=os.environ.get("OCTOP_NODE_ID") or "web-" + uuid.uuid4().hex,
        )
        app = None
        try:
            app = build_web_app(process, os.environ.get("OCTOP_WORKER_URL", ""))
            await process.start()
            await _serve(
                uvicorn.Server(
                    uvicorn.Config(
                        app,
                        host=host or process.config.bind_host,
                        port=port or process.config.port,
                        log_level=log_level or process.config.log_level,
                        ssl_certfile=ssl_certfile,
                        ssl_keyfile=ssl_keyfile,
                        ws_max_size=1024 * 1024,
                        ws_max_queue=16,
                        limit_concurrency=256,
                    )
                )
            )
        finally:
            if app is not None:
                await app.state.runtime_client.aclose()
            await process.stop()
        return

    from octop.api.app import build_app
    from octop.infra.server import OctopServer
    from octop.infra.setup.tls.http_companion import build_http_companion_app
    from octop.infra.setup.tls.listeners import build_listen_plan
    from octop.infra.setup.tls.store import resolve_tls_paths

    srv = OctopServer() if role == "monolith" else OctopServer(role=role)
    await srv.start()
    if role == "worker":
        from octop.infra.agents.memory.slim_control import MemorySlimControl

        assert srv.config is not None
        control = MemorySlimControl(srv)
        try:
            await control.start()
            # TLS terminates at the edge. Stored monolith TLS settings must not
            # change the private origin or start extra listeners in this role.
            await _serve(
                uvicorn.Server(
                    uvicorn.Config(
                        build_app(srv),
                        host=host or "0.0.0.0",
                        port=port or 8088,
                        log_level=log_level or srv.config.log_level,
                        ws_max_size=1024 * 1024,
                        ws_max_queue=16,
                        limit_concurrency=256,
                    )
                )
            )
        finally:
            await control.close()
            await srv.stop()
        return
    bwrap_task = _schedule_linux_bubblewrap_ensure()
    # Greenfield deferral leaves services unset until /setup/database binds.
    cfg = srv.services.config if srv.services is not None else srv.config
    assert cfg is not None
    bind_host = host or cfg.bind_host
    bind_port = port or cfg.port
    if ssl_certfile is None and ssl_keyfile is None:
        ssl_certfile, ssl_keyfile = resolve_tls_paths(srv.paths.root, cfg.tls)

    plan = build_listen_plan(
        cfg,
        bind_host=bind_host,
        bind_port=bind_port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )
    level = (log_level or cfg.log_level).lower()
    worker_count = 1 if plan.dual_listeners or reload else workers
    if plan.dual_listeners and (reload or workers > 1):
        logger.warning(
            "TLS dual-port mode forces workers=1 and disables reload",
        )
        reload = False

    app = build_app(srv)
    servers: list[uvicorn.Server] = []

    if plan.dual_listeners:
        assert plan.https_port is not None
        assert plan.http_port is not None
        https_config = uvicorn.Config(
            app,
            host=plan.bind_host,
            port=plan.https_port,
            log_level=level,
            workers=worker_count,
            reload=False,
            ssl_certfile=plan.ssl_certfile,
            ssl_keyfile=plan.ssl_keyfile,
        )
        companion = build_http_companion_app(https_port=plan.https_port)
        http_config = uvicorn.Config(
            companion,
            host=plan.bind_host,
            port=plan.http_port,
            log_level=level,
            workers=1,
            reload=False,
        )
        servers.append(uvicorn.Server(https_config))
        servers.append(uvicorn.Server(http_config))
        logger.info(
            "Listening on https://%s:%s and http://%s:%s (ACME + redirect)",
            plan.bind_host,
            plan.https_port,
            plan.bind_host,
            plan.http_port,
        )
    else:
        single_config = uvicorn.Config(
            app,
            host=plan.bind_host,
            port=plan.http_port or bind_port,
            log_level=level,
            workers=worker_count,
            reload=reload,
            ssl_certfile=plan.ssl_certfile,
            ssl_keyfile=plan.ssl_keyfile,
        )
        servers.append(uvicorn.Server(single_config))

    from octop.infra.agents.memory.slim_control import MemorySlimControl

    memory_control = MemorySlimControl(srv)
    try:
        await memory_control.start()
        await asyncio.gather(*(_serve(s) for s in servers))
    finally:
        await memory_control.close()
        await _cancel_background_task(bwrap_task)
        await srv.stop()


def run_foreground_blocking(**kwargs: Any) -> None:
    """Sync entry for Click commands and tests."""
    asyncio.run(run_foreground(**kwargs))
