"""In-memory wizard session tokens (post password verification)."""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

RATE_LIMIT_BURST = 5
RATE_LIMIT_WINDOW = 60.0


class RateLimited(Exception):
    """Raised when a client exceeds the verify-password attempt budget."""


@dataclass
class _TokenEntry:
    expires_at: float


@dataclass
class WizardTokenStore:
    ttl_seconds: int
    now: Callable[[], float] = field(default=time.monotonic)
    _tokens: dict[str, _TokenEntry] = field(default_factory=dict)
    _attempts: dict[str, deque[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def issue(self) -> tuple[str, int]:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._tokens[token] = _TokenEntry(expires_at=self.now() + self.ttl_seconds)
        return token, self.ttl_seconds

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None:
                return False
            if entry.expires_at <= self.now():
                self._tokens.pop(token, None)
                return False
            return True

    def consume(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()

    def record_attempt(self, client_id: str) -> None:
        cutoff = self.now() - RATE_LIMIT_WINDOW
        with self._lock:
            bucket = self._attempts.setdefault(client_id, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= RATE_LIMIT_BURST:
                raise RateLimited(f"too many wizard attempts; retry after {RATE_LIMIT_WINDOW:.0f}s")
            bucket.append(self.now())
