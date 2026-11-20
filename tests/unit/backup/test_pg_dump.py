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


@pytest.mark.parametrize(
    ("archive_version", "include_chats", "fail_migration", "preserve_users"),
    [
        (36, False, False, False),
        (36, True, False, False),
        (32, False, False, False),
        (33, False, False, False),
        (34, False, False, False),
        (35, False, False, False),
        (35, True, False, False),
        (35, False, True, False),
        (36, False, False, True),
    ],
)
def test_real_postgres_company_archive_relations(
    tmp_path, monkeypatch, archive_version, include_chats, fail_migration, preserve_users
):
    import os
    import uuid

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from octop.config import DatabaseConfig, OctopConfig
    from octop.infra.backup import system_archive
    from octop.infra.db import migrate
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
        migrations = migrate._discover("postgresql")
        with monkeypatch.context() as patch:
            patch.setattr(
                migrate,
                "_discover",
                lambda _: [(v, p) for v, p in migrations if v <= archive_version],
            )
            migrate.run_migrations(pool)
        paths = PathLayout(tmp_path)
        paths.config.write_text('{"label": "archive"}', encoding="utf-8")
        services = build_shared_services(db=pool, paths=paths, config=OctopConfig())
        owner = services.user_repo.create(username="employee", role="user")
        services.agent_repo.create(agent_id="expert", user_id=owner, name="Expert")
        services.company_project_repo.register("project", owner, "installation", "Project")
        services.company_project_repo.create_thread(
            "project",
            owner,
            "installation",
            thread_id="thread",
            agent_id="expert",
            device_id="device",
            title="Conversation",
        )
        repo = CompanyRuntimeRepo(pool)
        repo.acquire(
            task_id="task",
            thread_id="thread",
            owner=owner,
            agent_id="expert",
            device_id="device",
            request={"project_id": "project"},
            expert={},
            installation_hash="installation",
            recovery_hash="recovery",
        )
        repo.append_event("task", "event", 1, {"kind": "note"})
        repo.put_checkpoint("thread", "", "z-older-checkpoint", {"saved": False})
        repo.put_checkpoint("thread", "", "checkpoint", {"saved": True})
        repo.put_writes("thread", "", "checkpoint", "write", [{"idx": 0, "value": "write"}])
        repo.begin_operation("task", "operation", "hash")
        with pool.connect() as conn:
            conn.execute("INSERT INTO desktop_file_threads VALUES (?)", ("thread",))
        archive = tmp_path / "system.tar.gz"
        config = DatabaseConfig(driver="postgresql", url=dsn)
        system_archive.create_system_backup(
            paths=paths,
            agent_rows=[],
            pool=pool,
            db_config=config,
            dest=archive,
            include_chats=include_chats,
        )
        migrate.run_migrations(pool)
        repo.transition("task", "completed")
        paths.config.write_text('{"label": "current"}', encoding="utf-8")
        extra_user = services.user_repo.create(
            username="current-only", role="user", permissions=["test_permission"]
        )
        with pool.connect() as conn:
            if preserve_users:
                conn.execute(
                    "UPDATE users SET expert_access_mode='assigned_only' WHERE id=?", (extra_user,)
                )

        def restore():
            return system_archive.restore_system_backup(
                archive, paths=paths, pool=pool, db_config=config, preserve_users=preserve_users
            )

        if fail_migration:

            def refuse(_pool):
                # Fail after migrations have replaced the old schema.
                migrate.run_migrations(_pool)
                raise RuntimeError("injected migration failure")

            monkeypatch.setattr(system_archive, "run_migrations", refuse)
            with pytest.raises(OctopError, match="injected migration failure"):
                restore()
            assert services.user_repo.get(extra_user) is not None
            assert repo.task("task")["status"] == "completed"
            assert paths.config.read_text(encoding="utf-8") == '{"label": "current"}'
        else:
            assert restore()["schema_version"] == 36
            if preserve_users:
                retained = services.user_repo.get(extra_user)
                assert retained is not None
                assert retained.permissions == ["test_permission"]
                with pool.connect() as conn:
                    row = conn.execute(
                        "SELECT expert_access_mode FROM users WHERE id=?", (extra_user,)
                    ).fetchone()
                    assert row[0] == "assigned_only"
                assert services.user_repo.create(username="later", role="user") > extra_user
            else:
                assert services.user_repo.get(extra_user) is None
            assert paths.config.read_text(encoding="utf-8") == '{"label": "archive"}'
        assert migrate._current_version(pool) == 36
        assert repo.task("task")["status"] == ("running" if include_chats else "completed")
        assert len(repo.events("task")) == 1
        assert repo.checkpoint("thread", "", None) == {"saved": True, "pending_writes": ["write"]}
        assert repo.thread_state("thread")["project_id"] == "project"
        assert repo.begin_operation("task", "operation", "hash")["status"] == "running"
        with pool.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM desktop_file_threads").fetchone()[0] == 1
    finally:
        pool.close()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
