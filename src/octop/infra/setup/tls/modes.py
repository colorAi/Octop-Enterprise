"""TLS issuance mode helpers (first issue vs renewal)."""

from __future__ import annotations

from enum import StrEnum

from octop.config import OctopConfig
from octop.infra.setup.tls.listeners import TLS_HTTP_PORT, TLS_HTTPS_PORT


class TlsIssueMode(StrEnum):
    FIRST = "first"
    RENEWAL = "renewal"
    NONE = "none"


def normalize_domain(domain: str) -> str:
    return domain.strip().lower().rstrip(".")


def tls_issue_mode(config: OctopConfig) -> TlsIssueMode:
    if config.bind_host != "0.0.0.0":
        return TlsIssueMode.NONE
    if (
        config.tls.enabled
        and config.port == TLS_HTTPS_PORT
        and config.tls.http_port == TLS_HTTP_PORT
    ):
        return TlsIssueMode.RENEWAL
    if not config.tls.enabled and config.port == TLS_HTTP_PORT:
        return TlsIssueMode.FIRST
    return TlsIssueMode.NONE


def is_issue_eligible(config: OctopConfig) -> bool:
    return tls_issue_mode(config) != TlsIssueMode.NONE


def is_renewal_mode(config: OctopConfig) -> bool:
    return tls_issue_mode(config) == TlsIssueMode.RENEWAL


def validate_issue_domain(domain: str, config: OctopConfig) -> str:
    """Return normalized domain; raise ValueError when renewal domain mismatches."""
    normalized = normalize_domain(domain)
    if not normalized:
        msg = "domain is required"
        raise ValueError(msg)
    if is_renewal_mode(config) and config.tls.domains:
        expected = normalize_domain(config.tls.domains[0])
        if normalized != expected:
            msg = f"renewal domain must be {expected!r}, got {normalized!r}"
            raise ValueError(msg)
    return normalized
