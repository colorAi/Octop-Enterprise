"""CAS pointers and durable change records for verified employee resource bundles.

These records pin content; they never replace fresh source/assignment checks.
"""

from __future__ import annotations

import re
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts


class ManifestConflict(ValueError):
    pass


class ResourceManifestRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    def head(self, agent_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM managed_resource_manifests WHERE agent_id=?", (agent_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    @staticmethod
    def _authorized(
        conn: Any,
        user_id: int,
        agent_id: str,
        source_row_id: int,
        selection: int,
        source: int,
        membership: int,
    ) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM assigned_expert_instances i "
                "JOIN agents instance ON instance.agent_id=i.instance_agent_id "
                "JOIN agents g ON g.agent_id=i.source_agent_id AND g.id=i.source_row_id "
                "JOIN expert_assignments a ON a.user_id=i.user_id AND a.source_agent_id=g.agent_id "
                "JOIN expert_assignment_source s ON s.source_user_id=g.user_id AND s.id=1 "
                "JOIN users owner ON owner.id=s.source_user_id "
                "JOIN users employee ON employee.id=i.user_id "
                "JOIN employee_resource_scopes scope ON scope.user_id=i.user_id "
                "WHERE i.instance_agent_id=? AND i.user_id=? AND i.source_row_id=? "
                "AND instance.user_id=i.user_id AND instance.enabled=1 AND instance.kind='expert' "
                "AND g.enabled=1 AND g.kind='expert' AND owner.disabled=0 AND owner.role='admin' "
                "AND employee.disabled=0 AND employee.expert_assignment_revision=? "
                "AND s.revision=? AND scope.membership_revision=? AND scope.pending_scope IS NULL",
                (agent_id, user_id, source_row_id, selection, source, membership),
            ).fetchone()
            is not None
        )

    def publish(
        self,
        *,
        user_id: int,
        agent_id: str,
        source_row_id: int,
        expected_generation: int,
        manifest_sha256: str,
        resource_revision: str,
    ) -> dict[str, Any]:
        """Atomically switch the pointer and append an event after files are durable.

        The lock order matches assignment saves: source, employee, scope, head.
        Source serialization also keeps outbox IDs in publication commit order.
        Direct legacy file edits still require content verification by the caller.
        """
        if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
            raise ValueError("invalid_manifest_digest")
        parts = resource_revision.split(":")
        if len(parts) != 4 or not re.fullmatch(r"[0-9a-f]{64}", parts[3]):
            raise ValueError("invalid_resource_revision")
        selection, source, membership = map(int, parts[:3])
        if min(selection, source, membership, expected_generation) < 0:
            raise ValueError("invalid_resource_revision")
        # Reusing a verified bundle is read-only; it must not update the shared
        # source row on every preparation. Check current authority even on a hit.
        with self.db.connect() as conn:
            old = conn.execute(
                "SELECT * FROM managed_resource_manifests WHERE agent_id=?", (agent_id,)
            ).fetchone()
            if (
                old
                and old["manifest_sha256"] == manifest_sha256
                and old["resource_revision"] == resource_revision
                and self._authorized(
                    conn, user_id, agent_id, source_row_id, selection, source, membership
                )
            ):
                return dict(old)
        now = now_ts()
        with self.db.transaction() as conn:
            if not conn.execute(
                "UPDATE expert_assignment_source SET revision=revision WHERE id=1 AND revision=?",
                (source,),
            ).rowcount:
                raise ManifestConflict("employee_resources_changed")
            if not conn.execute(
                "UPDATE users SET expert_assignment_revision=expert_assignment_revision "
                "WHERE id=? AND disabled=0 AND expert_assignment_revision=?",
                (user_id, selection),
            ).rowcount:
                raise ManifestConflict("employee_resources_changed")
            if not conn.execute(
                "UPDATE employee_resource_scopes SET membership_revision=membership_revision "
                "WHERE user_id=? AND membership_revision=? AND pending_scope IS NULL",
                (user_id, membership),
            ).rowcount:
                raise ManifestConflict("employee_resources_changed")
            if not self._authorized(
                conn, user_id, agent_id, source_row_id, selection, source, membership
            ):
                raise ManifestConflict("resource_not_assigned")
            old = conn.execute(
                "SELECT * FROM managed_resource_manifests WHERE agent_id=?", (agent_id,)
            ).fetchone()
            if old and (
                old["manifest_sha256"] == manifest_sha256
                and old["resource_revision"] == resource_revision
            ):
                return dict(old)
            if (old["generation"] if old else 0) != expected_generation:
                raise ManifestConflict("employee_resources_changed")
            generation = expected_generation + 1
            if old is None:
                # A private instance may be recreated with its reserved public ID.
                # Its cascaded head must not reuse an existing event generation.
                generation = conn.execute(
                    "SELECT COALESCE(MAX(generation),0)+1 FROM managed_resource_outbox WHERE agent_id=?",
                    (agent_id,),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO managed_resource_manifests VALUES (?,?,?,?,?)",
                    (agent_id, generation, manifest_sha256, resource_revision, now),
                )
            elif not conn.execute(
                "UPDATE managed_resource_manifests SET generation=?,manifest_sha256=?,"
                "resource_revision=?,updated_at=? WHERE agent_id=? AND generation=?",
                (
                    generation,
                    manifest_sha256,
                    resource_revision,
                    now,
                    agent_id,
                    expected_generation,
                ),
            ).rowcount:
                raise ManifestConflict("employee_resources_changed")
            conn.execute(
                "INSERT INTO managed_resource_outbox "
                "(agent_id,user_id,generation,manifest_sha256,resource_revision,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (agent_id, user_id, generation, manifest_sha256, resource_revision, now),
            )
            return {
                "agent_id": agent_id,
                "generation": generation,
                "manifest_sha256": manifest_sha256,
                "resource_revision": resource_revision,
                "updated_at": now,
            }

    def events(self, *, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        if after < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid_event_cursor")
        with self.db.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM managed_resource_outbox WHERE id>? ORDER BY id LIMIT ?",
                    (after, limit),
                ).fetchall()
            ]
