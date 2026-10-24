"""Published source heads and automatic draft-change tracking."""

from __future__ import annotations

import re
import time
from typing import Any

from octop.infra.db.pool import DatabasePool


class SourceConflict(ValueError):
    pass


class SourcePublicationRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    def head(self, user: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM resource_source_heads WHERE user_id=?", (user,)
            ).fetchone()
            return dict(row) if row else None

    def begin(self, user: int) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO resource_source_heads(user_id) VALUES(?) ON CONFLICT(user_id) DO NOTHING",
                (user,),
            )
            conn.execute(
                "UPDATE resource_source_heads SET editing=1,failed=0,dirty_revision=dirty_revision+1 WHERE user_id=?",
                (user,),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM resource_source_heads WHERE user_id=?", (user,)
                ).fetchone()
            )

    def fail(self, user: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE resource_source_heads SET editing=0,failed=1 WHERE user_id=?", (user,)
            )

    def dirty(self) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE resource_source_heads SET dirty_revision=dirty_revision+1")

    def publish(
        self,
        user: int,
        *,
        generation: int,
        revision: int,
        digest: str,
        executor: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid_source_manifest_digest")
        with self.db.transaction() as conn:
            if executor is not None:
                # Share ordering with lease expiry and runtime-host handover.
                conn.execute("UPDATE execution_control SET id=id WHERE id=1")
                clock = (
                    "CAST(EXTRACT(EPOCH FROM clock_timestamp()) AS BIGINT)"
                    if self.db.dialect == "postgresql"
                    else "CAST(strftime('%s','now') AS INTEGER)"
                )
                if not conn.execute(
                    "SELECT 1 FROM execution_nodes n JOIN execution_leaders l "
                    "ON l.node_id=n.node_id AND l.incarnation=n.incarnation "
                    "WHERE l.name='runtime-host' AND n.node_id=? AND n.incarnation=? "
                    "AND n.state='ready' AND n.lease_until>" + clock,
                    executor,
                ).fetchone():
                    raise SourceConflict("resource_executor_fenced")
            conn.execute(
                "UPDATE resource_source_heads SET user_id=user_id WHERE user_id=?", (user,)
            )
            row = conn.execute(
                "SELECT * FROM resource_source_heads WHERE user_id=?", (user,)
            ).fetchone()
            authorized = conn.execute(
                "SELECT 1 FROM expert_assignment_source s JOIN users u ON u.id=s.source_user_id WHERE s.source_user_id=? AND u.role='admin' AND u.disabled=0",
                (user,),
            ).fetchone()
            if (
                not row
                or not authorized
                or row["generation"] != generation
                or row["dirty_revision"] != revision
            ):
                raise SourceConflict("resource_source_changed")
            changed = row["manifest_sha256"] != digest
            next_generation = generation + int(changed)
            now = int(time.time())
            conn.execute(
                "UPDATE resource_source_heads SET generation=?,manifest_sha256=?,published_revision=?,editing=0,failed=0,updated_at=? WHERE user_id=?",
                (next_generation, digest, revision, now, user),
            )
            if changed:
                conn.execute(
                    "INSERT INTO resource_source_events(user_id,generation,manifest_sha256,created_at) VALUES(?,?,?,?)",
                    (user, next_generation, digest, now),
                )
            if self.db.dialect == "postgresql":
                conn.execute("SELECT pg_notify('octop_execution','resources')")
            return dict(
                conn.execute(
                    "SELECT * FROM resource_source_heads WHERE user_id=?", (user,)
                ).fetchone()
            )
