import asyncio
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.execution import ExecutionConflict
from octop.infra.execution.service import ExecutionService, current_execution
from octop.infra.execution.stop_verifier import confirm_container_stopped
from tests.unit.db.test_execution import seed_execution, submit


@pytest.fixture
def setup(tmp_path):
    pool = SqlitePool(tmp_path / "execution.db")
    run_migrations(pool)
    repo, user = seed_execution(pool)
    yield pool, repo, user
    pool.close()


@pytest.mark.asyncio
async def test_resource_save_cancels_a_different_workers_live_stream(setup):
    from octop.infra.users.employee_resources import EmployeeResources
    from octop.infra.utils.bounded_io import BoundedIO

    _, repo, user = setup
    worker = ExecutionService(repo, node="worker2", incarnation="boot2", role="worker")
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def handler(run, decision):
        started.set()
        yield {"type": "token", "text": "before revocation"}
        await cancelled.wait()
        yield {"type": "done"}

    worker.handler = handler
    worker.cancel = lambda agent, thread: cancelled.set()
    run = submit(repo, user)
    admin = SimpleNamespace(execution=SimpleNamespace(repo=repo), app_runtime=None)
    io = BoundedIO()
    resources = SimpleNamespace(server=admin, _read=io.run)
    await worker.start()
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert repo.get(run["run_id"])["node_id"] == "worker2"
        await EmployeeResources.cancel_streams(resources, [user])
        await asyncio.wait_for(cancelled.wait(), 5)

        async def settled():
            return [event async for event in worker.replay(run["run_id"], user=user)]

        await asyncio.wait_for(settled(), 5)
        assert repo.get(run["run_id"])["state"] == "cancelled"
        assert repo.active("one") is None
    finally:
        cancelled.set()
        await worker.close()
        await io.drain()


@pytest.mark.asyncio
async def test_done_is_not_visible_until_history_and_terminal_commit(setup):
    _, repo, user = setup
    service = ExecutionService(repo, node="worker", incarnation="boot1", role="worker")
    release = asyncio.Event()
    reached = asyncio.Event()
    executions = []
    from contextvars import ContextVar

    scope = ContextVar("test_stream_scope", default=None)

    async def handler(run, decision):
        executions.append(run["run_id"])
        assert current_execution.get().run["user_id"] == user
        token = scope.set(run["run_id"])
        try:
            yield {"type": "token", "text": "hello"}
            assert scope.get() == run["run_id"]
            yield {"type": "done"}
            reached.set()
            await release.wait()  # Final history projection is still in progress.
        finally:
            scope.reset(token)

    service.handler = handler
    run = submit(repo, user)
    await service.start()
    try:
        await asyncio.wait_for(reached.wait(), 5)
        assert repo.get(run["run_id"])["state"] == "running"
        assert not any(
            f["type"] == "done"
            for e in repo.events(run["run_id"], user=user)
            for f in e["payload"].get("frames", [])
        )
        release.set()
        events = [event async for event in service.replay(run["run_id"], user=user)]
        assert repo.get(run["run_id"])["state"] == "completed"
        assert events[-1]["payload"]["frames"] == [{"type": "done"}]
        assert submit(repo, user)["run_id"] == run["run_id"]
        assert executions == [run["run_id"]]
    finally:
        release.set()
        await service.close()


@pytest.mark.asyncio
async def test_approval_reuses_task_and_fresh_decision_survives_replay(setup):
    _, repo, user = setup
    service = ExecutionService(repo, node="worker", incarnation="boot1", role="worker")
    seen = []

    async def handler(run, decision):
        seen.append(decision)
        if decision is None:
            yield {"type": "hitl_required", "pending_id": "card"}
        else:
            yield {"type": "token", "text": "approved"}
        yield {"type": "done"}

    service.handler = handler
    run = submit(repo, user)
    await service.start()
    try:
        paused = [e async for e in service.replay(run["run_id"], user=user)]
        assert repo.get(run["run_id"])["state"] == "awaiting_approval"
        payload = {"approval_id": "card", "decisions": [{"type": "approve"}]}
        repo.command(run["run_id"], user=user, key="approve", kind="approval", payload=payload)
        resumed = [
            e async for e in service.replay(run["run_id"], user=user, after=paused[-1]["seq"])
        ]
        assert resumed[-1]["payload"]["state"] == "completed"
        assert seen == [None, payload]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_forced_shutdown_keeps_unknown_and_occupancy(setup):
    _, repo, user = setup
    service = ExecutionService(repo, node="worker", incarnation="boot1", role="worker")
    started = asyncio.Event()

    async def handler(run, decision):
        started.set()
        await asyncio.Event().wait()
        yield {}

    service.handler = handler
    run = submit(repo, user)
    await service.start()
    await asyncio.wait_for(started.wait(), 5)
    await service.close(grace=0)
    assert repo.get(run["run_id"])["state"] == "unknown"
    assert repo.active("one")


def test_stop_verifier_requires_exact_container_and_boot_evidence(setup):
    _, repo, _ = setup
    inspected = {
        "Id": "container1",
        "Config": {"Labels": {"org.octop.node": "worker", "org.octop.incarnation": "boot1"}},
        "State": {
            "Running": True,
            "Restarting": False,
            "FinishedAt": "2026-10-22T15:00:00Z",
            "ExitCode": 0,
        },
    }
    docker = SimpleNamespace(
        api=SimpleNamespace(
            containers=lambda **kw: [], inspect_container=lambda identity: inspected
        )
    )
    with pytest.raises(ExecutionConflict, match="unconfirmed"):
        confirm_container_stopped(repo, docker, "worker", "boot1")
    inspected["State"]["Running"] = False
    confirm_container_stopped(repo, docker, "worker", "boot1")
    assert repo.node("worker")["state"] == "stopped"


@pytest.mark.parametrize("tracked", [True, False])
def test_worker_stop_does_not_prove_external_sandbox_stopped(setup, tracked):
    _, repo, _ = setup
    if tracked:
        repo.track_workspace("worker", "boot1", "expert", "sandbox")
    worker = {
        "Id": "container1",
        "Config": {"Labels": {"org.octop.node": "worker", "org.octop.incarnation": "boot1"}},
        "State": {"Running": False, "Restarting": False, "FinishedAt": "2026-10-23T00:00:00Z"},
    }
    sandbox = {
        "Id": "sandbox",
        "Config": {"Labels": {"octop.agent.id": "expert"}},
        "State": {"Running": True, "Restarting": False, "FinishedAt": "2026-10-23T00:00:00Z"},
    }
    docker = SimpleNamespace(
        api=SimpleNamespace(
            containers=lambda **kw: [{"Id": "sandbox", "Labels": {"octop.agent.id": "expert"}}],
            inspect_container=lambda identity: sandbox if identity == "sandbox" else worker,
        )
    )
    with pytest.raises(ExecutionConflict, match="workspace_stop_unconfirmed"):
        confirm_container_stopped(repo, docker, "worker", "boot1")
    assert repo.node("worker")["state"] == "ready"
    sandbox["State"]["Running"] = False
    confirm_container_stopped(repo, docker, "worker", "boot1")
    assert repo.node("worker")["state"] == "stopped"


def test_worker_identity_distinguishes_python_launches_and_container_restarts(setup):
    from octop.infra.execution.stop_verifier import worker_identity

    _, repo, _ = setup
    inspected = {
        "Id": "identity-container",
        "Config": {
            "Labels": {"org.octop.node": "identity-worker", "org.octop.incarnation": "nonce"}
        },
        "HostConfig": {"RestartPolicy": {"Name": "no"}},
        "RestartCount": 0,
        "State": {"Running": True, "Restarting": False, "StartedAt": "2026-10-23T00:00:00Z"},
    }
    docker = SimpleNamespace(
        api=SimpleNamespace(
            inspect_container=lambda _: inspected,
            containers=lambda **kw: [],
        )
    )
    container, first = worker_identity(docker, "self", "identity-worker", "nonce")
    _, second = worker_identity(docker, "self", "identity-worker", "nonce")
    assert first != second and first.split(":")[:2] == second.split(":")[:2]
    repo.register("identity-worker", first, "worker", container_id=container)
    inspected["RestartCount"] = 1
    with pytest.raises(ExecutionConflict, match="identity_invalid"):
        worker_identity(docker, "self", "identity-worker", "nonce")
    inspected["State"].update(Running=False, FinishedAt="2026-10-23T00:05:00Z")
    inspected["State"]["StartedAt"] = "2026-10-23T00:04:00Z"
    with pytest.raises(ExecutionConflict, match="stop_unconfirmed"):
        confirm_container_stopped(repo, docker, "identity-worker", first)
    inspected["State"]["StartedAt"] = "2026-10-23T00:00:00Z"
    confirm_container_stopped(repo, docker, "identity-worker", first)


def test_stop_cli_closes_docker_client_without_requiring_context_manager(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock

    from click.testing import CliRunner

    from octop.cli.commands import execution as commands

    client = SimpleNamespace(close=Mock())
    services = SimpleNamespace(execution_repo=object())
    monkeypatch.setattr(commands, "open_cli_services", lambda **_: nullcontext(services))
    monkeypatch.setattr("docker.client.DockerClient.from_env", lambda **_: client)
    confirm = Mock()
    monkeypatch.setattr("octop.infra.execution.stop_verifier.confirm_container_stopped", confirm)
    result = CliRunner().invoke(
        commands.execution, ["confirm-stop", "--node", "worker", "--incarnation", "boot"]
    )
    assert result.exit_code == 0, result.exception
    confirm.assert_called_once_with(services.execution_repo, client, "worker", "boot")
    client.close.assert_called_once()
