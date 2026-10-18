"""Unit tests for TLS auto-renewal helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from octop.infra.setup.tls.renewal import cert_expires_within_days


def test_cert_expires_within_days_true():
    soon = (datetime.now(UTC) + timedelta(days=10)).replace(microsecond=0).isoformat()
    assert cert_expires_within_days(soon, days=30)


def test_cert_expires_within_days_false():
    later = (datetime.now(UTC) + timedelta(days=60)).replace(microsecond=0).isoformat()
    assert not cert_expires_within_days(later, days=30)


def test_cert_expires_within_days_invalid():
    assert not cert_expires_within_days("")
