"""Identity-scoped channel lifecycle. No agent cache or generic proxy is shared."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Protocol

from octop.infra.db.repos.devices import DeviceRepo
from octop.infra.devices.protocol import (
    FILE_TOOLS,
    HEARTBEAT_SECONDS,
    IDLE_TIMEOUT_SECONDS,
    MAX_FRAME_BYTES,
    PROBE_TOOL,
    DeviceMessage,
    DeviceSelection,
    DirectoryGrant,
    FileResult,
    Hello,
    ProbeResult,
)
from octop.infra.devices.tasks import CONFIG_KEY, DeviceTask
from octop.infra.errors import ErrorCode, OctopError


async def _db[T](fn: Callable[..., T], *args: Any) -> T:
    return await asyncio.get_running_loop().run_in_executor(None, partial(fn, *args))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _error(reason: str, *, status: int = 409) -> OctopError:
    return OctopError(ErrorCode.FORBIDDEN, reason, status=status, details={"reason": reason})


class DeviceSocket(Protocol):
    async def receive_text(self) -> str: ...
    async def send_json(self, data: Any) -> None: ...
    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


@dataclass
class Connection:
    row: dict[str, Any]
    socket: DeviceSocket
    capabilities: list[str]
    grants: list[DirectoryGrant] = field(default_factory=list)
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    pending_tools: dict[str, str] = field(default_factory=dict)
    pending_tasks: dict[str, DeviceTask] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    generation: int = 0

    async def send(self, frame: dict[str, Any]) -> None:
        async with self.send_lock:
            await asyncio.wait_for(self.socket.send_json(frame), timeout=5)


class DeviceManager:
    def __init__(self, repo: DeviceRepo, *, execution: Any = None) -> None:
        self.repo = repo
        self.execution = execution
        self.connections: dict[str, Connection] = {}
        self.closed = False
        self.tasks: dict[str, DeviceTask] = {}
        self.paused: dict[tuple[str, str], dict[str, Any]] = {}

    async def issue_pairing(self, owner: int, session: str, expires: int) -> dict[str, Any]:
        now = int(time.time())
        if expires <= now:
            raise _error("session_expired", status=401)
        code = "octop-pair." + secrets.token_urlsafe(32)
        deadline = min(now + 120, expires)
        credential_deadline = min(now + 8 * 3600, expires)
        await _db(
            self.repo.issue_code, _digest(code), owner, session, deadline, credential_deadline
        )
        return {"code": code, "expires_at": deadline, "credential_expires_at": credential_deadline}

    async def _identity(self, digest: str) -> dict[str, Any]:
        row = await _db(self.repo.authenticate, digest, int(time.time()))
        if row is None:
            # An expired device must not make the dashboard sign out its valid login.
            raise _error("device_credentials_invalid")
        return row

    async def serve(self, socket: DeviceSocket) -> None:
        conn: Connection | None = None
        try:
            raw = await asyncio.wait_for(socket.receive_text(), timeout=10)
            if len(raw.encode()) > MAX_FRAME_BYTES:
                raise _error("frame_too_large")
            hello = Hello.model_validate_json(raw)
            credential: str | None = None
            if hello.type == "pair":
                if not hello.credential.startswith("octop-pair."):
                    raise _error("pairing_code_invalid", status=401)
                credential = "octop-device." + secrets.token_urlsafe(32)
                row = await _db(
                    self.repo.pair,
                    _digest(hello.credential),
                    secrets.token_hex(16),
                    _digest(credential),
                    hello.name,
                    hello.platform,
                    hello.protocol_version,
                    int(time.time()),
                )
                if row is None:
                    raise _error("pairing_code_invalid", status=401)
                # Recheck session revocation racing code redemption.
                row = await self._identity(_digest(credential))
            else:
                if not hello.credential.startswith("octop-device."):
                    raise _error("device_credentials_invalid", status=401)
                row = await self._identity(_digest(hello.credential))
            if (
                hello.platform != row["platform"]
                or hello.protocol_version != row["protocol_version"]
            ):
                raise _error("device_identity_mismatch")
            if self.closed:
                raise _error("server_stopping")
            device_id = str(row["device_id"])
            if device_id in self.connections:
                raise _error("device_already_connected")
            conn = Connection(row, socket, list(hello.capabilities), hello.grants)
            if self.execution is not None:
                service = self.execution
                conn.generation = await service.io.run(
                    service.repo.connect_device,
                    device_id,
                    user=int(row["owner_user_id"]),
                    node=service.node,
                    incarnation=service.incarnation,
                    metadata={
                        "capabilities": list(hello.capabilities),
                        "grants": [g.model_dump() for g in hello.grants],
                    },
                )
            self.connections[device_id] = conn
            ready = {
                "type": "ready",
                "device_id": device_id,
                "owner_user_id": row["owner_user_id"],
                "protocol_version": hello.protocol_version,
                "heartbeat_seconds": HEARTBEAT_SECONDS,
                "expires_at": row["expires_at"],
            }
            if credential is not None:
                ready["credential"] = credential
            await conn.send(ready)
            while True:
                # The timeout is absolute for this receive, and also bounds expiry detection.
                remaining = max(0.01, min(IDLE_TIMEOUT_SECONDS, row["expires_at"] - time.time()))
                raw = await asyncio.wait_for(socket.receive_text(), timeout=remaining)
                if len(raw.encode()) > MAX_FRAME_BYTES:
                    raise _error("frame_too_large")
                message = DeviceMessage.model_validate_json(raw)
                await self._identity(str(row["credential_hash"]))
                if message.type == "revoke":
                    await self.revoke(int(row["owner_user_id"]), device_id)
                    return
                if message.type == "grants":
                    if row["protocol_version"] != 2 or len({g.id for g in message.grants}) != len(
                        message.grants
                    ):
                        raise _error("invalid_grants")
                    conn.grants = message.grants
                    continue
                if message.type == "heartbeat":
                    await _db(self.repo.touch, device_id, int(time.time()))
                    await conn.send({"type": "heartbeat_ack"})
                elif message.type in {"result", "cancelled"}:
                    if self.execution is not None:
                        await self._receipt(conn, message)
                    future = conn.pending.get(message.call_id or "")
                    if future is None or future.done():
                        continue  # Late results never resolve a different connection/call.
                    if message.type == "result":
                        scope = conn.pending_tasks.get(message.call_id or "")
                        if scope is not None and not scope.valid_grant(conn):
                            future.set_result({"status": "permission_revoked"})
                            continue
                        diagnostic = conn.pending_tools.get(message.call_id or "") == PROBE_TOOL
                        if diagnostic and (
                            not isinstance(message.result, ProbeResult)
                            or message.result.platform != row["platform"]
                            or message.result.protocol_version != row["protocol_version"]
                        ):
                            raise _error("invalid_tool_result")
                        if not diagnostic and not isinstance(message.result, FileResult):
                            raise _error("invalid_tool_result")
                        assert message.result is not None
                        future.set_result(
                            {
                                "status": message.result.status
                                if isinstance(message.result, FileResult)
                                else "completed",
                                "result": message.result.model_dump(),
                            }
                        )
                    else:
                        future.set_result({"status": "cancelled"})
        finally:
            if conn is not None:
                await self._detach(conn)

    async def _detach(self, conn: Connection) -> None:
        device_id = str(conn.row["device_id"])
        if self.execution is not None and conn.generation:
            service = self.execution
            with suppress(Exception):
                await service.io.run(
                    service.repo.disconnect_device,
                    device_id,
                    service.node,
                    service.incarnation,
                    conn.generation,
                )
        if self.connections.get(device_id) is conn:
            del self.connections[device_id]
        for future in conn.pending.values():
            if not future.done():
                future.set_result({"status": "device_disconnected"})
        conn.pending.clear()
        conn.pending_tools.clear()
        conn.pending_tasks.clear()

    async def _receipt(self, conn: Connection, message: DeviceMessage) -> None:
        service = self.execution
        operation = await service.io.run(
            service.repo.device_operation,
            str(conn.row["device_id"]),
            conn.generation,
            message.call_id or "",
        )
        if operation is None:
            return
        if message.type == "result" and not isinstance(message.result, FileResult):
            raise _error("invalid_tool_result")
        result: dict[str, Any]
        if message.type == "cancelled":
            result = {"status": "cancelled"}
        else:
            assert isinstance(message.result, FileResult)
            result = {"status": message.result.status, "result": message.result.model_dump()}
        await service.io.run(
            service.repo.complete_tool,
            operation["run_id"],
            operation["operation_id"],
            device=str(conn.row["device_id"]),
            node=service.node,
            incarnation=service.incarnation,
            generation=conn.generation,
            result=result,
        )

    async def list_devices(self, owner: int) -> list[dict[str, Any]]:
        rows = await _db(self.repo.list_for_owner, owner)
        for row in rows:
            conn = self.connections.get(str(row["device_id"]))
            row["online"] = False
            row["capabilities"] = []
            row["grants"] = []
            if conn is not None:
                try:
                    await self._identity(str(conn.row["credential_hash"]))
                except OctopError:
                    await self._close(conn)
                else:
                    row["online"] = True
                    row["capabilities"] = list(conn.capabilities)
                    row["grants"] = [g.model_dump() for g in conn.grants]
        return rows

    async def probe(self, owner: int, device_id: str, *, timeout: float = 10) -> dict[str, Any]:
        # Ownership applies even to administrators. No caller-selected owner or arbitrary tools.
        owned = await _db(self.repo.list_for_owner, owner)
        if not any(row["device_id"] == device_id for row in owned):
            raise _error("device_not_found", status=404)
        conn = self.connections.get(device_id)
        if conn is None:
            raise _error("device_offline")
        await self._identity(str(conn.row["credential_hash"]))
        if self.connections.get(device_id) is not conn:
            raise _error("device_offline")
        if PROBE_TOOL not in conn.capabilities:
            raise _error("capability_unavailable")
        return await self._call(conn, owner, PROBE_TOOL, {}, timeout=timeout)

    async def bind_task(
        self,
        *,
        owner: int,
        agent_id: str,
        thread_id: str,
        session_key: str,
        selection: DeviceSelection,
        request_id: str | None = None,
    ) -> str:
        conn = self.connections.get(selection.device_id)
        if conn is None or conn.row["owner_user_id"] != owner:
            raise _error("device_offline_or_not_owned")
        await self._identity(str(conn.row["credential_hash"]))
        if "local_files" not in conn.capabilities:
            raise _error("capability_unavailable")
        now = int(time.time())
        self.tasks = {k: t for k, t in self.tasks.items() if t.expires_at > now}
        self.paused = {k: v for k, v in self.paused.items() if v.get(CONFIG_KEY) in self.tasks}
        if request_id:
            for existing in self.tasks.values():
                if (existing.owner, existing.agent_id, existing.thread_id, existing.request_id) != (
                    owner,
                    agent_id,
                    thread_id,
                    request_id,
                ):
                    continue
                if existing.selection != selection or existing.session_key != session_key:
                    raise _error("device_task_identity_conflict", status=409)
                if not existing.valid_grant(conn):
                    raise _error("directory_permission_or_h3c_consent_required")
                return existing.token
        if len(self.tasks) >= 4096:
            raise _error("device_tasks_full", status=429)
        token = secrets.token_hex(32)
        task = DeviceTask(
            token,
            owner,
            agent_id,
            thread_id,
            session_key,
            selection,
            min(now + 3600, conn.row["expires_at"]),
            request_id,
        )
        if not task.valid_grant(conn):
            raise _error("directory_permission_or_h3c_consent_required")
        self.tasks[token] = task
        return token

    async def execute(
        self, task: DeviceTask, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        conn = self.connections.get(task.selection.device_id)
        if conn is None:
            raise _error("device_offline")
        await self._identity(str(conn.row["credential_hash"]))
        if (
            self.connections.get(task.selection.device_id) is not conn
            or "local_files" not in conn.capabilities
            or not task.valid_grant(conn)
            or tool not in FILE_TOOLS
        ):
            raise _error("directory_permission_revoked")
        if tool == "local_write_file" and not any(
            g.id == task.selection.grant_id and g.writable for g in conn.grants
        ):
            raise _error("directory_read_only")
        result = await self._call(conn, task.owner, tool, arguments, task=task, timeout=60)
        return {
            **result,
            "device_id": task.selection.device_id,
            "grant_id": task.selection.grant_id,
            "execution_location": "local",
        }

    async def _call(
        self,
        conn: Connection,
        owner: int,
        tool: str,
        arguments: dict[str, Any],
        *,
        task: DeviceTask | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        if (
            sum(
                len(c.pending) for c in self.connections.values() if c.row["owner_user_id"] == owner
            )
            >= 4
        ):
            raise _error("device_busy", status=429)
        call_id = secrets.token_hex(16)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        conn.pending[call_id] = future
        conn.pending_tools[call_id] = tool
        if task is not None:
            conn.pending_tasks[call_id] = task
        try:
            frame = {
                "type": "call",
                "call_id": call_id,
                "task_id": task.token if task else "probe:" + call_id,
                "device_id": conn.row["device_id"],
                "owner_user_id": owner,
                "tool": tool,
                "expires_at": min(
                    int(time.time() + timeout),
                    conn.row["expires_at"],
                    task.expires_at if task else conn.row["expires_at"],
                ),
                "arguments": arguments,
                **(
                    {
                        "agent_id": task.agent_id,
                        "thread_id": task.thread_id,
                        "grant_id": task.selection.grant_id,
                        "grant_version": task.selection.grant_version,
                    }
                    if task
                    else {}
                ),
            }
            if self.execution is not None and task is not None:
                from octop.infra.execution.service import current_execution

                active = current_execution.get()
                if active is None:
                    raise _error("shared_execution_required")
                service = self.execution
                await service.io.run(
                    service.repo.request_tool,
                    *active.fence,
                    operation=call_id,
                    device=str(conn.row["device_id"]),
                    generation=conn.generation,
                    request=frame,
                )
                await service.io.run(
                    service.repo.send_tool,
                    active.run["run_id"],
                    call_id,
                    device=str(conn.row["device_id"]),
                    node=service.node,
                    incarnation=service.incarnation,
                    generation=conn.generation,
                )
            await conn.send(frame)
            result = await asyncio.wait_for(asyncio.shield(future), timeout)
            return {"call_id": call_id, **result}
        except asyncio.CancelledError:
            with suppress(Exception):
                await conn.send({"type": "cancel", "call_id": call_id})
            raise
        except TimeoutError:
            # Cancellation requested is not proof of completion or rollback.
            with suppress(Exception):
                await conn.send({"type": "cancel", "call_id": call_id})
            return {"call_id": call_id, "status": "timed_out"}
        except (OSError, RuntimeError):
            await self._close(conn, code=1011, reason="device_disconnected")
            return {"call_id": call_id, "status": "device_disconnected"}
        finally:
            conn.pending.pop(call_id, None)
            conn.pending_tools.pop(call_id, None)
            conn.pending_tasks.pop(call_id, None)
            future.cancel()

    async def _close(
        self, conn: Connection, *, code: int = 4001, reason: str = "device_revoked"
    ) -> None:
        await self._detach(conn)
        with suppress(Exception):
            await asyncio.wait_for(conn.socket.close(code=code, reason=reason), 5)

    async def revoke(self, owner: int, device_id: str) -> None:
        if not await _db(self.repo.revoke, device_id, owner, int(time.time())):
            raise _error("device_not_found", status=404)
        conn = self.connections.get(device_id)
        if conn is not None:
            await self._close(conn)

    async def revoke_session(self, session: str) -> None:
        ids = await _db(self.repo.revoke_session, session, int(time.time()))
        for device_id in ids:
            conn = self.connections.get(device_id)
            if conn is not None:
                await self._close(conn)

    async def stop(self) -> None:
        self.closed = True
        self.tasks.clear()
        self.paused.clear()
        for conn in list(self.connections.values()):
            await self._close(conn, code=1012, reason="server_stopping")
