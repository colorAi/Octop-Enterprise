"""Unit tests for the in-memory wizard token store + rate limiter."""

from __future__ import annotations

import pytest

from octop.infra.setup.wizard_tokens import (
    RATE_LIMIT_BURST,
    RateLimited,
    WizardTokenStore,
)


def test_issue_returns_token_and_ttl() -> None:
    store = WizardTokenStore(ttl_seconds=300)
    token, ttl = store.issue()
    assert isinstance(token, str)
    assert len(token) >= 20
    assert ttl == 300


def test_validate_accepts_just_issued_token() -> None:
    store = WizardTokenStore(ttl_seconds=300)
    token, _ = store.issue()
    assert store.validate(token) is True


def test_validate_rejects_unknown_token() -> None:
    store = WizardTokenStore(ttl_seconds=300)
    assert store.validate("not-a-token") is False


def test_validate_rejects_none_or_empty() -> None:
    store = WizardTokenStore(ttl_seconds=300)
    assert store.validate(None) is False
    assert store.validate("") is False


def test_consume_invalidates_token() -> None:
    store = WizardTokenStore(ttl_seconds=300)
    token, _ = store.issue()
    store.consume(token)
    assert store.validate(token) is False


def test_token_expires_after_ttl() -> None:
    fake_now = [1000.0]
    store = WizardTokenStore(ttl_seconds=1, now=lambda: fake_now[0])
    token, _ = store.issue()
    fake_now[0] += 1.5
    assert store.validate(token) is False


def test_rate_limit_blocks_after_burst() -> None:
    store = WizardTokenStore(ttl_seconds=300)
    for _ in range(RATE_LIMIT_BURST):
        store.record_attempt("1.2.3.4")
    with pytest.raises(RateLimited):
        store.record_attempt("1.2.3.4")


def test_rate_limit_separate_clients_independent() -> None:
    store = WizardTokenStore(ttl_seconds=300)
    for _ in range(RATE_LIMIT_BURST):
        store.record_attempt("1.2.3.4")
    store.record_attempt("5.6.7.8")  # Other client unaffected.


def test_rate_limit_window_resets_after_60s() -> None:
    fake_now = [1000.0]
    store = WizardTokenStore(ttl_seconds=300, now=lambda: fake_now[0])
    for _ in range(RATE_LIMIT_BURST):
        store.record_attempt("1.2.3.4")
    fake_now[0] += 61
    # All previous attempts must have been evicted from the bucket;
    # we should be able to fully fill the budget again without raising.
    for _ in range(RATE_LIMIT_BURST):
        store.record_attempt("1.2.3.4")
    with pytest.raises(RateLimited):
        store.record_attempt("1.2.3.4")


def test_rate_limit_window_does_not_reset_too_early() -> None:
    """At t+59s (still inside the 60s window), prior attempts still count."""
    fake_now = [1000.0]
    store = WizardTokenStore(ttl_seconds=300, now=lambda: fake_now[0])
    for _ in range(RATE_LIMIT_BURST):
        store.record_attempt("1.2.3.4")
    fake_now[0] += 59
    with pytest.raises(RateLimited):
        store.record_attempt("1.2.3.4")
