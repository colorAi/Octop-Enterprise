"""Company services; device credentials never gain general administrator access."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator
from importlib.metadata import version
from typing import Any

from langchain_core.messages import message_to_dict, messages_from_dict

from octop.infra.company_runtime import PROTOCOL_VERSION
from octop.infra.db.repos.company_runtime import CompanyRuntimeRepo, RuntimeConflict, encode
from octop.infra.db.repos.thread_messages import ThreadMessageInput
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.resource_policy import assert_token_quota_available
from octop.infra.utils.asyncio import offload


def denied(reason: str, status: int = 409) -> OctopError:
    return OctopError(ErrorCode.FORBIDDEN, reason, status=status, details={"reason": reason})


class CompanyRuntimeService:
    def __init__(self, server: Any, identity: dict[str, Any]) -> None:
        self.server = server
        self.services = server.services
        self.identity = identity
        self.repo = CompanyRuntimeRepo(self.services.db)
        self.owner = int(identity["owner_user_id"])

    async def authorize(self, agent_id: str, thread_id: str | None = None) -> Any:
        row = await offload(self.services.agent_repo.get, agent_id)
        # No admin bypass: a desktop runs only its employee's private instance.
        if row is None or row.user_id != self.owner or not row.enabled or row.kind != "expert":
            raise denied("expert_not_owned", 403)
        if thread_id:
            thread = await offload(self.services.thread_repo.get, thread_id)
            if thread is None or thread.agent_id != agent_id or thread.user_id != self.owner:
                raise denied("thread_not_owned", 403)
        return row

    async def task(self, task_id: str, *, active: bool = False) -> dict[str, Any]:
        task = await offload(self.repo.task, task_id)
        if not task:
            raise denied("task_not_found", 404)
        binding = await offload(self.repo.binding, task["thread_id"])
        if (
            not binding
            or binding["owner_user_id"] != self.owner
            or task["device_id"] != self.identity["device_id"]
        ):
            raise denied("task_not_owned", 403)
        await self.authorize(binding["agent_id"], binding["thread_id"])
        if active and task["status"] != "running":
            raise denied("task_" + task["status"])
        return {**task, "agent_id": binding["agent_id"]}

    def agent(self, agent_id: str) -> Any:
        return self.server.app_runtime.agent_registry.get_agent(agent_id)

    async def expert(self, agent_id: str) -> dict[str, Any]:
        row = await self.authorize(agent_id)
        agent = self.agent(agent_id)
        instructions: list[str] = [row.system_prompt or ""]
        for path in ("AGENTS.md", "SOUL.md", "USER.md", "MEMORY.md"):
            content = await agent.workspace.aread_text(path, limit=262144)
            if content:
                instructions.append(f"\n--- Company {path} ---\n{content}")
        from octop.infra.company_runtime.skills import snapshot_skills

        skills = await snapshot_skills(agent, self.services.paths.root / "company-runtime-skills")
        models = agent.model_factory.list_refs()
        default_model = agent.config.pick_default_model_ref()
        if default_model not in models:
            fallback = self.server.app_runtime.agent_registry.resolve_fallback_model_ref()
            default_model = fallback if fallback in models else next(iter(models), None)
        snapshot = {
            "agent_id": agent_id,
            "name": row.name,
            "instructions": "\n".join(instructions),
            "skills": skills,
            "models": models,
            "default_model": default_model,
            "harness_version": version("octop-harness"),
            "protocol_version": PROTOCOL_VERSION,
        }
        snapshot["version"] = hashlib.sha256(encode(snapshot).encode()).hexdigest()
        return snapshot

    async def start(self, body: dict[str, Any]) -> dict[str, Any]:
        await self.authorize(body["agent_id"], body["thread_id"])
        hub = self.server.app_runtime.gateway.ws_hub
        if hub.is_turn_active(body["thread_id"]):
            raise denied("server_thread_running")
        snapshot = await self.expert(body["agent_id"])
        if body.get("model") and body["model"] not in snapshot["models"]:
            raise denied("model_not_available", 403)
        await offload(
            assert_token_quota_available,
            self.services.user_policy_repo,
            self.services.usage_repo,
            self.owner,
        )
        request = {
            k: v for k, v in body.items() if k not in {"recovery_token", "installation_token"}
        }
        if not request.get("edit_message_id"):
            request.pop("edit_message_id", None)
        try:
            edit = None
            if request.get("edit_message_id") and not await offload(
                self.repo.task, body["task_id"]
            ):
                from octop.infra.company_runtime.history import edit_checkpoint

                edit = await offload(
                    self.repo.edit_context, body["thread_id"], request["edit_message_id"]
                )
                edit["checkpoint"] = await offload(
                    edit_checkpoint, body["thread_id"], edit.pop("messages"), edit["checkpoint_id"]
                )
            task = await offload(
                self.repo.acquire,
                task_id=body["task_id"],
                thread_id=body["thread_id"],
                owner=self.owner,
                agent_id=body["agent_id"],
                device_id=self.identity["device_id"],
                request=request,
                expert=snapshot,
                installation_hash=hashlib.sha256(body["installation_token"].encode()).hexdigest(),
                recovery_hash=hashlib.sha256(body["recovery_token"].encode()).hexdigest(),
                edit=edit,
            )
        except RuntimeConflict as exc:
            raise denied(str(exc)) from exc
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "expert": json.loads(task["expert_json"]),
            "project_name": body["thread_id"],
        }

    async def event(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        task = await self.task(task_id)
        payload = body["payload"]
        inputs = []
        if payload.get("kind") == "messages":
            try:
                raw_messages = payload["messages"]
                if not isinstance(raw_messages, list):
                    raise ValueError("messages must be a list")
                messages = messages_from_dict(raw_messages)
            except (KeyError, TypeError, ValueError) as exc:
                raise denied("invalid_history_messages", 422) from exc
            for i, msg in enumerate(messages):
                if msg.type not in {"human", "ai", "tool"}:
                    raise denied("invalid_history_role", 422)
                wire = message_to_dict(msg)
                ident = msg.id or f"{body['event_id']}:{i}"
                wire["data"]["id"] = ident
                inputs.append(ThreadMessageInput(ident, msg.type, encode(wire), int(time.time())))
        # Reject malformed history before reserving the event identity or sequence.
        try:
            inserted = await offload(
                self.repo.append_event,
                task_id,
                body["event_id"],
                body["seq"],
                payload,
                messages=inputs,
            )
        except RuntimeConflict as exc:
            raise denied(str(exc)) from exc
        if payload.get("kind") == "messages":
            await offload(self.services.thread_repo.touch_last_active, task["thread_id"])
        return {"accepted": inserted, "seq": body["seq"]}

    async def model(self, task_id: str, body: dict[str, Any]) -> AsyncIterator[str]:
        task = await self.task(task_id, active=True)
        snapshot = json.loads(task["expert_json"])
        ref = body["model"]
        agent = self.agent(task["agent_id"])
        if ref not in snapshot["models"] or ref not in agent.model_factory.list_refs():
            raise denied("model_not_available", 403)
        await offload(
            assert_token_quota_available,
            self.services.user_policy_repo,
            self.services.usage_repo,
            self.owner,
        )
        model = agent.model_factory.get_chat_model(ref)
        if body.get("tools"):
            model = model.bind_tools(body["tools"])
        usage: dict[str, Any] = {}
        try:
            async for chunk in model.astream(messages_from_dict(body["messages"])):
                if chunk.usage_metadata:
                    usage = chunk.usage_metadata
                yield encode(message_to_dict(chunk)) + "\n"
        finally:
            await offload(
                self.services.usage_repo.record,
                agent_id=task["agent_id"],
                user_id=self.owner,
                thread_id=task["thread_id"],
                model=ref,
                source="company_runtime",
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
            )

    async def memory(self, task_id: str, action: str, body: dict[str, Any]) -> Any:
        from octop_memory import MemoryService

        task = await self.task(task_id, active=True)
        memory = self.agent(task["agent_id"]).memory
        if memory is None:
            raise denied("company_memory_unavailable", 503)
        service = MemoryService(memory)
        if action == "search":
            return await offload(
                service.search, body["query"], max_results=min(int(body.get("max_results", 5)), 20)
            )
        if action == "get":
            return await offload(service.get, body["path"])
        operation = body["operation_id"]
        old = await offload(
            self.repo.begin_operation,
            task_id,
            operation,
            hashlib.sha256(encode(body).encode()).hexdigest(),
        )
        if old:
            if old["status"] != "completed":
                raise denied("operation_result_unknown")
            return json.loads(old["result_json"])

        def remember() -> Any:
            # octop-memory 1.0.2 initializes a thread-local connection lazily.
            # Warm it before entering its transaction; no upstream upgrade needed.
            memory.list_atoms(limit=1)
            atom, _, _ = memory.create_atom(
                body["assertion"],
                entity_name="Employee preferences",
                entity_type="User",
                actor="user",
                note="company runtime " + task_id,
            )
            return atom

        atom = await offload(remember)
        result = {"atom_id": atom.id}
        await offload(self.repo.finish_operation, task_id, operation, result)
        return result

    async def knowledge(self, task_id: str, body: dict[str, Any]) -> str:
        from octop.infra.knowledge.retrieve import retrieve_context

        task = await self.task(task_id, active=True)
        ids = json.loads(task["request_json"]).get("knowledge_base_ids", [])
        return await retrieve_context(
            self.services,
            user_id=self.owner,
            is_admin=False,
            query=body["query"],
            knowledge_base_ids=ids,
            k=min(body.get("k", 8), 20),
            locale="zh",
        )
