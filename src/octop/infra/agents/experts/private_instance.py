"""Reuse shared expertise without sharing an employee's writable runtime."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from octop.infra.errors import ErrorCode, OctopError


async def instantiate_shared(registry: Any, source_id: str, user_id: int) -> Any:
    from octop.infra.agents.experts.catalog import seed_expert_directory
    from octop.infra.agents.experts.publish import (
        PublishedExpertSnapshotMeta,
        export_agent_workspace_to_dir,
    )
    from octop.infra.agents.manager import AgentCreateSpec

    source = registry.get_row(source_id)
    if source is None:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, "shared expert not found")
    if source.user_id == user_id:
        return source
    if not source.is_shared or source.kind == "team":
        raise OctopError(ErrorCode.FORBIDDEN, "expert is not shared")
    identity = "P" + hashlib.sha256(f"{user_id}:{source_id}".encode()).hexdigest()[:15].upper()
    async with registry._shared_instance_lock:
        existing = registry.get_row(identity)
        if existing is not None:
            if existing.user_id != user_id:
                raise OctopError(ErrorCode.FORBIDDEN, "private expert ownership mismatch")
            return existing
        workspace = registry.workspace_for_agent(source_id)
        if workspace is None:
            raise OctopError(ErrorCode.AGENT_NOT_RUNNING, "shared expert workspace unavailable")
        with tempfile.TemporaryDirectory(prefix="octop-expert-template-") as temp:
            directory = Path(temp) / "template"
            await export_agent_workspace_to_dir(
                workspace=workspace,
                dest=directory,
                metadata=PublishedExpertSnapshotMeta(
                    name=source.name,
                    description=source.description or "",
                    icon_name=source.icon_name,
                    color=source.color,
                    label_zh=source.name,
                    label_en=source.name,
                    welcome_message_zh=source.welcome_message or "",
                    welcome_message_en=source.welcome_message or "",
                ),
            )
            # USER.md belongs to the source owner's personal context.
            await asyncio.to_thread((directory / "USER.md").unlink, missing_ok=True)

            async def initialize(_row: Any, target: Any) -> None:
                await seed_expert_directory(expert_dir=directory, workspace=target)
                await target.aupload_many([(".octop/.bootstrapped", b"shared expert instance\n")])

            names = {row.name for row in registry.list_agents(user_id)}
            name = source.name
            if name in names:
                name = f"{name} ({identity[-6:]})"
            return await registry.create(
                AgentCreateSpec(
                    name=name,
                    agent_id=identity,
                    user_id=user_id,
                    description=source.description,
                    default_model=source.default_model,
                    system_prompt=source.system_prompt,
                    icon_name=source.icon_name,
                    color=source.color,
                    welcome_message=source.welcome_message,
                    skill_package_ids=registry.get_config(source_id).get("skill_package_ids", []),
                    config={"shared_template_id": source_id},
                ),
                workspace_initializer=initialize,
            )
