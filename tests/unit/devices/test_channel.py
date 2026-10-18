import asyncio
import json
import time
from contextlib import suppress

import pytest

from octop.infra.devices.manager import _digest
from octop.infra.errors import OctopError


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.closed = False

    async def receive_text(self):
        item = await self.incoming.get()
        if item is None:
            raise EOFError
        return item

    async def send_json(self, data):
        await self.outgoing.put(data)

    async def close(self, code=1000, reason=None):
        self.closed = True
        await self.incoming.put(None)

    async def send(self, value):
        await self.incoming.put(json.dumps(value))

    async def receive(self):
        return await asyncio.wait_for(self.outgoing.get(), 2)


async def open_device(manager, owner, *, platform="darwin", credential=None):
    if credential is None:
        pair = await manager.issue_pairing(owner, "session-alice", int(time.time()) + 600)
        credential = pair["code"]
    socket = Socket()
    await socket.send(
        {
            "type": "hello" if credential.startswith("octop-device.") else "pair",
            "credential": credential,
            "name": "Employee computer",
            "platform": platform,
            "protocol_version": 1,
            "capabilities": ["device_status"],
        }
    )
    task = asyncio.create_task(manager.serve(socket))
    ready = await socket.receive()
    return socket, task, ready


async def finish(task):
    task.cancel()
    with suppress(asyncio.CancelledError, EOFError, OctopError):
        await task


@pytest.mark.parametrize("platform", ["windows", "darwin"])
async def test_pair_probe_and_owner_isolation(stack, platform):
    server, alice, bob, _ = stack
    manager = server.devices
    socket, task, ready = await open_device(manager, alice, platform=platform)
    try:
        assert ready["owner_user_id"] == alice
        assert "credential" not in (await manager.list_devices(alice))[0]
        assert await manager.list_devices(bob) == []
        for fn in (manager.probe, manager.revoke):
            with pytest.raises(OctopError) as exc:
                await fn(bob, ready["device_id"])
            assert exc.value.status == 404  # admin has no employee-device bypass
        call = asyncio.create_task(manager.probe(alice, ready["device_id"]))
        frame = await socket.receive()
        assert frame["owner_user_id"] == alice
        assert frame["tool"] == "device_status"
        await socket.send(
            {
                "type": "result",
                "call_id": frame["call_id"],
                "result": {"platform": platform, "protocol_version": 1},
            }
        )
        assert (await call)["status"] == "completed"
        await socket.send({"type": "heartbeat"})
        assert (await socket.receive())["type"] == "heartbeat_ack"
    finally:
        await finish(task)


async def test_single_use_and_hashed_credentials(stack):
    server, alice, _, _ = stack
    manager = server.devices
    pair = await manager.issue_pairing(alice, "session-alice", int(time.time()) + 600)
    socket, task, ready = await open_device(manager, alice, credential=pair["code"])
    try:
        assert (
            manager.repo.pair(
                _digest(pair["code"]), "replay", "fake", "PC", "darwin", 1, int(time.time())
            )
            is None
        )
        row = manager.repo.authenticate(_digest(ready["credential"]), int(time.time()))
        assert row and row["credential_hash"] != ready["credential"]
        assert "credential" not in row
    finally:
        await finish(task)


async def test_pair_code_replacement_expiry_and_revoked_login(stack):
    server, alice, _, _ = stack
    manager = server.devices
    old = await manager.issue_pairing(alice, "session-alice", int(time.time()) + 600)
    new = await manager.issue_pairing(alice, "session-alice", int(time.time()) + 600)
    assert (
        manager.repo.pair(_digest(old["code"]), "old", "fake", "PC", "darwin", 1, int(time.time()))
        is None
    )
    assert (
        manager.repo.pair(
            _digest(new["code"]), "expired", "fake", "PC", "darwin", 1, int(time.time()) + 121
        )
        is None
    )
    new = await manager.issue_pairing(alice, "session-alice", int(time.time()) + 600)
    server.services.communication_identity_repo.revoke_session(
        "session-alice", int(time.time()) + 600
    )
    assert (
        manager.repo.pair(
            _digest(new["code"]), "revoked", "fake", "PC", "darwin", 1, int(time.time())
        )
        is None
    )


@pytest.mark.parametrize("action", ["revoke", "logout", "disable", "expire"])
async def test_authorization_rechecked_before_calls(stack, action):
    server, alice, _, _ = stack
    manager = server.devices
    socket, task, ready = await open_device(manager, alice)
    try:
        if action == "revoke":
            await manager.revoke(alice, ready["device_id"])
        elif action == "logout":
            await manager.revoke_session("session-alice")
        elif action == "disable":
            server.services.user_repo.set_disabled(alice, True)
        else:
            with server.services.db.connect() as conn:
                conn.execute("UPDATE desktop_devices SET expires_at=0")
        with pytest.raises(OctopError):
            await manager.probe(alice, ready["device_id"])
        assert not (await manager.list_devices(alice))[0]["online"]
        assert socket.closed
    finally:
        await finish(task)


async def test_disconnect_does_not_replay_on_reconnect(stack):
    server, alice, _, _ = stack
    manager = server.devices
    socket, task, ready = await open_device(manager, alice)
    call = asyncio.create_task(manager.probe(alice, ready["device_id"]))
    await socket.receive()
    await finish(task)
    assert (await call)["status"] == "device_disconnected"
    replacement, new_task, _ = await open_device(manager, alice, credential=ready["credential"])
    try:
        assert replacement.outgoing.empty()
        assert not manager.connections[ready["device_id"]].pending
    finally:
        await finish(new_task)


async def test_timeout_and_cancellation_are_forwarded(stack):
    server, alice, _, _ = stack
    manager = server.devices
    socket, task, ready = await open_device(manager, alice)
    try:
        result = await manager.probe(alice, ready["device_id"], timeout=0.01)
        assert result["status"] == "timed_out"
        await socket.receive()
        assert (await socket.receive())["type"] == "cancel"
        call = asyncio.create_task(manager.probe(alice, ready["device_id"]))
        await socket.receive()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert (await socket.receive())["type"] == "cancel"
        assert not manager.connections[ready["device_id"]].pending
    finally:
        await finish(task)


async def test_two_devices_and_forged_result_do_not_cross_calls(stack):
    server, alice, _, _ = stack
    manager = server.devices
    first, task1, ready1 = await open_device(manager, alice)
    second, task2, _ = await open_device(manager, alice)
    try:
        call = asyncio.create_task(manager.probe(alice, ready1["device_id"]))
        frame = await first.receive()
        reply = {
            "type": "result",
            "call_id": frame["call_id"],
            "result": {"platform": "darwin", "protocol_version": 1},
        }
        await second.send(reply)
        await second.send({"type": "heartbeat"})
        await second.receive()
        assert not call.done()
        await first.send(reply)
        assert (await call)["status"] == "completed"
    finally:
        await finish(task1)
        await finish(task2)


async def test_per_employee_inflight_limit(stack):
    server, alice, _, _ = stack
    manager = server.devices
    socket, task, ready = await open_device(manager, alice)
    calls = []
    try:
        for _ in range(4):
            calls.append(asyncio.create_task(manager.probe(alice, ready["device_id"])))
            await socket.receive()
        with pytest.raises(OctopError) as exc:
            await manager.probe(alice, ready["device_id"])
        assert exc.value.status == 429
    finally:
        await finish(task)
        await asyncio.gather(*calls)
