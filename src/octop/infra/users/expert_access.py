"""Server-authoritative expert selection, independent of management-menu permissions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from octop.infra.db.repos.expert_assignments import AssignmentConflict, ExpertAssignmentRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.asyncio import offload

_CREATING_INSTANCE: ContextVar[str | None] = ContextVar("assigned_expert_creation", default=None)


def denied(reason: str, status: int = 403) -> OctopError:
    return OctopError(ErrorCode.FORBIDDEN, reason, status=status, details={"reason": reason})


class ExpertAccess:
    def __init__(self, repos: Any) -> None:
        self.repos = repos
        self.repo: ExpertAssignmentRepo = repos.expert_assignment_repo

    def require_admin(self, user_id: int) -> Any:
        row = self.repos.user_repo.get(user_id)
        if row is None or row.disabled or row.role != "admin":
            raise denied("expert_assignment_admin_required")
        return row

    def assigned_only(self, user_id: int) -> bool:
        row = self.repos.user_repo.get(user_id)
        state = self.repo.state(user_id)
        return bool(row and row.role != "admin" and state and state["mode"] == "assigned_only")

    def assert_use(self, user_id: int, agent_id: str) -> None:
        user = self.repos.user_repo.get(user_id)
        if user is None or user.disabled:
            raise denied("resource_not_assigned")
        instance = self.repo.instance(agent_id)
        if instance is not None:
            if instance["user_id"] != user_id or not self.repo.granted(
                user_id, instance["source_agent_id"], instance["source_row_id"]
            ):
                raise denied("resource_not_assigned")
        elif self.assigned_only(user_id):
            raise denied("resource_not_assigned")

    def assert_owner_execution(self, agent_id: str) -> None:
        row = self.repos.agent_repo.get(agent_id)
        if row is not None and row.user_id is not None:
            self.assert_use(row.user_id, agent_id)

    def assert_create(self, user_id: int, agent_id: str | None = None) -> bool:
        """Return True only for a trusted, currently granted system instance creation."""
        if agent_id and _CREATING_INSTANCE.get() == agent_id:
            instance = self.repo.instance(agent_id)
            if (
                instance
                and instance["user_id"] == user_id
                and self.repo.granted(
                    user_id, instance["source_agent_id"], instance["source_row_id"]
                )
            ):
                _CREATING_INSTANCE.set(None)
                return True
        if self.assigned_only(user_id):
            raise denied("managed_resource_read_only")
        return False

    def assert_manage(self, agent_id: str) -> None:
        if self.repo.instance(agent_id) is not None:
            raise denied("managed_resource_read_only")
        row = self.repos.agent_repo.get(agent_id)
        if row and row.user_id is not None and self.assigned_only(row.user_id):
            raise denied("managed_resource_read_only")

    def configure_source(self, actor_id: int, source_id: int, revision: int) -> dict[str, Any]:
        actor = self.require_admin(actor_id)
        self.require_admin(source_id)
        try:
            self.repo.set_source(source_id, revision, actor.username)
        except AssignmentConflict as exc:
            raise denied(str(exc), 409) from exc
        return self.repo.source()

    def save(
        self,
        actor_id: int,
        user_id: int,
        expert_ids: list[str],
        mode: str,
        revision: int,
        source_revision: int,
    ) -> dict[str, Any]:
        actor = self.require_admin(actor_id)
        target = self.repos.user_repo.get(user_id)
        if target is None:
            raise OctopError(ErrorCode.NOT_FOUND, "user not found")
        if target.role == "admin" or (target.disabled and expert_ids):
            raise denied("expert_assignment_employee_required", 400)
        if mode not in {"legacy", "assigned_only"} or (mode == "legacy" and expert_ids):
            raise denied("invalid_expert_assignment", 422)
        if len(set(expert_ids)) != len(expert_ids):
            raise denied("invalid_expert_assignment", 422)
        try:
            self.repo.save(user_id, expert_ids, mode, revision, source_revision, actor.username)
        except AssignmentConflict as exc:
            raise denied(str(exc), 409) from exc
        state = self.repo.state(user_id)
        assert state is not None
        return state

    async def instantiate(self, registry: Any, source_id: str, user_id: int) -> Any:
        from octop.infra.agents.experts.private_instance import instantiate_private

        resources = getattr(registry, "employee_resources", None)
        if resources is not None:
            await resources.synchronize(user_id)
        if not await offload(self.repo.granted, user_id, source_id):
            raise denied("resource_not_assigned")
        source = registry.get_row(source_id)
        if source is None:
            raise denied("resource_source_unavailable")
        try:
            mapping = await offload(self.repo.reserve, user_id, source_id)
        except AssignmentConflict as exc:
            raise denied(str(exc), 409) from exc
        if source.id != mapping["source_row_id"]:
            raise denied("resource_source_unavailable", 409)
        identity = mapping["instance_agent_id"]
        if mapping["state"] == "ready":
            row = registry.get_row(identity)
            if row is not None:
                return row
        if not await offload(self.repo.claim, identity):
            raise denied("assigned_expert_preparing", 409)
        try:
            with _instance_creation(identity):
                row = await instantiate_private(registry, source, user_id, identity)
            await offload(self.repo.finish_instance, identity, ready=True)
        except BaseException:
            await offload(self.repo.finish_instance, identity, ready=False)
            raise
        # A save/revocation may have happened while files were being copied.
        await offload(self.assert_use, user_id, row.agent_id)
        return row


@contextmanager
def _instance_creation(agent_id: str) -> Iterator[None]:
    token = _CREATING_INSTANCE.set(agent_id)
    try:
        yield
    finally:
        _CREATING_INSTANCE.reset(token)
