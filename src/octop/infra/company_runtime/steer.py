"""Use the shared steer queue for the company client's local Harness turn."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict
from typing import Any

from langchain_core.messages import HumanMessage

from octop.infra.company_runtime.approvals import CompanyApprovals
from octop.infra.gateway.steer import (
    DiscardBundle,
    DrainBundle,
    SteerAdmitReject,
    SteerCoordinator,
)


class CompanySteering:
    def __init__(self, emit: Callable[[dict[str, Any]], None]) -> None:
        self.coordinator = SteerCoordinator()
        self.emit = emit
        self.approvals = CompanyApprovals()

    def frame(self, thread: str, **frame: Any) -> None:
        self.emit({"event": "steer", "thread_id": thread, "frame": frame})

    def begin(self, thread: str) -> str:
        turn_id, seq, discarded = self.coordinator.begin_turn(thread)
        if discarded:
            self.frame(thread, type="steer.discarded", **asdict(discarded))
        self.frame(thread, type="turn.started", turn_id=turn_id, seq=seq)
        return turn_id

    def send(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = str(params.get("thread_id") or "")
        text = params.get("text")
        pending = params.get("client_pending_id")
        expected = params.get("expected_turn_id")
        if not isinstance(text, str) or not isinstance(pending, str) or not pending.strip():
            raise ValueError("invalid_steer_input")
        if not isinstance(expected, str) or not expected.strip():
            raise ValueError("expected_turn_id_required")
        result = self.coordinator.admit(
            thread,
            text=text,
            client_pending_id=pending,
            expected_turn_id=expected,
            delivery="steer",
        )
        assert result is not None
        if isinstance(result, SteerAdmitReject):
            return {"type": "steer.rejected", **asdict(result)}
        if result.message_id:
            return {
                "type": "steer.drained",
                "turn_id": result.turn_id,
                "client_pending_ids": [result.client_pending_id],
                "pending_ids": [result.pending_id],
                "message_id": result.message_id,
            }
        return {"type": "steer.queued", **asdict(result), "preview": text.strip()[:200]}

    def remove(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self.coordinator.remove(
            str(params.get("thread_id") or ""),
            turn_id=str(params.get("turn_id") or ""),
            client_pending_id=str(params.get("client_pending_id") or ""),
            pending_id=str(params.get("pending_id") or ""),
        )
        return {**asdict(result), "type": "steer.ack", "status": "removed"}

    def cancel(self, thread: str) -> None:
        self.coordinator.set_phase(thread, "cancelling")

    def end(self, thread: str, *, failed: bool) -> None:
        self.approvals.end(thread)
        discarded = self.coordinator.end_turn(thread, failed=failed)
        if discarded:
            self.frame(thread, type="steer.discarded", **asdict(discarded))
        self.frame(thread, type="turn.ended", failed=failed)

    async def stream(
        self,
        agent: Any,
        thread: str,
        message: HumanMessage,
        persist: Callable[[HumanMessage], Awaitable[None]],
        *,
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Continue at completed tool batches, or after a text-only answer.

        A first post-tool token is withheld before cancelling that model step,
        matching the upstream WebSocket path. Tool execution is never cancelled
        to insert a message, and the next invocation uses the same checkpoint.
        """
        current = message
        while True:
            saw_tool_result = False
            preempted = False
            async for chunk in agent.stream(
                {**(options or {}), "messages": [current], "thread_id": thread}
            ):
                kind = str(chunk.get("type") or "")
                if kind == "error":
                    raise RuntimeError(
                        str(chunk.get("message") or chunk.get("content") or "Local model failed")
                    )
                if kind == "hitl_required":
                    # Tool approvals use CompanyToolGuard's per-call native
                    # futures. An unrelated graph interrupt must fail closed.
                    raise RuntimeError("unsupported_local_interrupt")
                if preempted or kind == "done":
                    continue
                if (
                    kind in {"token", "tool_call_chunk"}
                    and saw_tool_result
                    and self.coordinator.has_pending(thread)
                ):
                    preempted = True
                    self.coordinator.set_phase(thread, "between_model_steps")
                    agent.cancel(thread)
                    continue
                if kind == "tool_result":
                    saw_tool_result = True
                    self.coordinator.set_phase(thread, "awaiting_tools")
                yield chunk
            outcome = self.coordinator.drain_for_continuation(thread)
            if outcome is None:
                # Reject late inserts while the runtime persists final history.
                self.coordinator.set_phase(thread, "terminal")
                return
            if isinstance(outcome, DiscardBundle):
                self.frame(thread, type="steer.discarded", **asdict(outcome))
                self.coordinator.set_phase(thread, "terminal")
                return
            assert isinstance(outcome, DrainBundle)
            current = HumanMessage(content=outcome.text, id=outcome.message_id)
            await persist(current)
            self.frame(
                thread,
                type="user",
                content=outcome.text,
                message_id=outcome.message_id,
                client_pending_ids=outcome.client_pending_ids,
                meta={"steer": True},
            )
            self.frame(
                thread,
                type="steer.drained",
                turn_id=outcome.turn_id,
                seq=self.coordinator.drained_event_seq(thread),
                client_pending_ids=outcome.client_pending_ids,
                pending_ids=outcome.pending_ids,
                message_id=outcome.message_id,
            )
            self.frame(
                thread,
                type="turn.resumed",
                turn_id=outcome.turn_id,
                seq=self.coordinator.allocate_seq(thread),
            )
