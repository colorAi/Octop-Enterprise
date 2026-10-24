"""Reuse shared expertise without sharing an employee's writable runtime."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from octop.infra.errors import ErrorCode, OctopError


async def instantiate_shared(registry: Any, source_id: str, user_id: int) -> Any:
    source = registry.get_row(source_id)
    if source is None:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, "shared expert not found")
    if source.user_id == user_id:
        return source
    if not source.is_shared or source.kind == "team":
        raise OctopError(ErrorCode.FORBIDDEN, "expert is not shared")
    identity = "P" + hashlib.sha256(f"{user_id}:{source_id}".encode()).hexdigest()[:15].upper()
    return await instantiate_private(registry, source, user_id, identity, shared=True)


async def instantiate_private(
    registry: Any, source: Any, user_id: int, identity: str, *, shared: bool = False
) -> Any:
    shards = getattr(registry, "runtime_shards", None)
    if shards is not None:
        async with shards.activity(user_id, identity):
            await asyncio.to_thread(shards.assert_user, user_id)
            return await shards.retry_sandbox(
                lambda: _instantiate_private(registry, source, user_id, identity, shared=shared)
            )
    return await _instantiate_private(registry, source, user_id, identity, shared=shared)


async def _instantiate_private(
    registry: Any, source: Any, user_id: int, identity: str, *, shared: bool
) -> Any:
    """Copy approved definition files, never the administrator's personal runtime data."""
    from octop.infra.agents.experts.catalog import seed_expert_directory
    from octop.infra.agents.experts.publish import (
        PublishedExpertSnapshotMeta,
        export_agent_workspace_to_dir,
    )
    from octop.infra.agents.manager import AgentCreateSpec

    source_id = source.agent_id
    async with registry._shared_instance_lock:
        existing = registry.get_row(identity)
        if existing is not None:
            if existing.user_id != user_id:
                raise OctopError(ErrorCode.FORBIDDEN, "private expert ownership mismatch")
            if not shared and getattr(registry, "runtime_shards", None) is None:
                target = registry.workspace_for_agent(identity)
                if target is None or await target.aread_text(".octop/.bootstrapped") is None:
                    raise OctopError(
                        ErrorCode.FORBIDDEN,
                        "assigned expert initialization is incomplete",
                        status=409,
                        details={"reason": "assigned_expert_preparing"},
                    )
            return existing
        resources = getattr(registry, "employee_resources", None)
        definition = (
            await resources.definition(source_id) if resources is not None and not shared else None
        )
        if definition is not None and definition["source_row_id"] != source.id:
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "resource_source_unavailable",
                status=409,
                details={"reason": "resource_source_unavailable"},
            )
        # Employee workers consume the published definition, never open the
        # administrator's mutable workspace on a different runtime shard.
        workspace = None if definition is not None else registry.workspace_for_agent(source_id)
        if workspace is None and definition is None:
            raise OctopError(ErrorCode.AGENT_NOT_RUNNING, "shared expert workspace unavailable")
        with tempfile.TemporaryDirectory(prefix="octop-expert-template-") as temp:
            directory = Path(temp) / "template"
            if not shared:
                from octop.infra.users.managed_resources import DEFINITION_FILES

                directory.mkdir(parents=True)
                # A managed copy never exports all skills, packages, subagents
                # or personal files. Approved skills are materialized separately.
                for name in DEFINITION_FILES:
                    if definition is not None:
                        value = definition["files"].get(name)
                    else:
                        assert workspace is not None
                        value = await workspace.aread_text(name, limit=262144)
                    if value is not None:
                        await asyncio.to_thread(
                            (directory / name).write_text, value, encoding="utf-8"
                        )
            else:
                assert workspace is not None
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
                if shared:
                    await seed_expert_directory(expert_dir=directory, workspace=target)
                else:
                    await target.aupload_many(
                        [
                            (name, await asyncio.to_thread((directory / name).read_bytes))
                            for name in DEFINITION_FILES
                            if (directory / name).is_file()
                        ]
                    )
                await target.aupload_many([(".octop/.bootstrapped", b"shared expert instance\n")])

            names = {row.name for row in registry.list_agents(user_id)}
            fields = definition["fields"] if definition is not None else vars(source)
            name = fields["name"]
            if name in names:
                name = f"{name} ({identity[-6:]})"
            return await registry.create(
                AgentCreateSpec(
                    name=name,
                    agent_id=identity,
                    user_id=user_id,
                    description=fields["description"],
                    default_model=fields["default_model"],
                    system_prompt=fields["system_prompt"],
                    icon_name=fields["icon_name"],
                    icon=fields.get("icon"),
                    icon_url=fields.get("icon_url"),
                    template_name=fields.get("template_name"),
                    persona_mbti=fields.get("persona_mbti"),
                    color=fields["color"],
                    welcome_message=fields["welcome_message"],
                    runtime_config=definition["runtime_settings"] if definition is not None else {},
                    skill_package_ids=registry.get_config(source_id).get("skill_package_ids", [])
                    if shared
                    else [],
                    config={"shared_template_id": source_id} if shared else {},
                ),
                workspace_initializer=initialize,
                **(
                    {"initialize_runtime": False}
                    if definition is not None
                    and getattr(registry, "runtime_shards", None) is not None
                    else {}
                ),
            )
