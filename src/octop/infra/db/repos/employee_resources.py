"""Atomic department limits, employee selections and organization reconciliation."""

from __future__ import annotations

import json
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.expert_assignments import AssignmentConflict, ExpertAssignmentRepo

UNASSIGNED = "unassigned"


class EmployeeResourceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    def skills(self, user_id: int) -> list[str]:
        with self.db.connect() as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT resource_id FROM employee_skill_assignments WHERE user_id=? ORDER BY resource_id",
                    (user_id,),
                ).fetchall()
            ]

    def directory(self) -> list[dict[str, Any]]:
        """Read saved selections in three queries, without reconciliation or workspace I/O."""
        with self.db.connect() as conn:
            employees = {
                row["id"]: dict(row)
                for row in conn.execute(
                    "SELECT u.id,u.username,u.display_name,u.disabled,"
                    "u.expert_access_mode AS mode,u.expert_assignment_revision AS revision,"
                    "s.scope_id,s.membership_revision,s.pending_scope "
                    "FROM users u LEFT JOIN employee_resource_scopes s ON s.user_id=u.id "
                    "WHERE u.role<>'admin' ORDER BY u.username,u.id"
                ).fetchall()
            }
            for row in employees.values():
                row.update(expert_ids=[], skill_ids=[])
            for table, column, key in (
                ("expert_assignments", "source_agent_id", "expert_ids"),
                ("employee_skill_assignments", "resource_id", "skill_ids"),
            ):
                for row in conn.execute(
                    f"SELECT a.user_id,a.{column} FROM {table} a "
                    "JOIN users u ON u.id=a.user_id WHERE u.role<>'admin' "
                    f"ORDER BY a.user_id,a.{column}"
                ).fetchall():
                    uid, resource = row[0], row[1]
                    if uid in employees:
                        employees[uid][key].append(resource)
            return list(employees.values())

    def defaults(self) -> list[str]:
        with self.db.connect() as conn:
            return list(
                json.loads(
                    conn.execute(
                        "SELECT default_skill_ids FROM expert_assignment_source WHERE id=1"
                    ).fetchone()[0]
                )
            )

    def save_defaults(self, skill_ids: list[str], revision: int, actor: str) -> None:
        with self.db.transaction() as conn:
            self._lock(conn, revision)
            conn.execute(
                "UPDATE expert_assignment_source SET default_skill_ids=?,revision=revision+1 WHERE id=1",
                (json.dumps(skill_ids),),
            )
            conn.execute(
                "UPDATE company_runtime_tasks SET status='cancel_requested',updated_at=? WHERE status='running' "
                "AND thread_id IN (SELECT t.thread_id FROM company_runtime_threads t JOIN assigned_expert_instances i ON i.instance_agent_id=t.agent_id AND i.user_id=t.owner_user_id)",
                (now_ts(),),
            )
            ExpertAssignmentRepo._audit(
                conn, actor, "employee_resources.defaults", "1", {"skill_ids": skill_ids}
            )

    def scope(self, user_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM employee_resource_scopes WHERE user_id=?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _pool(conn: Any, scope_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM department_resource_pools WHERE scope_id=?", (scope_id,)
        ).fetchone()
        return {
            "scope_id": scope_id,
            "revision": int(row["revision"]) if row else 0,
            "expert_ids": json.loads(row["expert_ids"]) if row else [],
            "skill_ids": json.loads(row["skill_ids"]) if row else [],
        }

    def pool(self, scope_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            return self._pool(conn, scope_id)

    def pools(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return [
                {
                    "scope_id": r["scope_id"],
                    "revision": int(r["revision"]),
                    "expert_ids": json.loads(r["expert_ids"]),
                    "skill_ids": json.loads(r["skill_ids"]),
                }
                for r in conn.execute(
                    "SELECT * FROM department_resource_pools ORDER BY scope_id"
                ).fetchall()
            ]

    @staticmethod
    def _lock(conn: Any, revision: int | None = None) -> None:
        sql = "UPDATE expert_assignment_source SET revision=revision WHERE id=1"
        if revision is not None:
            sql += " AND revision=?"
        changed = conn.execute(sql, () if revision is None else (revision,)).rowcount
        if not changed:
            raise AssignmentConflict("policy_revision_conflict")

    @staticmethod
    def _changed(conn: Any, user_id: int) -> None:
        conn.execute(
            "UPDATE users SET expert_assignment_revision=expert_assignment_revision+1 WHERE id=?",
            (user_id,),
        )
        conn.execute(
            "UPDATE company_runtime_tasks SET status='cancel_requested',updated_at=? WHERE status='running' "
            "AND thread_id IN (SELECT thread_id FROM company_runtime_threads WHERE owner_user_id=?)",
            (now_ts(), user_id),
        )

    @staticmethod
    def _replace(conn: Any, user_id: int, experts: list[str], skills: list[str]) -> None:
        conn.execute("DELETE FROM expert_assignments WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM employee_skill_assignments WHERE user_id=?", (user_id,))
        for item in experts:
            conn.execute(
                "INSERT INTO expert_assignments(user_id,source_agent_id) VALUES (?,?)",
                (user_id, item),
            )
        for item in skills:
            conn.execute(
                "INSERT INTO employee_skill_assignments(user_id,resource_id) VALUES (?,?)",
                (user_id, item),
            )

    @staticmethod
    def _selection(conn: Any, user_id: int) -> tuple[list[str], list[str]]:
        return (
            [
                r[0]
                for r in conn.execute(
                    "SELECT source_agent_id FROM expert_assignments WHERE user_id=?", (user_id,)
                ).fetchall()
            ],
            [
                r[0]
                for r in conn.execute(
                    "SELECT resource_id FROM employee_skill_assignments WHERE user_id=?", (user_id,)
                ).fetchall()
            ],
        )

    def reconcile(
        self, user_id: int, scope_id: str, membership_revision: int, enabled: bool
    ) -> bool:
        """Only a prepared, acknowledged single move can retain intersecting selections.

        Unknown/out-of-band moves clear selections; A → B → A can never resurrect them.
        """
        with self.db.transaction() as conn:
            self._lock(conn)
            row = conn.execute(
                "SELECT * FROM employee_resource_scopes WHERE user_id=?", (user_id,)
            ).fetchone()
            if row and membership_revision < row["membership_revision"]:
                raise AssignmentConflict("organization_revision_conflict")
            experts, skills = self._selection(conn, user_id)
            next_experts, next_skills = experts, skills
            changed_membership = row and (
                row["scope_id"] != scope_id or row["membership_revision"] != membership_revision
            )
            if changed_membership or (row and row["pending_scope"] is not None):
                if (
                    row["pending_scope"] == scope_id
                    and membership_revision == row["membership_revision"] + 1
                ):
                    prepared = json.loads(row["pending_json"] or "{}")
                    next_experts = [x for x in experts if x in prepared.get("expert_ids", [])]
                    next_skills = [x for x in skills if x in prepared.get("skill_ids", [])]
                elif (
                    row["pending_scope"] is not None
                    and membership_revision == row["membership_revision"]
                    and row["scope_id"] == scope_id
                ):
                    # The upstream mutation may still commit after a lost response. Stay blocked.
                    raise AssignmentConflict("organization_change_pending")
                else:
                    next_experts, next_skills = [], []
            pool = self._pool(conn, scope_id)
            # A previously deployed direct assignment remains usable until a department
            # range is explicitly saved. All new editor saves always require that range.
            if pool["revision"] or not enabled:
                next_experts = [x for x in next_experts if enabled and x in pool["expert_ids"]]
                next_skills = [x for x in next_skills if enabled and x in pool["skill_ids"]]
            changed = set(experts) != set(next_experts) or set(skills) != set(next_skills)
            if changed:
                self._replace(conn, user_id, next_experts, next_skills)
            if changed or changed_membership:
                self._changed(conn, user_id)
                ExpertAssignmentRepo._audit(
                    conn,
                    "system",
                    "employee_resources.reconcile",
                    str(user_id),
                    {"scope": scope_id, "membership_revision": membership_revision},
                )
            conn.execute(
                "INSERT INTO employee_resource_scopes(user_id,scope_id,membership_revision) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET scope_id=excluded.scope_id,membership_revision=excluded.membership_revision,pending_scope=NULL,pending_json=NULL",
                (user_id, scope_id, membership_revision),
            )
            return bool(changed or changed_membership)

    def prepare_move(
        self,
        user_id: int,
        target_scope: str,
        membership_revision: int,
        expected_revision: int,
        source_revision: int,
        operation_id: str,
    ) -> None:
        with self.db.transaction() as conn:
            self._lock(conn, source_revision)
            pool = self._pool(conn, target_scope)
            pool.update(operation_id=operation_id, created_at=now_ts())
            if not conn.execute(
                "UPDATE employee_resource_scopes SET pending_scope=?,pending_json=? "
                "WHERE user_id=? AND membership_revision=? AND pending_scope IS NULL "
                "AND EXISTS (SELECT 1 FROM users WHERE id=? AND expert_assignment_revision=?)",
                (
                    target_scope,
                    json.dumps(pool),
                    user_id,
                    membership_revision,
                    user_id,
                    expected_revision,
                ),
            ).rowcount:
                raise AssignmentConflict("organization_revision_conflict")
            self._changed(conn, user_id)

    def cancel_move(self, user_id: int, membership_revision: int) -> None:
        """Only called after an explicit upstream rejection (never a timeout)."""
        with self.db.transaction() as conn:
            self._lock(conn)
            conn.execute(
                "UPDATE employee_resource_scopes SET pending_scope=NULL,pending_json=NULL WHERE user_id=? AND membership_revision=?",
                (user_id, membership_revision),
            )

    def save_pool(
        self,
        scope_id: str,
        experts: list[str],
        skills: list[str],
        revision: int,
        source_revision: int,
        actor: str,
    ) -> list[int]:
        with self.db.transaction() as conn:
            self._lock(conn, source_revision)
            old = self._pool(conn, scope_id)
            if old["revision"] != revision:
                raise AssignmentConflict("policy_revision_conflict")
            conn.execute(
                "INSERT INTO department_resource_pools(scope_id,revision,expert_ids,skill_ids) VALUES (?,?,?,?) "
                "ON CONFLICT(scope_id) DO UPDATE SET revision=excluded.revision,expert_ids=excluded.expert_ids,skill_ids=excluded.skill_ids",
                (scope_id, revision + 1, json.dumps(experts), json.dumps(skills)),
            )
            affected = []
            for row in conn.execute(
                "SELECT user_id FROM employee_resource_scopes WHERE scope_id=?", (scope_id,)
            ).fetchall():
                uid = row[0]
                before_experts, before_skills = self._selection(conn, uid)
                after_experts = [x for x in before_experts if x in experts]
                after_skills = [x for x in before_skills if x in skills]
                if set(before_experts) != set(after_experts) or set(before_skills) != set(
                    after_skills
                ):
                    self._replace(conn, uid, after_experts, after_skills)
                    self._changed(conn, uid)
                    affected.append(uid)
            for pending in conn.execute(
                "SELECT user_id,pending_json FROM employee_resource_scopes WHERE pending_scope=?",
                (scope_id,),
            ).fetchall():
                prepared = json.loads(pending["pending_json"] or "{}")
                prepared["expert_ids"] = [x for x in prepared.get("expert_ids", []) if x in experts]
                prepared["skill_ids"] = [x for x in prepared.get("skill_ids", []) if x in skills]
                conn.execute(
                    "UPDATE employee_resource_scopes SET pending_json=? WHERE user_id=?",
                    (json.dumps(prepared), pending["user_id"]),
                )
            conn.execute("UPDATE expert_assignment_source SET revision=revision+1 WHERE id=1")
            ExpertAssignmentRepo._audit(
                conn,
                actor,
                "department_resources.save",
                scope_id,
                {"before": old, "expert_ids": experts, "skill_ids": skills, "affected": affected},
            )
            return affected

    def save_users(self, employees: list[dict[str, Any]], source_revision: int, actor: str) -> None:
        with self.db.transaction() as conn:
            self._lock(conn, source_revision)
            for item in employees:
                user_id = item["id"]
                user = conn.execute(
                    "SELECT role,disabled,expert_assignment_revision FROM users WHERE id=?",
                    (user_id,),
                ).fetchone()
                scope = conn.execute(
                    "SELECT * FROM employee_resource_scopes WHERE user_id=?", (user_id,)
                ).fetchone()
                if (
                    not user
                    or user["role"] == "admin"
                    or user["expert_assignment_revision"] != item["expected_revision"]
                ):
                    raise AssignmentConflict("policy_revision_conflict")
                if (
                    not scope
                    or scope["pending_scope"] is not None
                    or scope["membership_revision"] != item["membership_revision"]
                    or scope["scope_id"] != item["scope_id"]
                ):
                    raise AssignmentConflict("organization_revision_conflict")
                pool = self._pool(conn, scope["scope_id"])
                experts, skills = item["expert_ids"], item["skill_ids"]
                if user["disabled"] and (experts or skills):
                    raise AssignmentConflict("expert_assignment_employee_required")
                if item["mode"] == "assigned_only" and (
                    not set(experts) <= set(pool["expert_ids"])
                    or not set(skills) <= set(pool["skill_ids"])
                ):
                    raise AssignmentConflict("resource_outside_department")
                if item["mode"] == "legacy" and (experts or skills):
                    raise AssignmentConflict("invalid_expert_assignment")
                # Source validity is rechecked inside the same serialization window.
                valid_experts = {
                    r[0]
                    for r in conn.execute(
                        "SELECT g.agent_id FROM agents g JOIN expert_assignment_source s ON s.source_user_id=g.user_id JOIN users u ON u.id=s.source_user_id WHERE s.id=1 AND u.role='admin' AND u.disabled=0 AND g.enabled=1 AND g.kind='expert'"
                    ).fetchall()
                }
                if not set(experts) <= valid_experts:
                    raise AssignmentConflict("resource_source_unavailable")
                before = self._selection(conn, user_id)
                self._replace(conn, user_id, experts, skills)
                conn.execute(
                    "UPDATE users SET expert_access_mode=? WHERE id=?", (item["mode"], user_id)
                )
                self._changed(conn, user_id)
                ExpertAssignmentRepo._audit(
                    conn,
                    actor,
                    "employee_resources.save",
                    str(user_id),
                    {
                        "before": before,
                        "expert_ids": experts,
                        "skill_ids": skills,
                        "mode": item["mode"],
                    },
                )
