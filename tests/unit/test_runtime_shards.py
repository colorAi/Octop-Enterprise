import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from octop.infra.db.repos.execution import ExecutionConflict
from octop.infra.execution.service import ExecutionService
from octop.infra.execution.shards import RuntimeShards, worker_topology
from tests.unit.agents.test_agent_registry import _make_registry, _make_services


@pytest.fixture
def shards(tmp_path):
    services = _make_services(tmp_path)
    repo = services.execution_repo
    repo.configure_sandbox_budget(total=512 * 1024**2, sandbox=256 * 1024**2)
    repo.configure_topology({"worker": "http://worker:8088", "worker2": "http://worker2:8088"})
    for node in ("worker", "worker2"):
        repo.register(node, "boot", "worker", container_id=node)
    users = [services.user_repo.create(username=f"shard-{i}", role="user") for i in range(4)]
    for user in users:
        services.agent_repo.create(agent_id=str(user), user_id=user, name=str(user))
        services.thread_repo.insert(
            thread_id=str(user),
            agent_id=str(user),
            user_id=user,
            channel_type="dashboard",
            session_key=str(user),
        )
    yield services, repo, users
    services.db.close()


def submit(repo, user, **kwargs):
    return repo.submit(
        user=user,
        agent=str(user),
        thread=str(user),
        operation="chat",
        key=str(user),
        request=kwargs,
    )


def stop(repo, node):
    repo.record_stop(
        node, "boot", {"container_id": node, "running": False, "finished_at": "verified"}
    )


def test_user_shards_route_claim_and_fence_the_same_owner(shards):
    _, repo, users = shards
    repo.configure(running=4, models=4, queued=16, user_queued=8)
    for user in users:
        submit(repo, user)
        node = repo.worker_for(user)
        assert repo.route(user) == f"http://{node}:8088"
        other = "worker2" if node == "worker" else "worker"
        with pytest.raises(ExecutionConflict, match="owner_mismatch"):
            repo.own_user(user, other, "boot")
    claims = [repo.claim(node, "boot") for node in ("worker", "worker2", "worker", "worker2")]
    assert {run["user_id"] for run in claims} == set(users)
    assert all(run["node_id"] == repo.worker_for(run["user_id"]) for run in claims)
    assert repo.claim("worker", "boot") is None


def test_topology_changes_require_all_stop_proofs_and_preserve_unknown(shards):
    _, repo, users = shards
    submit(repo, users[0])
    run = repo.claim("worker", "boot")
    updated = {"worker": "http://worker:8088"}
    with pytest.raises(ExecutionConflict, match="stop_unconfirmed"):
        repo.configure_topology(updated)
    stop(repo, "worker")
    with pytest.raises(ExecutionConflict, match="stop_unconfirmed"):
        repo.configure_topology(updated)
    stop(repo, "worker2")
    repo.configure_topology(updated)
    repo.register("worker", "next", "worker", container_id="new")
    assert repo.get(run["run_id"])["state"] == "unknown"
    assert repo.claim("worker", "next") is None
    assert repo.active(str(users[0]))["run_id"] == run["run_id"]


def test_admin_and_anonymous_routes_use_coordinator_and_unavailable_is_not_rerouted(shards):
    services, repo, users = shards
    admin = services.user_repo.create(username="admin", role="admin")
    assert repo.worker_for(admin) == repo.worker_for(None) == "worker"
    repo.drain("worker2", "boot")
    with pytest.raises(ExecutionConflict, match="fenced"):
        repo.route(users[1])
    assert repo.route(users[0]) == "http://worker:8088"


def test_role_change_handoff_requires_stop_proof_even_without_topology_change(shards):
    services, repo, users = shards
    user = users[1]
    repo.own_user(user, "worker2", "boot")
    services.user_repo.set_role(user, "admin")
    assert repo.worker_for(user) == "worker"
    repo.configure_topology(repo.topology())
    with pytest.raises(ExecutionConflict, match="stop_unconfirmed"):
        repo.own_user(user, "worker", "boot")
    for node in ("worker", "worker2"):
        stop(repo, node)
    repo.configure_topology(repo.topology())
    for node in ("worker", "worker2"):
        repo.register(node, "new-boot", "worker", container_id=node + "-new")
    repo.own_user(user, "worker", "new-boot")


def test_other_shard_queue_cannot_hide_eligible_work(shards):
    services, repo, users = shards
    repo.configure(running=4, models=4, queued=128, user_queued=64)
    for i in range(40):
        thread = f"queue-{i}"
        services.thread_repo.insert(
            thread_id=thread,
            agent_id=str(users[0]),
            user_id=users[0],
            channel_type="dashboard",
            session_key=thread,
        )
        repo.submit(
            user=users[0],
            agent=str(users[0]),
            thread=thread,
            operation="chat",
            key=thread,
            request={},
        )
    submit(repo, users[1])
    assert repo.claim("worker2", "boot")["user_id"] == users[1]


@pytest.mark.asyncio
async def test_independent_workers_share_global_capacity_and_never_duplicate(shards):
    _, repo, users = shards
    release, both = asyncio.Event(), asyncio.Event()
    seen = []

    async def handler(run, decision):
        seen.append((run["run_id"], run["node_id"]))
        if len(seen) == 2:
            both.set()
        await release.wait()
        yield {"type": "done"}

    workers = [
        ExecutionService(repo, node=node, incarnation="boot", role="worker", capacity=2)
        for node in ("worker", "worker2")
    ]
    for service in workers:
        service.handler = handler
    for user in users:
        submit(repo, user)
    try:
        await asyncio.gather(*(worker.start() for worker in workers))
        await asyncio.wait_for(both.wait(), 5)
        await asyncio.sleep(0.15)
        assert len(seen) == 2
        assert {node for _, node in seen} == {"worker", "worker2"}
        release.set()
        for _ in range(100):
            if len(seen) == 4 and all(
                repo.by_request(user=u, operation="chat", key=str(u))["state"] == "completed"
                for u in users
            ):
                break
            await asyncio.sleep(0.05)
        assert len({run for run, _ in seen}) == 4
    finally:
        release.set()
        await asyncio.gather(*(worker.close() for worker in workers))


def runtime(services, repo, user):
    registry = _make_registry(services)
    service = ExecutionService(repo, node=repo.worker_for(user), incarnation="boot", role="worker")
    server = SimpleNamespace(
        execution=service, app_runtime=SimpleNamespace(agent_registry=registry)
    )
    control = RuntimeShards(server)
    registry.runtime_shards = control
    registry.runtime_last_used[str(user)] = time.monotonic() - 600
    backend = Mock()
    registry._isolated_backends[json.dumps([{"agent_id": str(user)}])] = backend
    return registry, service, control, backend


@pytest.mark.asyncio
async def test_idle_expert_read_reloads_only_its_own_enabled_runtime(shards, monkeypatch):
    services, repo, users = shards
    user = users[1]
    agent = str(user)
    registry, _, _, _ = runtime(services, repo, user)
    services.agent_repo.set_state(agent, "running")
    loaded = {}
    harness = SimpleNamespace()
    monkeypatch.setattr(registry, "_octop_harness_or_none", lambda aid: loaded.get(aid))
    monkeypatch.setattr(registry, "get_agent", lambda aid: loaded[aid])

    async def start(aid):
        loaded[aid] = harness

    starter = AsyncMock(side_effect=start)
    monkeypatch.setattr(registry, "start", starter)
    assert await registry.ensure_runtime(agent) is harness
    assert await registry.ensure_runtime(agent) is harness
    starter.assert_awaited_once_with(agent)
    with pytest.raises(ExecutionConflict, match="owner_mismatch"):
        await registry.ensure_runtime(str(users[0]))
    assert starter.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state,enabled", [("stopped", 1), ("failed", 1), ("running", 0)])
async def test_idle_read_does_not_restart_stopped_failed_or_disabled_expert(
    shards, monkeypatch, state, enabled
):
    from octop.infra.errors import OctopError

    services, repo, users = shards
    agent = str(users[1])
    registry, _, _, _ = runtime(services, repo, users[1])
    with services.db.transaction() as conn:
        conn.execute(
            "UPDATE agents SET last_state=?,enabled=? WHERE agent_id=?", (state, enabled, agent)
        )
    monkeypatch.setattr(registry, "_octop_harness_or_none", lambda _: None)
    starter = AsyncMock()
    monkeypatch.setattr(registry, "start", starter)
    with pytest.raises(OctopError):
        await registry.ensure_runtime(agent)
    starter.assert_not_awaited()


@pytest.mark.asyncio
async def test_sandbox_denial_keeps_agent_enabled_and_reload_pending(shards, monkeypatch):
    services, repo, users = shards
    agent = str(users[0])
    services.agent_repo.set_state(agent, "running", error=None)
    registry = _make_registry(services)
    registry._harness_manager.shared_factory = Mock()
    monkeypatch.setattr(registry, "_octop_harness_or_none", lambda _: None)
    monkeypatch.setattr(
        registry,
        "_agent_runtime_bundle",
        Mock(side_effect=ExecutionConflict("sandbox_memory_exhausted")),
    )
    for operation in (registry.start, registry._reload_agent):
        with pytest.raises(ExecutionConflict, match="sandbox_memory_exhausted"):
            await operation(agent)
        row = services.agent_repo.get(agent)
        assert row.enabled and row.last_state == "running"
    assert agent in registry._runtime_config_dirty


@pytest.mark.asyncio
async def test_idle_unloads_without_stopping_agent_or_deleting_workspace(shards):
    services, repo, users = shards
    registry, service, control, backend = runtime(services, repo, users[0])
    original = services.agent_repo.get(str(users[0]))
    try:
        async with control.activity(users[0]):
            assert await control.reap() == []
        assert await control.reap() == [str(users[0])]
        backend.hibernate.assert_called_once()
        backend.destroy.assert_not_called()
        assert registry.runtime_last_used == {}
        assert services.agent_repo.get(str(users[0])).last_state == original.last_state
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "busy",
    [None, "pinned", "queued", "running", "awaiting_approval", "cancel_requested", "unknown"],
)
async def test_memory_pressure_on_other_worker_reclaims_only_quiescent_runtime(shards, busy):
    services, repo, users = shards
    registry, service, control, backend = runtime(services, repo, users[0])
    memory = 256 * 1024**2
    token = repo.reserve_sandbox("worker", "boot", str(users[0]), "one", memory)
    repo.reserve_sandbox("worker2", "boot", str(users[1]), "two", memory)
    assert repo.reserve_sandbox("worker2", "boot", str(users[3]), "three", memory) is None
    registry.runtime_last_used[str(users[0])] = time.monotonic()
    backend.hibernate.side_effect = lambda **_: repo.release_sandbox("worker", "boot", "one", token)
    if busy == "pinned":
        control._pins[users[0]] = 1
    elif busy is not None:
        run = submit(repo, users[0])
        if busy != "queued":
            run = repo.claim("worker", "boot")
            if busy == "cancel_requested":
                repo.command(run["run_id"], user=users[0], key="cancel", kind="cancel", payload={})
            elif busy != "running":
                repo.transition(
                    run["run_id"],
                    "worker",
                    "boot",
                    run["token"],
                    busy,
                    approval_id="approval" if busy == "awaiting_approval" else None,
                )
    try:
        assert await control.reap() == ([] if busy else [str(users[0])])
        assert repo.sandbox_budget()["used_bytes"] == (2 if busy else 1) * memory
        if not busy:
            assert repo.reserve_sandbox("worker2", "boot", str(users[3]), "three", memory)
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["queued", "running", "awaiting_approval", "cancel_requested", "unknown"]
)
async def test_unresolved_runs_are_never_reclaimed(shards, state):
    services, repo, users = shards
    registry, service, control, backend = runtime(services, repo, users[0])
    run = submit(repo, users[0])
    if state != "queued":
        run = repo.claim("worker", "boot")
        fence = (run["run_id"], "worker", "boot", run["token"])
        if state == "cancel_requested":
            repo.command(run["run_id"], user=users[0], key="cancel", kind="cancel", payload={})
        elif state != "running":
            repo.transition(
                *fence, state, approval_id="approval" if state == "awaiting_approval" else None
            )
    try:
        assert await control.reap() == []
        backend.hibernate.assert_not_called()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_reclamation_waits_for_background_work_and_new_access(shards):
    services, repo, users = shards
    registry, service, control, backend = runtime(services, repo, users[0])
    agent = str(users[0])
    try:
        registry._active_invocations[agent] = 1
        assert await control.reap() == []
        registry._active_invocations.clear()
        assert registry.try_begin_history_backfill(agent)
        assert await control.reap() == []
        registry.end_history_backfill(agent)
        registry.runtime_last_used[agent] = time.monotonic()
        assert await control.reap() == []
        backend.hibernate.assert_not_called()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_failed_sandbox_stop_retains_handle_and_does_not_report_reclaimed(shards):
    services, repo, users = shards
    registry, service, control, backend = runtime(services, repo, users[0])
    backend.hibernate.side_effect = RuntimeError("stop unconfirmed")
    try:
        with pytest.raises(RuntimeError, match="unconfirmed"):
            await control.reap()
        assert registry._isolated_backends
        assert str(users[0]) in registry.runtime_last_used
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_switching_experts_reclaims_same_users_idle_runtime_and_retries(shards):
    services, repo, users = shards
    registry, service, control, backend = runtime(services, repo, users[0])
    services.agent_repo.create(agent_id="next-expert", user_id=users[0], name="Next")
    memory = 256 * 1024**2
    token = repo.reserve_sandbox("worker", "boot", str(users[0]), "old", memory)
    repo.reserve_sandbox("worker2", "boot", str(users[1]), "other-worker", memory)
    backend.hibernate.side_effect = lambda **_: repo.release_sandbox("worker", "boot", "old", token)

    async def open_next():
        result = repo.reserve_sandbox("worker", "boot", "next-expert", "next", memory)
        if result is None:
            raise ExecutionConflict("sandbox_memory_exhausted")
        return result

    try:
        async with control.activity(users[0], "next-expert"):
            assert await control.retry_sandbox(open_next, timeout=1)
        backend.hibernate.assert_called_once_with(idle=True)
        assert repo.sandbox_budget()["used_bytes"] == 2 * memory
        assert services.agent_repo.get(str(users[0])) is not None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_agent_pin_preserves_current_expert_during_pressure(shards):
    services, repo, users = shards
    registry, service, control, backend = runtime(services, repo, users[0])
    memory = 256 * 1024**2
    repo.reserve_sandbox("worker", "boot", str(users[0]), "old", memory)
    repo.reserve_sandbox("worker2", "boot", str(users[1]), "other-worker", memory)
    assert repo.reserve_sandbox("worker2", "boot", str(users[3]), "next", memory) is None
    try:
        async with control.activity(users[0], str(users[0])):
            assert await control.reap() == []
        backend.hibernate.assert_not_called()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_im_media_reopens_workspace_after_eviction_and_pins_inflight_io(shards):
    from octop.infra.gateway.media.ingress import RuntimeMediaBackend

    services, repo, users = shards
    registry, service, control, backend = runtime(services, repo, users[0])
    first, second = Mock(), Mock()
    first.aupload_bytes = AsyncMock()
    second.adownload_bytes = AsyncMock(return_value=b"saved attachment")
    registry.workspace_for_agent = Mock(side_effect=[first, second])
    media = RuntimeMediaBackend(registry, str(users[0]))

    async def upload(*_):
        assert await control.reap() == []

    first.aupload_bytes.side_effect = upload
    try:
        await media.save(b"saved attachment", "file.txt")
        assert await control.reap() == [str(users[0])]
        assert await media.read("file.txt") == b"saved attachment"
        assert registry.workspace_for_agent.call_count == 2
        first.adownload_bytes.assert_not_called()
        backend.hibernate.assert_called_once()
        assert media.get_local_path("file.txt") is None
        repo.drain(service.node, service.incarnation)
        with pytest.raises(ExecutionConflict, match="fenced"):
            await media.read("file.txt")
        assert registry.workspace_for_agent.call_count == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_http_stream_and_background_work_hold_runtime_pin(shards):
    from starlette.background import BackgroundTask
    from starlette.responses import StreamingResponse

    from octop.api.middleware.runtime_activity import RuntimeActivityMiddleware

    services, repo, users = shards
    registry, service, control, backend = runtime(services, repo, users[0])

    async def background():
        assert await control.reap() == []

    async def content():
        assert await control.reap() == []
        yield b"workspace bytes"

    app = StreamingResponse(content(), background=BackgroundTask(background))
    server = SimpleNamespace(runtime_shards=control, services=services, management_io=service.io)
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/api/agents/{users[0]}/workspace/download",
        "query_string": b"",
        "headers": [],
        "asgi": {"spec_version": "2.4"},
        "state": {"octop_user": SimpleNamespace(id=users[0])},
    }
    try:
        await RuntimeActivityMiddleware(app, server=server)(scope, AsyncMock(), AsyncMock())
        assert await control.reap() == [str(users[0])]
        backend.hibernate.assert_called_once()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 400])
async def test_successful_global_plugin_change_refreshes_other_worker_registry(shards, status):
    from starlette.responses import Response

    from octop.api.middleware.runtime_activity import RuntimeActivityMiddleware

    services, repo, users = shards
    registry, service, control, _ = runtime(services, repo, users[0])
    _, other_service, other, _ = runtime(services, repo, users[1])
    other_registry = SimpleNamespace(
        on_provider_changed=AsyncMock(), invalidate_mcp_tool_cache=Mock(), harness_manager=None
    )
    other.server.services = services
    other.server.plugin_manager = Mock()
    other.server.app_runtime.agent_registry = other_registry
    other._revision = repo.runtime_revision()
    server = SimpleNamespace(runtime_shards=control, services=services, management_io=service.io)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/plugins/reload",
        "query_string": b"",
        "headers": [],
        "state": {"octop_user": SimpleNamespace(id=users[0])},
    }
    try:
        await RuntimeActivityMiddleware(Response(status_code=status), server=server)(
            scope, AsyncMock(), AsyncMock()
        )
        await other.refresh_configuration()
        await other.refresh_configuration()
        if status == 200:
            other.server.plugin_manager.load_installed.assert_called_once_with(install_deps=False)
            other_registry.on_provider_changed.assert_awaited_once()
        else:
            other.server.plugin_manager.load_installed.assert_not_called()
            other_registry.on_provider_changed.assert_not_awaited()
    finally:
        await service.close()
        await other_service.close()


@pytest.mark.parametrize(
    "value",
    [
        "[]",
        "{}",
        '{"w":"http://user:secret@worker"}',
        '{"w":"file:///tmp"}',
        '{"w":"http://worker/path"}',
        '{"w":"http://worker","x":"http://worker"}',
        '{"w":"http://worker/","x":"http://worker"}',
        '{"w":42}',
    ],
)
def test_topology_rejects_non_origin_or_duplicate_destinations(monkeypatch, value):
    monkeypatch.setenv("OCTOP_WORKER_URLS", value)
    with pytest.raises(ValueError):
        worker_topology()
