"""Employee project metadata and atomic, immutable conversation bindings."""

from __future__ import annotations

import hmac
import json
import time
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos.company_runtime import RuntimeConflict, encode
from octop.infra.db.repos.threads import clip_thread_title


class CompanyProjectRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    @staticmethod
    def _owned(conn: Any, project_id: str, owner: int, installation: str) -> Any:
        row = conn.execute(
            "SELECT * FROM company_projects WHERE project_id=? AND owner_user_id=?",
            (project_id, owner),
        ).fetchone()
        if not row or not hmac.compare_digest(row["installation_hash"], installation):
            raise RuntimeConflict("project_not_owned")
        return row

    def register(self, project_id: str, owner: int, installation: str, name: str) -> dict[str, Any]:
        now = int(time.time())
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO company_projects(project_id,owner_user_id,installation_hash,name,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(project_id) DO NOTHING",
                (project_id, owner, installation, name, now, now),
            )
            # A retry preserves a later rename and can never take over another installation.
            return dict(self._owned(conn, project_id, owner, installation))

    def rename(self, project_id: str, owner: int, installation: str, name: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            self._owned(conn, project_id, owner, installation)
            conn.execute(
                "UPDATE company_projects SET name=?,updated_at=? WHERE project_id=?",
                (name, int(time.time()), project_id),
            )
            return dict(self._owned(conn, project_id, owner, installation))

    def list_projects(
        self, owner: int, installation: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM company_projects WHERE owner_user_id=? AND installation_hash=? "
                    "ORDER BY updated_at DESC,project_id DESC LIMIT ? OFFSET ?",
                    (owner, installation, limit, offset),
                ).fetchall()
            ]

    def create_thread(
        self,
        project_id: str,
        owner: int,
        installation: str,
        *,
        thread_id: str,
        agent_id: str,
        device_id: str,
        title: str,
        fork: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        now = int(time.time())
        key = f"dashboard:{agent_id}:{owner}"
        with self.db.transaction() as conn:
            if fork:
                conn.execute("UPDATE execution_control SET id=id WHERE id=1")
                current = conn.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM thread_messages WHERE thread_id=?",
                    (fork["source"],),
                ).fetchone()[0]
                source = conn.execute(
                    "SELECT t.agent_id,p.project_id FROM threads t JOIN company_project_threads p ON p.thread_id=t.thread_id WHERE t.thread_id=? AND t.user_id=?",
                    (fork["source"], owner),
                ).fetchone()
                if (
                    not source
                    or source["agent_id"] != agent_id
                    or source["project_id"] != project_id
                    or current != fork["head"]
                ):
                    raise RuntimeConflict("edit_history_changed")
                if conn.execute(
                    "SELECT 1 FROM company_runtime_tasks WHERE thread_id=? AND status IN ('running','cancel_requested','unknown')",
                    (fork["source"],),
                ).fetchone():
                    raise RuntimeConflict("thread_already_running")
            self._owned(conn, project_id, owner, installation)
            expert = conn.execute(
                "SELECT user_id,enabled,kind FROM agents WHERE agent_id=?", (agent_id,)
            ).fetchone()
            if (
                not expert
                or expert["user_id"] != owner
                or not expert["enabled"]
                or expert["kind"] != "expert"
            ):
                raise RuntimeConflict("expert_not_owned")
            created = conn.execute(
                "INSERT INTO threads(thread_id,agent_id,user_id,channel_type,session_key,title,last_active,created_at) "
                "VALUES(?,?,?,'dashboard',?,?,0,?) ON CONFLICT(thread_id) DO NOTHING RETURNING thread_id",
                (thread_id, agent_id, owner, key, clip_thread_title(title) or None, now),
            ).fetchone()
            if created is None:
                old = conn.execute(
                    "SELECT t.agent_id,t.user_id,p.project_id FROM threads t "
                    "LEFT JOIN company_project_threads p ON p.thread_id=t.thread_id WHERE t.thread_id=?",
                    (thread_id,),
                ).fetchone()
                if not old or (old["agent_id"], old["user_id"], old["project_id"]) != (
                    agent_id,
                    owner,
                    project_id,
                ):
                    raise RuntimeConflict("thread_project_conflict")
            else:
                conn.execute(
                    "INSERT INTO company_project_threads(thread_id,project_id) VALUES(?,?)",
                    (thread_id, project_id),
                )
                # Reserve desktop ownership even before the first message. Ordinary
                # server execution must never run this conversation in a server folder.
                conn.execute(
                    "INSERT INTO company_runtime_threads(thread_id,owner_user_id,agent_id,device_id,project_name,installation_hash,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (thread_id, owner, agent_id, device_id, thread_id, installation, now),
                )
                conn.execute(
                    "INSERT INTO thread_history_projection(thread_id,status,updated_at,error) VALUES(?,'ready',?,NULL)",
                    (thread_id, now),
                )
                if fork:
                    for index, message in enumerate(fork["messages"], 1):
                        conn.execute(
                            "INSERT INTO thread_messages(thread_id,seq,message_id,role,message_json,created_at) VALUES(?,?,?,?,?,?)",
                            (
                                thread_id,
                                index,
                                message["data"]["id"],
                                message["type"],
                                encode(message),
                                now,
                            ),
                        )
                    checkpoint = fork["checkpoint"]
                    conn.execute(
                        "INSERT INTO company_runtime_checkpoints(thread_id,namespace,checkpoint_id,payload) VALUES(?,'',?,?)",
                        (
                            thread_id,
                            checkpoint["config"]["configurable"]["checkpoint_id"],
                            encode(checkpoint),
                        ),
                    )
                conn.execute(
                    "UPDATE company_projects SET updated_at=? WHERE project_id=?", (now, project_id)
                )
            return {
                "thread_id": thread_id,
                "agent_id": agent_id,
                "project_id": project_id,
                "session_key": key,
            }

    def fork_context(
        self, project_id: str, owner: int, installation: str, thread_id: str
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            self._owned(conn, project_id, owner, installation)
            source = conn.execute(
                "SELECT t.agent_id,t.title FROM threads t JOIN company_project_threads p ON p.thread_id=t.thread_id WHERE t.thread_id=? AND t.user_id=? AND p.project_id=?",
                (thread_id, owner, project_id),
            ).fetchone()
            if not source:
                raise RuntimeConflict("thread_not_owned")
            rows = conn.execute(
                "SELECT seq,message_json FROM thread_messages WHERE thread_id=? ORDER BY seq LIMIT 100001",
                (thread_id,),
            ).fetchall()
            if len(rows) > 100000:
                raise RuntimeConflict("project_history_too_large")
            return {
                "agent_id": source["agent_id"],
                "title": source["title"],
                "head": rows[-1]["seq"] if rows else 0,
                "messages": [json.loads(row["message_json"]) for row in rows],
            }

    def adopt(self, project_id: str, owner: int, installation: str, thread_id: str) -> None:
        """Attach metadata to an existing desktop thread without touching files/history."""
        with self.db.transaction() as conn:
            conn.execute("UPDATE execution_control SET id=id WHERE id=1")
            self._owned(conn, project_id, owner, installation)
            binding = conn.execute(
                "SELECT owner_user_id,installation_hash FROM company_runtime_threads WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
            if (
                not binding
                and not conn.execute(
                    "SELECT 1 FROM threads WHERE thread_id=?", (thread_id,)
                ).fetchone()
            ):
                return
            if (
                not binding
                or binding["owner_user_id"] != owner
                or not hmac.compare_digest(binding["installation_hash"], installation)
            ):
                raise RuntimeConflict("project_on_another_device")
            conn.execute(
                "INSERT INTO company_project_threads(thread_id,project_id) VALUES(?,?) ON CONFLICT(thread_id) DO NOTHING",
                (thread_id, project_id),
            )
            current = conn.execute(
                "SELECT project_id FROM company_project_threads WHERE thread_id=?", (thread_id,)
            ).fetchone()
            if current["project_id"] != project_id:
                raise RuntimeConflict("thread_project_conflict")

    def threads(
        self, project_id: str, owner: int, installation: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            self._owned(conn, project_id, owner, installation)
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT t.thread_id,t.agent_id,t.title,t.last_active,t.created_at,t.pinned,p.project_id, "
                    "(SELECT r.status FROM company_runtime_tasks r WHERE r.thread_id=t.thread_id ORDER BY r.id DESC LIMIT 1) AS task_status "
                    "FROM company_project_threads p JOIN threads t ON t.thread_id=p.thread_id "
                    "WHERE p.project_id=? AND t.user_id=? ORDER BY t.pinned DESC, "
                    "CASE WHEN t.last_active>0 THEN t.last_active ELSE t.created_at END DESC,t.thread_id DESC LIMIT ? OFFSET ?",
                    (project_id, owner, limit, offset),
                ).fetchall()
            ]
