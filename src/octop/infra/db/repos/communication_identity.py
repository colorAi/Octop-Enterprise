"""Durable identity revisions, delivery attempts and one-use realtime tickets."""

from __future__ import annotations

import json
import time
from typing import Any

from octop.infra.db.pool import DatabasePool


class CommunicationIdentityRepo:
    def __init__(self, db: DatabasePool):
        self._db = db

    def enqueue_reconciliation(self) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO communication_identity_outbox(user_id,version,payload) "
                "SELECT user_id,version,payload FROM communication_identities WHERE 1=1 "
                "ON CONFLICT(user_id,version) DO NOTHING"
            )

    def refresh_profile(self, user_id: int) -> None:
        """Publish a revision when a file-backed portrait changes."""
        with self._db.transaction() as conn:
            row = conn.execute(
                "UPDATE communication_identities SET version=version+1 "
                "WHERE user_id=? RETURNING version,payload",
                (user_id,),
            ).fetchone()
            if row is not None:
                conn.execute(
                    "INSERT INTO communication_identity_outbox(user_id,version,payload) VALUES(?,?,?)",
                    (user_id, row["version"], row["payload"]),
                )

    def current(self, user_id: int) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT version,payload FROM communication_identities WHERE user_id=?", (user_id,)
            ).fetchone()
        if row is None:
            return None
        return {**json.loads(row["payload"]), "version": int(row["version"])}

    def pending(self, user_id: int | None = None, *, due: bool = True) -> list[dict[str, Any]]:
        sql = (
            "SELECT o.* FROM communication_identity_outbox o WHERE NOT EXISTS("
            "SELECT 1 FROM communication_identity_outbox older "
            "WHERE older.user_id=o.user_id AND older.version<o.version)"
        )
        args: list[Any] = []
        if user_id is not None:
            sql += " AND o.user_id=?"
            args.append(user_id)
        if due:
            sql += " AND o.next_attempt_at<=?"
            args.append(int(time.time()))
        with self._db.connect() as conn:
            rows = conn.execute(sql + " ORDER BY o.user_id LIMIT 100", args).fetchall()
        return [dict(row) for row in rows]

    def acknowledge(self, user_id: int, version: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM communication_identity_outbox WHERE user_id=? AND version=?",
                (user_id, version),
            )

    def failed(self, user_id: int, version: int, attempts: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE communication_identity_outbox SET attempts=attempts+1,"
                "next_attempt_at=?,last_error='upstream_unavailable' WHERE user_id=? AND version=?",
                (int(time.time()) + min(300, 2 ** min(attempts + 1, 8)), user_id, version),
            )

    def store_ticket(
        self, digest: str, user_id: int, session: str, expires: int, session_expires: int
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM communication_tickets WHERE expires_at<=?", (int(time.time()),)
            )
            conn.execute(
                "INSERT INTO communication_tickets(token_hash,user_id,session_id,expires_at,session_expires_at)"
                " VALUES(?,?,?,?,?)",
                (digest, user_id, session, expires, session_expires),
            )

    def consume_ticket(self, digest: str) -> dict[str, Any] | None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "DELETE FROM communication_tickets WHERE token_hash=? RETURNING *", (digest,)
            ).fetchone()
        if row is None or int(row["expires_at"]) <= int(time.time()):
            return None
        return dict(row)

    def revoke_session(self, session: str, expires: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO communication_revoked_sessions(session_id,expires_at)VALUES(?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET expires_at=CASE "
                "WHEN communication_revoked_sessions.expires_at>excluded.expires_at "
                "THEN communication_revoked_sessions.expires_at ELSE excluded.expires_at END",
                (session, expires),
            )
            conn.execute("DELETE FROM communication_tickets WHERE session_id=?", (session,))

    def session_revoked(self, session: str) -> bool:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM communication_revoked_sessions WHERE session_id=? AND expires_at>?",
                (session, int(time.time())),
            ).fetchone()
        return row is not None
