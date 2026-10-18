"""Postgres restore must never turn a failed transaction into success."""

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from octop.infra.backup.pg_dump import restore_postgres
from octop.infra.errors import OctopError


@pytest.mark.parametrize("code", [1, 2, -9])
def test_restore_rejects_every_failure(monkeypatch, tmp_path: Path, code: int):
    monkeypatch.setattr("octop.infra.backup.pg_dump._require_tool", lambda name: name)

    def run(command, **kwargs):
        assert "--single-transaction" in command
        assert "--exit-on-error" in command
        return CompletedProcess(command, code, "", "permission denied for schema public")

    monkeypatch.setattr("octop.infra.backup.pg_dump.subprocess.run", run)
    with pytest.raises(OctopError, match="permission denied"):
        restore_postgres("test-database", tmp_path / "backup.dump")


def test_real_postgres_restore_rolls_back_on_error(tmp_path: Path):
    import os
    import uuid

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from octop.infra.backup.pg_dump import dump_postgres

    admin_url = os.environ.get("OCTOP_IM_TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("dedicated temporary PostgreSQL admin URL required")
    name = "octop_restore_test_" + uuid.uuid4().hex[:12]
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    params = conninfo_to_dict(admin_url)
    params["dbname"] = name
    conninfo = make_conninfo(**params)
    try:
        with psycopg.connect(conninfo) as conn:
            conn.execute("CREATE TABLE proof (id integer PRIMARY KEY, text_value text)")
            conn.execute("INSERT INTO proof VALUES (1, 'archived')")
        dump = tmp_path / "backup.dump"
        dump_postgres(conninfo, dump)
        with psycopg.connect(conninfo) as conn:
            conn.execute("UPDATE proof SET text_value='current'")
        with pytest.raises(OctopError):
            restore_postgres(
                make_conninfo(conninfo, options="-c default_transaction_read_only=on"), dump
            )
        with psycopg.connect(conninfo) as conn:
            assert conn.execute("SELECT text_value FROM proof").fetchone()[0] == "current"
        restore_postgres(conninfo, dump)
        with psycopg.connect(conninfo) as conn:
            assert conn.execute("SELECT text_value FROM proof").fetchone()[0] == "archived"
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.mark.parametrize("include_chats", [False, True])
def test_real_postgres_company_archive_relations(tmp_path, include_chats):
    import os
    import uuid

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from octop.config import DatabaseConfig, OctopConfig
    from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import PostgresPool
    from octop.infra.db.repos.company_runtime import CompanyRuntimeRepo
    from octop.infra.db.services import build_shared_services
    from octop.infra.utils.paths import PathLayout

    admin_url = os.environ.get("OCTOP_IM_TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("dedicated temporary PostgreSQL admin URL required")
    name = "octop_archive_test_" + uuid.uuid4().hex[:12]
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    params = conninfo_to_dict(admin_url)
    params["dbname"] = name
    dsn = make_conninfo(**params)
    pool = PostgresPool(dsn)
    try:
        run_migrations(pool)
        paths = PathLayout(tmp_path)
        services = build_shared_services(db=pool, paths=paths, config=OctopConfig())
        owner = services.user_repo.create(username="employee", role="user")
        services.agent_repo.create(agent_id="expert", user_id=owner, name="Expert")
        services.thread_repo.insert(
            thread_id="thread",
            agent_id="expert",
            user_id=owner,
            channel_type="dashboard",
            session_key="thread",
            last_active=0,
        )
        repo = CompanyRuntimeRepo(pool)
        repo.acquire(
            task_id="task",
            thread_id="thread",
            owner=owner,
            agent_id="expert",
            device_id="device",
            request={},
            expert={},
            installation_hash="installation",
            recovery_hash="recovery",
        )
        repo.append_event("task", "event", 1, {"kind": "note"})
        repo.put_checkpoint("thread", "", "checkpoint", {"saved": True})
        repo.put_writes("thread", "", "checkpoint", "write", [{"idx": 0, "value": "write"}])
        repo.begin_operation("task", "operation", "hash")
        with pool.connect() as conn:
            conn.execute("INSERT INTO desktop_file_threads VALUES (?)", ("thread",))
        archive = tmp_path / "system.tar.gz"
        config = DatabaseConfig(driver="postgresql", url=dsn)
        create_system_backup(
            paths=paths,
            agent_rows=[],
            pool=pool,
            db_config=config,
            dest=archive,
            include_chats=include_chats,
        )
        repo.transition("task", "completed")
        restore_system_backup(
            archive, paths=paths, pool=pool, db_config=config, restore_config=False
        )
        assert repo.task("task")["status"] == ("running" if include_chats else "completed")
        assert len(repo.events("task")) == 1
        assert repo.checkpoint("thread", "", None)["pending_writes"] == ["write"]
        assert repo.begin_operation("task", "operation", "hash")["status"] == "running"
        with pool.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM desktop_file_threads").fetchone()[0] == 1
    finally:
        pool.close()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
