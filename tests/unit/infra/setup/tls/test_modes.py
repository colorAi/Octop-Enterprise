"""Unit tests for TLS issue mode helpers."""

from __future__ import annotations

import pytest

from octop.config import OctopConfig, TlsConfig
from octop.infra.setup.tls.modes import (
    TlsIssueMode,
    is_issue_eligible,
    is_renewal_mode,
    tls_issue_mode,
    validate_issue_domain,
)


def test_first_issue_mode():
    cfg = OctopConfig(bind_host="0.0.0.0", port=80)
    assert tls_issue_mode(cfg) is TlsIssueMode.FIRST
    assert is_issue_eligible(cfg)
    assert not is_renewal_mode(cfg)


def test_renewal_mode():
    cfg = OctopConfig(
        bind_host="0.0.0.0",
        port=443,
        tls=TlsConfig(enabled=True, http_port=80, domains=["a.example.com"]),
    )
    assert tls_issue_mode(cfg) is TlsIssueMode.RENEWAL
    assert is_renewal_mode(cfg)


def test_validate_issue_domain_on_renewal():
    cfg = OctopConfig(
        bind_host="0.0.0.0",
        port=443,
        tls=TlsConfig(enabled=True, http_port=80, domains=["a.example.com"]),
    )
    assert validate_issue_domain("A.EXAMPLE.COM", cfg) == "a.example.com"
    with pytest.raises(ValueError, match="renewal domain"):
        validate_issue_domain("other.example.com", cfg)


def test_none_mode_when_wrong_port():
    cfg = OctopConfig(bind_host="0.0.0.0", port=8088)
    assert tls_issue_mode(cfg) is TlsIssueMode.NONE
    assert not is_issue_eligible(cfg)
