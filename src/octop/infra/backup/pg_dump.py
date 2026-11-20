"""PostgreSQL dump/restore via client tools on PATH."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from octop.infra.db.migrate import _discover
from octop.infra.db.pool import DatabasePool
from octop.infra.errors import ErrorCode, OctopError

_MIGRATION_OBJECT = re.compile(
    r"^CREATE\s+(?:OR REPLACE\s+)?(TABLE|FUNCTION)\s+(?:IF NOT EXISTS\s+)?(\w+)",
    re.IGNORECASE | re.MULTILINE,
)


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"{name} not found on PATH; required for PostgreSQL backup/restore",
        )
    return path


def dump_postgres(
    conninfo: str,
    dest: Path,
    *,
    exclude_table_data: Sequence[str] = (),
) -> None:
    pg_dump = _require_tool("pg_dump")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [pg_dump, "-Fc", "-f", str(dest), "--dbname", conninfo]
    for table in exclude_table_data:
        cmd.extend(["--exclude-table-data", table])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"pg_dump failed: {proc.stderr.strip() or proc.stdout.strip()}",
        )


def restore_postgres(conninfo: str, dump_file: Path, *, cleanup_sql: str = "") -> None:
    pg_restore = _require_tool("pg_restore")
    options = ["--clean", "--if-exists", "--no-owner"]
    if cleanup_sql:
        # pg_restore only cleans objects listed in its input. Apply the extra
        # cleanup and the regular restore script within the same transaction.
        psql = _require_tool("psql")
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "restore.sql"
            cleanup = Path(directory) / "cleanup.sql"
            cleanup.write_text(cleanup_sql, encoding="utf-8")
            _run_restore([pg_restore, *options, "--file", str(script), str(dump_file)])
            _run_restore(
                [
                    psql,
                    "--no-psqlrc",
                    "--single-transaction",
                    "--set=ON_ERROR_STOP=1",
                    "--dbname",
                    conninfo,
                    "--file",
                    str(cleanup),
                    "--file",
                    str(script),
                ]
            )
        return
    _run_restore(
        [
            pg_restore,
            *options,
            "--single-transaction",
            "--exit-on-error",
            "--dbname",
            conninfo,
            str(dump_file),
        ]
    )


def _run_restore(command: list[str]) -> None:
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"pg_restore failed: {proc.stderr.strip() or proc.stdout.strip()}",
        )


def _cleanup_newer_objects(pool: DatabasePool, schema_version: int) -> str:
    from psycopg import sql

    # Derive the scope from Octop's migrations so other database objects stay
    # out of the cleanup. Existing tables will be recreated by pg_restore.
    names: dict[str, set[str]] = {"TABLE": set(), "FUNCTION": set()}
    for version, path in _discover("postgresql"):
        if version > schema_version:
            for kind, name in _MIGRATION_OBJECT.findall(path.read_text(encoding="utf-8")):
                names[kind.upper()].add(name.lower())
    if not any(names.values()):
        return ""

    with pool.connect() as conn:
        tables = conn.execute(
            "SELECT schemaname, tablename FROM pg_tables"
            " WHERE schemaname=current_schema() AND tablename=ANY(?) ORDER BY tablename",
            (sorted(names["TABLE"]),),
        ).fetchall()
        routines = conn.execute(
            "SELECT p.oid, n.nspname, p.proname, pg_get_function_identity_arguments(p.oid)"
            " FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace"
            " WHERE n.nspname=current_schema() AND p.proname=ANY(?) AND p.prokind='f'"
            " ORDER BY p.oid",
            (sorted(names["FUNCTION"]),),
        ).fetchall()
        triggers = conn.execute(
            "SELECT t.tgname, n.nspname, c.relname FROM pg_trigger t"
            " JOIN pg_class c ON c.oid=t.tgrelid"
            " JOIN pg_namespace n ON n.oid=c.relnamespace"
            " WHERE n.nspname=current_schema() AND NOT t.tgisinternal"
            " AND t.tgfoid=ANY(?::oid[]) ORDER BY t.oid",
            ([row[0] for row in routines],),
        ).fetchall()

    statements = [
        sql.SQL("DROP TRIGGER {} ON {};").format(
            sql.Identifier(row[0]), sql.Identifier(row[1], row[2])
        )
        for row in triggers
    ]
    if tables:
        statements.append(
            sql.SQL("DROP TABLE {};").format(
                sql.SQL(", ").join(sql.Identifier(row[0], row[1]) for row in tables)
            )
        )
    statements.extend(
        sql.SQL("DROP FUNCTION {}({});").format(sql.Identifier(row[1], row[2]), sql.SQL(row[3]))
        for row in routines
    )
    return "\n".join(statement.as_string() for statement in statements)


@contextmanager
def replace_postgres_for_restore(
    conninfo: str,
    dump_file: Path,
    *,
    pool: DatabasePool,
    schema_version: int,
    recovery_root: Path,
) -> Iterator[None]:
    """Keep a recovery dump until schema upgrades and database replay succeed."""
    cleanup_sql = _cleanup_newer_objects(pool, schema_version)
    recovery_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=".postgres-restore-", dir=recovery_root))
    snapshot = directory / "before-restore.dump"
    keep_recovery = False
    try:
        dump_postgres(conninfo, snapshot)
        restore_postgres(conninfo, dump_file, cleanup_sql=cleanup_sql)
        try:
            yield
        except Exception:
            try:
                restore_postgres(conninfo, snapshot)
            except Exception as recovery_error:
                keep_recovery = True
                raise OctopError(
                    ErrorCode.INTERNAL_ERROR,
                    f"PostgreSQL restore recovery failed. Recovery dump: {snapshot}",
                    details={"recovery_dump": str(snapshot)},
                ) from recovery_error
            raise
    finally:
        if not keep_recovery:
            snapshot.unlink(missing_ok=True)
            directory.rmdir()
