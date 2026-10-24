"""Company services; device credentials never gain general administrator access."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from importlib.metadata import version
from typing import Any

import httpx
from anyio import CancelScope
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import message_to_dict, messages_from_dict

from octop.infra.agents.providers.reasoning import (
    reasoning_capability,
    reasoning_request_parameters,
)
from octop.infra.agents.settings.runtime_limits import agent_model_settings, agent_runtime_values
from octop.infra.company_runtime import PROTOCOL_VERSION
from octop.infra.company_runtime.model_context import model_token_limits
from octop.infra.db.repos.company_runtime import CompanyRuntimeRepo, RuntimeConflict, encode
from octop.infra.db.repos.execution import ExecutionConflict
from octop.infra.db.repos.thread_messages import ThreadMessageInput
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.resource_policy import assert_token_quota_available
from octop.infra.utils.asyncio import offload


def denied(reason: str, status: int = 409) -> OctopError:
    return OctopError(ErrorCode.FORBIDDEN, reason, status=status, details={"reason": reason})


def authenticate_company_device(services: Any, credential: str, origin: str | None = None) -> Any:
    if origin:
        raise denied("native_runtime_required", 403)
    if not credential.startswith("octop-device.") or len(credential) > 256:
        raise denied("device_credential_required", 401)
    identity = services.device_repo.authenticate(
        hashlib.sha256(credential.encode()).hexdigest(), int(time.time())
    )
    if identity is None:
        raise denied("device_session_expired", 401)
    return identity


def model_failure(exc: Exception) -> dict[str, Any]:
    """Expose a stable failure category without provider payloads or credentials."""
    if isinstance(exc, OctopError):
        if exc.code == ErrorCode.MODEL_NOT_ASSIGNED:
            return {"error": exc.code.value, "retryable": False}
        return {"error": exc.details.get("reason", "company_model_failed"), "retryable": False}
    status = (
        exc.response.status_code
        if isinstance(exc, httpx.HTTPStatusError)
        else getattr(exc, "status_code", None)
    )
    if isinstance(exc, ContextOverflowError) or (
        status in {400, 413, 422}
        and any(
            marker in str(exc).lower()
            for marker in (
                "context_length_exceeded",
                "contextwindowexceedederror",
                "maximum context length",
                "exceeds the context window",
                "exceeds the available context size",
                "context window exceeded",
                "context limit exceeded",
                "input tokens exceed the configured limit",
                "prompt is too long",
            )
        )
    ):
        return {"error": "company_model_context_overflow", "retryable": False}
    if isinstance(exc, TimeoutError | httpx.TimeoutException) or "Timeout" in type(exc).__name__:
        return {"error": "company_model_timeout", "retryable": True}
    if isinstance(exc, httpx.TransportError) or type(exc).__name__ in {
        "APIConnectionError",
        "ConnectError",
    }:
        return {"error": "company_model_connection_lost", "retryable": True}
    if isinstance(status, int):
        return {
            "error": f"company_model_http_{status}",
            "retryable": status in {408, 429} or status >= 500,
        }
    return {"error": "company_model_failed", "retryable": False}


class CompanyRuntimeService:
    def __init__(self, server: Any, identity: dict[str, Any]) -> None:
        self.server = server
        self.services = server.services
        self.identity = identity
        self.repo = CompanyRuntimeRepo(self.services.db)
        self.owner = int(identity["owner_user_id"])

    async def authorize(
        self, agent_id: str, thread_id: str | None = None, *, execution: bool = True
    ) -> Any:
        row = await offload(self.services.agent_repo.get, agent_id)
        # No admin bypass: a desktop runs only its employee's private instance.
        if row is None or row.user_id != self.owner or not row.enabled or row.kind != "expert":
            raise denied("expert_not_owned", 403)
        if execution:
            from octop.infra.users.expert_access import ExpertAccess

            await offload(ExpertAccess(self.services).assert_use, self.owner, agent_id)
            resources = getattr(self.server, "employee_resources", None)
            if resources is not None:
                await resources.authorize(self.owner, agent_id)
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
        await self.authorize(binding["agent_id"], binding["thread_id"], execution=active)
        resources = getattr(self.server, "employee_resources", None)
        saved_revision = json.loads(task.get("expert_json") or "{}").get("resource_revision")
        if resources is not None and saved_revision is not None:
            try:
                await resources.authorize(self.owner, binding["agent_id"])
                if saved_revision != await resources.revision(self.owner):
                    raise denied("employee_resources_changed", 403)
            except OctopError:
                if active:
                    raise
                await offload(self.repo.cancel_running, task_id)
                task = await offload(self.repo.task, task_id)
                assert task is not None
        if not active and task["status"] == "running":
            from octop.infra.users.expert_access import ExpertAccess

            try:
                await offload(
                    ExpertAccess(self.services).assert_use, self.owner, binding["agent_id"]
                )
            except OctopError:
                await offload(self.repo.cancel_running, task_id)
                task = await offload(self.repo.task, task_id)
                assert task is not None
        if active and task["status"] != "running":
            raise denied("task_" + task["status"])
        return {**task, "agent_id": binding["agent_id"]}

    def agent(self, agent_id: str) -> Any:
        return self.server.app_runtime.agent_registry.get_agent(agent_id)

    async def media_agent(self, agent_id: str) -> Any:
        """Desktop snapshots do not load a server executor; media needs one."""
        registry = self.server.app_runtime.agent_registry
        if getattr(self.server, "role", None) == "worker":
            await registry.ensure_runtime(agent_id)
        resources = getattr(self.server, "employee_resources", None)
        if resources is not None:
            await resources.prepare(agent_id)
        return self.agent(agent_id)

    async def require_capability(self, task_id: str, capability: str) -> dict[str, Any]:
        task = await self.task(task_id, active=True)
        resources = getattr(self.server, "employee_resources", None)
        if resources is not None and not await resources.has_capability(self.owner, capability):
            from octop.infra.users.capabilities import CAPABILITIES

            raise denied("company_" + CAPABILITIES[capability][0] + "_not_assigned", 403)
        return task

    async def expert(self, agent_id: str) -> dict[str, Any]:
        row = await self.authorize(agent_id)
        registry = self.server.app_runtime.agent_registry
        resources = getattr(self.server, "employee_resources", None)
        assigned = await resources.snapshot(self.owner, agent_id) if resources is not None else None
        definition = assigned["definition"] if assigned is not None else None
        detached = getattr(self.server, "role", None) == "worker" and definition is not None
        if detached:
            assert definition is not None
            workspace = await registry.desktop_context_workspace(agent_id)
            factory = registry.company_model_factory()
            default_model = definition["fields"]["default_model"]
        else:
            if getattr(self.server, "role", None) == "worker":
                await registry.ensure_runtime(agent_id)
            if resources is not None:
                await resources.prepare(agent_id)
            agent = self.agent(agent_id)
            workspace = agent.workspace
            factory = agent.model_factory
            default_model = agent.config.pick_default_model_ref()
        instructions: list[str] = [
            (definition["fields"]["system_prompt"] if definition else row.system_prompt) or ""
        ]
        from octop.infra.users.personal_history import legacy_personal_files, personal_predecessors

        previous = await offload(personal_predecessors, self.services, self.owner, agent_id)
        inherited = {}
        for personal_id in [*previous, agent_id]:
            inherited.update(
                await offload(legacy_personal_files, self.services, self.owner, personal_id)
            )
        for path in ("AGENTS.md", "SOUL.md", "USER.md", "MEMORY.md"):
            content = (
                definition["files"].get(path)
                if definition is not None and path in {"AGENTS.md", "SOUL.md"}
                else await workspace.aread_text(path, limit=262144)
            )
            if not content and path in {"USER.md", "MEMORY.md"}:
                content = inherited.get(path)
            if content:
                instructions.append(f"\n--- Company {path} ---\n{content}")
        from octop.infra.company_runtime.skills import snapshot_skills

        skills = (
            assigned["assets"]
            if assigned is not None
            else await snapshot_skills(agent, self.services.paths.root / "company-runtime-skills")
        )
        from octop.infra.users.model_access import ModelAccess

        models = await offload(
            ModelAccess(self.services).filter_refs, self.owner, factory.list_refs()
        )
        if not models:
            raise OctopError(ErrorCode.MODEL_NOT_ASSIGNED, "no models assigned to this employee")
        if default_model not in models:
            fallback = self.server.app_runtime.agent_registry.resolve_fallback_model_ref()
            default_model = fallback if fallback in models else next(iter(models), None)
        from octop.infra.users.capabilities import CAPABILITIES

        granted = (
            await resources.granted_capabilities(self.owner)
            if resources is not None
            else set(CAPABILITIES)
        )
        capabilities = {item[0]: identity in granted for identity, item in CAPABILITIES.items()}
        snapshot = {
            "agent_id": agent_id,
            "name": definition["fields"]["name"] if definition else row.name,
            "instructions": "\n".join(instructions),
            "skills": skills,
            "models": models,
            "model_profiles": {
                ref: model_token_limits(getattr(factory.get_chat_model(ref), "profile", None))
                for ref in models
            },
            "default_model": default_model,
            "harness_version": version("octop-harness"),
            "protocol_version": PROTOCOL_VERSION,
            "capabilities": capabilities,
            "runtime_settings": (
                definition["runtime_settings"]
                if definition
                else agent_runtime_values(json.loads(row.config_json or "{}"))
            ),
        }
        snapshot["version"] = hashlib.sha256(encode(snapshot).encode()).hexdigest()
        if assigned is not None:
            assert resources is not None
            # Definition reads and skill snapshots must describe the same
            # prepared revision, even if an administrator edits during export.
            await resources.authorize(self.owner, agent_id, prepared=not detached)
            current_revision = (
                await resources.revision(self.owner)
                if detached
                else getattr(agent, "_octop_resource_revision", None)
            )
            if assigned["revision"] != current_revision:
                raise denied("employee_resources_changed", 409)
            snapshot["resource_revision"] = assigned["revision"]
            snapshot["resource_manifest_sha256"] = assigned["manifest_sha256"]
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
        from octop.infra.agents.security.hitl_session import parse_hitl_session_policy

        policy = parse_hitl_session_policy(request.get("hitl_policy"))
        if policy.mode == "ask":
            request.pop("hitl_policy", None)
        else:
            request["hitl_policy"] = policy.to_dict()
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
            # Retry this even when the ordered event was already acknowledged:
            # deterministic raw IDs make a failed memory delivery replay safe.
            await self.capture_memory(task, messages)
        return {"accepted": inserted, "seq": body["seq"]}

    async def with_memory(self, agent_id: str, operation: Any) -> Any:
        if getattr(self.server, "role", None) == "worker":
            from octop.infra.company_runtime.memory import with_company_memory

            return await self.server.management_io.run(
                with_company_memory, self.server, agent_id, operation
            )
        return await offload(operation, self.agent(agent_id).memory)

    async def capture_memory(self, task: dict[str, Any], messages: list[Any]) -> None:
        from octop_memory.application.runtime import MemoryRuntime

        def capture(memory: Any) -> None:
            if memory is None:
                return
            events = []
            for message in messages:
                if message.type not in {"human", "ai"} or not message.id:
                    continue
                content = message.content
                if isinstance(content, list):
                    content = "\n".join(
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict) and item.get("type") == "text"
                    )
                ident = (
                    "company-"
                    + hashlib.sha256(f"{task['thread_id']}:{message.id}".encode()).hexdigest()
                )
                if not content or memory.get_raw(ident) is not None:
                    continue
                events.append(
                    {
                        "id": ident,
                        "event_type": "user_message"
                        if message.type == "human"
                        else "assistant_message",
                        "content": content,
                    }
                )
            if events:
                MemoryRuntime(memory).capture(
                    {
                        "host": "octop-company",
                        "session_id": task["thread_id"],
                        "thread_id": task["thread_id"],
                        "user": str(self.owner),
                        "events": events,
                    }
                )

        await self.with_memory(task["agent_id"], capture)

    async def model(self, task_id: str, body: dict[str, Any]) -> AsyncIterator[str]:
        task = await self.task(task_id, active=True)
        snapshot = json.loads(task["expert_json"])
        ref = body["model"]
        factory = (
            self.server.app_runtime.agent_registry.company_model_factory()
            if getattr(self.server, "role", None) == "worker"
            else self.agent(task["agent_id"]).model_factory
        )
        if ref not in snapshot["models"] or ref not in factory.list_refs():
            raise denied("model_not_available", 403)
        from octop.infra.users.model_access import ModelAccess

        await offload(ModelAccess(self.services).require, self.owner, ref)
        await offload(
            assert_token_quota_available,
            self.services.user_policy_repo,
            self.services.usage_repo,
            self.owner,
        )
        model = factory.get_chat_model(ref)
        provider_name, _, model_id = ref.partition("/")
        provider = await offload(self.services.provider_repo.get_by_name, provider_name)
        if provider is not None:
            entry: dict[str, Any] = next(
                (item for item in provider.get_models() if item.get("id") == model_id), {}
            )
            requested = json.loads(task["request_json"])
            overrides = reasoning_request_parameters(
                reasoning_capability(entry, base_url=provider.base_url),
                mode=requested.get("reasoning_mode"),
                effort=requested.get("reasoning_effort"),
            )
            updates = dict(overrides.get("model_fields", {}))
            if overrides.get("extra_body") and hasattr(model, "extra_body"):
                updates["extra_body"] = {
                    **(getattr(model, "extra_body", None) or {}),
                    **overrides["extra_body"],
                }
            if updates:
                model = model.model_copy(update=updates)
        if body.get("tools"):
            model = model.bind_tools(body["tools"])
        settings = agent_model_settings(snapshot.get("runtime_settings", {}))
        if settings:
            model = model.bind(**settings)
        usage: dict[str, Any] = {}
        execution = getattr(self.server, "execution", None)
        shared = None
        reservation_id = uuid.uuid4().hex
        reserved_tokens = 0
        succeeded = False
        if execution is not None:
            shared = await execution.io.run(
                execution.repo.proxy_run,
                task=task_id,
                user=self.owner,
                device=self.identity["device_id"],
                node=execution.node,
                incarnation=execution.incarnation,
            )
            fence = (shared["run_id"], shared["node_id"], shared["incarnation"], shared["token"])
            output = int(
                settings.get("max_tokens") or settings.get("max_completion_tokens") or 8192
            )
            if not 1 <= output <= 65536:
                await execution.io.run(execution.repo.transition, *fence, "failed")
                raise denied("model_output_limit_exceeded", 422)
            request_size = len(json.dumps(body, ensure_ascii=False).encode())
            if request_size > 1024 * 1024:
                await execution.io.run(execution.repo.transition, *fence, "failed")
                raise denied("model_input_limit_exceeded", 413)
            text_only = all(
                isinstance(item.get("data", {}).get("content"), str) for item in body["messages"]
            )
            profile = snapshot.get("model_profiles", {}).get(ref, {})
            reserved_tokens = (
                request_size + 4096
                if text_only
                else max(
                    request_size,
                    int(profile.get("max_input_tokens") or profile.get("context_window") or 128000),
                )
            ) + output
            try:
                deadline = time.monotonic() + 90
                while True:
                    try:
                        await execution.io.run(
                            execution.repo.reserve_model,
                            *fence,
                            key=reservation_id,
                            provider=ref,
                            tokens=reserved_tokens,
                            quota_remaining=None,
                        )
                        break
                    except ExecutionConflict as exc:
                        if str(exc) != "model_capacity_exceeded" or time.monotonic() >= deadline:
                            raise
                        await self.task(task_id, active=True)
                        await asyncio.sleep(0.1)
            except BaseException:
                await execution.io.run(execution.repo.transition, *fence, "failed")
                raise
            model = model.bind(
                **{
                    "max_completion_tokens"
                    if "max_completion_tokens" in settings
                    else "max_tokens": output
                }
            )
        try:
            next_check = 0.0
            async for chunk in model.astream(messages_from_dict(body["messages"])):
                if shared is not None and time.monotonic() >= next_check:
                    assert execution is not None
                    await execution.io.run(execution.repo.check, *fence)
                    await self.task(task_id, active=True)
                    next_check = time.monotonic() + 1
                if chunk.usage_metadata:
                    usage = chunk.usage_metadata
                yield encode(message_to_dict(chunk)) + "\n"
            succeeded = True
        finally:
            # StreamingResponse cancels this scope again while the client is
            # disconnecting. Finish both writes before acknowledging teardown.
            with CancelScope(shield=True):
                if shared is not None:
                    assert execution is not None
                    with suppress(Exception):
                        await execution.io.run(
                            execution.repo.settle_model,
                            *fence,
                            key=reservation_id,
                            actual=int(usage.get("total_tokens", reserved_tokens))
                            if succeeded
                            else None,
                        )
                        await execution.io.run(
                            execution.repo.transition,
                            *fence,
                            "completed" if succeeded else "unknown",
                        )
                await offload(
                    self.services.usage_repo.record,
                    agent_id=task["agent_id"],
                    user_id=self.owner,
                    thread_id=task["thread_id"],
                    model=ref,
                    source="company_runtime",
                    input_tokens=int(usage.get("input_tokens", 0)),
                    output_tokens=int(usage.get("output_tokens", 0)),
                    **({"execution_id": shared["run_id"]} if shared is not None else {}),
                )

    async def checkpoints(self, task_id: str, operations: list[dict[str, Any]]) -> dict[str, int]:
        task = await self.task(task_id)
        if task["status"] not in {"running", "cancel_requested", "unknown"}:
            raise denied("task_already_finished")
        for operation in operations:
            if not operation["checkpoint_id"]:
                raise denied("checkpoint_id_required", 422)
            if operation["action"] == "put" and operation["payload"] is None:
                raise denied("checkpoint_payload_required", 422)
            if any(
                set(write) != {"idx", "value"} or type(write["idx"]) is not int
                for write in operation["writes"]
            ):
                raise denied("invalid_checkpoint_writes", 422)
        try:
            await offload(self.repo.checkpoint_batch, task_id, task["thread_id"], operations)
        except RuntimeConflict as exc:
            raise denied(str(exc)) from exc
        return {"saved": len(operations)}

    async def memory(self, task_id: str, action: str, body: dict[str, Any]) -> Any:
        from octop_memory import MemoryService
        from octop_memory.pipeline.recall import recall_for_prompt

        task = await self.task(task_id, active=True)

        from octop.infra.users.personal_history import personal_predecessors

        previous = await offload(personal_predecessors, self.services, self.owner, task["agent_id"])
        if action in {"search", "recall", "get"}:
            sources = [task["agent_id"], *previous]
            path = body.get("path", "")
            if action == "get":
                source = task["agent_id"]
                if path.startswith("legacy/"):
                    parts = path.split("/", 2)
                    if len(parts) != 3 or parts[1] not in previous:
                        raise denied("memory_source_not_owned", 403)
                    _, source, path = parts

                def get(mem: Any) -> Any:
                    if mem is None:
                        raise denied("company_memory_unavailable", 503)
                    return {
                        **MemoryService(mem).get(path),
                        "path": body["path"],
                        "source_agent_id": source,
                        "source_scope": "current" if source == task["agent_id"] else "legacy",
                    }

                return await self.with_memory(source, get)
            results = []
            for source in sources:

                def read(mem: Any, source: str = source) -> Any:
                    if mem is None:
                        return "" if action == "recall" else {"hits": []}
                    service = MemoryService(mem)
                    if action == "recall":
                        # Raw chat is searchable evidence, not a durable fact or
                        # a description of today's tools/workspace. Inject only
                        # promoted memories; keep raw history available to search/get.
                        recall = recall_for_prompt(
                            mem,
                            body["query"],
                            thread_id=task["thread_id"],
                            limit=5,
                            raw_policy="never",
                        )
                        if not recall.snippets:
                            return ""
                        origin = (
                            "current expert instance"
                            if source == task["agent_id"]
                            else "earlier personal expert instance"
                        )
                        lines = [
                            f"[memory] Employee personal memory from {origin} ({source}).",
                            "Historical reference, not current project state, tool availability or task results.",
                        ]
                        prefix = "" if source == task["agent_id"] else f"legacy/{source}/"
                        lines.extend(
                            f"- {prefix}atom/{item.source_id}.md ({item.timestamp_iso}): {item.text}"
                            for item in recall.snippets
                        )
                        return "\n".join([*lines, "[/memory]"])
                    return service.search(body["query"], max_results=body.get("max_results", 5))

                result = await self.with_memory(source, read)
                if action == "recall":
                    if result:
                        results.append(result)
                else:
                    for hit in result.get("hits", []):
                        hit = {
                            **hit,
                            "source_agent_id": source,
                            "source_scope": "current" if source == task["agent_id"] else "legacy",
                        }
                        if source != task["agent_id"]:
                            hit = {**hit, "path": f"legacy/{source}/{hit['path']}"}
                        results.append(hit)
            if action == "recall":
                return {"rendered": "\n\n".join(results)[:16000]}
            # Each source is ranked internally; null scores are not comparable
            # across stores. Preserve rank within each source and interleave so
            # current-session mentions cannot hide every matching legacy record.
            buckets = {
                source: [h for h in results if h["source_agent_id"] == source] for source in sources
            }
            hits = [
                bucket[index]
                for index in range(max((len(b) for b in buckets.values()), default=0))
                for bucket in buckets.values()
                if index < len(bucket)
            ][: body.get("max_results", 5)]
            return {
                "hits": hits,
                "total": len(hits),
                "empty_reason": None if hits else "no_matches",
            }

        def perform(memory: Any) -> Any:
            if memory is None:
                raise denied("company_memory_unavailable", 503)
            operation = body["operation_id"]
            old = self.repo.begin_operation(
                task_id, operation, hashlib.sha256(encode(body).encode()).hexdigest()
            )
            if old:
                if old["status"] != "completed":
                    raise denied("operation_result_unknown")
                return json.loads(old["result_json"])
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
            result = {"atom_id": atom.id}
            self.repo.finish_operation(task_id, operation, result)
            return result

        return await self.with_memory(task["agent_id"], perform)

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
