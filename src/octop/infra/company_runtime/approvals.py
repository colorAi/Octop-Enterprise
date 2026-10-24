"""Native, task-bound approval pauses; no command runs before the decision."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from octop_harness.security.tool_guard.engine import ToolGuardEngine, build_tool_guard_hitl_request

from octop.infra.agents.security.hitl_session import parse_hitl_session_policy


class CompanyApprovals:
    def __init__(self) -> None:
        self.pending: dict[str, tuple[dict[str, Any], asyncio.Future[list[dict[str, Any]]]]] = {}
        self.policies: dict[str, Any] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    def begin(self, thread: str, policy: Any) -> None:
        self.policies[thread] = parse_hitl_session_policy(policy)

    def prepare(
        self, thread: str, request: dict[str, Any]
    ) -> tuple[dict[str, Any], asyncio.Future[list[dict[str, Any]]] | list[dict[str, Any]]]:
        actions = request.get("action_requests")
        if not isinstance(actions, list) or not actions:
            raise ValueError("invalid_local_approval")
        policy = self.policies.get(thread) or parse_hitl_session_policy(None)
        if all(policy.allows(action.get("name", "")) for action in actions):
            return request, [{"type": "approve"} for _ in actions]
        pending = {**request, "pending_id": "local-" + secrets.token_hex(16)}
        future: asyncio.Future[list[dict[str, Any]]] = asyncio.get_running_loop().create_future()
        self.pending[thread] = (pending, future)
        return pending, future

    def snapshot(self, thread: str) -> dict[str, Any] | None:
        entry = self.pending.get(thread)
        return entry[0] if entry and not entry[1].done() else None

    def resolve(self, params: dict[str, Any]) -> dict[str, str]:
        thread = str(params.get("thread_id") or "")
        entry = self.pending.get(thread)
        if not entry or entry[1].done() or params.get("pending_id") != entry[0]["pending_id"]:
            raise ValueError("local_approval_not_pending")
        decisions = params.get("decisions")
        if (
            not isinstance(decisions, list)
            or len(decisions) != len(entry[0]["action_requests"])
            or any(
                not isinstance(d, dict)
                or d.get("type") not in {"approve", "reject"}
                or not isinstance(d.get("message", ""), str)
                for d in decisions
            )
        ):
            raise ValueError("invalid_local_approval_decisions")
        if "hitl_policy" in params and all(d["type"] == "approve" for d in decisions):
            self.policies[thread] = parse_hitl_session_policy(params["hitl_policy"])
        entry[1].set_result(decisions)
        return {"status": "resuming"}

    def end(self, thread: str) -> None:
        entry = self.pending.pop(thread, None)
        if entry and not entry[1].done():
            entry[1].cancel()
        self.policies.pop(thread, None)
        self.locks.pop(thread, None)

    async def decide(self, thread: str, request: dict[str, Any], emit: Any) -> list[dict[str, Any]]:
        # Parallel tool calls each keep their own decision. A scalar LangGraph
        # resume can otherwise approve several independent interrupts at once.
        async with self.locks.setdefault(thread, asyncio.Lock()):
            pending, future = self.prepare(thread, request)
            if isinstance(future, list):
                return future
            emit(
                {
                    "event": "chunk",
                    "thread_id": thread,
                    "data": {"type": "hitl_required", "request": pending},
                }
            )
            return await future


class CompanyToolGuard(AgentMiddleware):
    """Use the harness rules with an async native approval for each tool call."""

    def __init__(self, approvals: CompanyApprovals, thread: str, emit: Any) -> None:
        self.approvals, self.thread, self.emit = approvals, thread, emit
        self.engine = ToolGuardEngine(enabled=True, mode="require_approval")

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        result = self.engine.guard(call["name"], call.get("args") or {})
        if result is not None and self.engine.should_block(result):
            decisions = await self.approvals.decide(
                self.thread, build_tool_guard_hitl_request(result), self.emit
            )
            if decisions[0]["type"] != "approve":
                return ToolMessage(
                    content=decisions[0].get("message") or "The user declined this tool operation.",
                    tool_call_id=call["id"],
                    status="error",
                )
        return await handler(request)
