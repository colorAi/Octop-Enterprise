"""In-memory HTTP-01 challenge responses for ACME."""

from __future__ import annotations

import threading


class ChallengeStore:
    """Maps ACME challenge token → key authorization (plain text response body)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._responses: dict[str, str] = {}

    def set(self, token: str, key_authorization: str) -> None:
        with self._lock:
            self._responses[token] = key_authorization

    def get(self, token: str) -> str | None:
        with self._lock:
            return self._responses.get(token)

    def clear(self) -> None:
        with self._lock:
            self._responses.clear()


challenge_store = ChallengeStore()
