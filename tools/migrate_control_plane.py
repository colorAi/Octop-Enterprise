#!/usr/bin/env python3
"""Offline SQLite -> PostgreSQL import. The source is opened read-only.

Default: inventory only. --apply requires an acknowledged maintenance window,
a target URL supplied through an environment variable, and an empty target.
Existing schema defaults are accepted only when every table exactly matches a
freshly migrated database. AI file stores remain at the existing mount paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Json, Jsonb

from octop.infra.db.migrate import _discover, run_migrations
from octop.infra.db.pool import PostgresPool, SqlitePool


def digest_rows(rows: list[tuple[Any, ...]]) -> str:
    def encode(value: Any) -> Any:
        if isinstance(value, (bytes, memoryview)):
            return {"bytes": bytes(value).hex()}
        return value

    hashes = sorted(
        hashlib.sha256(
            json.dumps([encode(v) for v in row], ensure_ascii=False, separators=(",", ":")).encode()
        ).digest()
        for row in rows
    )
    return hashlib.sha256(b"".join(hashes)).hexdigest()


def sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'_schema_version' ORDER BY name"
        )
    ]


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def pin_memory(conn: sqlite3.Connection) -> list[str]:
    changed = []
    for agent_id, raw in conn.execute("SELECT agent_id,config_json FROM agents").fetchall():
        config = json.loads(raw or "{}")
        if not isinstance(config, dict):
            raise ValueError("invalid agent configuration")
        memory = config.get("memory")
        if memory is None:
            memory = {}
            config["memory"] = memory
        if not isinstance(memory, dict):
            raise ValueError("invalid agent memory configuration")
        if memory.get("backend") is None:
            memory["backend"] = {"type": "sqlite"}
            conn.execute(
                "UPDATE agents SET config_json=? WHERE agent_id=?",
                (json.dumps(config, ensure_ascii=False), agent_id),
            )
            changed.append(agent_id)
    return changed


def target_tables(conn: psycopg.Connection[Any]) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'_schema_version' ORDER BY tablename"
        )
    ]


def migration_defaults() -> dict[str, str]:
    # Derive the only permissible target defaults from a fresh current schema.
    with tempfile.TemporaryDirectory() as tmp:
        p = SqlitePool(Path(tmp) / "default.sqlite")
        try:
            run_migrations(p)
            with p.connect() as conn:
                result = {}
                for t in sqlite_tables(conn):
                    names = sorted(r[1] for r in conn.execute(f"PRAGMA table_info({quote(t)})"))
                    if t == "user_role":
                        names = [n for n in names if n not in {"created_at", "updated_at"}]
                    rows = [
                        tuple(r)
                        for r in conn.execute(
                            "SELECT " + ",".join(quote(n) for n in names) + " FROM " + quote(t)
                        )
                    ]
                    result[t] = digest_rows(rows)
                return result
        finally:
            p.close()


def import_database(source: Path, dsn: str, *, data_root: Path | None = None) -> dict[str, Any]:
    if not dsn.strip():
        raise ValueError("an explicit target database DSN is required")
    # Backup includes committed WAL data and never modifies the original database.
    with tempfile.TemporaryDirectory() as tmp:
        copy_path = Path(tmp) / "source.sqlite"
        with (
            sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original,
            sqlite3.connect(copy_path) as copied,
        ):
            original.backup(copied)
        copied_pool = SqlitePool(copy_path)
        try:
            with copied_pool.connect() as c:
                source_version = c.execute("SELECT version FROM _schema_version").fetchone()[0]
                if source_version > max(v for v, _ in _discover()):
                    raise ValueError("source schema version is newer than this migration tool")
                has_sequences = c.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
                ).fetchone()
                original_watermarks = (
                    {r[0]: int(r[1]) for r in c.execute("SELECT name,seq FROM sqlite_sequence")}
                    if has_sequences
                    else {}
                )
            run_migrations(copied_pool)
            with copied_pool.connect() as copied:
                if copied.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("source has foreign key violations")
                pinned = pin_memory(copied)
                tables = sqlite_tables(copied)
                high_water = {
                    r[0]: int(r[1]) for r in copied.execute("SELECT name,seq FROM sqlite_sequence")
                }
                for table, high in original_watermarks.items():
                    high_water[table] = max(high_water.get(table, 0), high)
                with psycopg.connect(dsn, autocommit=True) as target:
                    if target.info.dbname in {"postgres", "template0", "template1"}:
                        raise ValueError("system databases cannot be migration targets")
                    schema_row = target.execute(
                        "SELECT to_regclass('public._schema_version')"
                    ).fetchone()
                    assert schema_row is not None
                    if schema_row[0]:
                        schema_watermark = target.execute(
                            "SELECT version FROM _schema_version"
                        ).fetchone()
                        if schema_watermark and schema_watermark[0] > max(
                            v for v, _ in _discover("postgresql")
                        ):
                            raise ValueError("target schema is newer than this migration tool")
                    existing = target_tables(target)
                    defaults = migration_defaults()
                    for t in existing:
                        names = sorted(
                            r[0]
                            for r in target.execute(
                                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
                                (t,),
                            )
                        )
                        if t == "user_role":
                            names = [n for n in names if n not in {"created_at", "updated_at"}]
                        rows = target.execute(
                            sql.SQL("SELECT {} FROM {}").format(
                                sql.SQL(",").join(map(sql.Identifier, names)), sql.Identifier(t)
                            )
                        ).fetchall()
                        if t not in defaults or digest_rows(rows) != defaults[t]:
                            raise ValueError(
                                "target contains non-default data; use a new empty rehearsal database"
                            )
                    pool = PostgresPool(dsn)
                    try:
                        run_migrations(pool)
                    finally:
                        pool.close()
                    target_names = target_tables(target)
                    if tables != target_names:
                        raise ValueError(
                            f"table mismatch: source-only={sorted(set(tables) - set(target_names))}, target-only={sorted(set(target_names) - set(tables))}"
                        )
                    columns = {}
                    json_columns = {}
                    for t in tables:
                        names = sorted(
                            r[1] for r in copied.execute(f"PRAGMA table_info({quote(t)})")
                        )
                        target_columns = sorted(
                            r[0]
                            for r in target.execute(
                                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
                                (t,),
                            )
                        )
                        if names != target_columns:
                            raise ValueError(f"column mismatch in {t}; refusing to skip fields")
                        columns[t] = names
                        json_columns[t] = {
                            r[0]: r[1]
                            for r in target.execute(
                                "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND data_type IN ('json','jsonb')",
                                (t,),
                            )
                        }
                    report: dict[str, Any] = {
                        "source": str(source.resolve()),
                        "memory_pinned": pinned,
                        "tables": {},
                        "ai_files": "retained at existing paths",
                        "sequences": {},
                    }
                    with target.transaction():
                        for table in tables:
                            target.execute(
                                sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(
                                    sql.Identifier(table)
                                )
                            )
                        # Recheck after locking so concurrent writes can never be deleted as defaults.
                        for table in tables:
                            check_columns = [
                                n
                                for n in columns[table]
                                if table != "user_role" or n not in {"created_at", "updated_at"}
                            ]
                            current_rows = target.execute(
                                sql.SQL("SELECT {} FROM {}").format(
                                    sql.SQL(",").join(map(sql.Identifier, check_columns)),
                                    sql.Identifier(table),
                                )
                            ).fetchall()
                            if digest_rows(current_rows) != defaults[table]:
                                raise ValueError(
                                    "target gained business data during migration preparation"
                                )
                        # Defer FK checks during import, then restore the original constraint behavior.
                        fks = target.execute(
                            "SELECT c.conname,t.relname,c.condeferrable,c.condeferred FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE c.contype='f' AND n.nspname='public'"
                        ).fetchall()
                        for name, table, _, _ in fks:
                            target.execute(
                                sql.SQL(
                                    "ALTER TABLE {} ALTER CONSTRAINT {} DEFERRABLE INITIALLY DEFERRED"
                                ).format(sql.Identifier(table), sql.Identifier(name))
                            )
                        for trigger in (
                            "communication_user_insert",
                            "communication_user_update",
                            "communication_user_delete",
                        ):
                            target.execute(
                                sql.SQL("ALTER TABLE users DISABLE TRIGGER {}").format(
                                    sql.Identifier(trigger)
                                )
                            )
                        # Only freshly bootstrapped defaults can reach here; clear them together.
                        for t in tables:
                            target.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(t)))
                        for t in tables:
                            names = columns[t]
                            select = (
                                "SELECT " + ",".join(quote(n) for n in names) + " FROM " + quote(t)
                            )
                            rows = [
                                tuple(
                                    json.loads(v) if n in json_columns[t] and v is not None else v
                                    for n, v in zip(names, r, strict=True)
                                )
                                for r in copied.execute(select)
                            ]
                            statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                                sql.Identifier(t),
                                sql.SQL(",").join(map(sql.Identifier, names)),
                                sql.SQL(",").join(sql.Placeholder() for _ in names),
                            )
                            with target.cursor() as cur:
                                adapted = [
                                    tuple(
                                        (Jsonb(v) if json_columns[t][n] == "jsonb" else Json(v))
                                        if n in json_columns[t] and v is not None
                                        else v
                                        for n, v in zip(names, row, strict=True)
                                    )
                                    for row in rows
                                ]
                                cur.executemany(statement, adapted)
                            imported = target.execute(
                                sql.SQL("SELECT {} FROM {}").format(
                                    sql.SQL(",").join(map(sql.Identifier, names)), sql.Identifier(t)
                                )
                            ).fetchall()
                            if len(imported) != len(rows) or digest_rows(imported) != digest_rows(
                                rows
                            ):
                                mismatched = [
                                    n
                                    for i, n in enumerate(names)
                                    if digest_rows([(r[i],) for r in imported])
                                    != digest_rows([(r[i],) for r in rows])
                                ]
                                raise ValueError(
                                    f"verification failed in {t}: columns={mismatched}"
                                )
                            report["tables"][t] = {"rows": len(rows), "sha256": digest_rows(rows)}
                            for col in names:
                                seq_row = target.execute(
                                    "SELECT pg_get_serial_sequence(%s,%s)", (t, col)
                                ).fetchone()
                                assert seq_row is not None
                                seq = seq_row[0]
                                if seq:
                                    maximum_row = target.execute(
                                        sql.SQL("SELECT COALESCE(MAX({}),0) FROM {}").format(
                                            sql.Identifier(col), sql.Identifier(t)
                                        )
                                    ).fetchone()
                                    assert maximum_row is not None
                                    maximum = maximum_row[0]
                                    watermark = max(int(maximum), high_water.get(t, 0))
                                    target.execute(
                                        "SELECT setval(%s::regclass,%s,%s)",
                                        (seq, max(1, watermark), watermark > 0),
                                    )
                                    report["sequences"][t] = watermark
                        target.execute("SET CONSTRAINTS ALL IMMEDIATE")
                        for name, table, deferrable, deferred in fks:
                            suffix = (
                                "DEFERRABLE INITIALLY DEFERRED"
                                if deferred
                                else "DEFERRABLE INITIALLY IMMEDIATE"
                                if deferrable
                                else "NOT DEFERRABLE"
                            )
                            target.execute(
                                sql.SQL("ALTER TABLE {} ALTER CONSTRAINT {} ").format(
                                    sql.Identifier(table), sql.Identifier(name)
                                )
                                + sql.SQL(suffix)
                            )
                        for trigger in (
                            "communication_user_insert",
                            "communication_user_update",
                            "communication_user_delete",
                        ):
                            target.execute(
                                sql.SQL("ALTER TABLE users ENABLE TRIGGER {}").format(
                                    sql.Identifier(trigger)
                                )
                            )
                    return report
        finally:
            copied_pool.close()


def inventory(source: Path, data_root: Path | None) -> dict[str, Any]:
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {quote(t)}").fetchone()[0]
            for t in sqlite_tables(conn)
        }
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    return {
        "source": str(source.resolve()),
        "schema_version": version,
        "rows": counts,
        "ai_files": sorted(str(p.relative_to(data_root)) for p in data_root.rglob("*.sqlite*"))
        if data_root
        else [],
        "writes": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--target-dsn-env", default="OCTOP_MIGRATION_DATABASE_URL")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--maintenance-confirmed", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.maintenance_confirmed:
        parser.error(
            "--apply requires --maintenance-confirmed: stop application and automation writes; back up AI files and source database first"
        )
    report = (
        import_database(args.source, os.environ[args.target_dsn_env], data_root=args.data_root)
        if args.apply
        else inventory(args.source, args.data_root)
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
