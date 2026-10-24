"""Durable desktop leases, idempotent events, and opaque LangGraph checkpoints."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos.thread_messages import ThreadMessageInput


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RuntimeConflict(ValueError):
    pass


class CompanyRuntimeRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    def binding(self, thread_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM company_runtime_threads WHERE thread_id=?", (thread_id,)
            ).fetchone()
        return dict(row) if row else None

    def task(self, task_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM company_runtime_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def storage_summary(self) -> dict[str, Any]:
        tables = {
            "tasks": ("company_runtime_tasks", "LENGTH(request_json)+LENGTH(expert_json)"),
            "events": ("company_runtime_events", "LENGTH(payload)"),
            "checkpoints": ("company_runtime_checkpoints", "LENGTH(payload)"),
            "writes": ("company_runtime_writes", "LENGTH(payload)"),
            "operations": ("company_runtime_operations", "LENGTH(COALESCE(result_json,''))"),
        }
        with self.db.connect() as conn:
            result = {}
            for key, (table, length) in tables.items():
                row = conn.execute(
                    f"SELECT COUNT(*),COALESCE(SUM({length}),0) FROM {table}"
                ).fetchone()
                result[key] = {"rows": int(row[0]), "payload_characters": int(row[1])}
            result["task_statuses"] = {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    "SELECT status,COUNT(*) FROM company_runtime_tasks GROUP BY status"
                ).fetchall()
            }
            row = conn.execute(
                "SELECT COUNT(*),COALESCE(MAX(attempts),0) FROM communication_identity_outbox"
            ).fetchone()
            result["identity_delivery"] = {"pending": int(row[0]), "max_attempts": int(row[1])}
            row = conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(CASE WHEN revoked_at IS NOT NULL THEN 1 ELSE 0 END),0) FROM desktop_devices"
            ).fetchone()
            result["devices"] = {"total": int(row[0]), "revoked": int(row[1])}
        return result

    def acquire(
        self,
        *,
        task_id: str,
        thread_id: str,
        owner: int,
        agent_id: str,
        device_id: str,
        request: dict[str, Any],
        expert: dict[str, Any],
        installation_hash: str,
        recovery_hash: str,
        edit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = int(time.time())
        with self.db.transaction() as conn:
            # The server queue and desktop leases share admission ordering.
            # Keep this lock before the thread lock, as in ExecutionRepo.submit.
            conn.execute("UPDATE execution_control SET id=id WHERE id=1")
            # Serializes acquisition across processes and PostgreSQL workers too.
            conn.execute(
                "UPDATE threads SET last_active=last_active WHERE thread_id=?", (thread_id,)
            )
            thread = conn.execute(
                "SELECT * FROM threads WHERE thread_id=?", (thread_id,)
            ).fetchone()
            if not thread or thread["user_id"] != owner or thread["agent_id"] != agent_id:
                raise RuntimeConflict("thread_not_owned")
            existing = conn.execute(
                "SELECT * FROM company_runtime_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if existing:
                if (
                    existing["thread_id"] != thread_id
                    or existing["device_id"] != device_id
                    or existing["request_json"] != encode(request)
                ):
                    raise RuntimeConflict("task_identity_conflict")
                return dict(existing)
            if conn.execute(
                "SELECT 1 FROM execution_sessions WHERE thread_id=?", (thread_id,)
            ).fetchone():
                raise RuntimeConflict("thread_already_running")
            binding = conn.execute(
                "SELECT * FROM company_runtime_threads WHERE thread_id=?", (thread_id,)
            ).fetchone()
            if binding and binding["installation_hash"] != installation_hash:
                raise RuntimeConflict("project_on_another_device")
            if not binding:
                has_history = conn.execute(
                    "SELECT 1 FROM thread_messages WHERE thread_id=? LIMIT 1", (thread_id,)
                ).fetchone()
                if thread["last_active"] or has_history:
                    raise RuntimeConflict("legacy_thread_read_only")
                conn.execute(
                    "INSERT INTO company_runtime_threads VALUES(?,?,?,?,?,?,?)",
                    (thread_id, owner, agent_id, device_id, thread_id, installation_hash, now),
                )
            active = conn.execute(
                "SELECT task_id FROM company_runtime_tasks WHERE thread_id=? AND status IN ('running','cancel_requested','unknown')",
                (thread_id,),
            ).fetchone()
            if active:
                raise RuntimeConflict("thread_already_running")
            if request.get("edit_message_id"):
                if edit is None:
                    raise RuntimeConflict("edit_history_changed")
                target = conn.execute(
                    "SELECT seq,role FROM thread_messages WHERE thread_id=? AND message_id=?",
                    (thread_id, request["edit_message_id"]),
                ).fetchone()
                head = conn.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM thread_messages WHERE thread_id=?",
                    (thread_id,),
                ).fetchone()[0]
                latest = conn.execute(
                    "SELECT task_id FROM company_runtime_tasks WHERE thread_id=? ORDER BY id DESC LIMIT 1",
                    (thread_id,),
                ).fetchone()
                previous_checkpoint = conn.execute(
                    "SELECT checkpoint_id FROM company_runtime_checkpoints WHERE thread_id=? AND namespace='' ORDER BY checkpoint_id DESC LIMIT 1",
                    (thread_id,),
                ).fetchone()
                if (
                    not target
                    or target["role"] != "human"
                    or head != edit["head"]
                    or (latest["task_id"] if latest else None) != edit["task_id"]
                    or (previous_checkpoint["checkpoint_id"] if previous_checkpoint else None)
                    != edit["checkpoint_id"]
                ):
                    raise RuntimeConflict("edit_history_changed")
                checkpoint = edit["checkpoint"]
                checkpoint_id = checkpoint["config"]["configurable"]["checkpoint_id"]
                conn.execute(
                    "INSERT INTO company_runtime_checkpoints VALUES(?,?,?,?)",
                    (thread_id, "", checkpoint_id, encode(checkpoint)),
                )
                conn.execute(
                    "DELETE FROM thread_messages WHERE thread_id=? AND seq>=?",
                    (thread_id, target["seq"]),
                )
            conn.execute(
                "UPDATE company_runtime_threads SET device_id=? WHERE thread_id=?",
                (device_id, thread_id),
            )
            conn.execute(
                "INSERT INTO company_runtime_tasks(task_id,thread_id,device_id,status,request_json,expert_json,recovery_hash,started_at,updated_at) VALUES(?,?,?,'running',?,?,?,?,?)",
                (
                    task_id,
                    thread_id,
                    device_id,
                    encode(request),
                    encode(expert),
                    recovery_hash,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM company_runtime_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        return dict(row)

    def edit_context(self, thread_id: str, message_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            target = conn.execute(
                "SELECT seq,role FROM thread_messages WHERE thread_id=? AND message_id=?",
                (thread_id, message_id),
            ).fetchone()
            if not target or target["role"] != "human":
                raise RuntimeConflict("edit_message_not_found")
            rows = conn.execute(
                "SELECT message_json FROM thread_messages WHERE thread_id=? AND seq<? ORDER BY seq",
                (thread_id, target["seq"]),
            ).fetchall()
            head = conn.execute(
                "SELECT COALESCE(MAX(seq),0) FROM thread_messages WHERE thread_id=?", (thread_id,)
            ).fetchone()[0]
            latest = conn.execute(
                "SELECT task_id FROM company_runtime_tasks WHERE thread_id=? ORDER BY id DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            checkpoint = conn.execute(
                "SELECT checkpoint_id FROM company_runtime_checkpoints WHERE thread_id=? AND namespace='' ORDER BY checkpoint_id DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        return {
            "head": head,
            "task_id": latest["task_id"] if latest else None,
            "messages": [json.loads(row["message_json"]) for row in rows],
            "checkpoint_id": checkpoint["checkpoint_id"] if checkpoint else None,
        }

    def append_event(
        self,
        task_id: str,
        event_id: str,
        seq: int,
        payload: dict[str, Any],
        *,
        messages: Sequence[ThreadMessageInput] = (),
    ) -> bool:
        wire = encode(payload)
        with self.db.transaction() as conn:
            task = conn.execute(
                "SELECT thread_id FROM company_runtime_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not task:
                raise RuntimeConflict("task_not_found")
            thread_id = task["thread_id"]
            # Match acquisition lock order. An old receipt must not resurrect an
            # edited branch after a new task has atomically replaced it.
            conn.execute(
                "UPDATE threads SET last_active=last_active WHERE thread_id=?", (thread_id,)
            )
            conn.execute(
                "UPDATE company_runtime_tasks SET updated_at=? WHERE task_id=?",
                (int(time.time()), task_id),
            )
            old = conn.execute(
                "SELECT seq,payload FROM company_runtime_events WHERE task_id=? AND event_id=?",
                (task_id, event_id),
            ).fetchone()
            if old:
                if old["seq"] != seq or old["payload"] != wire:
                    raise RuntimeConflict("event_identity_conflict")
            else:
                head = conn.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM company_runtime_events WHERE task_id=?",
                    (task_id,),
                ).fetchone()[0]
                if seq != head + 1:
                    raise RuntimeConflict("event_sequence_conflict")
                conn.execute(
                    "INSERT INTO company_runtime_events VALUES(?,?,?,?)",
                    (task_id, event_id, seq, wire),
                )
            latest = conn.execute(
                "SELECT task_id FROM company_runtime_tasks WHERE thread_id=? ORDER BY id DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            if messages and latest["task_id"] == task_id:
                rows = conn.execute(
                    "SELECT id,seq,message_id FROM thread_messages WHERE thread_id=? ORDER BY seq",
                    (thread_id,),
                ).fetchall()
                incoming = {message.message_id: message for message in messages}
                existing = {row["message_id"]: row["id"] for row in rows}
                # A failed turn may first arrive in a later cumulative receipt.
                # Follow its message order instead of appending behind the retry.
                # Keep rows absent from that receipt beside their original anchor.
                retained: dict[str | None, list[int]] = {}
                anchor = None
                for row in rows:
                    if row["message_id"] in incoming:
                        anchor = row["message_id"]
                    else:
                        retained.setdefault(anchor, []).append(row["id"])
                ordered: list[int | str | None] = list(retained.get(None, []))
                for ident in incoming:
                    ordered.append(existing.get(ident, ident))
                    ordered.extend(retained.get(ident, []))
                unchanged = 0
                while unchanged < len(rows) and rows[unchanged]["id"] == ordered[unchanged]:
                    unchanged += 1
                if unchanged < len(rows):
                    # Avoid UNIQUE(thread_id,seq) collisions during the move.
                    # Readers see only the final order when this transaction commits.
                    conn.execute(
                        "UPDATE thread_messages SET seq=-seq WHERE thread_id=? AND seq>=?",
                        (thread_id, rows[unchanged]["seq"]),
                    )
                message_seq = rows[unchanged - 1]["seq"] if unchanged else 0
                for ordered_id in ordered[unchanged:]:
                    message_seq += 1
                    if isinstance(ordered_id, int):
                        conn.execute(
                            "UPDATE thread_messages SET seq=? WHERE thread_id=? AND id=?",
                            (message_seq, thread_id, ordered_id),
                        )
                        continue
                    message = incoming[ordered_id]
                    conn.execute(
                        "INSERT INTO thread_messages(thread_id,seq,message_id,role,message_json,created_at) VALUES(?,?,?,?,?,?)",
                        (
                            thread_id,
                            message_seq,
                            message.message_id,
                            message.role,
                            message.message_json,
                            message.created_at,
                        ),
                    )
        return old is None

    def events(self, task_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT event_id,seq,payload FROM company_runtime_events WHERE task_id=? AND seq>? ORDER BY seq LIMIT 500",
                (task_id, after),
            ).fetchall()
        return [
            {"event_id": r["event_id"], "seq": r["seq"], "payload": json.loads(r["payload"])}
            for r in rows
        ]

    def cancel_running(self, task_id: str) -> None:
        """Policy cancellation never changes a task that already finished."""
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE company_runtime_tasks SET status='cancel_requested',updated_at=? "
                "WHERE task_id=? AND status='running'",
                (int(time.time()), task_id),
            )

    def has_active_tasks(self, owner_user_id: int) -> bool:
        with self.db.connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM company_runtime_tasks t JOIN company_runtime_threads b "
                    "ON b.thread_id=t.thread_id WHERE b.owner_user_id=? "
                    "AND t.status IN ('running','cancel_requested') LIMIT 1",
                    (owner_user_id,),
                ).fetchone()
                is not None
            )

    def transition(self, task_id: str, status: str) -> None:
        if status not in {"completed", "failed", "cancelled", "unknown", "cancel_requested"}:
            raise RuntimeConflict("invalid_task_status")
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE company_runtime_tasks SET updated_at=updated_at WHERE task_id=?", (task_id,)
            )
            row = conn.execute(
                "SELECT status FROM company_runtime_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not row or (
                row["status"] not in {"running", "cancel_requested", "unknown"}
                and row["status"] != status
            ):
                raise RuntimeConflict("task_already_finished")
            if row["status"] == "cancel_requested" and status == "completed":
                raise RuntimeConflict("task_cancelled")
            conn.execute(
                "UPDATE company_runtime_tasks SET status=?,updated_at=? WHERE task_id=?",
                (status, int(time.time()), task_id),
            )

    def checkpoint(
        self, thread: str, namespace: str, checkpoint_id: str | None
    ) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            if checkpoint_id:
                row = conn.execute(
                    "SELECT checkpoint_id,payload FROM company_runtime_checkpoints WHERE thread_id=? AND namespace=? AND checkpoint_id=?",
                    (thread, namespace, checkpoint_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT checkpoint_id,payload FROM company_runtime_checkpoints WHERE thread_id=? AND namespace=? ORDER BY checkpoint_id DESC LIMIT 1",
                    (thread, namespace),
                ).fetchone()
            if not row:
                return None
            writes = conn.execute(
                "SELECT payload FROM company_runtime_writes WHERE thread_id=? AND namespace=? AND checkpoint_id=? ORDER BY task_key,idx",
                (thread, namespace, row["checkpoint_id"]),
            ).fetchall()
        return {
            **json.loads(row["payload"]),
            "pending_writes": [json.loads(w["payload"]) for w in writes],
        }

    def put_checkpoint(
        self, thread: str, namespace: str, checkpoint_id: str, payload: dict[str, Any]
    ) -> None:
        with self.db.transaction() as conn:
            old = conn.execute(
                "SELECT payload FROM company_runtime_checkpoints WHERE thread_id=? AND namespace=? AND checkpoint_id=?",
                (thread, namespace, checkpoint_id),
            ).fetchone()
            if old:
                if old["payload"] != encode(payload):
                    raise RuntimeConflict("checkpoint_identity_conflict")
                return
            conn.execute(
                "INSERT INTO company_runtime_checkpoints VALUES(?,?,?,?)",
                (thread, namespace, checkpoint_id, encode(payload)),
            )

    def checkpoint_batch(self, task_id: str, thread: str, operations: list[dict[str, Any]]) -> None:
        """Commit a device's ordered outbox atomically while its task owns the thread."""
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE company_runtime_tasks SET updated_at=updated_at WHERE task_id=?", (task_id,)
            )
            latest = conn.execute(
                "SELECT task_id,status FROM company_runtime_tasks WHERE thread_id=? ORDER BY id DESC LIMIT 1",
                (thread,),
            ).fetchone()
            if (
                not latest
                or latest["task_id"] != task_id
                or latest["status"] not in {"running", "cancel_requested", "unknown"}
            ):
                raise RuntimeConflict("task_already_finished")
            for item in operations:
                namespace, checkpoint_id = item["namespace"], item["checkpoint_id"]
                if item["action"] == "put":
                    payload = encode(item["payload"])
                    old = conn.execute(
                        "SELECT payload FROM company_runtime_checkpoints WHERE thread_id=? AND namespace=? AND checkpoint_id=?",
                        (thread, namespace, checkpoint_id),
                    ).fetchone()
                    if old:
                        if old["payload"] != payload:
                            raise RuntimeConflict("checkpoint_identity_conflict")
                    else:
                        conn.execute(
                            "INSERT INTO company_runtime_checkpoints VALUES(?,?,?,?)",
                            (thread, namespace, checkpoint_id, payload),
                        )
                else:
                    for write in item["writes"]:
                        conn.execute(
                            "INSERT INTO company_runtime_writes VALUES(?,?,?,?,?,?) ON CONFLICT(thread_id,namespace,checkpoint_id,task_key,idx) DO UPDATE SET payload=excluded.payload",
                            (
                                thread,
                                namespace,
                                checkpoint_id,
                                item["task_key"],
                                write["idx"],
                                encode(write["value"]),
                            ),
                        )

    def put_writes(
        self,
        thread: str,
        namespace: str,
        checkpoint_id: str,
        task_key: str,
        writes: list[dict[str, Any]],
    ) -> None:
        with self.db.transaction() as conn:
            for write in writes:
                conn.execute(
                    "INSERT INTO company_runtime_writes VALUES(?,?,?,?,?,?) ON CONFLICT(thread_id,namespace,checkpoint_id,task_key,idx) DO UPDATE SET payload=excluded.payload",
                    (
                        thread,
                        namespace,
                        checkpoint_id,
                        task_key,
                        write["idx"],
                        encode(write["value"]),
                    ),
                )

    def begin_operation(
        self, task: str, operation: str, request_hash: str
    ) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE company_runtime_tasks SET updated_at=updated_at WHERE task_id=?", (task,)
            )
            row = conn.execute(
                "SELECT * FROM company_runtime_operations WHERE task_id=? AND operation_id=?",
                (task, operation),
            ).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise RuntimeConflict("operation_identity_conflict")
                return dict(row)
            conn.execute(
                "INSERT INTO company_runtime_operations VALUES(?,?,?,'running',NULL)",
                (task, operation, request_hash),
            )
        return None

    def finish_operation(self, task: str, operation: str, result: Any) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE company_runtime_operations SET status='completed',result_json=? WHERE task_id=? AND operation_id=?",
                (encode(result), task, operation),
            )

    def operation(self, task: str, operation: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM company_runtime_operations WHERE task_id=? AND operation_id=?",
                (task, operation),
            ).fetchone()
        return dict(row) if row else None

    def fail_operation(self, task: str, operation: str, reason: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE company_runtime_operations SET status='failed',result_json=? WHERE task_id=? AND operation_id=? AND status='running'",
                (encode({"reason": reason}), task, operation),
            )

    def thread_state(self, thread_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            binding = conn.execute(
                "SELECT device_id FROM company_runtime_threads WHERE thread_id=?", (thread_id,)
            ).fetchone()
            if not binding:
                return {"execution_location": "server"}
            task = conn.execute(
                "SELECT task_id,status FROM company_runtime_tasks WHERE thread_id=? ORDER BY id DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        return {
            "execution_location": "desktop",
            "local_device_id": binding["device_id"],
            "local_task_status": task["status"] if task else None,
            "local_task_id": task["task_id"] if task else None,
        }

    def recover(
        self,
        task_id: str,
        *,
        owner: int,
        device_id: str,
        installation_hash: str,
        recovery_hash: str,
    ) -> dict[str, Any]:
        """Rebind receipts after login, never start or steal an active executor."""
        import hmac

        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE company_runtime_tasks SET updated_at=updated_at WHERE task_id=?", (task_id,)
            )
            task = conn.execute(
                "SELECT * FROM company_runtime_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not task:
                raise RuntimeConflict("task_not_found")
            binding = conn.execute(
                "SELECT * FROM company_runtime_threads WHERE thread_id=?", (task["thread_id"],)
            ).fetchone()
            if (
                not binding
                or binding["owner_user_id"] != owner
                or not hmac.compare_digest(binding["installation_hash"], installation_hash)
                or not hmac.compare_digest(task["recovery_hash"], recovery_hash)
            ):
                raise RuntimeConflict("recovery_not_owned")
            if task["device_id"] != device_id and task["status"] in {"running", "cancel_requested"}:
                old = conn.execute(
                    "SELECT revoked_at,expires_at,last_seen_at FROM desktop_devices WHERE device_id=?",
                    (task["device_id"],),
                ).fetchone()
                if (
                    old
                    and old["revoked_at"] is None
                    and old["expires_at"] > int(time.time())
                    and old["last_seen_at"] > int(time.time()) - 60
                ):
                    raise RuntimeConflict("previous_device_still_active")
            status = (
                "unknown" if task["status"] in {"running", "cancel_requested"} else task["status"]
            )
            conn.execute(
                "UPDATE company_runtime_tasks SET device_id=?,status=?,updated_at=? WHERE task_id=?",
                (device_id, status, int(time.time()), task_id),
            )
            conn.execute(
                "UPDATE company_runtime_threads SET device_id=? WHERE thread_id=?",
                (device_id, task["thread_id"]),
            )
        return {**dict(task), "device_id": device_id, "status": status}

    def artifact_turns(self, thread_id: str) -> list[dict[str, Any]]:
        """Reconstruct turn ownership from receipts, including old cumulative clients.

        No checkpoint decode or file copy is needed. Earlier versions emitted a
        complete project snapshot each turn; subtract unchanged path/hash pairs
        in task order while retaining overwritten files under their original turn.
        """
        tasks: dict[str, dict[str, Any]] = {}
        with self.db.connect() as conn:
            visible_users = {
                row["message_id"]
                for row in conn.execute(
                    "SELECT message_id FROM thread_messages WHERE thread_id=? AND role='human'",
                    (thread_id,),
                ).fetchall()
            }
            rows = conn.execute(
                "SELECT t.task_id,t.device_id,e.payload FROM company_runtime_events e "
                "JOIN company_runtime_tasks t ON t.task_id=e.task_id "
                "WHERE t.thread_id=? AND e.payload LIKE ? ORDER BY t.id,e.seq",
                (thread_id, '%"kind":"artifacts"%'),
            ).fetchall()
            for row in rows:
                task_id = row["task_id"]
                tasks[task_id] = {
                    "task_id": task_id,
                    "device_id": row["device_id"],
                    "user_message_id": task_id + "_user",
                    "message_id": None,
                    "receipt": json.loads(row["payload"]),
                }
            for task in tasks.values():
                if task["receipt"].get("scope") == "turn":
                    # Current clients provide anchors in their compact receipt;
                    # do not load their full cumulative message event payloads.
                    continue
                row = conn.execute(
                    "SELECT payload FROM company_runtime_events "
                    "WHERE task_id=? AND payload LIKE ? ORDER BY seq DESC LIMIT 1",
                    (task["task_id"], '%"kind":"messages"%'),
                ).fetchone()
                if row is None:
                    continue
                # Old final history events include every earlier round. Only
                # inspect messages after this task's own HumanMessage boundary.
                in_turn = False
                for msg in json.loads(row["payload"]).get("messages", []):
                    data = msg.get("data", {})
                    if msg.get("type") == "human":
                        in_turn = data.get("id") == task["user_message_id"]
                    elif in_turn and msg.get("type") == "ai" and data.get("id"):
                        task["message_id"] = data["id"]
        known: dict[str, str] = {}
        turns = []
        for task in tasks.values():
            receipt = task.pop("receipt", None)
            if receipt is None:
                continue
            files = receipt.get("files", [])
            current = {item["path"]: item["sha256"] for item in files}
            if receipt.get("scope") == "turn":
                changes = files
                known.update(current)
            else:
                changes = [item for item in files if known.get(item["path"]) != item["sha256"]]
                known = current
            if not changes:
                continue
            # Future receipts carry the real boundary directly. Legacy records
            # use the task's deterministic user id and AI id from message events.
            for key in ("user_message_id", "message_id"):
                if isinstance(receipt.get(key), str) and receipt[key]:
                    task[key] = receipt[key]
            if task["user_message_id"] not in visible_users:
                continue
            turns.append({**task, "files": changes})
        return turns

    def artifacts(self, thread_id: str) -> list[dict[str, Any]]:
        """Compatibility view for clients predating per-turn artifact ownership."""
        files = {}
        for turn in self.artifact_turns(thread_id):
            for item in turn["files"]:
                files[item["path"]] = item
        return list(files.values())
