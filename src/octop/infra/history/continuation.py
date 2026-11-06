"""Continue an owned transcript with a currently authorized expert.

Only messages are carried forward. Old permissions, pending tools and execution
checkpoints are never resumed under a different expert or execution location.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.messages import message_to_dict, messages_from_dict

from octop.infra.agents.threads.fork import load_checkpoint_messages, write_checkpoint_messages
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.history.reader import export_messages
from octop.infra.history.service import HistoryArchive
from octop.infra.utils.asyncio import offload


async def owned_transcript(server: Any, owner: int, thread_id: str) -> tuple[Any, list[Any]]:
    services = server.services
    row = await offload(services.thread_repo.get, thread_id)
    agent = await offload(services.agent_repo.get, row.agent_id) if row else None
    if not row or row.user_id != owner or not agent or agent.user_id != owner:
        raise OctopError(ErrorCode.FORBIDDEN, "thread_not_owned")
    active = await offload(services.execution_repo.active, thread_id)
    if active and active["state"] in {"running", "cancel_requested", "unknown"}:
        raise OctopError(ErrorCode.FORBIDDEN, "thread_already_running")
    local = await offload(services.company_runtime_repo.thread_state, thread_id)
    if local and local.get("local_task_status") in {"running", "cancel_requested", "unknown"}:
        raise OctopError(ErrorCode.FORBIDDEN, "thread_already_running")
    hub = getattr(getattr(server.app_runtime, "gateway", None), "ws_hub", None)
    if hub is not None and hub.is_turn_active(thread_id) is True:
        raise OctopError(ErrorCode.FORBIDDEN, "thread_already_running")
    archive = getattr(server.app_runtime, "history_archive", None)
    registry = server.app_runtime.agent_registry
    if isinstance(archive, HistoryArchive):
        wires = await export_messages(archive, registry, row.agent_id, thread_id)
    elif await offload(services.thread_message_repo.projection_status, thread_id) == "ready":
        rows, more = await offload(
            services.thread_message_repo.page, thread_id, limit=100_001, offset=0
        )
        if more:
            raise OctopError(ErrorCode.FORBIDDEN, "project_history_too_large")
        wires = [json.loads(item.message_json) for item in rows]
    else:
        messages = await load_checkpoint_messages(registry.get_agent(row.agent_id), thread_id)
        wires = [message_to_dict(message) for message in messages]
    if len(wires) > 100_000:
        raise OctopError(ErrorCode.FORBIDDEN, "project_history_too_large")
    # Legacy messages can predate stable IDs. Assign IDs in the copy, not the source.
    for wire in wires:
        if not wire["data"].get("id"):
            wire["data"]["id"] = str(uuid.uuid4())
    return row, wires


async def continue_dashboard_thread(
    server: Any, owner: int, source_id: str, target_id: str
) -> dict[str, Any]:
    from octop.infra.history.projection import message_inputs
    from octop.infra.users.expert_access import ExpertAccess

    target = await offload(server.services.agent_repo.get, target_id)
    if not target or target.user_id != owner or not target.enabled or target.kind != "expert":
        raise OctopError(ErrorCode.FORBIDDEN, "expert_not_owned")
    await offload(ExpertAccess(server.services).assert_use, owner, target_id)
    resources = getattr(server, "employee_resources", None)
    if resources is not None:
        await resources.authorize(owner, target_id)
    if await offload(server.services.company_runtime_repo.binding, source_id):
        raise OctopError(ErrorCode.FORBIDDEN, "project_runtime_required")
    source, wires = await owned_transcript(server, owner, source_id)
    harness = server.app_runtime.agent_registry.get_agent(target_id)
    registry = server.app_runtime.gateway.thread_registry
    key = ThreadRegistry.dashboard_key(agent_id=target_id, user_id=owner)
    dest = await offload(
        registry.create_thread,
        agent_id=target_id,
        user_id=owner,
        channel_type=ThreadRegistry.CHANNEL_DASHBOARD,
        session_key=key,
        title=source.title,
        last_active=0,
    )
    archive = getattr(server.app_runtime, "history_archive", None)
    try:
        messages = messages_from_dict(wires)
        await write_checkpoint_messages(harness, thread_id=dest, messages=messages)
        turn = (
            await offload(archive.begin, target_id, dest)
            if isinstance(archive, HistoryArchive)
            else None
        )
        if turn is not None and isinstance(archive, HistoryArchive):
            await offload(archive.save_messages, turn, wires)
            await offload(archive.finish, turn["id"], "complete")
        else:
            await offload(
                server.services.thread_message_repo.replace_all, dest, message_inputs(messages)
            )
        await registry.rebind(session_key=key, thread_id=dest, agent_id=target_id)
    except Exception:
        if isinstance(archive, HistoryArchive):
            await offload(archive.remove_thread, dest)
        await offload(registry.delete_thread, dest)
        raise
    return {"thread_id": dest, "agent_id": target_id, "copied_messages": len(wires)}
