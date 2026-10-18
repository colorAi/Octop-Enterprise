"""Unit tests for ACME challenge store."""

from __future__ import annotations

from octop.infra.setup.tls.challenge import ChallengeStore


def test_challenge_store_roundtrip():
    store = ChallengeStore()
    store.set("tok123", "key-auth-value")
    assert store.get("tok123") == "key-auth-value"
    assert store.get("missing") is None
    store.clear()
    assert store.get("tok123") is None
