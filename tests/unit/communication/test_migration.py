from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from tools.migrate_control_plane import import_database, inventory, pin_memory

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def source(tmp_path: Path):
    path = tmp_path / "original.sqlite"
    pool = SqlitePool(path)
    run_migrations(pool)
    users = UserRepo(pool)
    user = users.create(username="alice", role="admin", password_hash="retained-hash")
    with pool.transaction() as conn:
        conn.execute("INSERT INTO secrets(k,v,created_at)VALUES('jwt',?,1)", (b"old-key",))
        conn.execute(
            "INSERT INTO agents(agent_id,user_id,name,config_json,created_at,updated_at)VALUES('old-agent',?,'Old',?,1,1)",
            (
                user,
                json.dumps(
                    {"memory": {"other": True}, "workspace_dir": str(tmp_path / "workspace")}
                ),
            ),
        )
        conn.execute("UPDATE sqlite_sequence SET seq=900 WHERE name='users'")
    pool.close()
    return path


@pytest.fixture
def target():
    admin = os.environ.get("OCTOP_IM_TEST_DATABASE_URL")
    if not admin:
        pytest.skip("isolated PostgreSQL admin URL not provided")
    name = "octop_migration_test_" + uuid.uuid4().hex[:12]
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    args = conninfo_to_dict(admin)
    args["dbname"] = name
    yield make_conninfo(**args)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_inventory_and_pin_do_not_modify_source(source):
    report = inventory(source, source.parent)
    assert report["rows"]["users"] == 1 and report["writes"] is False
    with sqlite3.connect(source) as conn:
        raw = conn.execute("SELECT config_json FROM agents").fetchone()[0]
        assert "backend" not in json.loads(raw)["memory"]
        conn.execute("BEGIN")
        assert pin_memory(conn) == ["old-agent"]
        changed = json.loads(conn.execute("SELECT config_json FROM agents").fetchone()[0])
        assert changed["memory"] == {"other": True, "backend": {"type": "sqlite"}}
        assert changed["workspace_dir"] == json.loads(raw)["workspace_dir"]
        conn.rollback()


def test_real_import_preserves_ids_secrets_memory_and_high_water(source, target):
    before = source.read_bytes()
    report = import_database(source, target)
    assert source.read_bytes() == before
    assert report["memory_pinned"] == ["old-agent"]
    assert report["sequences"]["users"] == 900
    with psycopg.connect(target) as conn:
        assert conn.execute("SELECT v FROM secrets WHERE k='jwt'").fetchone()[0] == b"old-key"
        assert conn.execute("SELECT username,password_hash,id FROM users").fetchone() == (
            "alice",
            "retained-hash",
            1,
        )
        row = conn.execute(
            "INSERT INTO users(username,password_hash,role,created_at)VALUES('new','hash','user',1) RETURNING id"
        ).fetchone()
        assert row[0] == 901
        assert (
            conn.execute(
                "SELECT version FROM communication_identities WHERE user_id=901"
            ).fetchone()[0]
            == 1
        )
        conn.execute("DELETE FROM users WHERE id=901")
        assert (
            json.loads(
                conn.execute(
                    "SELECT payload FROM communication_identities WHERE user_id=901"
                ).fetchone()[0]
            )["status"]
            == "deleted"
        )
    with pytest.raises(ValueError, match="target contains"):
        import_database(source, target)


def test_unknown_table_fails_and_bootstrap_target_can_retry(source, target):
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE unexpected(data TEXT)")
    with pytest.raises(ValueError, match="table mismatch"):
        import_database(source, target)
    with sqlite3.connect(source) as conn:
        conn.execute("DROP TABLE unexpected")
    assert import_database(source, target)["tables"]["users"]["rows"] == 1


def test_unknown_column_and_future_source_version_are_rejected(source, target):
    with sqlite3.connect(source) as conn:
        conn.execute("ALTER TABLE users ADD COLUMN unsupported TEXT")
    with pytest.raises(ValueError, match="column mismatch"):
        import_database(source, target)
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE _schema_version SET version=999")
    with pytest.raises(ValueError, match="source schema version"):
        import_database(source, target)
