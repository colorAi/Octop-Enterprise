import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.employee_resources import EmployeeResourceRepo
from octop.infra.db.repos.expert_assignments import ExpertAssignmentRepo
from octop.infra.db.repos.resource_manifests import ManifestConflict, ResourceManifestRepo
from octop.infra.db.repos.users import UserRepo


def publication_fixture(pool):
    users = UserRepo(pool)
    admin = users.create(username="manifest-admin", role="admin")
    employee = users.create(username="manifest-employee", role="user")
    agents = AgentRepo(pool)
    agents.create(agent_id="manifest-source", user_id=admin, name="Source")
    assignments = ExpertAssignmentRepo(pool)
    assignments.set_source(admin, 0, "admin")
    EmployeeResourceRepo(pool).reconcile(employee, "A", 0, True)
    assignments.save(employee, ["manifest-source"], "assigned_only", 0, 1, "admin")
    mapping = assignments.reserve(employee, "manifest-source")
    agents.create(agent_id=mapping["instance_agent_id"], user_id=employee, name="Employee")
    return {
        "user_id": employee,
        "agent_id": mapping["instance_agent_id"],
        "source_row_id": mapping["source_row_id"],
        "expected_generation": 0,
        "manifest_sha256": hashlib.sha256(b"first manifest").hexdigest(),
        "resource_revision": "1:1:0:" + hashlib.sha256(b"first content").hexdigest(),
    }


@pytest.fixture
def publication(tmp_path):
    pool = SqlitePool(tmp_path / "manifests.db")
    run_migrations(pool)
    arguments = publication_fixture(pool)
    yield pool, ResourceManifestRepo(pool), arguments
    pool.close()


def assert_cas_and_outbox(pool, arguments):
    repo = ResourceManifestRepo(pool)
    first = repo.publish(**arguments)
    assert first["generation"] == 1
    assert repo.publish(**arguments) == first
    assert len(repo.events()) == 1
    second = {**arguments, "manifest_sha256": hashlib.sha256(b"second manifest").hexdigest()}
    with pytest.raises(ManifestConflict):
        repo.publish(**second)
    assert repo.head(arguments["agent_id"]) == first
    assert len(repo.events()) == 1
    newer = repo.publish(**{**second, "expected_generation": 1})
    assert newer["generation"] == 2
    events = repo.events(limit=1)
    assert events[0]["manifest_sha256"] == first["manifest_sha256"]
    remaining = repo.events(after=events[0]["id"])
    assert len(remaining) == 1 and remaining[0]["manifest_sha256"] == newer["manifest_sha256"]


def test_cas_publication_and_replay_cursor(publication):
    pool, _, arguments = publication
    assert_cas_and_outbox(pool, arguments)


def test_unchanged_publication_checks_authority_without_writes(publication):
    pool, repo, arguments = publication
    first = repo.publish(**arguments)
    with pool.connect() as conn:
        previous = conn.total_changes
    assert repo.publish(**arguments) == first
    with pool.connect() as conn:
        assert conn.total_changes == previous


def test_recreated_instance_continues_its_publication_generation(publication):
    pool, repo, arguments = publication
    first = repo.publish(**arguments)
    with pool.transaction() as conn:
        conn.execute("DELETE FROM agents WHERE agent_id=?", (arguments["agent_id"],))
    assert repo.head(arguments["agent_id"]) is None
    AgentRepo(pool).create(
        agent_id=arguments["agent_id"], user_id=arguments["user_id"], name="Recreated instance"
    )
    restored = repo.publish(**arguments)
    assert restored["generation"] == first["generation"] + 1
    assert [event["generation"] for event in repo.events()] == [1, 2]


def assert_transaction_rollback(pool, arguments):
    class FailingPool:
        connect = pool.connect

        @contextmanager
        def transaction(self):
            with pool.transaction() as connection:

                class Connection:
                    def execute(self, sql, params=()):
                        if sql.startswith("INSERT INTO managed_resource_outbox"):
                            raise RuntimeError("injected outbox failure")
                        return connection.execute(sql, params)

                yield Connection()

    with pytest.raises(RuntimeError, match="outbox failure"):
        ResourceManifestRepo(FailingPool()).publish(**arguments)
    repo = ResourceManifestRepo(pool)
    assert repo.head(arguments["agent_id"]) is None
    assert repo.events() == []
    assert repo.publish(**arguments)["generation"] == 1


def test_pointer_and_outbox_roll_back_together(publication):
    pool, _, arguments = publication
    assert_transaction_rollback(pool, arguments)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE users SET disabled=1 WHERE username='manifest-employee'",
        "UPDATE users SET disabled=1 WHERE username='manifest-admin'",
        "UPDATE users SET role='user' WHERE username='manifest-admin'",
        "UPDATE agents SET enabled=0 WHERE agent_id='manifest-source'",
        "DELETE FROM expert_assignments",
        "UPDATE users SET expert_assignment_revision=expert_assignment_revision+1",
        "UPDATE expert_assignment_source SET revision=revision+1",
        "UPDATE employee_resource_scopes SET membership_revision=membership_revision+1",
        "UPDATE employee_resource_scopes SET pending_scope='B'",
    ],
)
def test_authority_change_prevents_publication(publication, mutation):
    pool, repo, arguments = publication
    with pool.transaction() as conn:
        conn.execute(mutation)
    with pytest.raises(ManifestConflict):
        repo.publish(**arguments)
    assert repo.head(arguments["agent_id"]) is None
    assert repo.events() == []


def test_separate_connections_cannot_overwrite_each_others_publication(publication):
    pool, repo, arguments = publication
    barrier = threading.Barrier(2)

    def compete(number):
        connection = SqlitePool(pool.path)
        try:
            barrier.wait(timeout=10)
            return ResourceManifestRepo(connection).publish(
                **{**arguments, "manifest_sha256": hashlib.sha256(str(number).encode()).hexdigest()}
            )
        except ManifestConflict:
            return None
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(compete, range(2)))
    winners = [row for row in results if row is not None]
    assert len(winners) == 1
    assert repo.head(arguments["agent_id"]) == winners[0]
    assert len(repo.events()) == 1
