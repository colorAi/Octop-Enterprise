"""Compute which HTTP/HTTPS ports ``octop run`` should bind."""

from __future__ import annotations

from dataclasses import dataclass

from octop.config import OctopConfig

TLS_HTTPS_PORT = 443
TLS_HTTP_PORT = 80


@dataclass(frozen=True)
class ListenPlan:
    """Resolved bind plan for uvicorn."""

    bind_host: str
    https_port: int | None
    http_port: int | None
    ssl_certfile: str | None
    ssl_keyfile: str | None

    @property
    def dual_listeners(self) -> bool:
        return (
            self.https_port is not None
            and self.http_port is not None
            and self.ssl_certfile is not None
            and self.ssl_keyfile is not None
        )


def build_listen_plan(
    config: OctopConfig,
    *,
    bind_host: str,
    bind_port: int,
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
) -> ListenPlan:
    """When TLS is active, bind HTTPS on ``port`` and HTTP on ``tls.http_port``."""
    if (
        ssl_certfile
        and ssl_keyfile
        and config.tls.enabled
        and config.tls.http_port > 0
        and config.tls.http_port != bind_port
    ):
        return ListenPlan(
            bind_host=bind_host,
            https_port=bind_port,
            http_port=config.tls.http_port,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
        )
    return ListenPlan(
        bind_host=bind_host,
        https_port=None,
        http_port=bind_port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )
