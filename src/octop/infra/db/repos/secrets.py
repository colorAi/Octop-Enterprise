"""Secrets table access."""

from __future__ import annotations

from collections.abc import Callable

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts


class SecretRepo:
    def __init__(self, db: DatabasePool):
        self._db = db

    def get(self, k: str) -> bytes | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT v FROM secrets WHERE k = ?", (k,)).fetchone()
        return bytes(r["v"]) if r else None

    def get_or_create(self, k: str, factory: Callable[[], bytes]) -> bytes:
        existing = self.get(k)
        if existing is not None:
            return existing
        value = factory()
        with self._db.transaction() as conn:
            # Concurrent PostgreSQL workers may both observe an absent row.
            # Only the winning insert selects the shared identity.
            conn.execute(
                "INSERT INTO secrets(k, v, created_at) VALUES (?, ?, ?) ON CONFLICT(k) DO NOTHING",
                (k, value, now_ts()),
            )
            r = conn.execute("SELECT v FROM secrets WHERE k = ?", (k,)).fetchone()
            assert r is not None
            return bytes(r["v"])

    def rotate(self, k: str, new_value: bytes) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE secrets SET v = ?, rotated_at = ? WHERE k = ?",
                (new_value, now_ts(), k),
            )
