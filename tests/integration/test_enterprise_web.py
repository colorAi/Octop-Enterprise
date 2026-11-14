"""Two independent edge apps share durable state and freshly read authorization."""

from dataclasses import replace
from types import SimpleNamespace

import httpx

from octop.api.enterprise import build_web_app
from octop.infra.users.manager import UserManager
from octop.infra.utils.bounded_io import BoundedIO


async def test_web_forwarder_allows_one_hundred_simultaneous_http_streams(env):
    import asyncio

    _, server, headers = env
    entered = asyncio.Queue()
    release = asyncio.Event()

    async def upstream(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n")
            await writer.drain()
            entered.put_nowait(True)
            await release.wait()
            writer.write(b"ok")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    listener = await asyncio.start_server(upstream, "127.0.0.1", 0)
    origin = f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    repo = server.services.execution_repo
    repo.configure_topology({"worker": origin})
    repo.register("worker", "boot", "worker")
    app = build_web_app(server, origin)
    requests = []
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://edge", timeout=10
        ) as edge:
            try:
                for _ in range(100):
                    requests.append(asyncio.create_task(edge.get("/api/agents", headers=headers)))
                    await asyncio.wait_for(entered.get(), timeout=5)
                assert not any(request.done() for request in requests)
            finally:
                release.set()
                responses = await asyncio.gather(*requests)
            assert all(
                response.status_code == 200 and response.text == "ok" for response in responses
            )
    finally:
        release.set()
        listener.close()
        await listener.wait_closed()
        await app.state.runtime_client.aclose()


async def test_company_device_requests_route_by_employee_credential_without_jwt(env):
    import hashlib
    import time

    _, server, headers = env
    repo = server.services.execution_repo
    repo.configure_topology({"worker": "http://worker:8088", "worker2": "http://worker2:8088"})
    repo.register("worker", "boot", "worker")
    repo.register("worker2", "boot", "worker")
    users = [
        server.services.user_repo.create(username=f"native-{i}", role="user") for i in range(2)
    ]
    owner = next(uid for uid in users if repo.worker_for(uid) == "worker2")
    devices = server.services.device_repo
    now = int(time.time())
    credential = "octop-device.native-routing-test"
    devices.issue_code("native-code", owner, "session", now + 100, now + 3600)
    devices.pair(
        "native-code",
        "native-device",
        hashlib.sha256(credential.encode()).hexdigest(),
        "Desktop",
        "darwin",
        2,
        now,
    )
    calls = []

    async def remote(request):
        calls.append(request)
        assert request.url.host == "worker2"
        assert request.headers["x-octop-device-credential"] == credential
        return httpx.Response(200, stream=httpx.ByteStream(b"{}"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    try:
        app = build_web_app(server, "http://fallback.invalid", runtime_client=client)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://edge"
        ) as edge:
            native = {"X-Octop-Device-Credential": credential}
            assert (
                await edge.get("/api/company-runtime/capabilities", headers=native)
            ).status_code == 200
            # Neither an administrator JWT nor a caller-supplied agent query
            # can override the owner of a native execution credential.
            assert (
                await edge.post(
                    "/api/company-runtime/tasks?agent_id=unrelated",
                    headers={**headers, **native},
                    json={},
                )
            ).status_code == 200
            assert (
                await edge.post("/api/company-runtime/tasks/task/model", headers=native, json={})
            ).status_code == 200
            assert [call.extensions["timeout"] for call in calls] == [
                client.timeout.as_dict(),
                client.timeout.as_dict(),
                {**client.timeout.as_dict(), "read": 600},
            ]
            assert (
                await edge.get(
                    "/api/company-runtime/capabilities",
                    headers={**native, "Origin": "http://browser"},
                )
            ).status_code == 403
            devices.revoke("native-device", owner, now)
            assert (
                await edge.get("/api/company-runtime/capabilities", headers=native)
            ).status_code == 401
            assert len(calls) == 3
    finally:
        await client.aclose()


async def test_edges_cancel_and_replay_same_run_without_runtime(env):
    _, server, headers = env
    services = server.services
    owner = services.user_repo.get_by_username("admin").id
    services.agent_repo.create(agent_id="edge-agent", user_id=owner, name="Edge")
    services.thread_repo.insert(
        thread_id="edge-thread",
        agent_id="edge-agent",
        user_id=owner,
        channel_type="dashboard",
        session_key="edge-thread",
    )
    repo = services.execution_repo
    run = repo.submit(
        user=owner,
        agent="edge-agent",
        thread="edge-thread",
        operation="chat",
        key="edge-request",
        request={"private_input": "never return this in status"},
    )
    edges = []
    for _ in range(2):
        manager = UserManager(services)
        edge = SimpleNamespace(
            _started=True,
            services=services,
            management_io=BoundedIO(workers=2, capacity=8),
            user_manager=manager,
            app_runtime=SimpleNamespace(user_manager=manager),
            database_bound=True,
        )
        edges.append(build_web_app(edge, "http://worker.invalid:8088"))
    try:
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=edges[0]), base_url="http://edge"
            ) as first,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=edges[1]), base_url="http://edge"
            ) as second,
        ):
            assert (await first.get("/api/users/page", headers=headers)).status_code == 200
            status = await second.get(f"/api/executions/{run['run_id']}", headers=headers)
            assert status.status_code == 200 and "private_input" not in status.text
            response = await first.post(
                f"/api/executions/{run['run_id']}/cancel",
                headers=headers,
                json={"request_id": "cancel"},
            )
            assert response.status_code == 200
            replay = (
                await second.get(f"/api/executions/{run['run_id']}/events?after=1", headers=headers)
            ).json()
            assert replay["state"] == "cancelled"
            assert replay["events"][-1]["payload"]["state"] == "cancelled"
            services.user_repo.set_disabled(owner, True)
            assert (
                await second.get(f"/api/executions/{run['run_id']}", headers=headers)
            ).status_code in {401, 403}
    finally:
        for app in edges:
            await app.state.runtime_client.aclose()


async def test_runtime_forwarding_preserves_auth_and_does_not_retry(env):
    _, server, headers = env
    calls = []

    async def remote(request):
        calls.append(request)
        assert request.headers["authorization"] == headers["Authorization"]
        return httpx.Response(
            503, stream=httpx.ByteStream(b"unavailable"), headers={"Retry-After": "1"}
        )

    # The edge deliberately has no retry loop for an uncertain POST.
    client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    app = build_web_app(server, "http://worker.invalid:8088", runtime_client=client)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://edge"
        ) as edge:
            response = await edge.post("/api/runtime-check", headers=headers, json={"test": True})
            assert response.status_code == 503 and len(calls) == 1
    finally:
        await client.aclose()


async def test_web_forwarder_rejects_oversized_chunked_upload_with_413(env, monkeypatch):
    _, server, headers = env
    monkeypatch.setattr(server, "config", replace(server.config, max_upload_mb=1))
    calls = []

    async def remote(request):
        calls.append(request.content)
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    async def chunks(sizes):
        for size in sizes:
            yield b"x" * size

    limit = server.config.max_upload_bytes
    async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as upstream:
        app = build_web_app(server, "http://worker.invalid", runtime_client=upstream)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://edge"
        ) as edge:
            for content in (chunks([limit, 1]), b"x" * (limit + 1)):
                response = await edge.post("/api/runtime-check", headers=headers, content=content)
                assert response.status_code == 413
                assert response.json() == {"detail": "request_too_large"}
                assert "Retry-After" not in response.headers
            assert calls == []
            response = await edge.post(
                "/api/runtime-check", headers=headers, content=chunks([limit // 2, limit // 2])
            )
            assert response.status_code == 200
            assert [len(body) for body in calls] == [limit]


async def test_native_runtime_request_gets_retryable_response_when_read_queue_is_full(
    env, monkeypatch
):
    import asyncio
    from threading import Event

    worker, server, _ = env
    reader = BoundedIO(workers=1, capacity=1)
    monkeypatch.setattr(server, "management_io", reader)
    entered = asyncio.Event()
    release = Event()
    loop = asyncio.get_running_loop()

    def hold_read():
        loop.call_soon_threadsafe(entered.set)
        release.wait(5)

    async def unexpected_upstream(_request):
        raise AssertionError("an overloaded edge must respond before forwarding")

    busy = asyncio.create_task(reader.run(hold_read))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(unexpected_upstream)
        ) as upstream:
            app = build_web_app(server, "http://worker.invalid", runtime_client=upstream)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://edge"
            ) as edge:
                with monkeypatch.context() as sharded:
                    sharded.setattr(server, "runtime_shards", SimpleNamespace())
                    for client in (edge, worker):
                        response = await client.get(
                            "/api/company-runtime/capabilities",
                            headers={"X-Octop-Device-Credential": "octop-device.overload-test"},
                        )
                        assert response.status_code == 503
                        assert response.json()["error"]["details"] == {
                            "reason": "read_capacity_exceeded",
                            "retryable": True,
                        }
    finally:
        release.set()
        await busy


async def test_edges_route_employee_workspaces_to_owner_and_global_writes_to_primary(env):
    _, server, headers = env
    repo = server.services.execution_repo
    repo.configure_topology({"worker": "http://worker:8088", "worker2": "http://worker2:8088"})
    repo.register("worker", "boot", "worker")
    repo.register("worker2", "boot", "worker")
    users = [server.services.user_repo.create(username=f"route-{i}", role="user") for i in range(2)]
    owner = next(user for user in users if repo.worker_for(user) == "worker2")
    server.services.agent_repo.create(agent_id="remote-agent", user_id=owner, name="Remote")
    calls = []

    async def remote(request):
        calls.append((request.url.host, request.method, request.url.path))
        return httpx.Response(200, stream=httpx.ByteStream(b"{}"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    try:
        for _ in range(2):
            app = build_web_app(server, "http://fallback.invalid", runtime_client=client)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://edge"
            ) as edge:
                response = await edge.put(
                    "/api/agents/remote-agent/workspace/file?path=test.txt",
                    headers=headers,
                    json={"content": "test"},
                )
                assert response.status_code == 200
                assert calls[-1][0] == "worker2"
                assert (
                    await edge.patch(
                        "/api/plugins/agents/remote-agent/tools", headers=headers, json={}
                    )
                ).status_code == 200
                assert calls[-1][0] == "worker2"
                assert (
                    await edge.post("/api/admin/providers", headers=headers, json={})
                ).status_code == 200
                assert calls[-1][0] == "worker"
        repo.drain("worker2", "boot")
        before = len(calls)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://edge"
        ) as edge:
            assert (
                await edge.put("/api/agents/remote-agent/workspace/file", headers=headers, json={})
            ).status_code == 503
        assert len(calls) == before
    finally:
        await client.aclose()


async def test_colleague_http_bypasses_unavailable_expert_workers_and_keeps_auth(env, monkeypatch):
    from unittest.mock import AsyncMock

    _, server, headers = env
    owner = server.services.user_repo.get_by_username("admin").id
    server.services.execution_repo.configure_topology({"worker": "http://offline:8088"})
    proxy = AsyncMock(
        return_value=httpx.Response(
            200,
            stream=httpx.ByteStream(b'{"data":{"items":[]}}'),
            headers={"content-type": "application/json"},
        )
    )
    monkeypatch.setattr(server, "communication", SimpleNamespace(proxy=proxy, stop=AsyncMock()))

    async def blocked_worker(request):
        raise AssertionError("colleague messaging must not contact an expert worker")

    client = httpx.AsyncClient(transport=httpx.MockTransport(blocked_worker))
    app = build_web_app(server, "http://offline:8088", runtime_client=client)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://edge"
        ) as edge:
            assert (await edge.get("/api/im/contacts")).status_code == 401
            response = await edge.get("/api/im/contacts", headers=headers)
            assert response.status_code == 200
            assert proxy.call_args.kwargs["user_id"] == owner
            assert proxy.call_args.kwargs["path"] == "/contacts"
            server.services.user_repo.set_disabled(owner, True)
            assert (await edge.get("/api/im/contacts", headers=headers)).status_code in {401, 403}
            assert proxy.await_count == 1
    finally:
        await client.aclose()


async def test_colleague_realtime_uses_local_one_use_ticket_handler(env, monkeypatch):
    from unittest.mock import AsyncMock

    import pytest
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from octop.infra.errors import ErrorCode, OctopError

    _, server, _ = env
    consume = AsyncMock(side_effect=OctopError(ErrorCode.AUTH_FAILED, "expired ticket"))
    monkeypatch.setattr(
        server, "communication", SimpleNamespace(consume_ticket=consume, stop=AsyncMock())
    )
    app = build_web_app(server, "http://offline:8088")
    try:
        with TestClient(app) as client:
            with (
                pytest.raises(WebSocketDisconnect) as exc,
                client.websocket_connect(
                    "/api/im/realtime?ticket=one-use", headers={"origin": "http://testserver"}
                ),
            ):
                pass
            assert exc.value.code == 1008
            consume.assert_awaited_once_with("one-use")
    finally:
        await app.state.runtime_client.aclose()
