"""True independent Python processes competing on a dedicated PostgreSQL DB."""

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

import pytest

from octop.infra.db.migrate import initialize_checkpoints, run_migrations
from octop.infra.db.pool import PostgresPool
from octop.infra.db.repos.execution import ExecutionConflict, ExecutionRepo
from tests.support.postgresql import requires_postgresql
from tests.unit.db.test_execution import fence, seed_execution, submit

pytestmark = [requires_postgresql, pytest.mark.postgresql]


def _initialize_checkpoints(url):
    pool = PostgresPool(url, max_size=1)
    try:
        initialize_checkpoints(pool, url)
    finally:
        pool.close()


def _open_checkpointer(url):
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        return list(saver.list(None))


def test_checkpoint_init_serializes_before_concurrent_first_use(execution):
    from psycopg.conninfo import make_conninfo

    url = make_conninfo(os.environ["OCTOP_TEST_DATABASE_URL"], options="-c statement_timeout=10000")
    with ProcessPoolExecutor(
        max_workers=3, mp_context=multiprocessing.get_context("spawn")
    ) as workers:
        list(workers.map(_initialize_checkpoints, [url] * 3))
        assert list(workers.map(_open_checkpointer, [url] * 3)) == [[], [], []]


def _shard_claim(args):
    url, node = args
    pool = PostgresPool(url, max_size=1)
    try:
        repo = ExecutionRepo(pool)
        result = []
        while run := repo.claim(node, "shard-boot"):
            result.append((run["run_id"], run["user_id"], run["node_id"]))
            repo.transition(*fence(run), "completed")
        return result
    finally:
        pool.close()


def _cancel_from_another_process(args):
    url, user = args
    pool = PostgresPool(url, max_size=1)
    try:
        ExecutionRepo(pool).cancel_user(user)
    finally:
        pool.close()


def test_policy_revocation_cancels_across_processes(execution):
    _, repo, user = execution
    submit(repo, user)
    run = repo.claim("worker2", "boot2")
    queued = submit(repo, user, thread="two", key="queued")
    with ProcessPoolExecutor(
        max_workers=1, mp_context=multiprocessing.get_context("spawn")
    ) as workers:
        workers.submit(
            _cancel_from_another_process, (os.environ["OCTOP_TEST_DATABASE_URL"], user)
        ).result(timeout=15)
    assert repo.get(run["run_id"])["state"] == "cancel_requested"
    assert [c["kind"] for c in repo.commands(*fence(run))] == ["cancel"]
    assert repo.get(queued["run_id"])["state"] == "cancelled"


@pytest.mark.parametrize("count", [2, 3])
def test_postgres_user_shards_across_independent_processes(execution, count):
    from octop.infra.db.repos.agents import AgentRepo
    from octop.infra.db.repos.threads import ThreadRepo
    from octop.infra.db.repos.users import UserRepo

    pool, repo, _ = execution
    for node, boot, container in (
        ("worker", "boot1", "container1"),
        ("worker2", "boot2", "container2"),
    ):
        repo.record_stop(
            node, boot, {"container_id": container, "running": False, "finished_at": "verified"}
        )
    nodes = [f"shard-{n}" for n in range(count)]
    repo.configure_topology({node: f"http://{node}:8088" for node in nodes})
    repo.configure(running=count, models=count, queued=32, user_queued=4)
    for node in nodes:
        repo.register(node, "shard-boot", "worker")
    expected = {}
    for n in range(12):
        user = UserRepo(pool).create(username=f"shard-user-{n}", role="user")
        aid = f"shard-agent-{n}"
        AgentRepo(pool).create(agent_id=aid, user_id=user, name=aid)
        ThreadRepo(pool).insert(
            thread_id=aid, agent_id=aid, user_id=user, channel_type="dashboard", session_key=aid
        )
        run = repo.submit(user=user, agent=aid, thread=aid, operation="chat", key=aid, request={})
        expected[run["run_id"]] = repo.worker_for(user)
    with ProcessPoolExecutor(
        max_workers=count, mp_context=multiprocessing.get_context("spawn")
    ) as workers:
        claims = sum(
            workers.map(
                _shard_claim, [(os.environ["OCTOP_TEST_DATABASE_URL"], node) for node in nodes]
            ),
            [],
        )
    assert len(claims) == len(expected)
    assert len({run for run, _, _ in claims}) == len(expected)
    assert all(node == expected[run] for run, _, node in claims)


@pytest.fixture
def execution():
    pool = PostgresPool(os.environ["OCTOP_TEST_DATABASE_URL"], max_size=2)
    try:
        with pool.transaction() as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        run_migrations(pool)
        ExecutionRepo(pool).configure_sandbox_budget(total=512 * 1024**2, sandbox=256 * 1024**2)
        repo, user = seed_execution(pool)
        yield pool, repo, user
    finally:
        pool.close()


def _reserve_sandbox(args):
    url, index = args
    pool = PostgresPool(url, max_size=1)
    try:
        repo = ExecutionRepo(pool)
        node, boot = ("worker", "boot1") if index % 2 else ("worker2", "boot2")
        token = repo.reserve_sandbox(node, boot, "expert", f"sandbox-{index}", 256 * 1024**2)
        return token, repo.sandbox_budget()["used_bytes"]
    finally:
        pool.close()


def test_postgres_sandbox_memory_is_bounded_across_independent_processes(execution):
    _, repo, _ = execution
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as workers:
        results = list(
            workers.map(
                _reserve_sandbox, [(os.environ["OCTOP_TEST_DATABASE_URL"], n) for n in range(16)]
            )
        )
    assert sum(token is not None for token, _ in results) == 2
    assert all(used <= 512 * 1024**2 for _, used in results)
    assert repo.sandbox_budget()["used_bytes"] == 512 * 1024**2
    assert repo.sandbox_pressure()


def _reserve_models(args):
    url, run_fence, batch = args
    pool = PostgresPool(url, max_size=1)
    try:
        repo = ExecutionRepo(pool)
        accepted = 0
        for index in range(80):
            try:
                repo.reserve_model(
                    *run_fence,
                    key=f"batch-{batch}-{index}",
                    provider="api" if batch % 2 else "local",
                    tokens=1,
                    quota_remaining=None,
                )
                accepted += 1
            except ExecutionConflict as exc:
                assert str(exc) == "model_capacity_exceeded"
        return accepted
    finally:
        pool.close()


@pytest.mark.parametrize("models,expected", [(0, 320), (7, 7)])
def test_optional_model_limit_across_processes(execution, models, expected):
    pool, repo, user = execution
    repo.configure(running=2, models=models, queued=1, user_queued=1)
    submit(repo, user)
    run = repo.claim("worker", "boot1")
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as workers:
        counts = list(
            workers.map(
                _reserve_models,
                [(os.environ["OCTOP_TEST_DATABASE_URL"], fence(run), index) for index in range(4)],
            )
        )
    assert sum(counts) == expected
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM execution_reservations WHERE state='reserved'"
            ).fetchone()[0]
            == expected
        )


def test_existing_postgres_v29_accepts_unlimited_and_preserves_config(execution):
    pool, repo, _ = execution
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE execution_control SET max_models=4,max_running=8,max_queued=100,max_user_queued=12"
        )
        conn.execute(
            "ALTER TABLE execution_control DROP CONSTRAINT execution_control_max_models_check"
        )
        conn.execute(
            "ALTER TABLE execution_control ADD CONSTRAINT execution_control_max_models_check CHECK (max_models>0)"
        )
        conn.execute("ALTER TABLE execution_control ALTER COLUMN max_models SET DEFAULT 2")
    run_migrations(pool)
    with pool.connect() as conn:
        assert tuple(dict(conn.execute("SELECT * FROM execution_control").fetchone()).values()) == (
            1,
            8,
            4,
            100,
            12,
        )
    repo.configure(running=8, models=0, queued=100, user_queued=12)
    run_migrations(pool)
    with pool.connect() as conn:
        assert tuple(dict(conn.execute("SELECT * FROM execution_control").fetchone()).values()) == (
            1,
            8,
            0,
            100,
            12,
        )


def test_postgres_old_v29_stop_proof_then_idempotent_budget_repair(execution):
    pool, repo, _ = execution
    with pool.transaction() as conn:
        conn.execute("DROP TABLE execution_sandboxes")
        conn.execute("DROP TABLE execution_sandbox_budget")
    assert repo.sandboxes("worker", "boot1") == []
    repo.record_stop(
        "worker",
        "boot1",
        {"container_id": "container1", "running": False, "finished_at": "verified"},
    )
    run_migrations(pool)
    run_migrations(pool)
    assert repo.node("worker")["state"] == "stopped"
    assert repo.sandbox_budget()["limit_bytes"] == 0


def _compete(args):
    url, user, index, operation = args
    pool = PostgresPool(url, max_size=1)
    try:
        repo = ExecutionRepo(pool)
        if operation == "submit":
            return submit(repo, user)["run_id"]
        node, incarnation = "process" + str(index), "boot" + str(index)
        repo.register(node, incarnation, "worker")
        row = repo.claim(node, incarnation)
        return dict(row) if row else None
    finally:
        pool.close()


def test_separate_processes_deduplicate_and_claim_once(execution):
    _, repo, user = execution
    url = os.environ["OCTOP_TEST_DATABASE_URL"]
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as workers:
        results = list(workers.map(_compete, [(url, user, n, "submit") for n in range(8)]))
        assert len(set(results)) == 1
        claims = list(workers.map(_compete, [(url, user, n, "claim") for n in range(8)]))
    assert len([claim for claim in claims if claim]) == 1
    run = next(claim for claim in claims if claim)
    repo.append(*fence(run), key="result", payload={"text": "committed"})
    repo.transition(*fence(run), "completed")
    assert repo.events(run["run_id"], user=user)[-1]["payload"]["state"] == "completed"


def test_lease_fencing_and_notification_loss_do_not_lose_commits(execution):
    pool, repo, user = execution
    run = submit(repo, user)
    claimed = repo.claim("worker", "boot1")
    repo.append(*fence(claimed), key="saved", payload={"value": 1})
    with pool.transaction() as conn:
        conn.execute("UPDATE execution_nodes SET lease_until=0 WHERE node_id='worker'")
    repo.expire_nodes()
    with pytest.raises(ExecutionConflict, match="fenced"):
        repo.append(*fence(claimed), key="late", payload={"value": 2})
    assert repo.claim("worker2", "boot2") is None
    assert [e["seq"] for e in repo.events(run["run_id"], user=user)] == [1, 2, 3, 4]
    assert repo.get(run["run_id"])["state"] == "unknown"


def test_migration_lock_allows_size_one_pool(execution):
    url = os.environ["OCTOP_TEST_DATABASE_URL"]
    pool = PostgresPool(url, max_size=1)
    try:
        run_migrations(pool)
    finally:
        pool.close()


def _decide(args):
    url, run, user, kind = args
    pool = PostgresPool(url, max_size=1)
    try:
        payload = (
            {"approval_id": "card", "decisions": [{"type": "approve"}]}
            if kind == "approval"
            else {}
        )
        return ExecutionRepo(pool).command(run, user=user, key=kind, kind=kind, payload=payload)
    finally:
        pool.close()


def test_independent_processes_approve_and_cancel_one_execution(execution):
    _, repo, user = execution
    submit(repo, user)
    run = repo.claim("worker", "boot1")
    repo.transition(*fence(run), "awaiting_approval", approval_id="card")
    url = os.environ["OCTOP_TEST_DATABASE_URL"]
    with ProcessPoolExecutor(
        max_workers=2, mp_context=multiprocessing.get_context("spawn")
    ) as workers:
        approvals = list(workers.map(_decide, [(url, run["run_id"], user, "approval")] * 4))
        assert all(item == approvals[0] for item in approvals)
        repo.applied(*fence(run), "approval")
        cancels = list(workers.map(_decide, [(url, run["run_id"], user, "cancel")] * 4))
        assert all(item == cancels[0] for item in cancels)
    assert repo.get(run["run_id"])["state"] == "cancel_requested"
    assert repo.active("one")
    repo.transition(*fence(run), "cancelled")
    assert repo.active("one") is None


def test_source_triggers_and_publication_fence_are_transactional(execution):
    from octop.infra.db.repos.agents import AgentRepo
    from octop.infra.db.repos.expert_assignments import ExpertAssignmentRepo
    from octop.infra.db.repos.source_publication import SourceConflict, SourcePublicationRepo
    from octop.infra.db.repos.users import UserRepo

    pool, repo, _ = execution
    admin = UserRepo(pool).create(username="source-admin", role="admin")
    ExpertAssignmentRepo(pool).set_source(admin, 0, "test")
    repo.reserve_runtime_host("worker", "boot1")
    publisher = SourcePublicationRepo(pool)
    head = publisher.begin(admin)
    AgentRepo(pool).create(agent_id="source", user_id=admin, name="Source")
    with pytest.raises(SourceConflict, match="changed"):
        publisher.publish(
            admin,
            generation=head["generation"],
            revision=head["dirty_revision"],
            digest="a" * 64,
            executor=("worker", "boot1"),
        )
    head = publisher.head(admin)
    published = publisher.publish(
        admin,
        generation=head["generation"],
        revision=head["dirty_revision"],
        digest="a" * 64,
        executor=("worker", "boot1"),
    )
    with pool.transaction() as conn:
        conn.execute("UPDATE execution_nodes SET lease_until=0 WHERE node_id='worker'")
    with pytest.raises(SourceConflict, match="fenced"):
        publisher.publish(
            admin,
            generation=published["generation"],
            revision=published["dirty_revision"],
            digest="b" * 64,
            executor=("worker", "boot1"),
        )
    assert publisher.head(admin) == published
