import json

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.execution import ExecutionConflict, ExecutionRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo


def seed_execution(pool):
    user = UserRepo(pool).create(username="execution-user", role="user")
    AgentRepo(pool).create(agent_id="expert", user_id=user, name="Expert")
    for name in ("one", "two", "three"):
        ThreadRepo(pool).insert(
            thread_id=name,
            agent_id="expert",
            user_id=user,
            channel_type="dashboard",
            session_key=name,
        )
    repo = ExecutionRepo(pool)
    repo.register("worker", "boot1", "worker", container_id="container1")
    repo.register("worker2", "boot2", "worker", container_id="container2")
    return repo, user


@pytest.fixture
def execution(tmp_path):
    pool = SqlitePool(tmp_path / "execution.db")
    run_migrations(pool)
    repo, user = seed_execution(pool)
    yield pool, repo, user
    pool.close()


def submit(repo, user, thread="one", key="request1", **kwargs):
    return repo.submit(
        user=user, agent="expert", thread=thread, operation="chat", key=key, request=kwargs
    )


def claim(repo):
    return repo.claim("worker", "boot1")


def fence(run):
    return run["run_id"], run["node_id"], run["incarnation"], run["token"]


@pytest.mark.parametrize("state", ["queued", "running", "awaiting_approval", "unknown"])
def test_policy_change_cancel_is_durable_and_preserves_unknown(execution, state):
    pool, repo, user = execution
    run = submit(repo, user)
    if state != "queued":
        run = claim(repo)
        if state != "running":
            repo.transition(
                *fence(run), state, approval_id="approval" if state == "awaiting_approval" else None
            )
    other = UserRepo(pool).create(username="unaffected", role="user")
    AgentRepo(pool).create(agent_id="other", user_id=other, name="Other")
    ThreadRepo(pool).insert(
        thread_id="other",
        agent_id="other",
        user_id=other,
        channel_type="dashboard",
        session_key="other",
    )
    untouched = repo.submit(
        user=other, agent="other", thread="other", operation="chat", key="other", request={}
    )

    # A separate repository (the administrator worker) has no live agent objects.
    admin_repo = ExecutionRepo(pool)
    admin_repo.cancel_user(user)
    admin_repo.cancel_user(user)
    expected = (
        "cancelled"
        if state == "queued"
        else "unknown"
        if state == "unknown"
        else "cancel_requested"
    )
    assert repo.get(run["run_id"])["state"] == expected
    assert repo.get(untouched["run_id"])["state"] == "queued"
    if expected == "cancel_requested":
        assert [c["kind"] for c in repo.commands(*fence(run))] == ["cancel"]
    if expected == "unknown":
        assert repo.active("one")  # Unknown outcomes must retain their occupancy.


def test_reconciliation_requires_stop_and_every_model_receipt_even_after_replacement(execution):
    _, repo, user = execution
    submit(repo, user)
    run = claim(repo)
    repo.reserve_model(*fence(run), key="model", provider="test", tokens=20, quota_remaining=100)
    repo.transition(*fence(run), "unknown")
    evidence = {
        "reason": "Provider records confirm ten tokens used",
        "models": {"model": 10},
        "tools": {},
    }
    with pytest.raises(ExecutionConflict, match="stop_unconfirmed"):
        repo.reconcile(run["run_id"], outcome="failed", evidence=evidence)
    repo.record_stop(
        "worker",
        "boot1",
        {"container_id": "container1", "running": False, "finished_at": "verified"},
    )
    repo.register("worker", "replacement", "worker", container_id="container-new")
    with pytest.raises(ExecutionConflict, match="incomplete"):
        repo.reconcile(run["run_id"], outcome="failed", evidence={**evidence, "models": {}})
    repo.reconcile(run["run_id"], outcome="failed", evidence=evidence)
    assert repo.active("one") is None
    assert repo.get(run["run_id"])["state"] == "failed"
    assert repo.reconciliation(run["run_id"])["models"] == []


def test_desktop_model_proxy_shares_slots_and_honors_the_desktop_lease(execution):
    from octop.infra.db.repos.company_runtime import CompanyRuntimeRepo

    pool, repo, user = execution
    repo.configure(running=2, models=1, queued=8, user_queued=4)
    desktop = CompanyRuntimeRepo(pool)
    with pool.transaction() as conn:
        conn.execute("UPDATE threads SET last_active=0 WHERE thread_id='one'")
    desktop.acquire(
        task_id="desktop",
        thread_id="one",
        owner=user,
        agent_id="expert",
        device_id="device",
        request={"project_name": "Test"},
        expert={},
        installation_hash="installation",
        recovery_hash="recovery",
    )
    with pytest.raises(ExecutionConflict, match="thread_already_running"):
        submit(repo, user)
    run = repo.proxy_run(
        task="desktop", user=user, device="device", node="worker", incarnation="boot1"
    )
    repo.reserve_model(
        *fence(run), key="desktop-model", provider="test", tokens=30, quota_remaining=100
    )
    submit(repo, user, "two", "server")
    server = claim(repo)
    with pytest.raises(ExecutionConflict, match="capacity"):
        repo.reserve_model(
            *fence(server), key="server-model", provider="test", tokens=30, quota_remaining=100
        )
    repo.settle_model(*fence(run), key="desktop-model", actual=12)
    repo.transition(*fence(run), "completed")
    assert desktop.task("desktop")["status"] == "running"
    repo.reserve_model(
        *fence(server), key="server-model", provider="test", tokens=30, quota_remaining=100
    )


def test_default_unlimited_models_keep_quota_and_identity_checks(execution):
    pool, repo, user = execution
    with pool.connect() as conn:
        assert conn.execute("SELECT max_models FROM execution_control").fetchone()[0] == 0
    submit(repo, user)
    run = claim(repo)
    for index in range(300):
        repo.reserve_model(
            *fence(run),
            key=f"model-{index}",
            provider="local" if index % 2 else "api",
            tokens=1,
            quota_remaining=301,
        )
    with pytest.raises(ExecutionConflict, match="already_reserved"):
        repo.reserve_model(
            *fence(run), key="model-0", provider="api", tokens=1, quota_remaining=None
        )
    with pytest.raises(ExecutionConflict, match="token_quota_exceeded"):
        repo.reserve_model(*fence(run), key="quota", provider="api", tokens=2, quota_remaining=301)
    repo.configure(running=2, models=300, queued=8, user_queued=4)
    with pytest.raises(ExecutionConflict, match="model_capacity_exceeded"):
        repo.reserve_model(
            *fence(run), key="capped", provider="api", tokens=1, quota_remaining=None
        )
    repo.configure(running=2, models=0, queued=8, user_queued=4)
    repo.reserve_model(*fence(run), key="uncapped", provider="api", tokens=1, quota_remaining=None)


@pytest.mark.parametrize("models", [0, 1000])
def test_active_desktop_models_are_not_counted_as_queued_requests(execution, models):
    from octop.infra.db.repos.company_runtime import CompanyRuntimeRepo

    pool, repo, user = execution
    repo.configure(running=1, models=models, queued=1, user_queued=1)
    with pool.transaction() as conn:
        conn.execute("UPDATE threads SET last_active=0 WHERE thread_id='one'")
    CompanyRuntimeRepo(pool).acquire(
        task_id="desktop",
        thread_id="one",
        owner=user,
        agent_id="expert",
        device_id="device",
        request={},
        expert={},
        installation_hash="installation",
        recovery_hash="recovery",
    )
    for index in range(300):
        run = repo.proxy_run(
            task="desktop", user=user, device="device", node="worker", incarnation="boot1"
        )
        repo.reserve_model(
            *fence(run), key=f"model-{index}", provider="api", tokens=1, quota_remaining=None
        )
    # A genuine waiting request still fills the explicitly enabled queue.
    repo.proxy_run(task="desktop", user=user, device="device", node="worker", incarnation="boot1")
    if models:
        with pytest.raises(ExecutionConflict, match="execution_queue_full"):
            repo.proxy_run(
                task="desktop", user=user, device="device", node="worker", incarnation="boot1"
            )
    else:
        repo.proxy_run(
            task="desktop", user=user, device="device", node="worker", incarnation="boot1"
        )


def test_existing_v29_model_limit_schema_is_upgraded_without_losing_config(execution):
    pool, repo, _ = execution
    with pool.transaction() as conn:
        conn.execute("DROP TABLE execution_control")
        conn.execute(
            "CREATE TABLE execution_control (id INTEGER PRIMARY KEY CHECK(id=1), max_running INTEGER NOT NULL DEFAULT 2 CHECK(max_running>0), max_models INTEGER NOT NULL DEFAULT 2 CHECK(max_models>0), max_queued INTEGER NOT NULL DEFAULT 256 CHECK(max_queued>0), max_user_queued INTEGER NOT NULL DEFAULT 8 CHECK(max_user_queued>0))"
        )
        conn.execute("INSERT INTO execution_control VALUES(1,8,4,100,12)")
    run_migrations(pool)
    with pool.connect() as conn:
        assert tuple(conn.execute("SELECT * FROM execution_control").fetchone()) == (
            1,
            8,
            4,
            100,
            12,
        )
    repo.configure(running=8, models=0, queued=100, user_queued=12)
    run_migrations(pool)
    with pool.connect() as conn:
        assert tuple(conn.execute("SELECT * FROM execution_control").fetchone()) == (
            1,
            8,
            0,
            100,
            12,
        )


def test_idempotency_session_and_event_replay(execution):
    _, repo, user = execution
    first = submit(repo, user, text="hello")
    assert submit(repo, user, text="hello") == first
    with pytest.raises(ExecutionConflict, match="identity_conflict"):
        submit(repo, user, text="different")
    with pytest.raises(ExecutionConflict, match="thread_already_running"):
        submit(repo, user, key="different")
    run = claim(repo)
    assert repo.claim("worker2", "boot2") is None
    repo.append(*fence(run), key="answer", payload={"text": "saved"})
    repo.append(*fence(run), key="answer", payload={"text": "saved"})
    repo.transition(*fence(run), "completed")
    assert repo.active("one") is None
    events = repo.events(first["run_id"], user=user, after=2)
    assert [e["seq"] for e in events] == [3, 4]
    assert events[-1]["payload"]["state"] == "completed"
    assert repo.events(first["run_id"], user=user + 1) == []


def test_lease_expiry_never_releases_workspace_or_replays(execution):
    pool, repo, user = execution
    submit(repo, user)
    run = claim(repo)
    submit(repo, user, thread="two", key="second")
    with pool.transaction() as conn:
        conn.execute("UPDATE execution_nodes SET lease_until=0 WHERE node_id='worker'")
    assert repo.expire_nodes() == 1
    assert repo.get(run["run_id"])["state"] == "unknown"
    assert repo.active("one")
    assert repo.claim("worker2", "boot2") is None
    with pytest.raises(ExecutionConflict, match="fenced"):
        repo.append(*fence(run), key="late", payload={"text": "late"})
    with pytest.raises(ExecutionConflict, match="stop_unconfirmed"):
        repo.register("worker", "restart", "worker")
    repo.record_stop(
        "worker",
        "boot1",
        {"container_id": "container1", "running": False, "finished_at": "verified"},
    )
    other = repo.claim("worker2", "boot2")
    assert other["token"] == run["token"] + 1
    assert other["thread_id"] == "two"
    assert repo.get(run["run_id"])["state"] == "unknown"


def test_cancel_and_approval_are_persistent_and_single_decision(execution):
    _, repo, user = execution
    submit(repo, user)
    run = claim(repo)
    repo.transition(*fence(run), "awaiting_approval", approval_id="card1")
    payload = {"approval_id": "card1", "decisions": [{"type": "approve"}]}
    repo.command(run["run_id"], user=user, key="approval1", kind="approval", payload=payload)
    assert len(repo.commands(*fence(run))) == 1
    with pytest.raises(ExecutionConflict, match="approval_not_pending"):
        repo.command(run["run_id"], user=user, key="approval2", kind="approval", payload=payload)
    repo.applied(*fence(run), "approval1")
    repo.command(run["run_id"], user=user, key="cancel1", kind="cancel", payload={})
    with pytest.raises(ExecutionConflict, match="cancel_requested"):
        repo.check(*fence(run))
    assert repo.active("one")
    repo.applied(*fence(run), "cancel1")
    repo.transition(*fence(run), "cancelled")
    assert repo.active("one") is None


def test_queued_cancel_releases_and_model_unknown_holds_quota(execution):
    _, repo, user = execution
    queued = submit(repo, user)
    repo.command(queued["run_id"], user=user, key="cancel", kind="cancel", payload={})
    assert repo.active("one") is None
    submit(repo, user, key="new")
    run = claim(repo)
    repo.configure(running=2, models=1, queued=2, user_queued=1)
    repo.reserve_model(*fence(run), key="model1", provider="test", tokens=20, quota_remaining=30)
    with pytest.raises(ExecutionConflict, match="capacity"):
        repo.reserve_model(
            *fence(run), key="model2", provider="test", tokens=20, quota_remaining=30
        )
    repo.settle_model(*fence(run), key="model1", actual=None)
    with pytest.raises(ExecutionConflict, match="capacity"):
        repo.reserve_model(
            *fence(run), key="model2", provider="test", tokens=20, quota_remaining=30
        )
    repo.settle_model(*fence(run), key="model1", actual=12)
    repo.reserve_model(*fence(run), key="model2", provider="test", tokens=20, quota_remaining=30)
    submit(repo, user, thread="two", key="second")
    with pytest.raises(ExecutionConflict, match="queue_full"):
        submit(repo, user, thread="three", key="third")


def test_device_generation_and_sent_operation_are_not_replayed(execution):
    pool, repo, user = execution
    repo.register("web", "webboot", "web")
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO desktop_devices(device_id,owner_user_id,session_id,credential_hash,name,platform,protocol_version,expires_at,created_at,last_seen_at) VALUES(?,?,'session','hash','device','mac',2,9999999999,0,0)",
            ("device", user),
        )
    generation = repo.connect_device(
        "device", user=user, node="web", incarnation="webboot", metadata={}
    )
    submit(repo, user)
    run = claim(repo)
    repo.request_tool(
        *fence(run),
        operation="op1",
        device="device",
        generation=generation,
        request={"tool": "read_file"},
    )
    assert len(repo.take_tools("device", "web", "webboot", generation)) == 1
    assert repo.take_tools("device", "web", "webboot", generation) == []
    repo.complete_tool(
        run["run_id"],
        "op1",
        device="device",
        node="web",
        incarnation="webboot",
        generation=generation,
        result={"ok": True},
    )
    assert json.loads(repo.tool(run["run_id"], "op1")["result_json"]) == {"ok": True}
    repo.request_tool(
        *fence(run),
        operation="op2",
        device="device",
        generation=generation,
        request={"tool": "write_file"},
    )
    assert len(repo.take_tools("device", "web", "webboot", generation)) == 1
    new = repo.connect_device("device", user=user, node="web", incarnation="webboot", metadata={})
    assert new == generation + 1
    with pytest.raises(ExecutionConflict, match="connection_changed"):
        repo.complete_tool(
            run["run_id"],
            "op2",
            device="device",
            node="web",
            incarnation="webboot",
            generation=generation,
            result={"ok": True},
        )
    assert repo.tool(run["run_id"], "op2")["state"] == "unknown"
    with pytest.raises(ExecutionConflict, match="unconfirmed"):
        repo.transition(*fence(run), "completed")
    assert repo.active("one")


def test_global_slot_fairness_and_scheduler_deduplication(execution):
    pool, repo, user = execution
    other = UserRepo(pool).create(username="other", role="user")
    AgentRepo(pool).create(agent_id="other-expert", user_id=other, name="Other")
    ThreadRepo(pool).insert(
        thread_id="other",
        agent_id="other-expert",
        user_id=other,
        channel_type="dashboard",
        session_key="other",
    )
    repo.configure(running=1, models=1, queued=10, user_queued=8)
    submit(repo, user)
    run = claim(repo)
    submit(repo, user, thread="two", key="second")
    scheduled = repo.submit(
        user=other,
        agent="other-expert",
        thread="other",
        operation="cron",
        key="cron1",
        request={},
        schedule=("schedule", "2026-10-22T15:00:00Z"),
    )
    assert repo.claim("worker2", "boot2") is None
    repo.transition(*fence(run), "completed")
    assert claim(repo)["run_id"] == scheduled["run_id"]
    assert (
        repo.submit(
            user=other,
            agent="other-expert",
            thread="other",
            operation="cron",
            key="cron2",
            request={},
            schedule=("schedule", "2026-10-22T15:00:00Z"),
        )["run_id"]
        == scheduled["run_id"]
    )
    assert repo.leader("scheduler", "worker", "boot1")
    assert not repo.leader("scheduler", "worker2", "boot2")


def test_settled_usage_counts_before_projection_without_double_charge(execution):
    from octop.infra.db.repos.usage import UsageRepo
    from octop.infra.db.repos.user_policies import UserPolicyRepo
    from octop.infra.utils.execution_context import execution_run_id

    pool, repo, user = execution
    UserPolicyRepo(pool).set(user, "token_quota", "15")
    submit(repo, user)
    run = claim(repo)
    repo.reserve_model(*fence(run), key="first", provider="test", tokens=10, quota_remaining=None)
    repo.settle_model(*fence(run), key="first", actual=8)
    repo.transition(*fence(run), "completed")
    submit(repo, user, key="second")
    second = claim(repo)
    with pytest.raises(ExecutionConflict, match="quota"):
        repo.reserve_model(
            *fence(second), key="second", provider="test", tokens=8, quota_remaining=None
        )
    context = execution_run_id.set(run["run_id"])
    try:
        UsageRepo(pool).record(agent_id="expert", user_id=user, input_tokens=3, output_tokens=5)
    finally:
        execution_run_id.reset(context)
    repo.reserve_model(
        *fence(second), key="second", provider="test", tokens=7, quota_remaining=None
    )


def test_runtime_host_and_new_boot_require_verified_stop(execution):
    _, repo, user = execution
    repo.reserve_runtime_host("worker", "boot1")
    with pytest.raises(ExecutionConflict, match="stop_unconfirmed"):
        repo.reserve_runtime_host("worker2", "boot2")
    submit(repo, user)
    run = claim(repo)
    repo.record_stop(
        "worker",
        "boot1",
        {"container_id": "container1", "running": False, "finished_at": "verified"},
    )
    repo.register("worker", "next-boot", "worker", container_id="new-container")
    repo.reserve_runtime_host("worker", "next-boot")
    submit(repo, user, thread="two", key="new")
    next_run = repo.claim("worker", "next-boot")
    assert next_run["token"] > run["token"]
    with pytest.raises(ExecutionConflict, match="fenced"):
        repo.append(*fence(run), key="late", payload={})
