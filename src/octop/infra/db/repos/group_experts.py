"""SQL-only group admission, immutable snapshots and durable control receipts.

Transport/domain services must authenticate internal callers and obtain current
Go context before entering these short transactions. No group result is exposed
through the personal execution APIs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos.execution import (
    GROUP_EXPERT_UNKNOWN_HOLD_SECONDS,
    TERMINAL,
    ExecutionConflict,
    ExecutionRepo,
    wire,
)

PROTOCOL = 2
IDENTITY_FIELDS = (
    "call_id",
    "user_id",
    "conversation_id",
    "message_id",
    "binding_id",
    "binding_generation",
    "binding_scope_revision",
    "source_agent_id",
    "source_row_id",
    "authorization_versions",
    "max_text_chars",
)


def digest(value: Any) -> str:
    return hashlib.sha256(wire(value).encode()).hexdigest()


def _revision_totals(conn: Any, query: str) -> tuple[int, int]:
    row = conn.execute(query).fetchone()
    # PostgreSQL's compatibility row iterates keys, SQLite's iterates values;
    # PostgreSQL SUM(bigint) also returns Decimal. Use positional integers.
    return int(row[0]), int(row[1])


def enqueue(
    conn: Any, now: int, *, operation: str, kind: str, target: str, payload: dict[str, Any]
) -> None:
    encoded = wire(payload)
    old = conn.execute(
        "SELECT kind,target_id,payload FROM group_expert_outbox WHERE operation_id=?", (operation,)
    ).fetchone()
    if old:
        if old["kind"] != kind or old["target_id"] != target or old["payload"] != encoded:
            raise ExecutionConflict("group_expert_operation_conflict")
        return
    conn.execute(
        "INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) VALUES(?,?,?,?,?,?)",
        (operation, kind, target, encoded, hashlib.sha256(encoded.encode()).hexdigest(), now),
    )


def record_state(
    conn: Any,
    now: int,
    run: str,
    state: str,
    result: dict[str, Any] | None = None,
    *,
    reason: str = "",
    usage: dict[str, Any] | None = None,
) -> None:
    """Persist the first delivery conclusion in the same transaction as execution.

    Unknown is final for group delivery, even if accounting later reconciles it.
    Cancellation/stop-proof paths therefore do not need live read permission.
    """
    call = conn.execute("SELECT * FROM group_expert_calls WHERE run_id=?", (run,)).fetchone()
    if not call or call["result_json"] is not None:
        return
    if state == "completed":
        if not result or not isinstance(result.get("text"), str) or not result["text"].strip():
            raise ExecutionConflict("group_expert_result_required")
        maximum = json.loads(call["request_json"])["max_text_chars"]
        if (
            len(result["text"]) > maximum
            or set(result) - {"text", "truncated", "incomplete", "attachments"}
            or not isinstance(result.get("attachments", []), list)
            or len(result.get("attachments", [])) > 4
            or any(
                not isinstance(v, str) or not v or len(v) > 160
                for v in result.get("attachments", [])
            )
            or any(
                type(result[key]) is not bool
                for key in ("truncated", "incomplete")
                if key in result
            )
        ):
            raise ExecutionConflict("group_expert_result_invalid")
    elif result:
        raise ExecutionConflict("group_expert_result_invalid")
    value = {
        "call_id": call["call_id"],
        "execution_id": run,
        "execution_state": state,
        "result": result,
    }
    if reason:
        value["reason_code"] = reason
    if usage is not None:
        if any(
            type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")
        ):
            raise ExecutionConflict("group_expert_usage_invalid")
        conn.execute(
            "UPDATE group_expert_calls SET usage_json=? WHERE call_id=?",
            (wire(usage), call["call_id"]),
        )
        # Keep the immutable receipt even when the source was deleted while the
        # provider was running; usage_log has legacy source/user foreign keys.
        if conn.execute(
            "SELECT 1 FROM agents a,users u WHERE a.agent_id=? AND u.id=?",
            (call["source_agent_id"], call["user_id"]),
        ).fetchone():
            conn.execute(
                "INSERT INTO usage_log(ts,agent_id,user_id,thread_id,model,input_tokens,uncached_input_tokens,cache_read_tokens,cache_write_tokens,output_tokens,reasoning_tokens,total_tokens,model_calls,source,execution_run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now,
                    call["source_agent_id"],
                    call["user_id"],
                    "",
                    usage["model"],
                    usage["input_tokens"],
                    max(0, usage["input_tokens"] - usage.get("cache_read_tokens", 0)),
                    usage.get("cache_read_tokens", 0),
                    usage.get("cache_write_tokens", 0),
                    usage["output_tokens"],
                    usage.get("reasoning_tokens", 0),
                    usage["input_tokens"] + usage["output_tokens"],
                    1,
                    "group_expert",
                    run,
                ),
            )
    encoded = wire(value)
    result_digest = digest(value)
    conn.execute(
        "UPDATE group_expert_calls SET result_json=?,result_digest=?,reason_code=?,delivery_state='pending' WHERE call_id=?",
        (encoded, result_digest, reason, call["call_id"]),
    )
    enqueue(
        conn,
        now,
        operation="result:" + call["call_id"],
        kind="result",
        target=call["call_id"],
        payload={"call_id": call["call_id"], "result_digest": result_digest},
    )


def check_call(conn: Any, run: Any) -> None:
    """Validate a group subject without weakening the personal-thread predicate."""
    call = conn.execute(
        "SELECT g.*,c.dispatch_seq,c.cancelled FROM group_expert_calls g "
        "JOIN group_expert_controls c ON c.call_id=g.call_id WHERE g.run_id=?",
        (run["run_id"],),
    ).fetchone()
    if (
        not call
        or call["admission_state"] != "accepted"
        or call["cancelled"]
        or call["dispatch_seq"] is None
        or call["user_id"] != run["user_id"]
        or call["source_agent_id"] != run["agent_id"]
        or run["thread_id"] != "group-expert:" + call["call_id"]
        or run["request_key"] != call["call_id"]
    ):
        raise ExecutionConflict("group_expert_not_dispatched")
    check_authorization(conn, dict(call), json.loads(call["snapshot_json"]))


def check_authorization(conn: Any, call: dict[str, Any], snapshot: dict[str, Any]) -> None:
    identity = json.loads(call["request_json"]) if "request_json" in call else call
    if "binding_scope_revision" in identity:
        revoked = conn.execute(
            "SELECT 1 FROM group_expert_binding_revocations WHERE conversation_id=? AND source_agent_id=? AND source_row_id=? AND before_revision>?",
            (
                call["conversation_id"],
                call["source_agent_id"],
                call["source_row_id"],
                identity["binding_scope_revision"],
            ),
        ).fetchone()
        if revoked:
            raise ExecutionConflict("group_expert_removed")
    versions = call["authorization_versions"]
    if isinstance(versions, str):
        versions = json.loads(versions)
    required = {
        "scope:" + call["conversation_id"],
        "user:" + str(call["user_id"]),
        f"source:{call['source_agent_id']}:{call['source_row_id']}",
        "provider:" + snapshot["provider_generation"],
    }
    if not required.issubset(versions):
        raise ExecutionConflict("group_expert_authorization_syncing")
    for domain, version in versions.items():
        row = conn.execute(
            "SELECT revision FROM group_expert_authorizations WHERE domain_key=?", (domain,)
        ).fetchone()
        if type(version) is not int or version < 1 or not row or row[0] != version:
            raise ExecutionConflict("group_expert_authorization_changed")
    scope = conn.execute(
        "SELECT * FROM group_expert_scopes WHERE conversation_id=?", (call["conversation_id"],)
    ).fetchone()
    identity = {
        "source_agent_id": call["source_agent_id"],
        "source_row_id": call["source_row_id"],
    }
    if (
        not scope
        or scope["revision"] != versions["scope:" + call["conversation_id"]]
        or identity not in json.loads(scope["sources_json"])
    ):
        raise ExecutionConflict("group_expert_outside_scope")
    if scope["department_id"] is not None:
        department = conn.execute(
            "SELECT expert_ids FROM department_resource_pools WHERE scope_id=?",
            (scope["department_id"],),
        ).fetchone()
        if not department or call["source_agent_id"] not in json.loads(department[0]):
            raise ExecutionConflict("group_expert_outside_scope")
    user = conn.execute(
        "SELECT * FROM users WHERE id=? AND disabled=0", (call["user_id"],)
    ).fetchone()
    source = conn.execute(
        "SELECT a.* FROM agents a JOIN expert_assignment_source s ON s.source_user_id=a.user_id "
        "JOIN users u ON u.id=s.source_user_id AND u.disabled=0 AND u.role='admin' "
        "WHERE a.agent_id=? AND a.id=? AND a.enabled=1 AND a.kind='expert'",
        (call["source_agent_id"], call["source_row_id"]),
    ).fetchone()
    if not user or not source:
        raise ExecutionConflict("group_expert_not_authorized")
    if snapshot.get("tool_revision") is not None:
        revision = conn.execute(
            "SELECT revision FROM group_expert_tool_versions WHERE source_agent_id=? AND source_row_id=?",
            (call["source_agent_id"], call["source_row_id"]),
        ).fetchone()
        if not revision or revision[0] != snapshot["tool_revision"]:
            raise ExecutionConflict("group_expert_configuration_changed")
    published = conn.execute(
        "SELECT invalid_reason FROM group_expert_source_versions WHERE source_agent_id=? AND source_row_id=?",
        (call["source_agent_id"], call["source_row_id"]),
    ).fetchone()
    if not published or published[0]:
        raise ExecutionConflict("group_expert_configuration_changed")
    granted = conn.execute(
        "SELECT 1 FROM expert_assignments WHERE user_id=? AND source_agent_id=?",
        (call["user_id"], call["source_agent_id"]),
    ).fetchone()
    if user["role"] != "admin" and (
        (user["expert_access_mode"] == "assigned_only" and not granted)
        or (not granted and source["user_id"] != user["id"] and not source["is_shared"])
    ):
        raise ExecutionConflict("group_expert_not_authorized")
    if user["role"] != "admin":
        membership = conn.execute(
            "SELECT * FROM employee_resource_scopes WHERE user_id=?", (call["user_id"],)
        ).fetchone()
        if not membership or membership["pending_scope"] is not None:
            raise ExecutionConflict("group_expert_authorization_syncing")
        department = conn.execute(
            "SELECT expert_ids FROM department_resource_pools WHERE scope_id=? AND revision>0",
            (membership["scope_id"],),
        ).fetchone()
        # Preserve the existing legacy-assignment rule until a department pool
        # is explicitly saved; a saved empty pool revokes all experts.
        if department and call["source_agent_id"] not in json.loads(department[0]):
            raise ExecutionConflict("group_expert_not_authorized")
        policy = conn.execute(
            "SELECT value FROM user_policies WHERE user_id=? AND name='model_access' AND enabled=1",
            (call["user_id"],),
        ).fetchone()
        if not policy or snapshot["model_ref"] not in json.loads(policy[0]).get("model_refs", []):
            raise ExecutionConflict("group_expert_model_not_assigned")
    provider = conn.execute(
        "SELECT 1 FROM group_expert_provider_versions WHERE provider_id=? AND generation=? AND revision=? AND deleted=0",
        (snapshot["provider_id"], snapshot["provider_generation"], snapshot["provider_revision"]),
    ).fetchone()
    if not provider:
        raise ExecutionConflict("group_expert_configuration_changed")


def cancel_call(conn: Any, now: int, call_id: str, execution: ExecutionRepo) -> None:
    conn.execute(
        "INSERT INTO group_expert_controls(call_id,cancelled) VALUES(?,1) "
        "ON CONFLICT(call_id) DO UPDATE SET cancelled=1",
        (call_id,),
    )
    run = conn.execute(
        "SELECT r.* FROM execution_runs r JOIN group_expert_calls g ON g.run_id=r.run_id WHERE g.call_id=?",
        (call_id,),
    ).fetchone()
    if not run or run["state"] in TERMINAL | {"unknown"}:
        return
    if run["state"] == "queued":
        conn.execute(
            "UPDATE execution_runs SET state='cancelled',updated_at=? WHERE run_id=?",
            (now, run["run_id"]),
        )
        conn.execute("DELETE FROM execution_sessions WHERE run_id=?", (run["run_id"],))
        execution._event(
            conn, now, run["run_id"], "terminal", {"type": "execution.state", "state": "cancelled"}
        )
    else:
        conn.execute(
            "UPDATE execution_runs SET state='cancel_requested',updated_at=? WHERE run_id=?",
            (now, run["run_id"]),
        )
        conn.execute(
            "INSERT INTO execution_commands(run_id,command_key,kind,payload,state,created_at) "
            "VALUES(?,'group-cancel','cancel','{}','pending',?) ON CONFLICT(run_id,command_key) DO NOTHING",
            (run["run_id"], now),
        )


def require_unknown_cooldown_expired(conn: Any, now: int, user: int) -> None:
    # Bound the caller's pause to the same window as the global admission hold.
    # Expiry never resolves the unknown call or releases its provisional usage.
    if conn.execute(
        "SELECT 1 FROM execution_runs WHERE user_id=? AND operation='group_expert' AND state='unknown' AND updated_at>? LIMIT 1",
        (user, now - GROUP_EXPERT_UNKNOWN_HOLD_SECONDS),
    ).fetchone():
        raise ExecutionConflict("group_expert_usage_unknown")


class GroupExpertRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db
        self.execution = ExecutionRepo(db)

    @staticmethod
    def require_capabilities(conn: Any, now: int) -> None:
        mode = conn.execute("SELECT value FROM settings WHERE key='execution.mode'").fetchone()
        topology = ExecutionRepo._topology(conn)
        nodes = conn.execute(
            "SELECT * FROM execution_nodes WHERE role<>'web' AND state<>'stopped'"
        ).fetchall()
        if (
            not mode
            or mode[0] != "distributed-v1"
            or not topology
            or {row["node_id"] for row in nodes} != set(topology)
            or any(
                row["role"] != "worker"
                or row["state"] != "ready"
                or row["lease_until"] <= now
                or row["group_expert_protocol"] != PROTOCOL
                for row in nodes
            )
        ):
            raise ExecutionConflict("group_expert_protocol_unavailable")

    def provider_version(self, provider_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM group_expert_provider_versions WHERE provider_id=?", (provider_id,)
            ).fetchone()
            return dict(row) if row else None

    def tool_revision(self, source: str, source_row: int) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT revision FROM group_expert_tool_versions WHERE source_agent_id=? AND source_row_id=?",
                (source, source_row),
            ).fetchone()
            return int(row[0]) if row else 0

    def capabilities(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            schema = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
            mode = conn.execute("SELECT value FROM settings WHERE key='execution.mode'").fetchone()
        info = {"protocol": PROTOCOL, "schema": schema, "mode": mode[0] if mode else "monolith"}
        try:
            with self.db.connect() as conn:
                if self.db.dialect != "postgresql":
                    raise ExecutionConflict("group_expert_protocol_unavailable")
                self.require_capabilities(conn, self.execution._now(conn))
            return {**info, "ready": True}
        except ExecutionConflict:
            return {**info, "ready": False}

    def result_obligation(self, call: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM group_expert_outbox WHERE operation_id=? AND kind='result'",
                ("result:" + call,),
            ).fetchone()
            return dict(row) if row else None

    def sources(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM group_expert_source_versions ORDER BY source_agent_id"
                ).fetchall()
            ]

    def record_model(
        self, run: str, node: str, incarnation: str, token: int, *, key: str, usage: dict[str, Any]
    ) -> None:
        """Settle each model round and retain its usage before executing tools.

        Accounting must survive a crash in a subsequent tool/model round. A
        settled reservation is an idempotency receipt, never a retry permit.
        """
        with self.execution._transaction() as (conn, now):
            row = self.execution._fence(conn, now, run, node, incarnation, token)
            if row["operation"] != "group_expert":
                raise ExecutionConflict("invalid_group_result_transition")
            previous = conn.execute(
                "SELECT state FROM execution_reservations WHERE run_id=? AND reservation_id=?",
                (run, key),
            ).fetchone()
            if any(
                type(usage.get(k)) is not int or usage[k] < 0
                for k in ("input_tokens", "output_tokens")
            ):
                raise ExecutionConflict("group_expert_usage_invalid")
            self.execution._settle_model(
                conn, run, key=key, actual=usage["input_tokens"] + usage["output_tokens"]
            )
            if previous and previous["state"] == "settled":
                return
            call = conn.execute(
                "SELECT * FROM group_expert_calls WHERE run_id=?", (run,)
            ).fetchone()
            total = json.loads(call["usage_json"] or "{}")
            total["model"] = usage["model"]
            for field in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "reasoning_tokens",
            ):
                total[field] = total.get(field, 0) + usage.get(field, 0)
            total["model_calls"] = total.get("model_calls", 0) + 1
            conn.execute(
                "UPDATE group_expert_calls SET usage_json=? WHERE run_id=?", (wire(total), run)
            )
            if conn.execute(
                "SELECT 1 FROM agents a,users u WHERE a.agent_id=? AND u.id=?",
                (call["source_agent_id"], call["user_id"]),
            ).fetchone():
                conn.execute(
                    "INSERT INTO usage_log(ts,agent_id,user_id,thread_id,model,input_tokens,uncached_input_tokens,cache_read_tokens,cache_write_tokens,output_tokens,reasoning_tokens,total_tokens,model_calls,source,execution_run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        now,
                        call["source_agent_id"],
                        call["user_id"],
                        "",
                        usage["model"],
                        usage["input_tokens"],
                        max(0, usage["input_tokens"] - usage.get("cache_read_tokens", 0)),
                        usage.get("cache_read_tokens", 0),
                        usage.get("cache_write_tokens", 0),
                        usage["output_tokens"],
                        usage.get("reasoning_tokens", 0),
                        usage["input_tokens"] + usage["output_tokens"],
                        1,
                        "group_expert",
                        run,
                    ),
                )

    def begin_tool(
        self,
        run: str,
        node: str,
        incarnation: str,
        token: int,
        *,
        operation: str,
        request: dict[str, Any],
    ) -> None:
        with self.execution._transaction() as (conn, now):
            row = self.execution._fence(conn, now, run, node, incarnation, token)
            if row["operation"] != "group_expert" or row["state"] != "running":
                raise ExecutionConflict("execution_not_running")
            check_call(conn, row)
            call = conn.execute(
                "SELECT snapshot_json FROM group_expert_calls WHERE run_id=?", (run,)
            ).fetchone()
            if request.get("name") not in json.loads(call[0]).get("tools", []):
                raise ExecutionConflict("group_expert_tool_not_authorized")
            if conn.execute(
                "SELECT 1 FROM execution_tools WHERE run_id=? AND operation_id=?", (run, operation)
            ).fetchone():
                raise ExecutionConflict("group_expert_tool_already_sent")
            conn.execute(
                "INSERT INTO execution_tools VALUES(?,?,?,?,?,'sent',NULL)",
                (run, operation, "group:" + run, token, wire(request)),
            )

    def finish_tool(
        self,
        run: str,
        node: str,
        incarnation: str,
        token: int,
        *,
        operation: str,
        result: dict[str, Any],
    ) -> None:
        with self.execution._transaction() as (conn, now):
            row = self.execution._fence(conn, now, run, node, incarnation, token)
            if row["operation"] != "group_expert":
                raise ExecutionConflict("invalid_group_result_transition")
            if not conn.execute(
                "UPDATE execution_tools SET state='completed',result_json=? WHERE run_id=? AND operation_id=? AND device_id=? AND state='sent'",
                (wire(result), run, operation, "group:" + run),
            ).rowcount:
                raise ExecutionConflict("group_expert_tool_receipt_conflict")

    @staticmethod
    def _sync_targets(
        conn: Any, conversation_id: str, source: str | None, source_row: int | None
    ) -> list[str]:
        targets = {conversation_id, "scope:" + conversation_id, "bindings:" + conversation_id}
        scope = conn.execute(
            "SELECT sources_json FROM group_expert_scopes WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        sources = json.loads(scope[0]) if scope else []
        if source is not None and source_row is not None:
            sources = [{"source_agent_id": source, "source_row_id": source_row}]
        for item in sources:
            agent, row = item["source_agent_id"], item["source_row_id"]
            targets.add(f"source:{agent}:{row}")
            version = conn.execute(
                "SELECT snapshot_json FROM group_expert_source_versions WHERE source_agent_id=? AND source_row_id=?",
                (agent, row),
            ).fetchone()
            if version:
                generation = json.loads(version[0]).get("provider_generation")
                if generation:
                    targets.add("provider:" + generation)
        return sorted(targets)

    def syncing(
        self,
        *,
        conversation_id: str | None = None,
        source: str | None = None,
        source_row: int | None = None,
    ) -> bool:
        with self.db.connect() as conn:
            query = "SELECT 1 FROM group_expert_outbox WHERE kind IN ('scope','invalidate') AND acknowledged_at IS NULL"
            args: list[Any] = []
            if conversation_id is not None:
                args = self._sync_targets(conn, conversation_id, source, source_row)
                query += " AND target_id IN (" + ",".join("?" for _ in args) + ")"
            return bool(conn.execute(query + " LIMIT 1", args).fetchone())

    def publication_revision(self) -> str:
        with self.db.connect() as conn:
            values = [
                # Authority and tool tombstones never reset or disappear. Their
                # count and revision sum detect changes without shipping every
                # row, including provider deletion/recreation with the same ID.
                _revision_totals(
                    conn,
                    "SELECT COUNT(*),COALESCE(SUM(revision),0) FROM group_expert_authorizations WHERE domain_key LIKE 'provider:%'",
                ),
                _revision_totals(
                    conn,
                    "SELECT COUNT(*),COALESCE(SUM(revision),0) FROM group_expert_tool_versions",
                ),
                # The existing source-head trigger increments dirty_revision
                # for agent identity/configuration changes, even before the
                # agent has a group projection. Only one source owner exists.
                [
                    dict(row)
                    for row in conn.execute(
                        "SELECT s.source_user_id,s.revision,u.disabled,u.role,h.generation,h.dirty_revision,v.revision AS owner_revision "
                        "FROM expert_assignment_source s JOIN users u ON u.id=s.source_user_id "
                        "LEFT JOIN resource_source_heads h ON h.user_id=s.source_user_id "
                        "LEFT JOIN group_expert_authorizations v ON v.domain_key='user:'||s.source_user_id"
                    ).fetchall()
                ],
            ]
        return digest(values)

    def refresh_revision(self) -> str:
        with self.db.connect() as conn:
            values = [
                _revision_totals(
                    conn,
                    "SELECT COUNT(*),COALESCE(SUM(revision),0) FROM group_expert_authorizations",
                ),
                _revision_totals(
                    conn,
                    "SELECT COUNT(*),COALESCE(SUM(revision),0) FROM group_expert_scopes",
                ),
                _revision_totals(
                    conn,
                    "SELECT COUNT(*),COALESCE(SUM(revision),0) FROM department_resource_pools",
                ),
            ]
        return digest(values)

    def prepare_invitation(
        self,
        *,
        actor: int,
        request_id: str,
        conversation_id: str,
        department_id: str | None,
        source: str,
        source_row: int,
        revision: int,
        organization: bool,
    ) -> dict[str, Any]:
        from octop.infra.db.repos.group_expert_sources import available_sources, refresh_scopes

        request = wire(
            {
                "conversation_id": conversation_id,
                "source_agent_id": source,
                "source_row_id": source_row,
                "revision": revision,
                "organization": organization,
            }
        )
        with self.execution._transaction() as (conn, now):
            old = conn.execute(
                "SELECT * FROM group_expert_management_receipts WHERE actor_id=? AND request_id=?",
                (actor, request_id),
            ).fetchone()
            if old:
                if old["request_json"] != request:
                    raise ExecutionConflict("group_expert_identity_conflict")
                return dict(json.loads(old["result_json"]))
            self.require_capabilities(conn, now)
            scope = conn.execute(
                "SELECT * FROM group_expert_scopes WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            if (scope["revision"] if scope else 0) != revision:
                raise ExecutionConflict("group_expert_scope_changed")
            identities = json.loads(scope["sources_json"]) if scope else []
            identity = {"source_agent_id": source, "source_row_id": source_row}
            if identity not in identities:
                if not organization:
                    raise ExecutionConflict("group_expert_outside_scope")
                if not any(
                    v["source_agent_id"] == source and v["source_row_id"] == source_row
                    for v in available_sources(conn)
                ):
                    raise ExecutionConflict("group_expert_configuration_changed")
                if department_id is not None:
                    pool = conn.execute(
                        "SELECT expert_ids FROM department_resource_pools WHERE scope_id=?",
                        (department_id,),
                    ).fetchone()
                    if not pool or source not in json.loads(pool[0]):
                        raise ExecutionConflict("group_expert_outside_scope")
                identities.append(identity)
                conn.execute(
                    "INSERT INTO group_expert_scopes(conversation_id,department_id,sources_json) VALUES(?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET sources_json=excluded.sources_json,department_id=excluded.department_id",
                    (conversation_id, department_id, wire(identities)),
                )
                refresh_scopes(conn, now)
                revision = conn.execute(
                    "SELECT revision FROM group_expert_scopes WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()[0]
            value = {"source_agent_id": source, "source_row_id": source_row, "revision": revision}
            conn.execute(
                "INSERT INTO group_expert_management_receipts VALUES(?,?,?,?)",
                (actor, request_id, request, wire(value)),
            )
            return value

    def available_sources(self) -> list[dict[str, Any]]:
        from octop.infra.db.repos.group_expert_sources import available_sources

        with self.db.connect() as conn:
            return available_sources(conn)

    def scope(self, conversation_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM group_expert_scopes WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            if not row:
                return None
            pending = conn.execute(
                "SELECT 1 FROM group_expert_outbox WHERE target_id=? AND kind='scope' AND acknowledged_at IS NULL LIMIT 1",
                (conversation_id,),
            ).fetchone()
            return {**dict(row), "syncing": bool(pending)}

    def save_scope(
        self,
        *,
        actor: int,
        request_id: str,
        conversation_id: str,
        department_id: str | None,
        sources: list[dict[str, Any]],
        expected_revision: int,
    ) -> dict[str, Any]:
        from octop.infra.db.repos.group_expert_sources import available_sources, refresh_scopes

        if not request_id or len(request_id) > 128 or len(sources) > 500:
            raise ExecutionConflict("group_expert_identity_invalid")
        identities = sorted(sources, key=lambda v: (v["source_agent_id"], v["source_row_id"]))
        encoded = wire(
            {
                "conversation_id": conversation_id,
                "department_id": department_id,
                "sources": identities,
                "expected_revision": expected_revision,
            }
        )
        with self.execution._transaction() as (conn, now):
            old = conn.execute(
                "SELECT * FROM group_expert_management_receipts WHERE actor_id=? AND request_id=?",
                (actor, request_id),
            ).fetchone()
            if old:
                if old["request_json"] != encoded:
                    raise ExecutionConflict("group_expert_identity_conflict")
                return dict(json.loads(old["result_json"]))
            self.require_capabilities(conn, now)
            scope = conn.execute(
                "SELECT revision FROM group_expert_scopes WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            if (scope[0] if scope else 0) != expected_revision:
                raise ExecutionConflict("group_expert_scope_changed")
            allowed = {(v["source_agent_id"], v["source_row_id"]) for v in available_sources(conn)}
            if len({(v["source_agent_id"], v["source_row_id"]) for v in identities}) != len(
                identities
            ) or any(
                set(v) != {"source_agent_id", "source_row_id"}
                or (v["source_agent_id"], v["source_row_id"]) not in allowed
                for v in identities
            ):
                raise ExecutionConflict("group_expert_outside_scope")
            if department_id is not None:
                pool = conn.execute(
                    "SELECT expert_ids FROM department_resource_pools WHERE scope_id=?",
                    (department_id,),
                ).fetchone()
                ids = json.loads(pool[0]) if pool else []
                if any(v["source_agent_id"] not in ids for v in identities):
                    raise ExecutionConflict("group_expert_outside_scope")
            conn.execute(
                "INSERT INTO group_expert_scopes(conversation_id,department_id,sources_json) VALUES(?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET department_id=excluded.department_id,sources_json=excluded.sources_json",
                (conversation_id, department_id, wire(identities)),
            )
            refresh_scopes(conn, now)
            result = dict(
                conn.execute(
                    "SELECT conversation_id,revision,sources_json FROM group_expert_scopes WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()
            )
            conn.execute(
                "INSERT INTO group_expert_management_receipts VALUES(?,?,?,?)",
                (actor, request_id, encoded, wire(result)),
            )
            return result

    def bootstrap_authorizations(self) -> None:
        with self.execution._transaction() as (conn, now):
            for row in conn.execute(
                "SELECT a.* FROM group_expert_authorizations a WHERE NOT EXISTS "
                "(SELECT 1 FROM group_expert_outbox o WHERE o.operation_id='invalidate:'||a.domain_key||':'||CAST(a.revision AS TEXT)) ORDER BY a.domain_key"
            ).fetchall():
                operation = f"invalidate:{row['domain_key']}:{row['revision']}"
                if not conn.execute(
                    "SELECT 1 FROM group_expert_outbox WHERE operation_id=?", (operation,)
                ).fetchone():
                    enqueue(
                        conn,
                        now,
                        operation=operation,
                        kind="invalidate",
                        target=row["domain_key"],
                        payload=dict(row),
                    )

    def refresh(self) -> None:
        from octop.infra.db.repos.group_expert_sources import refresh_scopes

        with self.execution._transaction() as (conn, now):
            refresh_scopes(conn, now)
            rows = conn.execute(
                "SELECT g.* FROM group_expert_calls g JOIN execution_runs r ON r.run_id=g.run_id WHERE r.state IN ('queued','running') ORDER BY g.created_at"
            ).fetchall()
            for call in rows:
                try:
                    check_authorization(conn, dict(call), json.loads(call["snapshot_json"]))
                except ExecutionConflict:
                    cancel_call(conn, now, call["call_id"], self.execution)

    def refresh_publication(self, objects: list[dict[str, Any]], *, source_manifest: str) -> None:
        from octop.infra.db.repos.group_expert_sources import publish_objects, refresh_scopes

        with self.execution._transaction() as (conn, now):
            publish_objects(conn, now, objects, source_manifest=source_manifest)
            refresh_scopes(conn, now)

    def versions(
        self, *, user: int, conversation_id: str, source: str, source_row: int
    ) -> dict[str, int]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT snapshot_json FROM group_expert_source_versions WHERE source_agent_id=? AND source_row_id=? AND invalid_reason=''",
                (source, source_row),
            ).fetchone()
            if not row:
                raise ExecutionConflict("group_expert_configuration_changed")
            snapshot = json.loads(row[0])
            domains = [
                "user:" + str(user),
                "scope:" + conversation_id,
                f"source:{source}:{source_row}",
                "provider:" + snapshot["provider_generation"],
            ]
            values = {}
            for domain in domains:
                version = conn.execute(
                    "SELECT revision FROM group_expert_authorizations WHERE domain_key=?", (domain,)
                ).fetchone()
                if not version:
                    raise ExecutionConflict("group_expert_authorization_syncing")
                values[domain] = version[0]
            return values

    def can_call(
        self,
        *,
        user: int,
        conversation_id: str,
        source: str,
        source_row: int,
        binding_scope_revision: int | None = None,
    ) -> bool:
        try:
            versions = self.versions(
                user=user, conversation_id=conversation_id, source=source, source_row=source_row
            )
            with self.db.connect() as conn:
                snapshot = json.loads(
                    conn.execute(
                        "SELECT snapshot_json FROM group_expert_source_versions WHERE source_agent_id=? AND source_row_id=?",
                        (source, source_row),
                    ).fetchone()[0]
                )
                check_authorization(
                    conn,
                    {
                        "user_id": user,
                        "conversation_id": conversation_id,
                        "source_agent_id": source,
                        "source_row_id": source_row,
                        "authorization_versions": versions,
                        **(
                            {"binding_scope_revision": binding_scope_revision}
                            if binding_scope_revision is not None
                            else {}
                        ),
                    },
                    snapshot,
                )
            return True
        except ExecutionConflict:
            return False

    def execution_input(self, call_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            call = conn.execute(
                "SELECT * FROM group_expert_calls WHERE call_id=? AND admission_state='accepted'",
                (call_id,),
            ).fetchone()
            if not call:
                raise ExecutionConflict("group_expert_not_authorized")
            snapshot = json.loads(call["snapshot_json"])
            check_authorization(conn, dict(call), snapshot)
            return {"identity": json.loads(call["request_json"]), "snapshot": snapshot}

    def delivery(self, call_id: str) -> dict[str, Any]:
        """Service-only delivery, including a sealed unpublished conclusion."""
        with self.db.connect() as conn:
            call = conn.execute(
                "SELECT * FROM group_expert_calls WHERE call_id=?", (call_id,)
            ).fetchone()
            if not call or call["result_json"] is None:
                raise ExecutionConflict("group_expert_result_required")
            allowed = True
            try:
                check_authorization(conn, dict(call), json.loads(call["snapshot_json"]))
            except ExecutionConflict as exc:
                allowed = False
                reason = str(exc)
            result_wire = call["result_json"]
            if not allowed:
                result = json.loads(result_wire)
                result["result"] = None
                result["reason_code"] = reason
                result_wire = wire(result)
            return {
                "result_wire": result_wire,
                "result_digest": call["result_digest"],
                "unpublished": not allowed,
            }

    def publish(
        self, snapshot: dict[str, Any], *, source_manifest: str, display: dict[str, Any]
    ) -> dict[str, Any]:
        from octop.infra.db.repos.group_expert_sources import publish_objects

        with self.execution._transaction() as (conn, now):
            self.require_capabilities(conn, now)
            publish_objects(
                conn,
                now,
                [
                    {
                        "source_agent_id": snapshot["source_agent_id"],
                        "source_row_id": snapshot["source_row_id"],
                        "snapshot": snapshot,
                        "display": display,
                    }
                ],
                source_manifest=source_manifest,
            )
            return dict(
                conn.execute(
                    "SELECT revision,snapshot_digest FROM group_expert_source_versions WHERE source_agent_id=? AND source_row_id=?",
                    (snapshot["source_agent_id"], snapshot["source_row_id"]),
                ).fetchone()
            )

    def get(self, call_id: str) -> dict[str, Any] | None:
        """Internal metadata only; content delivery needs fresh Go authorization."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT g.call_id,g.admission_state,g.reason_code,g.run_id,r.state AS execution_state,g.delivery_state,g.result_digest "
                "FROM group_expert_calls g LEFT JOIN execution_runs r ON r.run_id=g.run_id WHERE g.call_id=?",
                (call_id,),
            ).fetchone()
            if row:
                return dict(row)
            cancelled = conn.execute(
                "SELECT 1 FROM group_expert_controls WHERE call_id=? AND cancelled=1", (call_id,)
            ).fetchone()
            if cancelled:
                return {
                    "call_id": call_id,
                    "admission_state": "cancelled",
                    "reason_code": "group_expert_cancelled",
                    "run_id": None,
                    "execution_state": None,
                    "delivery_state": "none",
                    "result_digest": None,
                }
            return None

    def admit(self, context: dict[str, Any], *, refusal: str = "") -> dict[str, Any]:
        if set(context) != set(IDENTITY_FIELDS):
            raise ExecutionConflict("group_expert_identity_invalid")
        for key in (
            "user_id",
            "source_row_id",
            "binding_generation",
            "binding_scope_revision",
            "max_text_chars",
        ):
            if type(context[key]) is not int or context[key] < 1:
                raise ExecutionConflict("group_expert_identity_invalid")
        for key in ("call_id", "conversation_id", "message_id", "binding_id", "source_agent_id"):
            if not isinstance(context[key], str) or not 1 <= len(context[key]) <= 160:
                raise ExecutionConflict("group_expert_identity_invalid")
        if (
            not isinstance(context["authorization_versions"], dict)
            or len(context["authorization_versions"]) > 16
        ):
            raise ExecutionConflict("group_expert_identity_invalid")
        encoded, fingerprint = wire(context), digest(context)
        call_id = context["call_id"]
        with self.execution._transaction() as (conn, now):
            old = conn.execute(
                "SELECT * FROM group_expert_calls WHERE call_id=?", (call_id,)
            ).fetchone()
            if old:
                original = json.loads(old["request_json"])
                if original != {**context, "max_text_chars": original["max_text_chars"]}:
                    raise ExecutionConflict("group_expert_identity_conflict")
                return {
                    "call_id": call_id,
                    "admission_state": old["admission_state"],
                    "run_id": old["run_id"],
                    # The accepted admission receipt predates execution. Its
                    # reason stays empty even after a later failure/cancellation.
                    "reason_code": ""
                    if old["admission_state"] == "accepted"
                    else old["reason_code"],
                }
            conn.execute(
                "INSERT INTO group_expert_controls(call_id) VALUES(?) ON CONFLICT(call_id) DO NOTHING",
                (call_id,),
            )
            control = conn.execute(
                "SELECT * FROM group_expert_controls WHERE call_id=?", (call_id,)
            ).fetchone()
            source = conn.execute(
                "SELECT * FROM group_expert_source_versions WHERE source_agent_id=? AND source_row_id=?",
                (context["source_agent_id"], context["source_row_id"]),
            ).fetchone()
            state, reason, run, snapshot = "accepted", "", None, None
            if control["cancelled"]:
                state, reason = "cancelled", "group_expert_cancelled"
            else:
                try:
                    self.require_capabilities(conn, now)
                    require_unknown_cooldown_expired(conn, now, context["user_id"])
                    if refusal:
                        raise ExecutionConflict(refusal)
                    if not source or source["invalid_reason"]:
                        raise ExecutionConflict(
                            source["invalid_reason"]
                            if source
                            else "group_expert_configuration_changed"
                        )
                    snapshot = json.loads(source["snapshot_json"])
                    check_authorization(conn, context, snapshot)
                    run = self.execution._enqueue(
                        conn,
                        now,
                        user=context["user_id"],
                        agent=context["source_agent_id"],
                        thread="group-expert:" + call_id,
                        operation="group_expert",
                        key=call_id,
                        encoded=wire({"call_id": call_id}),
                    )
                except ExecutionConflict as exc:
                    state, reason = "rejected", str(exc)
            conn.execute(
                "INSERT INTO group_expert_calls(call_id,user_id,conversation_id,message_id,binding_id,binding_generation,source_agent_id,source_row_id,request_json,request_digest,admission_state,reason_code,run_id,snapshot_json,snapshot_digest,authorization_versions,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    call_id,
                    context["user_id"],
                    context["conversation_id"],
                    context["message_id"],
                    context["binding_id"],
                    context["binding_generation"],
                    context["source_agent_id"],
                    context["source_row_id"],
                    encoded,
                    fingerprint,
                    state,
                    reason,
                    run["run_id"] if run else None,
                    wire(snapshot) if run else None,
                    source["snapshot_digest"] if run else None,
                    wire(context["authorization_versions"]),
                    now,
                ),
            )
            if run:
                for domain, revision in context["authorization_versions"].items():
                    conn.execute(
                        "INSERT INTO group_expert_call_authorizations VALUES(?,?,?)",
                        (call_id, domain, revision),
                    )
            return {
                "call_id": call_id,
                "admission_state": state,
                "run_id": run["run_id"] if run else None,
                "reason_code": reason,
            }

    def control(self, call_id: str, *, sequence: int, kind: str) -> dict[str, Any]:
        if (
            not call_id
            or len(call_id) > 160
            or type(sequence) is not int
            or sequence < 1
            or kind not in {"dispatch", "cancel"}
        ):
            raise ExecutionConflict("group_expert_control_invalid")
        encoded = wire({"kind": kind, "call_id": call_id, "control_seq": sequence})
        with self.execution._transaction() as (conn, now):
            old = conn.execute(
                "SELECT request_json FROM group_expert_control_receipts WHERE call_id=? AND control_seq=?",
                (call_id, sequence),
            ).fetchone()
            if old and old[0] != encoded:
                raise ExecutionConflict("group_expert_control_conflict")
            if not old:
                conn.execute(
                    "INSERT INTO group_expert_control_receipts VALUES(?,?,?)",
                    (call_id, sequence, encoded),
                )
                conn.execute(
                    "INSERT INTO group_expert_controls(call_id) VALUES(?) ON CONFLICT(call_id) DO NOTHING",
                    (call_id,),
                )
                if kind == "cancel":
                    cancel_call(conn, now, call_id, self.execution)
                else:
                    conn.execute(
                        "UPDATE group_expert_controls SET dispatch_seq=? WHERE call_id=? AND control_seq<? AND cancelled=0",
                        (sequence, call_id, sequence),
                    )
                conn.execute(
                    "UPDATE group_expert_controls SET control_seq=CASE WHEN control_seq<? THEN ? ELSE control_seq END WHERE call_id=?",
                    (sequence, sequence, call_id),
                )
            execution = conn.execute(
                "SELECT r.state FROM execution_runs r JOIN group_expert_calls g ON g.run_id=r.run_id WHERE g.call_id=?",
                (call_id,),
            ).fetchone()
            state = execution[0] if execution else None
            return {
                "call_id": call_id,
                "saved": True,
                "execution_state": state,
                "stopped": state in TERMINAL or (kind == "cancel" and execution is None),
            }

    def acknowledge(
        self, operation_id: str, *, request_digest: str, receipt: dict[str, Any]
    ) -> None:
        with self.execution._transaction() as (conn, now):
            row = conn.execute(
                "SELECT * FROM group_expert_outbox WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if not row or row["request_digest"] != request_digest:
                raise ExecutionConflict("group_expert_receipt_conflict")
            encoded = wire(receipt)
            if row["acknowledged_at"] is not None:
                if row["receipt"] != encoded:
                    raise ExecutionConflict("group_expert_receipt_conflict")
                return
            if row["kind"] == "result":
                payload = json.loads(row["payload"])
                if (
                    receipt.get("call_id") != payload["call_id"]
                    or receipt.get("result_digest") != payload["result_digest"]
                    or receipt.get("delivery_state") not in {"confirmed", "unpublished"}
                ):
                    raise ExecutionConflict("group_expert_receipt_conflict")
                conn.execute(
                    "UPDATE group_expert_calls SET delivery_state=?,delivery_receipt=? WHERE call_id=? AND result_digest=?",
                    (
                        receipt["delivery_state"],
                        encoded,
                        payload["call_id"],
                        payload["result_digest"],
                    ),
                )
            conn.execute(
                "UPDATE group_expert_outbox SET acknowledged_at=?,receipt=? WHERE operation_id=?",
                (now, encoded, operation_id),
            )

    def retry(self, operation_id: str, *, request_digest: str) -> None:
        """Back off delivery attempts without deleting the business obligation."""
        with self.execution._transaction() as (conn, now):
            row = conn.execute(
                "SELECT * FROM group_expert_outbox WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if not row or row["request_digest"] != request_digest:
                raise ExecutionConflict("group_expert_receipt_conflict")
            if row["acknowledged_at"] is not None:
                return
            attempts = row["attempts"] + 1
            conn.execute(
                "UPDATE group_expert_outbox SET attempts=?,next_attempt_at=? WHERE operation_id=?",
                (attempts, now + min(3600, 2 ** min(attempts, 12)), operation_id),
            )

    def pending(
        self,
        *,
        limit: int = 100,
        conversation_id: str | None = None,
        source: str | None = None,
        source_row: int | None = None,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("invalid_outbox_limit")
        with self.db.connect() as conn:
            now = self.execution._now(conn)
            condition = ""
            args: list[Any] = [now]
            if conversation_id is not None:
                targets = self._sync_targets(conn, conversation_id, source, source_row)
                condition = (
                    " AND kind IN ('scope','invalidate') AND target_id IN ("
                    + ",".join("?" for _ in targets)
                    + ")"
                )
                args.extend(targets)
            args.append(limit)
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM group_expert_outbox WHERE acknowledged_at IS NULL AND next_attempt_at<=?"
                    + condition
                    + " ORDER BY created_at,operation_id LIMIT ?",
                    args,
                ).fetchall()
            ]
