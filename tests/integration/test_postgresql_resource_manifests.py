"""Publication contention on a dedicated PostgreSQL database (schema is reset)."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import PostgresPool
from octop.infra.db.repos.resource_manifests import ManifestConflict, ResourceManifestRepo
from tests.support.postgresql import requires_postgresql
from tests.unit.db.test_resource_manifests import (
    assert_cas_and_outbox,
    assert_transaction_rollback,
    publication_fixture,
)

pytestmark = [requires_postgresql, pytest.mark.postgresql]


@pytest.fixture
def publication():
    pool = PostgresPool(os.environ["OCTOP_TEST_DATABASE_URL"])
    try:
        with pool.transaction() as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        run_migrations(pool)
        yield pool, publication_fixture(pool)
    finally:
        pool.close()


def test_pg_manifest_cas_and_outbox(publication):
    assert_cas_and_outbox(*publication)


def test_pg_manifest_transaction_rollback(publication):
    assert_transaction_rollback(*publication)


def test_pg_source_trigger_upgrade_ignores_runtime_but_tracks_definition_changes(publication):
    pool, _ = publication
    with pool.transaction() as conn:
        source = conn.execute("SELECT source_user_id FROM expert_assignment_source").fetchone()[0]
        conn.execute("INSERT INTO resource_source_heads(user_id) VALUES(?)", (source,))
        conn.execute(
            "CREATE OR REPLACE FUNCTION octop_source_agent_dirty() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN UPDATE resource_source_heads SET "
            "dirty_revision=dirty_revision+1; RETURN NULL; END; $$"
        )
    run_migrations(pool)
    run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE agents SET last_state='running',last_error=NULL,updated_at=updated_at+1"
        )
        conn.execute("UPDATE agents SET name=name,config_json=config_json")
        assert conn.execute("SELECT dirty_revision FROM resource_source_heads").fetchone()[0] == 0
        conn.execute(
            "UPDATE agents SET icon_url='/experts/avatars/test.svg' WHERE user_id=?", (source,)
        )
        assert conn.execute("SELECT dirty_revision FROM resource_source_heads").fetchone()[0] > 0


def test_pg_concurrent_manifest_publishers_keep_one_complete_generation(publication):
    pool, arguments = publication
    barrier = threading.Barrier(8)

    def publish(number):
        barrier.wait(timeout=10)
        try:
            return ResourceManifestRepo(pool).publish(
                **{**arguments, "manifest_sha256": str(number) * 64}
            )
        except ManifestConflict:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(publish, range(8)))
    winners = [row for row in results if row is not None]
    assert len(winners) == 1
    repo = ResourceManifestRepo(pool)
    assert repo.head(arguments["agent_id"]) == winners[0]
    assert len(repo.events()) == 1


def test_pg_revocation_prevents_reusing_an_existing_manifest(publication):
    pool, arguments = publication
    repo = ResourceManifestRepo(pool)
    first = repo.publish(**arguments)
    with pool.transaction() as conn:
        conn.execute("DELETE FROM expert_assignments WHERE user_id=?", (arguments["user_id"],))
    with pytest.raises(ManifestConflict, match="resource_not_assigned"):
        repo.publish(**arguments)
    assert repo.head(arguments["agent_id"]) == first
    assert len(repo.events()) == 1
