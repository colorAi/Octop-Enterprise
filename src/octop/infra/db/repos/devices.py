"""Device identities and one-use pairing codes; never store bearer credentials."""

from __future__ import annotations

from typing import Any

from octop.infra.db.pool import DatabasePool


class DeviceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def issue_code(
        self, digest: str, owner: int, session: str, expires: int, credential_expires: int
    ) -> None:
        with self._db.transaction() as conn:
            # One pending code per employee. Issuing another invalidates the previous one.
            conn.execute("DELETE FROM desktop_pairing_codes WHERE owner_user_id=?", (owner,))
            conn.execute(
                "INSERT INTO desktop_pairing_codes VALUES(?,?,?,?,?)",
                (digest, owner, session, expires, credential_expires),
            )

    def pair(
        self,
        digest: str,
        device_id: str,
        credential_hash: str,
        name: str,
        platform: str,
        protocol: int,
        now: int,
    ) -> dict[str, Any] | None:
        with self._db.transaction() as conn:
            code = conn.execute(
                "DELETE FROM desktop_pairing_codes WHERE code_hash=? RETURNING *", (digest,)
            ).fetchone()
            if code is None or code["expires_at"] <= now or code["credential_expires_at"] <= now:
                return None
            if self._db.dialect == "postgresql":
                # Serialize the per-employee device limit (SQLite already locks writes).
                conn.execute(
                    "SELECT id FROM users WHERE id=? FOR UPDATE", (code["owner_user_id"],)
                ).fetchone()
            active = conn.execute(
                "SELECT 1 FROM users u WHERE u.id=? AND u.disabled=0 AND NOT EXISTS("
                "SELECT 1 FROM communication_revoked_sessions s WHERE s.session_id=? "
                "AND s.expires_at>?)",
                (code["owner_user_id"], code["session_id"], now),
            ).fetchone()
            if active is None:
                return None
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM desktop_devices WHERE owner_user_id=? "
                "AND revoked_at IS NULL AND expires_at>?",
                (code["owner_user_id"], now),
            ).fetchone()
            if count["n"] >= 8:
                return None
            conn.execute(
                "INSERT INTO desktop_devices(device_id,owner_user_id,session_id,credential_hash,"
                "name,platform,protocol_version,expires_at,created_at,last_seen_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    device_id,
                    code["owner_user_id"],
                    code["session_id"],
                    credential_hash,
                    name,
                    platform,
                    protocol,
                    code["credential_expires_at"],
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM desktop_devices WHERE device_id=?", (device_id,)
            ).fetchone()
        return dict(row)

    def authenticate(self, digest: str, now: int) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT d.* FROM desktop_devices d JOIN users u ON u.id=d.owner_user_id "
                "WHERE d.credential_hash=? AND d.revoked_at IS NULL AND d.expires_at>? "
                "AND u.disabled=0 AND NOT EXISTS(SELECT 1 FROM communication_revoked_sessions s "
                "WHERE s.session_id=d.session_id AND s.expires_at>?)",
                (digest, now, now),
            ).fetchone()
        return dict(row) if row else None

    def pairing_owner(self, digest: str, now: int) -> int | None:
        """Route the initial device frame without consuming its one-use code."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT p.owner_user_id FROM desktop_pairing_codes p JOIN users u ON u.id=p.owner_user_id "
                "WHERE p.code_hash=? AND p.expires_at>? AND p.credential_expires_at>? AND u.disabled=0 "
                "AND NOT EXISTS(SELECT 1 FROM communication_revoked_sessions s WHERE s.session_id=p.session_id AND s.expires_at>?)",
                (digest, now, now, now),
            ).fetchone()
        return int(row[0]) if row else None

    def list_for_owner(self, owner: int) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT device_id,name,platform,protocol_version,expires_at,created_at,"
                "last_seen_at,revoked_at FROM desktop_devices WHERE owner_user_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT 100",
                (owner,),
            ).fetchall()
        return [dict(row) for row in rows]

    def touch(self, device_id: str, now: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE desktop_devices SET last_seen_at=? WHERE device_id=?",
                (now, device_id),
            )

    def revoke(self, device_id: str, owner: int, now: int) -> bool:
        with self._db.transaction() as conn:
            row = conn.execute(
                "UPDATE desktop_devices SET revoked_at=COALESCE(revoked_at,?) "
                "WHERE device_id=? AND owner_user_id=? RETURNING device_id",
                (now, device_id, owner),
            ).fetchone()
        return row is not None

    def revoke_session(self, session: str, now: int) -> list[str]:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM desktop_pairing_codes WHERE session_id=?", (session,))
            rows = conn.execute(
                "UPDATE desktop_devices SET revoked_at=COALESCE(revoked_at,?) "
                "WHERE session_id=? RETURNING device_id",
                (now, session),
            ).fetchall()
        return [str(row["device_id"]) for row in rows]

    def mark_file_thread(self, thread_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO desktop_file_threads(thread_id) VALUES(?) ON CONFLICT DO NOTHING",
                (thread_id,),
            )

    def is_file_thread(self, thread_id: str) -> bool:
        with self._db.connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM desktop_file_threads WHERE thread_id=?", (thread_id,)
                ).fetchone()
                is not None
            )
