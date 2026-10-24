"""Adapt existing chat/channel streams to durable execution without changing frames."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import asdict, replace
from typing import Any

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict
from octop_gateway.models import InboundMessage, MessageEvent

from octop.infra.db.repos.execution import ExecutionConflict
from octop.infra.execution.service import ExecutionService, current_execution, public_frames
from octop.infra.gateway.cli.events import message_event_to_cli_chunks
from octop.infra.gateway.hitl.coordinator import HitlStreamContext
from octop.infra.gateway.process.message_keys import (
    resolve_user_id_for_message,
    session_key_from_message,
)
from octop.infra.gateway.process.stream_project import project_chunks
from octop.infra.gateway.slash.catalog import HITL_APPROVAL_COMMANDS
from octop.infra.gateway.slash.parser import parse_slash


class GatewayExecution:
    def __init__(self, server: Any, service: ExecutionService) -> None:
        self.server, self.service = server, service
        self.processor = server.app_runtime.gateway.processor
        service.handler = self.execute
        service.cancel = server.app_runtime.agent_registry.cancel_stream

    @property
    def should_submit(self) -> bool:
        return current_execution.get() is None

    async def cron(self, command: Any) -> None:
        await self.server.employee_resources.authorize(command.user_id, command.agent_id)
        at = command.scheduled_at or "manual:" + uuid.uuid4().hex
        run = await self.service.io.run(self.service.repo.scheduled, command.cron_id, at)
        if run is None:
            gateway = self.server.app_runtime.gateway
            if command.fresh_thread:
                await gateway.thread_registry.reset_by_session_key(command.session_key)
            session = gateway.require_session(command.agent_id, command.session_key)
            if session.user_id != command.user_id:
                raise ExecutionConflict("thread_not_owned")
            payload = asdict(replace(command, fresh_thread=False, scheduled_at=at))
            run = await self.service.io.run(
                self.service.repo.submit,
                user=command.user_id,
                agent=command.agent_id,
                thread=session.thread_id,
                operation="cron",
                key=command.cron_id + ":" + at,
                request={"mode": "cron", "command": payload},
                schedule=(command.cron_id, at),
            )
        if run["user_id"] != command.user_id or run["agent_id"] != command.agent_id:
            raise ExecutionConflict("execution_identity_conflict")
        async for _ in self.service.replay(run["run_id"], user=command.user_id):
            pass
        final = await self.service.io.run(self.service.repo.get, run["run_id"])
        if not final or final["state"] != "completed":
            raise ExecutionConflict("cron_execution_incomplete")

    async def manager_stream(
        self, agent: str, request: dict[str, Any], *, call: bool = False
    ) -> AsyncIterator[dict[str, Any]]:
        thread = str(request.get("thread_id") or "")
        row = await self.service.io.run(self.server.services.thread_repo.get, thread)
        if row is None or row.agent_id != agent:
            raise ExecutionConflict("thread_not_owned")
        await self.server.employee_resources.authorize(row.user_id, agent)
        payload = dict(request)
        message_indices = []
        if isinstance(payload.get("messages"), list):
            messages = []
            for index, item in enumerate(payload["messages"]):
                if isinstance(item, BaseMessage):
                    message_indices.append(index)
                    item = messages_to_dict([item])[0]
                messages.append(item)
            payload["messages"] = messages
        payload = json.loads(json.dumps(payload, default=_json_value))
        key = str(payload.pop("client_request_id", None) or uuid.uuid4().hex)
        run = await self.service.io.run(
            self.service.repo.submit,
            user=row.user_id,
            agent=agent,
            thread=thread,
            operation="call" if call else "cron",
            key=key,
            request={
                "mode": "call" if call else "harness",
                "request": payload,
                "message_indices": message_indices,
            },
        )
        async for event in self.service.replay(run["run_id"], user=row.user_id):
            for frame in event["payload"].get("frames", []):
                yield frame

    async def _identity(self, message: InboundMessage) -> tuple[str, int, str, str]:
        registry = self.server.app_runtime.agent_registry
        agent = str(message.tenant_id or "")
        row = await self.service.io.run(registry.get_row, agent)
        user = resolve_user_id_for_message(message, agent_owner_id=row.user_id if row else None)
        if not user:
            raise ExecutionConflict("thread_not_owned")
        threads = self.server.app_runtime.gateway.thread_registry
        session = session_key_from_message(message, agent_id=agent)
        thread = str((message.metadata or {}).get("thread_id") or "")
        if not thread:
            thread = await threads.get_or_create_by_key(
                session_key=session,
                agent_id=agent,
                user_id=user,
                channel_type=message.channel_type or "unknown",
            )
        await self.server.employee_resources.authorize(user, agent)
        return agent, user, session, thread

    async def admit(self, message: InboundMessage, *, mode: str) -> dict[str, Any] | None:
        """Commit before a transport queue can delay delivery. HITL uses commands."""
        agent, user, session, thread = await self._identity(message)
        # IM message IDs are unique within a channel, not across all providers.
        if not message.metadata.get("client_request_id"):
            message_id = message.metadata.get("message_id")
            message.metadata["client_request_id"] = (
                "im:"
                + hashlib.sha256(
                    json.dumps([message.channel_id, session, message_id]).encode()
                ).hexdigest()
                if message_id
                else uuid.uuid4().hex
            )
        cmd = parse_slash(message.text)
        hitl = getattr(self.processor, "hitl_coordinator", None)
        if (cmd is not None and cmd.name in HITL_APPROVAL_COMMANDS) or (
            cmd is None
            and message.text.strip()
            and hitl is not None
            and hitl.resolve_ask_pending(session, agent_id=agent, user_id=user) is not None
        ):
            previous = await self.service.io.run(
                self.service.repo.by_request,
                user=user,
                operation="chat",
                key=message.metadata["client_request_id"],
            )
            # A delivery retry of the original prompt must not answer its own
            # newly opened ask_user_question card.
            if previous is None:
                return None
        payload = message.model_dump(mode="json")
        metadata = dict(payload.get("metadata") or {})
        key = str(
            metadata.pop("client_request_id", None)
            or metadata.get("message_id")
            or uuid.uuid4().hex
        )
        # Connection IDs/timestamps are delivery details, not request identity.
        metadata.pop("ws_connection_id", None)
        payload["metadata"] = {**metadata, "thread_id": thread}
        payload.pop("timestamp", None)
        if mode == "dashboard":
            # WebSocketChannel adds mutable routing metadata to the subject.
            payload["channel_subject"] = {"subject_id": str(user)}
        elif isinstance(payload.get("channel_subject"), dict):
            payload["channel_subject"].pop("first_seen", None)
            payload["channel_subject"].pop("last_seen", None)
        return await self.service.io.run(
            self.service.repo.submit,
            user=user,
            agent=agent,
            thread=thread,
            operation="chat",
            key=key,
            request={"mode": mode, "message": payload},
        )

    async def inbound(self, message: InboundMessage, *, mode: str) -> AsyncIterator[Any]:
        run = await self.admit(message, mode=mode)
        if run is None:
            async for control in self._control_events(message):
                if mode == "channel":
                    yield control
                else:
                    for frame in message_event_to_cli_chunks(control):
                        yield {**frame, "thread_id": message.metadata.get("thread_id")}
            return
        async for event in self.service.replay(run["run_id"], user=run["user_id"]):
            if mode != "channel":
                for frame in public_frames(event, run=run["run_id"], thread=run["thread_id"]):
                    yield frame
                continue
            body = event["payload"]
            if body.get("frames"):
                for frame in body["frames"]:
                    yield MessageEvent.model_validate(frame)

    async def _control_events(self, message: InboundMessage) -> AsyncIterator[MessageEvent]:
        agent, user, session, _ = await self._identity(message)
        hitl = self.processor.hitl_coordinator
        ctx = await self.service.io.run(
            self.processor._slash_ctx,
            agent_id=agent,
            user_id=user,
            channel_type=message.channel_type,
            session_key=session,
            metadata=message.metadata,
        )

        async def resume(
            thread: str, decisions: list[dict[str, Any]], pending: str | None
        ) -> AsyncIterator[MessageEvent]:
            run = await self.service.io.run(self.service.repo.active, thread)
            stream = self.resume(
                agent_id=agent,
                thread_id=thread,
                user_id=user,
                decisions=decisions,
                approval_id=pending,
            )
            if run and json.loads(run["request_json"]).get("mode") == "channel":
                async for frame in stream:
                    if not str(frame.get("type", "")).startswith("execution."):
                        yield MessageEvent.model_validate(frame)
            else:
                async for event in project_chunks(
                    stream,
                    agent_manager=self.server.app_runtime.agent_registry,
                    agent_id=agent,
                    locale=ctx.locale,
                    usage_tracker=None,
                    history_tracker=None,
                    projection_state=None,
                    hitl_coordinator=hitl,
                    hitl_ctx=HitlStreamContext(thread, agent, user, session, message.channel_type),
                ):
                    yield event

        cmd = parse_slash(message.text)
        if cmd is not None:
            events = hitl.iter_slash_resolution(
                cmd,
                ctx,
                agent_manager=self.server.app_runtime.agent_registry,
                locale=ctx.locale,
                persisted_resume=resume,
            )
        else:
            record = hitl.resolve_ask_pending(session, agent_id=agent, user_id=user)
            if record is None:
                raise ExecutionConflict("approval_not_pending")
            events = hitl.iter_answer_resolution(
                record,
                message.text,
                agent_manager=self.server.app_runtime.agent_registry,
                locale=ctx.locale,
                persisted_resume=resume,
            )
        async for event in events:
            yield event
        yield MessageEvent.completed()

    async def resume(
        self,
        *,
        agent_id: str,
        thread_id: str,
        user_id: int,
        decisions: list[dict[str, Any]],
        approval_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        run = await self.service.io.run(self.service.repo.active, thread_id)
        if not run or run["agent_id"] != agent_id or run["user_id"] != user_id:
            raise ExecutionConflict("approval_not_pending")
        pending = approval_id or run["approval_id"]
        if not pending:
            raise ExecutionConflict("approval_not_pending")
        await self.server.employee_resources.authorize(user_id, agent_id)
        key = "approval:" + str(pending)
        await self.service.io.run(
            self.service.repo.command,
            run["run_id"],
            user=user_id,
            key=key,
            kind="approval",
            payload={"approval_id": pending, "decisions": decisions},
        )
        hitl = getattr(self.processor, "hitl_coordinator", None)
        if hitl is not None:
            # Resolve only after the command commits, before a second identical
            # interrupt can be registered with the old process-local card ID.
            status = "rejected" if any(d.get("type") == "reject" for d in decisions) else "approved"
            hitl.store.mark_resolved(str(pending), status)
        async for event in self.service.replay(run["run_id"], user=user_id, after=run["event_seq"]):
            for frame in public_frames(event, run=run["run_id"], thread=thread_id):
                yield frame

    async def execute(
        self, run: dict[str, Any], decision: dict[str, Any] | None
    ) -> AsyncGenerator[dict[str, Any], None]:
        registry = self.server.app_runtime.agent_registry
        try:
            if run["agent_id"] in getattr(registry, "_runtime_config_dirty", set()):
                await registry._reload_agent(run["agent_id"], except_run=run["run_id"])
            await self.server.employee_resources.authorize(run["user_id"], run["agent_id"])
            if (
                getattr(registry, "runtime_shards", None) is not None
                or registry._octop_harness_or_none(run["agent_id"]) is None
            ):
                shards = getattr(registry, "runtime_shards", None)
                for attempt in range(11):
                    try:
                        await registry.start(run["agent_id"])
                        break
                    except ExecutionConflict as exc:
                        if (
                            str(exc) != "sandbox_memory_exhausted"
                            or shards is None
                            or attempt == 10
                        ):
                            raise
                        # No model/tool has run yet. Let both workers reclaim
                        # idle slots instead of failing a newly claimed turn.
                        await shards.reap()
                        await asyncio.sleep(1)
        except ExecutionConflict as exc:
            if str(exc) != "sandbox_memory_exhausted":
                raise
            # This rejection is before model/tool execution. Emit a failed receipt
            # instead of locking the thread forever as an unknown external effect.
            from octop.infra.errors import ErrorCode, OctopError

            user = await self.service.io.run(self.server.services.user_repo.get, run["user_id"])
            error = OctopError.localized(
                ErrorCode.SANDBOX_MEMORY_EXHAUSTED, getattr(user, "locale", "en")
            )
            yield {
                "type": "error",
                "message": error.message,
                "error": error.message,
                "code": error.code.value,
                "retry_after": 5,
            }
            return
        request = json.loads(run["request_json"])
        for index in request.get("message_indices", []):
            message = request["request"]["messages"][index]
            request["request"]["messages"][index] = messages_from_dict([message])[0]
        resumed_stream = None
        if decision is not None:
            stream = self.processor.iter_hitl_resume_chunks(
                agent_id=run["agent_id"],
                thread_id=run["thread_id"],
                user_id=run["user_id"],
                decisions=decision["decisions"],
            )
            resumed_stream = stream
            if request.get("mode") == "channel":
                message = InboundMessage.model_validate(request["message"])
                ctx = self.processor._slash_ctx(
                    agent_id=run["agent_id"],
                    user_id=run["user_id"],
                    channel_type=message.channel_type,
                    session_key=session_key_from_message(message, agent_id=run["agent_id"]),
                    metadata=message.metadata,
                )
                stream = project_chunks(
                    stream,
                    agent_manager=registry,
                    agent_id=run["agent_id"],
                    locale=ctx.locale,
                    usage_tracker=None,
                    history_tracker=None,
                    projection_state=None,
                    hitl_coordinator=self.processor.hitl_coordinator,
                    hitl_ctx=HitlStreamContext(
                        run["thread_id"],
                        run["agent_id"],
                        run["user_id"],
                        ctx.session_key,
                        message.channel_type,
                    ),
                )
        elif request["mode"] == "cron":
            from octop.infra.cron.delivery import CronDeliveryCommand

            await self.server.app_runtime.cron_manager._delivery_service.deliver(
                CronDeliveryCommand(**request["command"])
            )
            yield {"type": "done"}
            return
        elif request["mode"] == "call":
            result = await registry.call(run["agent_id"], request["request"])
            yield {
                "type": "execution.result",
                "result": json.loads(json.dumps(result, default=_json_value)),
            }
            return
        elif request["mode"] == "harness":
            stream = registry.stream(run["agent_id"], request["request"])
        elif request["mode"] == "channel":
            stream = self.processor(InboundMessage.model_validate(request["message"]))
        else:
            stream = self.processor.iter_turn_chunks(
                InboundMessage.model_validate(request["message"])
            )
        try:
            async for frame in stream:
                if isinstance(frame, MessageEvent):
                    yield frame.model_dump(mode="json")
                else:
                    # Existing stream chunks use datetimes and LangChain values in
                    # metadata; normalize only objects exposing the public dump API.
                    yield json.loads(json.dumps(frame, default=_json_value, ensure_ascii=False))
        finally:
            if resumed_stream is not None:
                await resumed_stream.aclose()


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"unsupported execution event value: {type(value).__name__}")
