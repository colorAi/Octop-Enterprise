"""Unit tests for dual-port listen plan."""

from __future__ import annotations

from octop.config import OctopConfig, TlsConfig
from octop.infra.setup.tls.listeners import build_listen_plan


def test_single_listener_without_tls():
    cfg = OctopConfig(port=8088)
    plan = build_listen_plan(
        cfg, bind_host="127.0.0.1", bind_port=8088, ssl_certfile=None, ssl_keyfile=None
    )
    assert not plan.dual_listeners
    assert plan.http_port == 8088
    assert plan.https_port is None


def test_dual_listeners_when_tls_enabled():
    cfg = OctopConfig(
        bind_host="0.0.0.0",
        port=443,
        tls=TlsConfig(
            enabled=True,
            http_port=80,
            cert_file="ssl/fullchain.pem",
            key_file="ssl/privkey.pem",
        ),
    )
    plan = build_listen_plan(
        cfg,
        bind_host="0.0.0.0",
        bind_port=443,
        ssl_certfile="/tmp/cert.pem",
        ssl_keyfile="/tmp/key.pem",
    )
    assert plan.dual_listeners
    assert plan.https_port == 443
    assert plan.http_port == 80
