"""Administrator assignments and employee instance identities."""

from __future__ import annotations

import json
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.utils.ulid import new_short_id


class AssignmentConflict(ValueError):
    pass


class ExpertAssignmentRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def source(self) -> dict[str, Any]:
        with self._db.connect() as conn:
            return dict(
                conn.execute(
                    "SELECT id,source_user_id,revision FROM expert_assignment_source WHERE id=1"
                ).fetchone()
            )

    def state(self, user_id: int) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT id, expert_access_mode AS mode, expert_assignment_revision AS revision "
                "FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["expert_ids"] = [
                r[0]
                for r in conn.execute(
                    "SELECT source_agent_id FROM expert_assignments WHERE user_id=? "
                    "ORDER BY source_agent_id",
                    (user_id,),
                ).fetchall()
            ]
            return result

    def set_source(self, source_user_id: int, expected_revision: int, actor: str) -> None:
        with self._db.transaction() as conn:
            old = conn.execute(
                "SELECT source_user_id FROM expert_assignment_source WHERE id=1"
            ).fetchone()
            if (
                old
                and old[0] != source_user_id
                and (
                    conn.execute("SELECT 1 FROM employee_skill_assignments LIMIT 1").fetchone()
                    or conn.execute(
                        "SELECT 1 FROM department_resource_pools WHERE expert_ids<>'[]' OR skill_ids<>'[]' LIMIT 1"
                    ).fetchone()
                    or conn.execute(
                        "SELECT 1 FROM expert_assignment_source WHERE default_skill_ids<>'[]'"
                    ).fetchone()
                )
            ):
                raise AssignmentConflict("source_has_assignments")
            changed = conn.execute(
                "UPDATE expert_assignment_source SET source_user_id=?, revision=revision+1 "
                "WHERE id=1 AND revision=?",
                (source_user_id, expected_revision),
            ).rowcount
            if not changed:
                raise AssignmentConflict("policy_revision_conflict")
            if conn.execute(
                "SELECT 1 FROM expert_assignments a JOIN agents g ON g.agent_id=a.source_agent_id "
                "WHERE g.user_id<>? OR g.user_id IS NULL LIMIT 1",
                (source_user_id,),
            ).fetchone():
                raise AssignmentConflict("source_has_assignments")
            self._audit(conn, actor, "expert_assignment.source", str(source_user_id), {})

    def save(
        self,
        user_id: int,
        expert_ids: list[str],
        mode: str,
        expected_revision: int,
        expected_source_revision: int,
        actor: str,
    ) -> None:
        with self._db.transaction() as conn:
            # Serialize selection saves against source changes on both database backends.
            if not conn.execute(
                "UPDATE expert_assignment_source SET revision=revision WHERE id=1 AND revision=?",
                (expected_source_revision,),
            ).rowcount:
                raise AssignmentConflict("policy_revision_conflict")
            valid = {
                r[0]
                for r in conn.execute(
                    "SELECT g.agent_id FROM agents g JOIN expert_assignment_source s "
                    "ON s.source_user_id=g.user_id JOIN users u ON u.id=s.source_user_id "
                    "WHERE s.id=1 AND u.role='admin' AND u.disabled=0 AND g.enabled=1 "
                    "AND g.kind='expert'",
                ).fetchall()
            }
            if not set(expert_ids).issubset(valid):
                raise AssignmentConflict("resource_source_unavailable")
            old = [
                r[0]
                for r in conn.execute(
                    "SELECT source_agent_id FROM expert_assignments WHERE user_id=?",
                    (user_id,),
                ).fetchall()
            ]
            if not conn.execute(
                "UPDATE users SET expert_access_mode=?, "
                "expert_assignment_revision=expert_assignment_revision+1 "
                "WHERE id=? AND role<>'admin' AND expert_assignment_revision=?",
                (mode, user_id, expected_revision),
            ).rowcount:
                raise AssignmentConflict("policy_revision_conflict")
            conn.execute("DELETE FROM expert_assignments WHERE user_id=?", (user_id,))
            for expert_id in expert_ids:
                conn.execute(
                    "INSERT INTO expert_assignments(user_id,source_agent_id) VALUES (?,?)",
                    (user_id, expert_id),
                )
            # Local tasks can still upload final checkpoints/events after cancellation.
            conn.execute(
                "UPDATE company_runtime_tasks SET status='cancel_requested',updated_at=? "
                "WHERE status='running' AND thread_id IN ("
                "SELECT t.thread_id FROM company_runtime_threads t WHERE t.owner_user_id=? "
                "AND ((?='assigned_only' AND NOT EXISTS (SELECT 1 FROM assigned_expert_instances i "
                "JOIN expert_assignments a ON a.user_id=i.user_id "
                "AND a.source_agent_id=i.source_agent_id "
                "JOIN agents g ON g.agent_id=a.source_agent_id AND g.id=i.source_row_id WHERE i.user_id=? "
                "AND i.instance_agent_id=t.agent_id)) OR EXISTS ("
                "SELECT 1 FROM assigned_expert_instances i WHERE i.user_id=? "
                "AND i.instance_agent_id=t.agent_id AND NOT EXISTS ("
                "SELECT 1 FROM expert_assignments a JOIN agents g "
                "ON g.agent_id=a.source_agent_id AND g.id=i.source_row_id WHERE a.user_id=i.user_id "
                "AND a.source_agent_id=i.source_agent_id))))",
                (now_ts(), user_id, mode, user_id, user_id),
            )
            self._audit(
                conn,
                actor,
                "expert_assignment.save",
                str(user_id),
                {
                    "before": old,
                    "after": expert_ids,
                    "mode": mode,
                },
            )

    def granted(self, user_id: int, source_id: str, source_row_id: int | None = None) -> bool:
        with self._db.connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM expert_assignments a JOIN agents g ON g.agent_id=a.source_agent_id "
                    "JOIN expert_assignment_source s ON s.source_user_id=g.user_id "
                    "JOIN users source ON source.id=s.source_user_id "
                    "JOIN users employee ON employee.id=a.user_id "
                    "WHERE a.user_id=? AND a.source_agent_id=? AND s.id=1 "
                    "AND (CAST(? AS INTEGER) IS NULL OR g.id=?) "
                    "AND g.enabled=1 AND g.kind='expert' AND source.role='admin' "
                    "AND source.disabled=0 AND employee.disabled=0",
                    (user_id, source_id, source_row_id, source_row_id),
                ).fetchone()
                is not None
            )

    def instance(self, instance_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM assigned_expert_instances WHERE instance_agent_id=?",
                (instance_id,),
            ).fetchone()
            return dict(row) if row else None

    def instances(self, user_id: int) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT i.* FROM assigned_expert_instances i JOIN agents g "
                    "ON g.agent_id=i.source_agent_id AND g.id=i.source_row_id WHERE i.user_id=?",
                    (user_id,),
                ).fetchall()
            ]

    def reserve(self, user_id: int, source_id: str) -> dict[str, Any]:
        with self._db.transaction() as conn:
            source = conn.execute("SELECT id FROM agents WHERE agent_id=?", (source_id,)).fetchone()
            if source is None:
                raise AssignmentConflict("resource_source_unavailable")
            source_row_id = int(source[0])
            conn.execute(
                "INSERT INTO assigned_expert_instances(user_id,source_agent_id,source_row_id,instance_agent_id) "
                "VALUES (?,?,?,?) ON CONFLICT(user_id,source_agent_id,source_row_id) DO NOTHING",
                (user_id, source_id, source_row_id, "M" + new_short_id(15)),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM assigned_expert_instances WHERE user_id=? AND source_agent_id=? AND source_row_id=?",
                    (user_id, source_id, source_row_id),
                ).fetchone()
            )

    def claim(self, instance_id: str) -> bool:
        with self._db.transaction() as conn:
            return bool(
                conn.execute(
                    "UPDATE assigned_expert_instances SET state='creating',claimed_at=? "
                    "WHERE instance_agent_id=? AND (state<>'creating' OR claimed_at<?)",
                    (now_ts(), instance_id, now_ts() - 600),
                ).rowcount
            )

    def finish_instance(self, instance_id: str, *, ready: bool) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE assigned_expert_instances SET state=? WHERE instance_agent_id=?",
                ("ready" if ready else "pending", instance_id),
            )

    @staticmethod
    def _audit(conn: Any, actor: str, action: str, target: str, payload: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES (?,?,?,?,?)",
            (now_ts(), actor, action, target, json.dumps(payload)),
        )
