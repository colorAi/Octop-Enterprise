"""Durable admission, fencing and receipts shared by all execution transports.

Mutations lock one small admission row. This gives portable short transactions,
global limits and commit-ordered outbox IDs; no network/file work runs in here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.utils.ulid import new_ulid

TERMINAL = frozenset({"completed", "failed", "cancelled"})
MAX_PAYLOAD = 1024 * 1024


class ExecutionConflict(ValueError):
    pass


def wire(value: Any, *, limit: int = MAX_PAYLOAD) -> str:
    result = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    if len(result.encode()) > limit:
        raise ExecutionConflict("execution_payload_too_large")
    return result


class ExecutionRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    def _now(self, conn: Any) -> int:
        sql = (
            "SELECT CAST(EXTRACT(EPOCH FROM clock_timestamp()) AS BIGINT)"
            if self.db.dialect == "postgresql"
            else "SELECT CAST(strftime('%s','now') AS INTEGER)"
        )
        return int(conn.execute(sql).fetchone()[0])

    @contextmanager
    def _transaction(self) -> Iterator[tuple[Any, int]]:
        with self.db.transaction() as conn:
            conn.execute("UPDATE execution_control SET id=id WHERE id=1")
            yield conn, self._now(conn)
            if self.db.dialect == "postgresql":
                # Payload is intentionally constant: no content, identity or secrets.
                conn.execute("SELECT pg_notify('octop_execution','changed')")

    def configure(self, *, running: int, models: int = 0, queued: int, user_queued: int) -> None:
        if min(running, queued, user_queued) < 1 or running > 256 or models < 0:
            raise ValueError("invalid_execution_limits")
        with self._transaction() as (conn, _):
            conn.execute(
                "UPDATE execution_control SET max_running=?,max_models=?,max_queued=?,max_user_queued=? WHERE id=1",
                (running, models, queued, user_queued),
            )

    def mode(self) -> str | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='execution.mode'").fetchone()
            return str(row[0]) if row else None

    def configure_sandbox_budget(self, *, total: int, sandbox: int) -> None:
        if not 6 * 1024**2 <= sandbox <= total <= 1024**5:
            raise ValueError("invalid_sandbox_memory_budget")
        with self._transaction() as (conn, _):
            old = conn.execute("SELECT * FROM execution_sandbox_budget WHERE id=1").fetchone()
            if (old["limit_bytes"], old["sandbox_bytes"]) == (total, sandbox):
                return
            if (
                conn.execute(
                    "SELECT 1 FROM execution_nodes WHERE role<>'web' AND state<>'stopped' LIMIT 1"
                ).fetchone()
                or conn.execute("SELECT 1 FROM execution_sandboxes LIMIT 1").fetchone()
            ):
                raise ExecutionConflict("executor_stop_unconfirmed")
            conn.execute(
                "UPDATE execution_sandbox_budget SET limit_bytes=?,sandbox_bytes=?,pressure_until=0 WHERE id=1",
                (total, sandbox),
            )

    def sandbox_budget(self) -> dict[str, int]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT b.*, (SELECT COALESCE(SUM(memory_bytes),0) FROM execution_sandboxes) AS used_bytes "
                "FROM execution_sandbox_budget b WHERE id=1"
            ).fetchone()
            return {key: int(value) for key, value in dict(row).items()}

    def sandbox_pressure(self) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM execution_sandbox_budget WHERE id=1 AND pressure_until>=? "
                "AND (SELECT COALESCE(SUM(memory_bytes),0) FROM execution_sandboxes)+sandbox_bytes>limit_bytes",
                (self._now(conn),),
            ).fetchone()
            return bool(row)

    def reserve_sandbox(
        self, node: str, incarnation: str, agent: str, name: str, memory: int
    ) -> str | None:
        """Reserve the full cgroup limit before Docker I/O, including initialization.

        None requests cooperative idle reclamation. It commits the pressure signal;
        raising inside this transaction would silently roll that signal back.
        Reservations never expire with a lease, nor with an uncertain Docker call.
        """
        if not name or memory < 6 * 1024**2:
            raise ValueError("invalid_sandbox_reservation")
        with self._transaction() as (conn, now):
            self._node(conn, now, node, incarnation, ready=True)
            row = conn.execute("SELECT user_id FROM agents WHERE agent_id=?", (agent,)).fetchone()
            if row and self._topology(conn):
                self._own_user(conn, now, int(row[0] or 0), node, incarnation)
            budget = conn.execute("SELECT * FROM execution_sandbox_budget WHERE id=1").fetchone()
            if memory != budget["sandbox_bytes"] or not budget["limit_bytes"]:
                raise ExecutionConflict("sandbox_budget_uninitialized")
            old = conn.execute(
                "SELECT * FROM execution_sandboxes WHERE container_name=?", (name,)
            ).fetchone()
            if old:
                if (old["node_id"], old["incarnation"], old["agent_id"], old["memory_bytes"]) != (
                    node,
                    incarnation,
                    agent,
                    memory,
                ):
                    raise ExecutionConflict("sandbox_reservation_conflict")
                return str(old["token"])
            used = conn.execute(
                "SELECT COALESCE(SUM(memory_bytes),0) FROM execution_sandboxes"
            ).fetchone()[0]
            if used + memory > budget["limit_bytes"]:
                conn.execute(
                    "UPDATE execution_sandbox_budget SET pressure_until=? WHERE id=1", (now + 30,)
                )
                return None
            token = new_ulid()
            conn.execute(
                "INSERT INTO execution_sandboxes(container_name,node_id,incarnation,agent_id,memory_bytes,token) VALUES(?,?,?,?,?,?)",
                (name, node, incarnation, agent, memory, token),
            )
            return token

    def release_sandbox(self, node: str, incarnation: str, name: str, token: str) -> None:
        """Only a verified stop of both the private and init containers releases RAM."""
        with self._transaction() as (conn, _):
            conn.execute(
                "DELETE FROM execution_sandboxes WHERE container_name=? AND node_id=? AND incarnation=? AND token=?",
                (name, node, incarnation, token),
            )

    def sandboxes(self, node: str, incarnation: str) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            if not self._has_sandbox_schema(conn):
                return []
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM execution_sandboxes WHERE node_id=? AND incarnation=?",
                    (node, incarnation),
                ).fetchall()
            ]

    def _has_sandbox_schema(self, conn: Any) -> bool:
        # A new maintenance CLI must be able to prove OLD workers stopped
        # before init is permitted to apply the unreleased-v29 schema repair.
        sql = (
            "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='execution_sandboxes'"
            if self.db.dialect == "postgresql"
            else "SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_sandboxes'"
        )
        return bool(conn.execute(sql).fetchone())

    def topology(self) -> dict[str, str]:
        with self.db.connect() as conn:
            return self._topology(conn)

    @staticmethod
    def _topology(conn: Any) -> dict[str, str]:
        row = conn.execute("SELECT value FROM settings WHERE key='execution.workers'").fetchone()
        return json.loads(row[0]) if row else {}

    def configure_topology(self, workers: dict[str, str]) -> None:
        """Change shard membership only after every old runtime has stop proof."""
        if not workers or len(workers) > 16:
            raise ValueError("invalid_worker_topology")
        with self._transaction() as (conn, _):
            if conn.execute(
                "SELECT 1 FROM execution_nodes WHERE role<>'web' AND state<>'stopped' LIMIT 1"
            ).fetchone():
                if self._topology(conn) == workers:
                    return
                raise ExecutionConflict("executor_stop_unconfirmed")
            # A full stopped-domain initialization also refreshes ownership
            # after an employee/admin role change with unchanged node membership.
            conn.execute("DELETE FROM execution_owners")
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('execution.workers',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (wire(workers),),
            )

    def _worker_for(self, conn: Any, user: int | None) -> str | None:
        nodes = sorted(self._topology(conn))
        if not nodes:
            return None
        row = (
            conn.execute("SELECT role FROM users WHERE id=?", (user,)).fetchone() if user else None
        )
        return (
            nodes[0] if not row or row[0] == "admin" else nodes[(int(user or 0) - 1) % len(nodes)]
        )

    def worker_for(self, user: int | None) -> str | None:
        with self.db.connect() as conn:
            return self._worker_for(conn, user)

    def route(self, user: int | None) -> str | None:
        with self.db.connect() as conn:
            node = self._worker_for(conn, user)
            if node is None:
                return None
            row = conn.execute("SELECT * FROM execution_nodes WHERE node_id=?", (node,)).fetchone()
            if not row:
                raise ExecutionConflict("executor_fenced")
            self._node(conn, self._now(conn), node, row["incarnation"], ready=True)
            return self._topology(conn)[node]

    def own_user(self, user: int, node: str, incarnation: str) -> None:
        with self._transaction() as (conn, now):
            self._own_user(conn, now, user, node, incarnation)

    def _own_user(self, conn: Any, now: int, user: int, node: str, incarnation: str) -> int:
        self._node(conn, now, node, incarnation, ready=True)
        target = self._worker_for(conn, user)
        if target is not None and target != node:
            raise ExecutionConflict("runtime_owner_mismatch")
        old = conn.execute("SELECT * FROM execution_owners WHERE user_id=?", (user,)).fetchone()
        if old and (old["node_id"], old["incarnation"]) == (node, incarnation):
            return int(old["token"])
        if (
            old
            and not conn.execute(
                "SELECT 1 FROM execution_stops WHERE node_id=? AND incarnation=?",
                (old["node_id"], old["incarnation"]),
            ).fetchone()
        ):
            raise ExecutionConflict("executor_stop_unconfirmed")
        token = int(old["token"]) + 1 if old else 1
        conn.execute(
            "INSERT INTO execution_owners VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "node_id=excluded.node_id,incarnation=excluded.incarnation,token=excluded.token",
            (user, node, incarnation, token),
        )
        return token

    def agent_busy(self, agent: str, *, except_run: str = "", workspace_only: bool = False) -> bool:
        """Unresolved executions keep their runtime, even without a local coroutine."""
        with self.db.connect() as conn:
            if workspace_only:
                # Desktop tools run on the employee's computer. Company model
                # and memory calls no longer borrow the server harness/workspace.
                return bool(
                    conn.execute(
                        "SELECT 1 FROM execution_runs WHERE agent_id=? AND run_id<>? "
                        "AND operation<>'desktop' "
                        "AND state NOT IN ('completed','failed','cancelled') LIMIT 1",
                        (agent, except_run),
                    ).fetchone()
                )
            return bool(
                conn.execute(
                    "SELECT 1 FROM execution_runs WHERE agent_id=? AND run_id<>? AND state NOT IN ('completed','failed','cancelled') "
                    "UNION ALL SELECT 1 FROM company_runtime_tasks t JOIN company_runtime_threads b ON b.thread_id=t.thread_id "
                    "WHERE b.agent_id=? AND t.status IN ('running','cancel_requested','unknown') LIMIT 1",
                    (agent, except_run, agent),
                ).fetchone()
            )

    def runtime_revision(self) -> str:
        """Digest small shared configuration tables; secrets never leave the repository."""
        digest = hashlib.sha256()
        with self.db.connect() as conn:
            for table in ("providers", "settings", "connectors", "skill_packages"):
                rows = sorted(
                    wire(
                        {
                            key: bytes(value).hex()
                            if isinstance(value, (bytes, memoryview))
                            else value
                            for key, value in dict(row).items()
                        }
                    )
                    for row in conn.execute(f"SELECT * FROM {table}").fetchall()
                )
                digest.update(wire(rows, limit=16 * MAX_PAYLOAD).encode())
        return digest.hexdigest()

    def runtime_ready(self) -> bool:
        with self.db.connect() as conn:
            return bool(
                conn.execute(
                    "SELECT 1 FROM execution_leaders l JOIN execution_nodes n ON n.node_id=l.node_id AND n.incarnation=l.incarnation WHERE l.name='runtime-host' AND n.state='ready' AND n.lease_until>?",
                    (self._now(conn),),
                ).fetchone()
            )

    def node_ready(self, node: str, incarnation: str) -> bool:
        with self.db.connect() as conn:
            try:
                self._node(conn, self._now(conn), node, incarnation, ready=True)
            except ExecutionConflict:
                return False
            return True

    def nodes(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT node_id,incarnation,role,state,container_id,lease_until FROM execution_nodes ORDER BY node_id"
                ).fetchall()
            ]

    def list_runs(
        self, *, state: str = "unknown", after: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        if (
            state
            not in TERMINAL
            | {"unknown", "queued", "running", "awaiting_approval", "cancel_requested"}
            or not 1 <= limit <= 500
        ):
            raise ValueError("invalid_execution_page")
        with self.db.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT run_id,user_id,agent_id,thread_id,state,node_id,incarnation,updated_at "
                    "FROM execution_runs WHERE state=? AND run_id>? ORDER BY run_id LIMIT ?",
                    (state, after, limit),
                ).fetchall()
            ]

    def reconciliation(self, run: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT run_id,user_id,state,node_id,incarnation FROM execution_runs WHERE run_id=?",
                (run,),
            ).fetchone()
            if not row:
                raise ExecutionConflict("execution_not_found")
            models = conn.execute(
                "SELECT reservation_id,provider,reserved,state FROM execution_reservations WHERE run_id=? AND state<>'settled'",
                (run,),
            ).fetchall()
            operations = conn.execute(
                "SELECT operation_id,device_id,generation,state FROM execution_tools WHERE run_id=? AND state<>'completed'",
                (run,),
            ).fetchall()
            return {
                "run": dict(row),
                "models": [dict(item) for item in models],
                "tools": [dict(item) for item in operations],
            }

    def reconcile(self, run: str, *, outcome: str, evidence: dict[str, Any]) -> None:
        """Operator reconciliation after stop proof; never replay unknown effects."""
        encoded = wire(evidence)
        if (
            outcome not in TERMINAL
            or not isinstance(evidence, dict)
            or not isinstance(evidence.get("reason"), str)
            or len(evidence["reason"].strip()) < 10
            or not isinstance(evidence.get("models", {}), dict)
            or not isinstance(evidence.get("tools", {}), dict)
        ):
            raise ExecutionConflict("reconciliation_evidence_required")
        with self._transaction() as (conn, now):
            row = conn.execute(
                "SELECT r.* FROM execution_runs r JOIN execution_stops s ON s.node_id=r.node_id AND s.incarnation=r.incarnation WHERE r.run_id=?",
                (run,),
            ).fetchone()
            if not row or row["state"] != "unknown":
                raise ExecutionConflict("executor_stop_unconfirmed")
            models = conn.execute(
                "SELECT reservation_id FROM execution_reservations WHERE run_id=? AND state<>'settled'",
                (run,),
            ).fetchall()
            operations = conn.execute(
                "SELECT operation_id FROM execution_tools WHERE run_id=? AND state<>'completed'",
                (run,),
            ).fetchall()
            if set(evidence.get("models", {})) != {item[0] for item in models} or set(
                evidence.get("tools", {})
            ) != {item[0] for item in operations}:
                raise ExecutionConflict("reconciliation_incomplete")
            for key, actual in evidence.get("models", {}).items():
                if type(actual) is not int or actual < 0:
                    raise ExecutionConflict("invalid_token_usage")
                conn.execute(
                    "UPDATE execution_reservations SET state='settled',actual=? WHERE reservation_id=? AND run_id=?",
                    (actual, key, run),
                )
            for key, receipt in evidence.get("tools", {}).items():
                if not isinstance(receipt, dict) or not receipt:
                    raise ExecutionConflict("tool_receipt_required")
                conn.execute(
                    "UPDATE execution_tools SET state='completed',result_json=? WHERE run_id=? AND operation_id=?",
                    (wire(receipt), run, key),
                )
            conn.execute(
                "UPDATE execution_runs SET state=?,updated_at=? WHERE run_id=?", (outcome, now, run)
            )
            conn.execute("DELETE FROM execution_sessions WHERE run_id=?", (run,))
            self._event(
                conn,
                now,
                run,
                "terminal",
                {"type": "execution.state", "state": outcome, "reconciled": True},
            )
            conn.execute(
                "INSERT INTO execution_reconciliations(run_id,evidence,created_at) VALUES(?,?,?)",
                (run, encoded, now),
            )

    def register(
        self, node: str, incarnation: str, role: str, *, container_id: str = "", ttl: int = 30
    ) -> None:
        if (
            not node
            or not incarnation
            or role not in {"web", "worker", "monolith"}
            or not 5 <= ttl <= 300
        ):
            raise ValueError("invalid_execution_node")
        with self._transaction() as (conn, now):
            old = conn.execute("SELECT * FROM execution_nodes WHERE node_id=?", (node,)).fetchone()
            if (
                old
                and (old["incarnation"] != incarnation or old["state"] != "ready")
                and old["state"] != "stopped"
            ):
                raise ExecutionConflict("executor_stop_unconfirmed")
            other = conn.execute(
                "SELECT 1 FROM execution_nodes WHERE node_id<>? AND state<>'stopped' "
                "AND (role='monolith' OR ?='monolith') LIMIT 1",
                (node, role),
            ).fetchone()
            if other:
                raise ExecutionConflict("execution_mode_conflict")
            if old and old["state"] == "stopped" and old["incarnation"] != incarnation:
                conn.execute(
                    "UPDATE execution_owners SET incarnation=?,token=token+1 WHERE node_id=? AND incarnation=?",
                    (incarnation, node, old["incarnation"]),
                )
                conn.execute(
                    "UPDATE execution_leaders SET incarnation=? WHERE name='runtime-host' AND node_id=? AND incarnation=?",
                    (incarnation, node, old["incarnation"]),
                )
            conn.execute(
                "INSERT INTO execution_nodes(node_id,incarnation,role,state,container_id,lease_until,updated_at) "
                "VALUES(?,?,?,'ready',?,?,?) ON CONFLICT(node_id) DO UPDATE SET "
                "incarnation=excluded.incarnation,role=excluded.role,state='ready',container_id=excluded.container_id,"
                "lease_until=excluded.lease_until,stop_evidence=NULL,updated_at=excluded.updated_at",
                (node, incarnation, role, container_id, now + ttl, now),
            )

    def _node(
        self, conn: Any, now: int, node: str, incarnation: str, *, ready: bool = False
    ) -> Any:
        row = conn.execute("SELECT * FROM execution_nodes WHERE node_id=?", (node,)).fetchone()
        if (
            not row
            or row["incarnation"] != incarnation
            or row["lease_until"] <= now
            or row["state"] not in ({"ready"} if ready else {"ready", "draining"})
        ):
            raise ExecutionConflict("executor_fenced")
        return row

    def heartbeat(self, node: str, incarnation: str, *, ttl: int = 30) -> None:
        if not 5 <= ttl <= 300:
            raise ValueError("invalid_execution_lease")
        with self._transaction() as (conn, now):
            self._node(conn, now, node, incarnation)
            conn.execute(
                "UPDATE execution_nodes SET lease_until=?,updated_at=? WHERE node_id=?",
                (now + ttl, now, node),
            )

    def drain(self, node: str, incarnation: str) -> None:
        with self._transaction() as (conn, now):
            self._node(conn, now, node, incarnation)
            conn.execute(
                "UPDATE execution_nodes SET state='draining',updated_at=? WHERE node_id=?",
                (now, node),
            )

    def node(self, node: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM execution_nodes WHERE node_id=?", (node,)).fetchone()
            return dict(row) if row else None

    def reserve_runtime_host(self, node: str, incarnation: str) -> None:
        """Fence legacy singleton services before any scheduler/runtime boots.

        This is deliberately not an expiring scheduler lease. Those services
        still own local containers/checkpoints; a replacement requires stop proof.
        """
        with self._transaction() as (conn, now):
            self._node(conn, now, node, incarnation, ready=True)
            old = conn.execute(
                "SELECT * FROM execution_leaders WHERE name='runtime-host'"
            ).fetchone()
            if old and (old["node_id"], old["incarnation"]) != (node, incarnation):
                previous = conn.execute(
                    "SELECT * FROM execution_nodes WHERE node_id=?", (old["node_id"],)
                ).fetchone()
                if (
                    not previous
                    or previous["incarnation"] != old["incarnation"]
                    or previous["state"] != "stopped"
                ):
                    raise ExecutionConflict("runtime_host_stop_unconfirmed")
            conn.execute(
                "INSERT INTO execution_leaders VALUES('runtime-host',?,?,?) ON CONFLICT(name) DO UPDATE SET node_id=excluded.node_id,incarnation=excluded.incarnation,lease_until=excluded.lease_until",
                (node, incarnation, now),
            )

    def track_workspace(self, node: str, incarnation: str, agent: str, container: str) -> None:
        with self._transaction() as (conn, now):
            self._node(conn, now, node, incarnation)
            row = conn.execute("SELECT user_id FROM agents WHERE agent_id=?", (agent,)).fetchone()
            if row and self._topology(conn):
                self._own_user(conn, now, int(row[0] or 0), node, incarnation)
            conn.execute(
                "INSERT INTO execution_workspaces VALUES(?,?,?,?) ON CONFLICT(node_id,incarnation,container_id) DO NOTHING",
                (node, incarnation, container, agent),
            )

    def workspaces(self, node: str, incarnation: str) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM execution_workspaces WHERE node_id=? AND incarnation=?",
                    (node, incarnation),
                ).fetchall()
            ]

    def expire_nodes(self) -> int:
        with self._transaction() as (conn, now):
            rows = conn.execute(
                "SELECT node_id,incarnation FROM execution_nodes WHERE state IN ('ready','draining') AND lease_until<=?",
                (now,),
            ).fetchall()
            for row in rows:
                self._lose_node(conn, now, row["node_id"], row["incarnation"])
            return len(rows)

    def _lose_node(self, conn: Any, now: int, node: str, incarnation: str) -> None:
        conn.execute(
            "UPDATE execution_nodes SET state='suspect',updated_at=? WHERE node_id=? AND incarnation=?",
            (now, node, incarnation),
        )
        runs = conn.execute(
            "SELECT * FROM execution_runs WHERE node_id=? AND incarnation=? AND state IN ('running','cancel_requested','awaiting_approval')",
            (node, incarnation),
        ).fetchall()
        for run in runs:
            conn.execute(
                "UPDATE execution_runs SET state='unknown',updated_at=? WHERE run_id=?",
                (now, run["run_id"]),
            )
            self._event(
                conn,
                now,
                run["run_id"],
                "executor_lost",
                {"type": "execution.state", "state": "unknown"},
            )
            conn.execute(
                "UPDATE execution_reservations SET state='unknown' WHERE run_id=? AND state='reserved'",
                (run["run_id"],),
            )
            conn.execute(
                "UPDATE execution_tools SET state='unknown' WHERE run_id=? AND state='sent'",
                (run["run_id"],),
            )
        conn.execute(
            "UPDATE execution_device_connections SET state='disconnected' WHERE node_id=? AND incarnation=?",
            (node, incarnation),
        )

    def record_stop(self, node: str, incarnation: str, evidence: dict[str, Any]) -> None:
        """Only the infrastructure stop verifier calls this, never a public payload."""
        with self._transaction() as (conn, now):
            row = conn.execute("SELECT * FROM execution_nodes WHERE node_id=?", (node,)).fetchone()
            if (
                not row
                or row["incarnation"] != incarnation
                or evidence.get("container_id") != row["container_id"]
                or not row["container_id"]
                or evidence.get("running") is not False
                or not evidence.get("finished_at")
            ):
                raise ExecutionConflict("executor_stop_unconfirmed")
            has_budget = self._has_sandbox_schema(conn)
            reserved = (
                conn.execute(
                    "SELECT container_name FROM execution_sandboxes WHERE node_id=? AND incarnation=?",
                    (node, incarnation),
                ).fetchall()
                if has_budget
                else []
            )
            if {item[0] for item in reserved} != set(evidence.get("sandboxes", [])):
                raise ExecutionConflict("workspace_stop_unconfirmed")
            self._lose_node(conn, now, node, incarnation)
            if has_budget:
                conn.execute(
                    "DELETE FROM execution_sandboxes WHERE node_id=? AND incarnation=?",
                    (node, incarnation),
                )
            conn.execute(
                "UPDATE execution_nodes SET state='stopped',stop_evidence=?,updated_at=? WHERE node_id=?",
                (wire(evidence), now, node),
            )
            conn.execute(
                "INSERT INTO execution_stops(node_id,incarnation,evidence,created_at) VALUES(?,?,?,?) ON CONFLICT(node_id,incarnation) DO NOTHING",
                (node, incarnation, wire(evidence), now),
            )

    def _thread(self, conn: Any, user: int, agent: str, thread: str) -> Any:
        row = conn.execute(
            "SELECT t.* FROM threads t JOIN users u ON u.id=t.user_id JOIN agents a ON a.agent_id=t.agent_id "
            "WHERE t.thread_id=? AND t.user_id=? AND t.agent_id=? AND u.disabled=0 AND a.enabled=1",
            (thread, user, agent),
        ).fetchone()
        if not row:
            raise ExecutionConflict("thread_not_owned")
        return row

    def submit(
        self,
        *,
        user: int,
        agent: str,
        thread: str,
        operation: str,
        key: str,
        request: dict[str, Any],
        schedule: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        if not key or len(key) > 200 or operation not in {"chat", "desktop", "cron", "call"}:
            raise ValueError("invalid_execution_identity")
        encoded = wire(request)
        with self._transaction() as (conn, now):
            self._thread(conn, user, agent, thread)
            old = conn.execute(
                "SELECT * FROM execution_runs WHERE user_id=? AND operation=? AND request_key=?",
                (user, operation, key),
            ).fetchone()
            if old:
                if (
                    old["request_json"] != encoded
                    or old["thread_id"] != thread
                    or old["agent_id"] != agent
                ):
                    raise ExecutionConflict("execution_identity_conflict")
                return dict(old)
            if schedule:
                old = conn.execute(
                    "SELECT r.* FROM execution_schedules s JOIN execution_runs r ON r.run_id=s.run_id WHERE s.schedule_id=? AND s.scheduled_at=?",
                    schedule,
                ).fetchone()
                if old:
                    if old["user_id"] != user or old["request_json"] != encoded:
                        raise ExecutionConflict("execution_identity_conflict")
                    return dict(old)
            if conn.execute(
                "SELECT 1 FROM execution_sessions WHERE thread_id=?", (thread,)
            ).fetchone():
                raise ExecutionConflict("thread_already_running")
            if conn.execute(
                "SELECT 1 FROM company_runtime_tasks WHERE thread_id=? AND status IN ('running','cancel_requested','unknown')",
                (thread,),
            ).fetchone():
                raise ExecutionConflict("thread_already_running")
            limits = conn.execute("SELECT * FROM execution_control WHERE id=1").fetchone()
            queued = conn.execute(
                "SELECT COUNT(*) FROM execution_runs WHERE state IN ('queued','awaiting_approval')"
            ).fetchone()[0]
            user_queued = conn.execute(
                "SELECT COUNT(*) FROM execution_runs WHERE state IN ('queued','awaiting_approval') AND user_id=?",
                (user,),
            ).fetchone()[0]
            if queued >= limits["max_queued"] or user_queued >= limits["max_user_queued"]:
                raise ExecutionConflict("execution_queue_full")
            run = new_ulid()
            conn.execute(
                "INSERT INTO execution_runs(run_id,user_id,agent_id,thread_id,operation,request_key,request_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'queued',?,?)",
                (run, user, agent, thread, operation, key, encoded, now, now),
            )
            conn.execute(
                "INSERT INTO execution_sessions(thread_id,run_id) VALUES(?,?)", (thread, run)
            )
            conn.execute(
                "INSERT INTO execution_fairness(user_id) VALUES(?) ON CONFLICT(user_id) DO NOTHING",
                (user,),
            )
            if schedule:
                conn.execute("INSERT INTO execution_schedules VALUES(?,?,?)", (*schedule, run))
            self._event(conn, now, run, "admitted", {"type": "execution.state", "state": "queued"})
            return dict(
                conn.execute("SELECT * FROM execution_runs WHERE run_id=?", (run,)).fetchone()
            )

    def claim(
        self, node: str, incarnation: str, *, run_id: str | None = None
    ) -> dict[str, Any] | None:
        with self._transaction() as (conn, now):
            process = self._node(conn, now, node, incarnation, ready=True)
            if process["role"] == "web":
                raise ExecutionConflict("executor_role_invalid")
            limit = conn.execute("SELECT max_running FROM execution_control WHERE id=1").fetchone()[
                0
            ]
            active = conn.execute(
                "SELECT COUNT(*) FROM execution_runs WHERE state IN ('running','cancel_requested','unknown') AND operation<>'desktop'"
            ).fetchone()[0]
            if active >= limit:
                return None
            nodes = sorted(self._topology(conn))
            if nodes and node not in nodes:
                return None
            index = nodes.index(node) if nodes else 0
            runs = conn.execute(
                "SELECT r.* FROM execution_runs r JOIN execution_fairness f ON f.user_id=r.user_id "
                "JOIN users u ON u.id=r.user_id "
                "LEFT JOIN execution_owners o ON o.user_id=r.user_id "
                "LEFT JOIN execution_nodes n ON n.node_id=o.node_id "
                "WHERE r.state='queued' AND (CAST(? AS TEXT) IS NULL OR r.run_id=?) "
                "AND (o.node_id IS NULL OR (o.node_id=? AND o.incarnation=?) OR (n.state='stopped' AND n.incarnation=o.incarnation)) "
                "AND (?=0 OR (u.role='admin' AND ?=0) OR (u.role<>'admin' AND MOD(r.user_id-1,?)=?)) "
                "ORDER BY f.last_dispatch,r.created_at,r.run_id LIMIT 32",
                (run_id, run_id, node, incarnation, len(nodes), index, max(1, len(nodes)), index),
            ).fetchall()
            run = None
            for candidate in runs:
                target = self._worker_for(conn, candidate["user_id"])
                if target is not None and target != node:
                    continue
                try:
                    self._thread(
                        conn, candidate["user_id"], candidate["agent_id"], candidate["thread_id"]
                    )
                except ExecutionConflict:
                    conn.execute(
                        "UPDATE execution_runs SET state='failed',updated_at=? WHERE run_id=?",
                        (now, candidate["run_id"]),
                    )
                    conn.execute(
                        "DELETE FROM execution_sessions WHERE run_id=?", (candidate["run_id"],)
                    )
                    self._event(
                        conn,
                        now,
                        candidate["run_id"],
                        "terminal",
                        {
                            "type": "execution.state",
                            "state": "failed",
                            "reason": "thread_not_owned",
                        },
                    )
                    continue
                run = dict(candidate)
                break
            if run is None:
                return None
            self._own_user(conn, now, run["user_id"], node, incarnation)
            owner = conn.execute(
                "SELECT * FROM execution_owners WHERE user_id=?", (run["user_id"],)
            ).fetchone()
            token = int(owner["token"]) if owner else 0
            if not owner or owner["node_id"] != node or owner["incarnation"] != incarnation:
                token += 1
                conn.execute(
                    "INSERT INTO execution_owners VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET node_id=excluded.node_id,incarnation=excluded.incarnation,token=excluded.token",
                    (run["user_id"], node, incarnation, token),
                )
            conn.execute(
                "UPDATE execution_runs SET state='running',node_id=?,incarnation=?,token=?,updated_at=? WHERE run_id=?",
                (node, incarnation, token, now, run["run_id"]),
            )
            serial = conn.execute(
                "SELECT COALESCE(MAX(last_dispatch),0)+1 FROM execution_fairness"
            ).fetchone()[0]
            conn.execute(
                "UPDATE execution_fairness SET last_dispatch=? WHERE user_id=?",
                (serial, run["user_id"]),
            )
            self._event(
                conn, now, run["run_id"], "claimed", {"type": "execution.state", "state": "running"}
            )
            return dict(
                conn.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (run["run_id"],)
                ).fetchone()
            )

    def _fence(
        self, conn: Any, now: int, run_id: str, node: str, incarnation: str, token: int
    ) -> Any:
        self._node(conn, now, node, incarnation)
        run = conn.execute(
            "SELECT r.* FROM execution_runs r JOIN execution_owners o ON o.user_id=r.user_id WHERE r.run_id=? AND r.node_id=? AND r.incarnation=? AND r.token=? AND o.node_id=r.node_id AND o.incarnation=r.incarnation AND o.token=r.token",
            (run_id, node, incarnation, token),
        ).fetchone()
        if not run or run["state"] in TERMINAL or run["state"] == "unknown":
            raise ExecutionConflict("executor_fenced")
        return run

    def check(self, run_id: str, node: str, incarnation: str, token: int) -> dict[str, Any]:
        with self.db.connect() as conn:
            run = self._fence(conn, self._now(conn), run_id, node, incarnation, token)
            self._thread(conn, run["user_id"], run["agent_id"], run["thread_id"])
            if run["state"] != "running":
                raise ExecutionConflict("execution_" + run["state"])
            return dict(run)

    def get(self, run_id: str, *, user: int | None = None) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row and (user is None or row["user_id"] == user) else None

    def active(self, thread: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT r.* FROM execution_sessions s JOIN execution_runs r ON r.run_id=s.run_id WHERE s.thread_id=?",
                (thread,),
            ).fetchone()
            return dict(row) if row else None

    def by_request(self, *, user: int, operation: str, key: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM execution_runs WHERE user_id=? AND operation=? AND request_key=?",
                (user, operation, key),
            ).fetchone()
            return dict(row) if row else None

    def scheduled(self, schedule: str, at: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT r.* FROM execution_schedules s JOIN execution_runs r ON r.run_id=s.run_id WHERE s.schedule_id=? AND s.scheduled_at=?",
                (schedule, at),
            ).fetchone()
            return dict(row) if row else None

    def _event(self, conn: Any, now: int, run: str, key: str, payload: dict[str, Any]) -> int:
        encoded = wire(payload, limit=256 * 1024)
        old = conn.execute(
            "SELECT seq,payload FROM execution_events WHERE run_id=? AND event_key=?", (run, key)
        ).fetchone()
        if old:
            if old["payload"] != encoded:
                raise ExecutionConflict("event_identity_conflict")
            return int(old["seq"])
        row = conn.execute(
            "UPDATE execution_runs SET event_seq=event_seq+1 WHERE run_id=? RETURNING event_seq",
            (run,),
        ).fetchone()
        seq = int(row[0])
        conn.execute(
            "INSERT INTO execution_events VALUES(?,?,?,?,?)", (run, seq, key, encoded, now)
        )
        conn.execute(
            "INSERT INTO execution_outbox(run_id,seq,created_at) VALUES(?,?,?)", (run, seq, now)
        )
        return seq

    def append(
        self,
        run: str,
        node: str,
        incarnation: str,
        token: int,
        *,
        key: str,
        payload: dict[str, Any],
    ) -> int:
        with self._transaction() as (conn, now):
            self._fence(conn, now, run, node, incarnation, token)
            return self._event(conn, now, run, key, payload)

    def events(
        self, run: str, *, user: int, after: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        if after < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid_event_page")
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT e.* FROM execution_events e JOIN execution_runs r ON r.run_id=e.run_id WHERE e.run_id=? AND r.user_id=? AND e.seq>? ORDER BY e.seq LIMIT ?",
                (run, user, after, limit),
            ).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def cancel_user(self, user: int) -> None:
        """Persist policy-change cancellation for every worker owning this user's runs."""
        with self.db.connect() as conn:
            runs = conn.execute(
                "SELECT run_id FROM execution_runs WHERE user_id=? AND operation<>'desktop' "
                "AND state IN ('queued','running','awaiting_approval','cancel_requested')",
                (user,),
            ).fetchall()
        for row in runs:
            try:
                self.command(
                    row["run_id"],
                    user=user,
                    key="employee-resources-changed",
                    kind="cancel",
                    payload={},
                )
            except ExecutionConflict as exc:
                # A worker may finish between the read and the cancellation.
                if str(exc) != "execution_not_active":
                    raise

    def command(
        self, run: str, *, user: int, key: str, kind: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if not key or len(key) > 200 or kind not in {"cancel", "approval"}:
            raise ValueError("invalid_execution_command")
        encoded = wire(payload, limit=64 * 1024)
        with self._transaction() as (conn, now):
            row = conn.execute(
                "SELECT * FROM execution_runs WHERE run_id=? AND user_id=?", (run, user)
            ).fetchone()
            if not row:
                raise ExecutionConflict("execution_not_found")
            self._thread(conn, user, row["agent_id"], row["thread_id"])
            old = conn.execute(
                "SELECT * FROM execution_commands WHERE run_id=? AND command_key=?", (run, key)
            ).fetchone()
            if old:
                if old["payload"] != encoded or old["kind"] != kind:
                    raise ExecutionConflict("command_identity_conflict")
                return dict(old)
            if row["state"] in TERMINAL or row["state"] == "unknown":
                raise ExecutionConflict("execution_not_active")
            if kind == "cancel" and row["state"] == "cancel_requested":
                pending = conn.execute(
                    "SELECT * FROM execution_commands WHERE run_id=? AND kind='cancel' ORDER BY created_at LIMIT 1",
                    (run,),
                ).fetchone()
                if pending:
                    return dict(pending)
            state = "pending"
            if kind == "approval":
                if (
                    row["state"] != "awaiting_approval"
                    or payload.get("approval_id") != row["approval_id"]
                ):
                    raise ExecutionConflict("approval_not_pending")
                if conn.execute(
                    "SELECT 1 FROM execution_commands WHERE run_id=? AND kind='approval' AND payload=?",
                    (run, encoded),
                ).fetchone():
                    raise ExecutionConflict("approval_already_decided")
                # Clear the pending ID atomically; another command cannot decide it again.
                conn.execute("UPDATE execution_runs SET approval_id=NULL WHERE run_id=?", (run,))
            if kind == "cancel":
                if row["state"] == "queued":
                    conn.execute(
                        "UPDATE execution_runs SET state='cancelled',updated_at=? WHERE run_id=?",
                        (now, run),
                    )
                    conn.execute("DELETE FROM execution_sessions WHERE run_id=?", (run,))
                    state = "applied"
                else:
                    conn.execute(
                        "UPDATE execution_runs SET state='cancel_requested',updated_at=? WHERE run_id=?",
                        (now, run),
                    )
            conn.execute(
                "INSERT INTO execution_commands VALUES(?,?,?,?,?,?)",
                (run, key, kind, encoded, state, now),
            )
            self._event(
                conn,
                now,
                run,
                "command:" + key,
                {"type": "execution.command", "kind": kind, "key": key, "state": state},
            )
            if kind == "cancel" and state == "applied":
                self._event(
                    conn, now, run, "terminal", {"type": "execution.state", "state": "cancelled"}
                )
            return dict(
                conn.execute(
                    "SELECT * FROM execution_commands WHERE run_id=? AND command_key=?", (run, key)
                ).fetchone()
            )

    def commands(self, run: str, node: str, incarnation: str, token: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            self._fence(conn, self._now(conn), run, node, incarnation, token)
            rows = conn.execute(
                "SELECT * FROM execution_commands WHERE run_id=? AND state='pending' ORDER BY created_at,command_key LIMIT 100",
                (run,),
            ).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def applied(self, run: str, node: str, incarnation: str, token: int, key: str) -> None:
        with self._transaction() as (conn, now):
            row = self._fence(conn, now, run, node, incarnation, token)
            command = conn.execute(
                "SELECT * FROM execution_commands WHERE run_id=? AND command_key=?", (run, key)
            ).fetchone()
            if not command:
                raise ExecutionConflict("command_not_found")
            if command["state"] == "applied":
                return
            if command["kind"] == "approval":
                if row["state"] != "awaiting_approval":
                    raise ExecutionConflict("approval_not_pending")
                limit = conn.execute(
                    "SELECT max_running FROM execution_control WHERE id=1"
                ).fetchone()[0]
                active = conn.execute(
                    "SELECT COUNT(*) FROM execution_runs WHERE state IN ('running','cancel_requested','unknown') AND operation<>'desktop'"
                ).fetchone()[0]
                if active >= limit:
                    raise ExecutionConflict("execution_capacity_exceeded")
                conn.execute(
                    "UPDATE execution_runs SET state='running',updated_at=? WHERE run_id=?",
                    (now, run),
                )
            conn.execute(
                "UPDATE execution_commands SET state='applied' WHERE run_id=? AND command_key=?",
                (run, key),
            )

    def transition(
        self,
        run: str,
        node: str,
        incarnation: str,
        token: int,
        state: str,
        *,
        approval_id: str | None = None,
        final_frames: list[dict[str, Any]] | None = None,
    ) -> None:
        if state not in TERMINAL | {"awaiting_approval", "unknown"} or (
            state == "awaiting_approval" and not approval_id
        ):
            raise ValueError("invalid_execution_transition")
        with self._transaction() as (conn, now):
            row = self._fence(conn, now, run, node, incarnation, token)
            if row["state"] == "cancel_requested" and state not in {
                "cancelled",
                "failed",
                "unknown",
            }:
                raise ExecutionConflict("execution_cancel_requested")
            conn.execute(
                "UPDATE execution_runs SET state=?,approval_id=?,updated_at=? WHERE run_id=?",
                (state, approval_id, now, run),
            )
            self._event(
                conn,
                now,
                run,
                "terminal" if state in TERMINAL else f"state:{state}:{approval_id or ''}",
                {
                    "type": "execution.state",
                    "state": state,
                    "approval_id": approval_id,
                    "frames": final_frames or [],
                },
            )
            if state in TERMINAL:
                pending = conn.execute(
                    "SELECT 1 FROM execution_tools WHERE run_id=? AND state IN ('sent','unknown') LIMIT 1",
                    (run,),
                ).fetchone()
                if pending:
                    raise ExecutionConflict("tool_result_unconfirmed")
                if conn.execute(
                    "SELECT 1 FROM execution_reservations WHERE run_id=? AND state<>'settled' LIMIT 1",
                    (run,),
                ).fetchone():
                    raise ExecutionConflict("model_result_unconfirmed")
                conn.execute("DELETE FROM execution_sessions WHERE run_id=?", (run,))
            elif state == "unknown":
                conn.execute(
                    "UPDATE execution_reservations SET state='unknown' WHERE run_id=? AND state='reserved'",
                    (run,),
                )
                conn.execute(
                    "UPDATE execution_tools SET state='unknown' WHERE run_id=? AND state='sent'",
                    (run,),
                )

    def reserve_model(
        self,
        run: str,
        node: str,
        incarnation: str,
        token: int,
        *,
        key: str,
        provider: str,
        tokens: int,
        quota_remaining: int | None,
    ) -> None:
        if tokens < 1:
            raise ValueError("invalid_token_reservation")
        with self._transaction() as (conn, now):
            row = self._fence(conn, now, run, node, incarnation, token)
            if row["state"] != "running":
                raise ExecutionConflict("execution_not_running")
            old = conn.execute(
                "SELECT * FROM execution_reservations WHERE reservation_id=?", (key,)
            ).fetchone()
            if old:
                # A duplicate is never permission to send the model request again.
                raise ExecutionConflict("model_request_already_reserved")
            limit = conn.execute("SELECT max_models FROM execution_control WHERE id=1").fetchone()[
                0
            ]
            if limit > 0:
                count = conn.execute(
                    "SELECT COUNT(*) FROM execution_reservations WHERE state IN ('reserved','unknown')"
                ).fetchone()[0]
                if count >= limit:
                    raise ExecutionConflict("model_capacity_exceeded")
            policy = conn.execute(
                "SELECT value FROM user_policies WHERE user_id=? AND name='token_quota' AND enabled=1",
                (row["user_id"],),
            ).fetchone()
            if policy:
                try:
                    quota = int(policy["value"])
                except (TypeError, ValueError):
                    raise ExecutionConflict("token_quota_invalid") from None
                used = conn.execute(
                    "SELECT COALESCE(SUM(total_tokens),0) FROM usage_log l WHERE user_id=? AND (execution_run_id IS NULL OR NOT EXISTS(SELECT 1 FROM execution_runs r WHERE r.run_id=l.execution_run_id))",
                    (row["user_id"],),
                ).fetchone()[0]
                charged = conn.execute(
                    "SELECT COALESCE(SUM(CASE WHEN COALESCE(s.total,0)>COALESCE(l.total,0) THEN s.total ELSE COALESCE(l.total,0) END),0) "
                    "FROM execution_runs r LEFT JOIN (SELECT run_id,SUM(actual) total FROM execution_reservations WHERE state='settled' GROUP BY run_id) s ON s.run_id=r.run_id "
                    "LEFT JOIN (SELECT execution_run_id,SUM(total_tokens) total FROM usage_log WHERE execution_run_id IS NOT NULL GROUP BY execution_run_id) l ON l.execution_run_id=r.run_id WHERE r.user_id=?",
                    (row["user_id"],),
                ).fetchone()[0]
                remaining = max(0, quota - used - charged)
                quota_remaining = (
                    remaining if quota_remaining is None else min(quota_remaining, remaining)
                )
            held = conn.execute(
                "SELECT COALESCE(SUM(s.reserved),0) FROM execution_reservations s JOIN execution_runs r ON r.run_id=s.run_id WHERE r.user_id=? AND s.state IN ('reserved','unknown')",
                (row["user_id"],),
            ).fetchone()[0]
            if quota_remaining is not None and held + tokens > quota_remaining:
                raise ExecutionConflict("token_quota_exceeded")
            conn.execute(
                "INSERT INTO execution_reservations VALUES(?,?,?,?,NULL,'reserved',?)",
                (key, run, provider, tokens, now),
            )

    def proxy_run(
        self, *, task: str, user: int, device: str, node: str, incarnation: str
    ) -> dict[str, Any]:
        """Join a desktop model request to global admission without stealing its lease."""
        with self._transaction() as (conn, now):
            self._node(conn, now, node, incarnation, ready=True)
            if self._topology(conn):
                self._own_user(conn, now, user, node, incarnation)
            remote = conn.execute(
                "SELECT t.thread_id,b.agent_id FROM company_runtime_tasks t JOIN company_runtime_threads b ON b.thread_id=t.thread_id WHERE t.task_id=? AND t.device_id=? AND b.owner_user_id=? AND t.status='running'",
                (task, device, user),
            ).fetchone()
            if not remote:
                raise ExecutionConflict("desktop_task_not_active")
            self._thread(conn, user, remote["agent_id"], remote["thread_id"])
            control = conn.execute("SELECT * FROM execution_control WHERE id=1").fetchone()
            if control["max_models"] > 0:
                # These are waiting requests, not active provider calls. Without
                # an explicit model limit there is no model-admission queue.
                pending = conn.execute(
                    "SELECT COUNT(*),COALESCE(SUM(CASE WHEN user_id=? THEN 1 ELSE 0 END),0) "
                    "FROM execution_runs r WHERE operation='desktop' AND state='running' "
                    "AND NOT EXISTS (SELECT 1 FROM execution_reservations s WHERE s.run_id=r.run_id)",
                    (user,),
                ).fetchone()
                if pending[0] >= control["max_queued"] or pending[1] >= control["max_user_queued"]:
                    raise ExecutionConflict("execution_queue_full")
            owner = conn.execute(
                "SELECT o.*,n.state FROM execution_owners o JOIN execution_nodes n ON n.node_id=o.node_id AND n.incarnation=o.incarnation WHERE o.user_id=?",
                (user,),
            ).fetchone()
            token = int(owner["token"]) if owner else 0
            if (
                owner
                and (owner["node_id"], owner["incarnation"]) != (node, incarnation)
                and owner["state"] != "stopped"
            ):
                raise ExecutionConflict("executor_stop_unconfirmed")
            if not owner or (owner["node_id"], owner["incarnation"]) != (node, incarnation):
                token += 1
                conn.execute(
                    "INSERT INTO execution_owners VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET node_id=excluded.node_id,incarnation=excluded.incarnation,token=excluded.token",
                    (user, node, incarnation, token),
                )
            run = new_ulid()
            conn.execute(
                "INSERT INTO execution_runs(run_id,user_id,agent_id,thread_id,operation,request_key,request_json,state,node_id,incarnation,token,created_at,updated_at) VALUES(?,?,?,?,'desktop',?,?,'running',?,?,?,?,?)",
                (
                    run,
                    user,
                    remote["agent_id"],
                    remote["thread_id"],
                    run,
                    wire({"task_id": task}),
                    node,
                    incarnation,
                    token,
                    now,
                    now,
                ),
            )
            return dict(
                conn.execute("SELECT * FROM execution_runs WHERE run_id=?", (run,)).fetchone()
            )

    def settle_model(
        self, run: str, node: str, incarnation: str, token: int, *, key: str, actual: int | None
    ) -> None:
        if actual is not None and actual < 0:
            raise ValueError("invalid_token_usage")
        with self._transaction() as (conn, now):
            self._fence(conn, now, run, node, incarnation, token)
            row = conn.execute(
                "SELECT * FROM execution_reservations WHERE reservation_id=? AND run_id=?",
                (key, run),
            ).fetchone()
            if not row:
                raise ExecutionConflict("reservation_not_found")
            state = "unknown" if actual is None else "settled"
            if row["state"] == "settled":
                if row["actual"] != actual:
                    raise ExecutionConflict("reservation_identity_conflict")
                return
            conn.execute(
                "UPDATE execution_reservations SET state=?,actual=? WHERE reservation_id=?",
                (state, actual, key),
            )

    def leader(self, name: str, node: str, incarnation: str, *, ttl: int = 15) -> bool:
        if not 5 <= ttl <= 60:
            raise ValueError("invalid_leader_lease")
        with self._transaction() as (conn, now):
            self._node(conn, now, node, incarnation, ready=True)
            old = conn.execute("SELECT * FROM execution_leaders WHERE name=?", (name,)).fetchone()
            if (
                old
                and old["lease_until"] > now
                and (old["node_id"], old["incarnation"]) != (node, incarnation)
            ):
                return False
            conn.execute(
                "INSERT INTO execution_leaders VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET node_id=excluded.node_id,incarnation=excluded.incarnation,lease_until=excluded.lease_until",
                (name, node, incarnation, now + ttl),
            )
            return True

    def connect_device(
        self, device: str, *, user: int, node: str, incarnation: str, metadata: dict[str, Any]
    ) -> int:
        encoded = wire(metadata, limit=64 * 1024)
        with self._transaction() as (conn, now):
            self._node(conn, now, node, incarnation, ready=True)
            if self._topology(conn):
                self._own_user(conn, now, user, node, incarnation)
            identity = conn.execute(
                "SELECT owner_user_id,revoked_at,expires_at FROM desktop_devices WHERE device_id=?",
                (device,),
            ).fetchone()
            if (
                not identity
                or identity["owner_user_id"] != user
                or identity["revoked_at"]
                or identity["expires_at"] <= now
            ):
                raise ExecutionConflict("device_credentials_invalid")
            old = conn.execute(
                "SELECT generation FROM execution_device_connections WHERE device_id=?", (device,)
            ).fetchone()
            generation = int(old["generation"]) + 1 if old else 1
            conn.execute(
                "UPDATE execution_tools SET state='unknown' WHERE device_id=? AND state IN ('pending','sent')",
                (device,),
            )
            conn.execute(
                "INSERT INTO execution_device_connections VALUES(?,?,?,?,?,'connected',?) ON CONFLICT(device_id) DO UPDATE SET user_id=excluded.user_id,node_id=excluded.node_id,incarnation=excluded.incarnation,generation=excluded.generation,state='connected',metadata=excluded.metadata",
                (device, user, node, incarnation, generation, encoded),
            )
            return generation

    def disconnect_device(self, device: str, node: str, incarnation: str, generation: int) -> None:
        with self._transaction() as (conn, _):
            changed = conn.execute(
                "UPDATE execution_device_connections SET state='disconnected' WHERE device_id=? AND node_id=? AND incarnation=? AND generation=?",
                (device, node, incarnation, generation),
            ).rowcount
            if changed:
                conn.execute(
                    "UPDATE execution_tools SET state='unknown' WHERE device_id=? AND generation=? AND state IN ('pending','sent')",
                    (device, generation),
                )

    def device(self, device: str, *, user: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT d.* FROM execution_device_connections d JOIN execution_nodes n ON n.node_id=d.node_id AND n.incarnation=d.incarnation JOIN desktop_devices i ON i.device_id=d.device_id WHERE d.device_id=? AND d.user_id=? AND d.state='connected' AND n.state IN ('ready','draining') AND n.lease_until>? AND i.revoked_at IS NULL AND i.expires_at>?",
                (device, user, self._now(conn), self._now(conn)),
            ).fetchone()
            return {**dict(row), "metadata": json.loads(row["metadata"])} if row else None

    def tool(self, run: str, operation: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM execution_tools WHERE run_id=? AND operation_id=?", (run, operation)
            ).fetchone()
            return dict(row) if row else None

    def request_tool(
        self,
        run: str,
        node: str,
        incarnation: str,
        token: int,
        *,
        operation: str,
        device: str,
        generation: int,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        encoded = wire(request)
        with self._transaction() as (conn, now):
            owner = self._fence(conn, now, run, node, incarnation, token)
            if owner["state"] != "running":
                raise ExecutionConflict("execution_not_running")
            connection = conn.execute(
                "SELECT * FROM execution_device_connections WHERE device_id=? AND generation=? AND user_id=? AND state='connected'",
                (device, generation, owner["user_id"]),
            ).fetchone()
            if not connection:
                raise ExecutionConflict("device_connection_changed")
            self._node(conn, now, connection["node_id"], connection["incarnation"])
            identity = conn.execute(
                "SELECT * FROM desktop_devices WHERE device_id=? AND revoked_at IS NULL AND expires_at>?",
                (device, now),
            ).fetchone()
            if not identity:
                raise ExecutionConflict("device_credentials_invalid")
            old = conn.execute(
                "SELECT * FROM execution_tools WHERE run_id=? AND operation_id=?", (run, operation)
            ).fetchone()
            if old:
                if (old["request_json"], old["device_id"], old["generation"]) != (
                    encoded,
                    device,
                    generation,
                ):
                    raise ExecutionConflict("tool_identity_conflict")
                return dict(old)
            conn.execute(
                "INSERT INTO execution_tools VALUES(?,?,?,?,?,'pending',NULL)",
                (run, operation, device, generation, encoded),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM execution_tools WHERE run_id=? AND operation_id=?",
                    (run, operation),
                ).fetchone()
            )

    def take_tools(
        self, device: str, node: str, incarnation: str, generation: int
    ) -> list[dict[str, Any]]:
        with self._transaction() as (conn, now):
            self._node(conn, now, node, incarnation)
            connection = conn.execute(
                "SELECT 1 FROM execution_device_connections WHERE device_id=? AND node_id=? AND incarnation=? AND generation=? AND state='connected'",
                (device, node, incarnation, generation),
            ).fetchone()
            if not connection:
                raise ExecutionConflict("device_connection_changed")
            rows = conn.execute(
                "SELECT t.* FROM execution_tools t JOIN execution_runs r ON r.run_id=t.run_id JOIN execution_nodes n ON n.node_id=r.node_id AND n.incarnation=r.incarnation WHERE t.device_id=? AND t.generation=? AND t.state='pending' AND r.state='running' AND n.state IN ('ready','draining') AND n.lease_until>? ORDER BY t.operation_id LIMIT 16",
                (device, generation, now),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE execution_tools SET state='sent' WHERE run_id=? AND operation_id=?",
                    (row["run_id"], row["operation_id"]),
                )
            return [{**dict(row), "request": json.loads(row["request_json"])} for row in rows]

    def send_tool(
        self, run: str, operation: str, *, device: str, node: str, incarnation: str, generation: int
    ) -> None:
        """Claim one socket delivery, persisting uncertainty before sending bytes."""
        with self._transaction() as (conn, now):
            self._node(conn, now, node, incarnation)
            connection = conn.execute(
                "SELECT 1 FROM execution_device_connections WHERE device_id=? AND node_id=? AND incarnation=? AND generation=? AND state='connected'",
                (device, node, incarnation, generation),
            ).fetchone()
            owner = conn.execute("SELECT * FROM execution_runs WHERE run_id=?", (run,)).fetchone()
            if not connection or not owner or owner["state"] != "running":
                raise ExecutionConflict("device_connection_changed")
            self._fence(conn, now, run, owner["node_id"], owner["incarnation"], owner["token"])
            if not conn.execute(
                "UPDATE execution_tools SET state='sent' WHERE run_id=? AND operation_id=? AND device_id=? AND generation=? AND state='pending'",
                (run, operation, device, generation),
            ).rowcount:
                raise ExecutionConflict("tool_already_sent")

    def device_operation(
        self, device: str, generation: int, operation: str
    ) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_tools WHERE device_id=? AND generation=? AND operation_id=? LIMIT 2",
                (device, generation, operation),
            ).fetchall()
            return dict(rows[0]) if len(rows) == 1 else None

    def complete_tool(
        self,
        run: str,
        operation: str,
        *,
        device: str,
        node: str,
        incarnation: str,
        generation: int,
        result: dict[str, Any],
    ) -> None:
        encoded = wire(result)
        with self._transaction() as (conn, now):
            self._node(conn, now, node, incarnation)
            connection = conn.execute(
                "SELECT 1 FROM execution_device_connections WHERE device_id=? AND node_id=? AND incarnation=? AND generation=? AND state='connected'",
                (device, node, incarnation, generation),
            ).fetchone()
            row = conn.execute(
                "SELECT * FROM execution_tools WHERE run_id=? AND operation_id=? AND device_id=? AND generation=?",
                (run, operation, device, generation),
            ).fetchone()
            if not connection or not row or row["state"] not in {"sent", "unknown", "completed"}:
                raise ExecutionConflict("device_connection_changed")
            if row["state"] == "completed":
                if row["result_json"] != encoded:
                    raise ExecutionConflict("tool_identity_conflict")
                return
            conn.execute(
                "UPDATE execution_tools SET state='completed',result_json=? WHERE run_id=? AND operation_id=?",
                (encoded, run, operation),
            )
            self._event(
                conn,
                now,
                run,
                "tool:" + operation,
                {"type": "execution.tool", "operation_id": operation, "state": "completed"},
            )
