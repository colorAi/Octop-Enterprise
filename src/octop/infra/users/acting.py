"""Resolve which user id a local CLI / API ``as_user`` action targets."""

from __future__ import annotations

from typing import Any

from octop.infra.errors import ErrorCode, OctopError


def resolve_user_id_by_username(services: Any, username: str) -> int:
    row = services.user_repo.get_by_username(username)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"user not found: {username}")
    return int(row.id)


def resolve_acting_user_id(
    services: Any,
    *,
    as_username: str | None = None,
    pinned_username: str | None = None,
    agent_id: str | None = None,
) -> int:
    """Map CLI/API acting user to a numeric user id.

    Priority: explicit ``--user`` → pinned CLI ``default_user`` → agent owner.
    """
    if as_username:
        return resolve_user_id_by_username(services, as_username)
    if pinned_username:
        return resolve_user_id_by_username(services, pinned_username)
    if agent_id is not None:
        row = services.agent_repo.get(agent_id)
        if row is None:
            raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"agent {agent_id!r} not found")
        if row.user_id is None:
            raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"agent {agent_id!r} has no owner")
        return int(row.user_id)
    raise OctopError(
        ErrorCode.NOT_FOUND,
        "user required (--user, CLI default_user, or agent with an owner)",
    )
