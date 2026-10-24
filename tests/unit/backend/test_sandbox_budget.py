"""Admission races, Docker uncertainty and verified release of sandbox RAM."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import NotFound
from starlette.requests import Request
from tests.unit.db.test_execution import seed_execution

from octop.infra.backend import isolation
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.execution import ExecutionConflict, ExecutionRepo
from octop.infra.execution.profiles import memory_bytes
from octop.infra.execution.stop_verifier import confirm_container_stopped

MEMORY = 256 * 1024**2


@pytest.fixture
def budget(tmp_path):
    pool = SqlitePool(tmp_path / "budget.db")
    run_migrations(pool)
    ExecutionRepo(pool).configure_sandbox_budget(total=2 * MEMORY, sandbox=MEMORY)
    repo, user = seed_execution(pool)
    yield pool, repo, user
    isolation.clear_execution_tracking()
    pool.close()


def reserve(repo, name, node="worker", boot="boot1"):
    return repo.reserve_sandbox(node, boot, "expert", name, MEMORY)


def test_atomic_global_limit_duplicate_reservations_and_generation_fence(budget):
    _, repo, _ = budget
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda n: reserve(repo, str(n)), range(16)))
    assert sum(token is not None for token in results) == 2
    assert repo.sandbox_budget()["used_bytes"] == 2 * MEMORY
    assert repo.sandbox_pressure()
    name = str(next(i for i, token in enumerate(results) if token))
    token = results[int(name)]
    assert reserve(repo, name) == token
    repo.release_sandbox("worker", "boot1", name, "stale-token")
    assert repo.sandbox_budget()["used_bytes"] == 2 * MEMORY
    repo.release_sandbox("worker", "boot1", name, token)
    assert not repo.sandbox_pressure()
    replacement = reserve(repo, name)
    assert replacement and replacement != token
    repo.release_sandbox("worker", "boot1", name, token)
    assert repo.sandbox_budget()["used_bytes"] == 2 * MEMORY


def test_expiry_never_releases_memory_and_cold_configuration_requires_stop(budget):
    pool, repo, _ = budget
    reserve(repo, "one")
    reserve(repo, "two", "worker2", "boot2")
    with pool.transaction() as conn:
        conn.execute("UPDATE execution_nodes SET lease_until=0 WHERE node_id='worker'")
    repo.expire_nodes()
    assert reserve(repo, "three", "worker2", "boot2") is None
    with pytest.raises(ExecutionConflict, match="fenced"):
        reserve(repo, "one")
    with pytest.raises(ExecutionConflict, match="stop_unconfirmed"):
        repo.configure_sandbox_budget(total=3 * MEMORY, sandbox=MEMORY)
    with pytest.raises(ExecutionConflict, match="workspace_stop_unconfirmed"):
        repo.record_stop(
            "worker",
            "boot1",
            {"container_id": "container1", "running": False, "finished_at": "proof"},
        )
    assert repo.sandbox_budget()["used_bytes"] == 2 * MEMORY


def test_v29_repair_is_idempotent_and_preserves_live_reservations(budget):
    pool, repo, _ = budget
    token = reserve(repo, "one")
    run_migrations(pool)
    assert reserve(repo, "one") == token
    assert repo.sandbox_budget()["limit_bytes"] == 2 * MEMORY
    with pool.transaction() as conn:
        conn.execute("DROP TABLE execution_sandboxes")
        conn.execute("DROP TABLE execution_sandbox_budget")
    run_migrations(pool)
    run_migrations(pool)
    assert repo.sandbox_budget()["limit_bytes"] == 0
    with pool.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 30


def test_new_stop_verifier_can_confirm_old_workers_before_schema_repair(budget):
    pool, repo, _ = budget
    with pool.transaction() as conn:
        conn.execute("DROP TABLE execution_sandboxes")
        conn.execute("DROP TABLE execution_sandbox_budget")
    worker = {
        "Id": "container1",
        "Config": {"Labels": {"org.octop.node": "worker", "org.octop.incarnation": "boot1"}},
        "State": {"Running": False, "Restarting": False, "FinishedAt": "verified"},
    }
    docker = SimpleNamespace(
        api=SimpleNamespace(inspect_container=lambda _: worker, containers=lambda **kwargs: [])
    )
    confirm_container_stopped(repo, docker, "worker", "boot1")
    assert repo.node("worker")["state"] == "stopped"
    run_migrations(pool)
    assert repo.sandbox_budget()["used_bytes"] == 0


@pytest.mark.parametrize(
    "value,expected", [("256m", MEMORY), ("1G", 1024**3), ("8192k", 8 * 1024**2), ("1024", 1024)]
)
def test_memory_size_units(value, expected):
    assert memory_bytes(value) == expected


@pytest.mark.parametrize("value", ["", "0", "-1g", "0.5g", "1gb", "2t", "unlimited"])
def test_invalid_memory_sizes(value):
    with pytest.raises(ValueError):
        memory_bytes(value)


class Container:
    def __init__(self, docker, options):
        self.docker, self.options = docker, options
        self.name = self.id = options["name"]
        self.status = "created"
        self.attrs = {
            "Id": self.id,
            "Config": {"Labels": options["labels"], "User": options["user"]},
            "HostConfig": {
                "ReadonlyRootfs": True,
                "NetworkMode": "none",
                "Memory": options["mem_limit"],
                "MemorySwap": options["memswap_limit"],
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Binds": [
                    f"{src}:{mount['bind']}:{mount['mode']}"
                    for src, mount in options["volumes"].items()
                ],
            },
            "State": {
                "Running": False,
                "Paused": False,
                "Restarting": False,
                "Status": "created",
                "FinishedAt": "0001-01-01",
            },
        }

    def reload(self):
        pass

    def start(self):
        self.status = "running"
        self.attrs["State"].update(Running=True, Status="running")
        running = sum(
            c.options["mem_limit"] for c in self.docker.items.values() if c.status == "running"
        )
        assert running <= self.docker.repo.sandbox_budget()["used_bytes"] <= 2 * MEMORY
        if self.docker.fail_start and not self.name.endswith("_init"):
            raise TimeoutError("start response lost")

    def stop(self, **kwargs):
        if self.docker.fail_stop:
            raise TimeoutError("stop response lost")
        self.status = "exited"
        self.attrs["State"].update(Running=False, Status="exited", FinishedAt="verified")

    def wait(self, **kwargs):
        self.stop()
        return {"StatusCode": 0}

    def exec_run(self, *args, **kwargs):
        return SimpleNamespace(exit_code=0, output=b"")


@pytest.fixture
def docker(budget, monkeypatch):
    _, repo, _ = budget
    fake = SimpleNamespace(items={}, repo=repo, fail_start=False, fail_stop=False, close=Mock())

    def get(name):
        if name not in fake.items:
            raise NotFound("no such container")
        return fake.items[name]

    def create(**options):
        assert options["name"] not in fake.items
        container = Container(fake, options)
        fake.items[container.name] = container
        return container

    fake.containers = SimpleNamespace(get=get, create=create)
    fake.volumes = SimpleNamespace(
        get=lambda _: SimpleNamespace(attrs={"Labels": {"octop.agent.id": "expert"}})
    )
    fake.api = SimpleNamespace(
        inspect_container=lambda name: get(name).attrs, containers=lambda **kwargs: []
    )
    monkeypatch.setattr("docker.from_env", lambda: fake)
    monkeypatch.setattr(
        "octop_harness.backends.docker_sandbox.ensure_docker_image", lambda *args, **kwargs: None
    )
    isolation.configure_execution_tracking(repo, "worker", "boot1")
    return fake


def backend(suffix=""):
    return isolation.create_isolated_backend(
        {
            "image": "test:image",
            "agent_id": "expert",
            "workspace_path": "/workspace",
            "volume": "volume" + suffix,
        },
        managed_lifecycle=True,
    )


def test_docker_initialization_reuse_hibernation_and_resume_share_budget(budget, docker):
    _, repo, _ = budget
    first = backend()
    again = backend()
    assert first._container is again._container
    assert repo.sandbox_budget()["used_bytes"] == MEMORY
    first.close()  # Harness reload does not own the cached backend's lifecycle.
    assert first._container.status == "running"
    first.hibernate()
    assert repo.sandbox_budget()["used_bytes"] == 0
    restarted = backend()
    assert restarted._container is first._container
    assert restarted._budget_token != first._budget_token
    first.hibernate()  # A stale closed handle cannot stop a new incarnation.
    assert restarted._container.status == "running"
    restarted.release()
    assert repo.sandbox_budget()["used_bytes"] == 0


def test_denial_starts_no_container_and_uncertain_start_keeps_reservation(budget, docker):
    _, repo, _ = budget
    reserve(repo, "other", "worker2", "boot2")
    docker.fail_start = True
    with pytest.raises(TimeoutError, match="start response lost"):
        backend()
    assert repo.sandbox_budget()["used_bytes"] == 2 * MEMORY
    before = len(docker.items)
    with pytest.raises(ExecutionConflict, match="sandbox_memory_exhausted"):
        backend("second")
    assert len(docker.items) == before
    assert repo.sandbox_pressure()
    docker.fail_start = False
    recovered = backend()
    docker.fail_stop = True
    with pytest.raises(TimeoutError):
        recovered.hibernate()
    assert repo.sandbox_budget()["used_bytes"] == 2 * MEMORY
    docker.fail_stop = False
    recovered.hibernate()
    assert repo.sandbox_budget()["used_bytes"] == MEMORY


def test_stop_proof_includes_initializers_and_releases_only_stopped_owner(budget, docker):
    _, repo, _ = budget
    reserve(repo, "other", "worker2", "boot2")
    instance = backend()
    worker = {
        "Id": "container1",
        "Config": {"Labels": {"org.octop.node": "worker", "org.octop.incarnation": "boot1"}},
        "State": {"Running": False, "Restarting": False, "FinishedAt": "verified"},
    }
    inspect = docker.api.inspect_container
    docker.api.inspect_container = lambda name: worker if name == "container1" else inspect(name)
    with pytest.raises(ExecutionConflict, match="workspace_stop_unconfirmed"):
        confirm_container_stopped(repo, docker, "worker", "boot1")
    instance._container.stop()
    helper = docker.items[instance._container.name + "_init"]
    helper.attrs["State"]["Running"] = True
    with pytest.raises(ExecutionConflict, match="workspace_stop_unconfirmed"):
        confirm_container_stopped(repo, docker, "worker", "boot1")
    helper.stop()
    confirm_container_stopped(repo, docker, "worker", "boot1")
    assert repo.sandbox_budget()["used_bytes"] == MEMORY
    assert repo.node("worker")["state"] == "stopped"


@pytest.mark.asyncio
@pytest.mark.parametrize("locale,part", [("zh", "沙箱总内存"), ("en", "sandbox memory budget")])
async def test_capacity_error_is_localized_and_retryable(locale, part):
    from octop.api.common.execution import execution_error_response

    request = Request({"type": "http", "headers": [(b"accept-language", locale.encode())]})
    response = await execution_error_response(
        request, ExecutionConflict("sandbox_memory_exhausted")
    )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    error = json.loads(response.body)["error"]
    assert error["code"] == "SANDBOX_MEMORY_EXHAUSTED" and part in error["message"]


def test_source_reader_has_independent_readonly_container_and_releases_only_its_slot(
    budget, docker
):
    _, repo, _ = budget
    active = backend()
    reader = isolation.create_isolated_backend(
        {
            "image": "test:image",
            "agent_id": "expert",
            "workspace_path": "/workspace",
            "volume": "volume",
            "read_only_workspace": True,
        }
    )
    assert active._container is not reader._container
    assert reader._container.options["volumes"]["volume"]["mode"] == "ro,nocopy"
    assert repo.sandbox_budget()["used_bytes"] == 2 * MEMORY
    reader.hibernate()
    assert active._container.attrs["State"]["Running"]
    assert repo.sandbox_budget()["used_bytes"] == MEMORY
    active.hibernate()
    assert repo.sandbox_budget()["used_bytes"] == 0
