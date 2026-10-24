"""Read-only management projections, separate from authoritative resource use."""

from __future__ import annotations

from typing import Any

from octop.infra.db.repos.employee_resources import UNASSIGNED
from octop.infra.users.expert_access import ExpertAccess, denied


class ResourceDirectory:
    def __init__(self, server: Any) -> None:
        self.server = server
        self.services = server.services
        self.access = ExpertAccess(self.services)

    def snapshot(self, actor_id: int) -> dict[str, Any]:
        self.access.require_admin(actor_id)
        source = self.access.repo.source()
        admins = [u for u in self.services.user_repo.list() if u.role == "admin" and not u.disabled]
        experts = []
        if any(u.id == source["source_user_id"] for u in admins):
            experts = [
                {"agent_id": r.agent_id, "name": r.name, "description": r.description}
                for r in self.services.agent_repo.list_by_user(source["source_user_id"])
                if r.enabled and r.kind == "expert"
            ]
        repo = self.services.employee_resource_repo
        return {
            "source": source,
            "administrators": [
                {"id": u.id, "username": u.username, "display_name": u.display_name} for u in admins
            ],
            "experts": experts,
            "employees": repo.directory(),
            "pools": repo.pools(),
            "defaults": repo.defaults(),
        }

    async def memberships(self, actor_id: int, data: dict[str, Any]) -> dict[str, Any]:
        bridge = getattr(self.server, "communication", None)
        memberships = (
            await bridge.resource_memberships(actor_id, [e["id"] for e in data["employees"]])
            if bridge and bridge.enabled
            else {}
        )
        for employee in data["employees"]:
            membership = memberships.get(employee["id"])
            status = "unavailable"
            saved_scope = employee["scope_id"]
            if membership and membership["found"]:
                scope = membership["department_id"] or UNASSIGNED
                status = (
                    "pending"
                    if employee["pending_scope"] is not None
                    else "changed"
                    if saved_scope is not None
                    and (
                        saved_scope != scope
                        or employee["membership_revision"] != membership["revision"]
                    )
                    else "current"
                )
                employee.update(scope_id=scope, membership_revision=membership["revision"])
            employee.update(
                scope_id=employee["scope_id"] or UNASSIGNED,
                membership_revision=employee["membership_revision"] or 0,
                membership_status=status,
                communication_user_id=bridge.mapped_id(employee["id"])
                if bridge and bridge.enabled
                else None,
            )
            employee.pop("pending_scope")
        # This is deliberately not passed to reconcile(). Saves and execution
        # obtain a new single-user authoritative membership before changing policy.
        return data

    def employee(self, actor_id: int, user_id: int) -> dict[str, Any]:
        self.access.require_admin(actor_id)
        user = self.services.user_repo.get(user_id)
        if user is None or user.role == "admin":
            raise denied("expert_assignment_employee_required", 404)
        resources = getattr(self.server, "employee_resources", None)
        state = resources.state(user_id) if resources else self.access.repo.state(user_id)
        if state is None:
            raise denied("expert_assignment_employee_required", 404)
        bridge = getattr(self.server, "communication", None)
        return {
            **state,
            "username": user.username,
            "display_name": user.display_name,
            "disabled": bool(user.disabled),
            "communication_user_id": bridge.mapped_id(user_id)
            if bridge and bridge.enabled
            else None,
        }
