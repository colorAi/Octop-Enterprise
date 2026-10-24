"""Bounded worker loop with durable admission, commands and stream replay."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from octop.infra.db.repos.execution import TERMINAL, ExecutionRepo
from octop.infra.db.repos.execution import ExecutionConflict as ExecutionConflict
from octop.infra.execution.notifications import ExecutionNotifications
from octop.infra.utils.bounded_io import BoundedIO
from octop.infra.utils.execution_context import execution_run_id

logger = logging.getLogger(__name__)
Handler = Callable[[dict[str, Any], dict[str, Any] | None], AsyncGenerator[dict[str, Any], None]]


def public_frames(
    event: dict[str, Any], *, run: str, thread: str, locale: str = "en"
) -> list[dict[str, Any]]:
    from octop.i18n import tr

    body = event["payload"]
    frames = body.get("frames") or [body]
    if body.get("type") == "execution.state" and body.get("state") == "unknown":
        frames = [
            *frames,
            {
                "type": "error",
                "code": "execution_result_unknown",
                "message": tr("execution.result_unknown", locale),
            },
        ]
    if (
        body.get("type") == "execution.state"
        and body.get("state") in {"unknown", "failed", "cancelled"}
        and not body.get("frames")
    ):
        frames = [*frames, {"type": "done", "execution_state": body["state"]}]
    return [
        {
            **frame,
            "execution_id": run,
            "event_seq": event["seq"],
            "event_index": index,
            "thread_id": thread,
        }
        for index, frame in enumerate(frames)
    ]


@dataclass(frozen=True)
class ActiveExecution:
    service: ExecutionService
    run: dict[str, Any]

    @property
    def fence(self) -> tuple[str, str, str, int]:
        return (self.run["run_id"], self.run["node_id"], self.run["incarnation"], self.run["token"])

    async def check(self) -> None:
        await self.service.io.run(self.service.repo.check, *self.fence)


current_execution: ContextVar[ActiveExecution | None] = ContextVar("octop_execution", default=None)


class ExecutionService:
    def __init__(
        self,
        repo: ExecutionRepo,
        *,
        node: str,
        incarnation: str,
        role: str,
        conninfo: str | None = None,
        container_id: str = "",
        capacity: int = 2,
    ) -> None:
        self.repo, self.node, self.incarnation, self.role = repo, node, incarnation, role
        self.container_id, self.capacity = container_id, capacity
        # Model streams may complete together. Serialize their short database
        # operations without rejecting already-running calls or losing receipts.
        self.io = BoundedIO(workers=2, capacity=32, wait=True)
        self.notifications = ExecutionNotifications(conninfo)
        self.handler: Handler | None = None
        self.cancel: Callable[[str, str], None] | None = None
        self._loops: list[asyncio.Task[None]] = []
        self._active: dict[str, asyncio.Task[None]] = {}
        self._paused: set[str] = set()
        self._accepting = False

    async def start(self) -> None:
        await self.start_lease()
        if self.role in {"worker", "monolith"}:
            if self.handler is None:
                raise RuntimeError("execution handler is required before starting a worker")
            self._loops.append(asyncio.create_task(self._work(), name="execution-worker"))

    async def start_lease(self) -> None:
        if self._loops:
            return
        await self.io.run(
            self.repo.register,
            self.node,
            self.incarnation,
            self.role,
            container_id=self.container_id,
        )
        self._accepting = True
        self.notifications.start()
        self._loops.append(asyncio.create_task(self._heartbeat(), name="execution-heartbeat"))

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await self.io.run(self.repo.heartbeat, self.node, self.incarnation)
                await self.io.run(self.repo.expire_nodes)
            except Exception:
                self._accepting = False
                # Cancel local coroutine work too. Stopping children still requires
                # supervisor evidence; task/ownership records remain occupied.
                for task in list(self._active.values()):
                    task.cancel()
                logger.exception("execution heartbeat failed; executor fenced")
                return

    async def _work(self) -> None:
        while self._accepting:
            try:
                while len(self._active) - len(self._paused) < self.capacity:
                    run = await self.io.run(self.repo.claim, self.node, self.incarnation)
                    if run is None:
                        break
                    task = asyncio.create_task(
                        self._execute(run), name=f"execution:{run['run_id']}"
                    )
                    self._active[run["run_id"]] = task

                    def finished(done: asyncio.Task[None], identity: str = run["run_id"]) -> None:
                        self._active.pop(identity, None)
                        self._paused.discard(identity)
                        if not done.cancelled():
                            done.exception()

                    task.add_done_callback(finished)
            except Exception:
                logger.exception("execution claim failed")
            await self.notifications.wait()

    async def _commands(self, active: ActiveExecution) -> None:
        while True:
            commands = await self.io.run(self.repo.commands, *active.fence)
            for command in commands:
                if command["kind"] == "cancel" and self.cancel:
                    self.cancel(active.run["agent_id"], active.run["thread_id"])
                    await self.io.run(self.repo.applied, *active.fence, command["command_key"])
            await asyncio.sleep(0.2)

    async def _emit(self, active: ActiveExecution, frames: list[dict[str, Any]]) -> None:
        if frames:
            await self.io.run(
                self.repo.append,
                *active.fence,
                key=uuid.uuid4().hex,
                payload={"type": "execution.frames", "frames": list(frames)},
            )
            frames.clear()

    async def _stream(
        self, active: ActiveExecution, decision: dict[str, Any] | None
    ) -> tuple[bool, str | None, list[dict[str, Any]]]:
        assert self.handler is not None
        frames: list[dict[str, Any]] = []
        size = 0
        failed = False
        approval: str | None = None
        final_frames: list[dict[str, Any]] = []
        stream = self.handler(active.run, decision)
        deliveries: asyncio.Queue[tuple[bool, Any]] = asyncio.Queue(maxsize=2)

        async def produce() -> None:
            # All generator advances and cleanup must share one asyncio Context:
            # the harness keeps ContextVar scopes open across yielded chunks.
            try:
                try:
                    async for frame in stream:
                        await deliveries.put((False, frame))
                finally:
                    await stream.aclose()
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                await deliveries.put((True, exc))
            else:
                await deliveries.put((True, None))

        producer = asyncio.create_task(produce())
        pending: asyncio.Future[Any] | None = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(deliveries.get())
                done, _ = await asyncio.wait({pending}, timeout=0.075)
                if not done:
                    await self._emit(active, frames)
                    size = 0
                    continue
                finished, frame = pending.result()
                pending = None
                if finished:
                    if frame is not None:
                        raise frame
                    break
                if frame.get("type") in {"done", "completed"}:
                    final_frames.append(frame)
                    continue
                # Handler owns conversion from harness objects to public JSON.
                size += len(json.dumps(frame, ensure_ascii=False).encode())
                if size > 64 * 1024:
                    await self._emit(active, frames)
                    size = len(json.dumps(frame, ensure_ascii=False).encode())
                frames.append(frame)
                kind = frame.get("type")
                if kind == "error":
                    failed = True
                if kind == "hitl_required":
                    approval = str(
                        frame.get("pending_id")
                        or frame.get("request", {}).get("pending_id")
                        or frame.get("request", {}).get("id")
                        or uuid.uuid4().hex
                    )
                elif frame.get("metadata", {}).get("execution_approval_id"):
                    approval = str(frame["metadata"]["execution_approval_id"])
                if kind not in {"token", "delta", "tool_call_chunk"}:
                    await self._emit(active, frames)
                    size = 0
            await self._emit(active, frames)
            return failed, approval, final_frames
        finally:
            if pending is not None:
                pending.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration):
                    await pending
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    async def _execute(self, run: dict[str, Any]) -> None:
        active = ActiveExecution(self, run)
        context = current_execution.set(active)
        usage_context = execution_run_id.set(run["run_id"])
        commands = asyncio.create_task(self._commands(active))
        try:
            decision = None
            while True:
                await active.check()
                failed, approval, final_frames = await self._stream(active, decision)
                latest = await self.io.run(self.repo.get, run["run_id"])
                if latest and latest["state"] == "cancel_requested":
                    await self.io.run(
                        self.repo.transition, *active.fence, "cancelled", final_frames=final_frames
                    )
                    break
                if approval and not failed:
                    await self.io.run(
                        self.repo.transition,
                        *active.fence,
                        "awaiting_approval",
                        approval_id=approval,
                        final_frames=final_frames,
                    )
                    self._paused.add(run["run_id"])
                    try:
                        decision = await self._wait_approval(active)
                    finally:
                        self._paused.discard(run["run_id"])
                    if decision is None:
                        break
                    continue
                await self.io.run(
                    self.repo.transition,
                    *active.fence,
                    "failed" if failed else "completed",
                    final_frames=final_frames,
                )
                break
        except BaseException:
            # Exceptions/cancellation can happen after external writes. Never infer
            # failure or release occupancy from a lost coroutine alone.
            with suppress(Exception):
                await self.io.run(self.repo.transition, *active.fence, "unknown")
            logger.warning(
                "execution interrupted; result remains unknown run=%s", run["run_id"], exc_info=True
            )
        finally:
            commands.cancel()
            with suppress(asyncio.CancelledError, ExecutionConflict):
                await commands
            current_execution.reset(context)
            execution_run_id.reset(usage_context)

    async def _wait_approval(self, active: ActiveExecution) -> dict[str, Any] | None:
        while True:
            row = await self.io.run(self.repo.get, active.run["run_id"])
            if row and row["state"] == "cancel_requested":
                await self.io.run(self.repo.transition, *active.fence, "cancelled")
                return None
            for command in await self.io.run(self.repo.commands, *active.fence):
                if command["kind"] == "approval":
                    if len(self._active) - len(self._paused) >= self.capacity:
                        continue
                    try:
                        await self.io.run(self.repo.applied, *active.fence, command["command_key"])
                    except ExecutionConflict as exc:
                        if str(exc) == "execution_capacity_exceeded":
                            continue
                        raise
                    return cast(dict[str, Any], command["payload"])
            await asyncio.sleep(0.2)

    async def replay(self, run: str, *, user: int, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        while True:
            row = await self.io.run(self.repo.get, run, user=user)
            if row is None:
                raise ExecutionConflict("execution_not_found")
            events = await self.io.run(self.repo.events, run, user=user, after=after)
            for event in events:
                after = event["seq"]
                yield event
            if after >= row["event_seq"] and (
                row["state"] in TERMINAL | {"unknown"}
                or (row["state"] == "awaiting_approval" and row["approval_id"] is not None)
            ):
                return
            if not events:
                await asyncio.sleep(0.1)

    async def close(self, *, grace: float = 10) -> None:
        self._accepting = False
        with suppress(Exception):
            await self.io.run(self.repo.drain, self.node, self.incarnation)
        if self._active:
            _, pending = await asyncio.wait(list(self._active.values()), timeout=grace)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        for task in self._loops:
            task.cancel()
        await asyncio.gather(*self._loops, return_exceptions=True)
        await self.notifications.close()
        await self.io.drain()
