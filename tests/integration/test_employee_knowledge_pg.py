"""Knowledge grant migration and ACLs on an isolated PostgreSQL schema."""

import os
import uuid

import pytest

from tests.support.postgresql import requires_postgresql


@requires_postgresql
@pytest.mark.postgresql
def test_employee_knowledge_postgresql() -> None:
    from types import SimpleNamespace

    import psycopg
    from psycopg.conninfo import make_conninfo

    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import PostgresPool
    from octop.infra.db.repos.knowledge import KnowledgeRepo
    from octop.infra.db.repos.users import UserRepo
    from octop.infra.knowledge.access import KnowledgeAccess

    conninfo = os.environ["OCTOP_TEST_DATABASE_URL"]
    schema = f"knowledge_test_{uuid.uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    pool = PostgresPool(make_conninfo(conninfo, options=f"-csearch_path={schema}"))
    try:
        run_migrations(pool)
        users = UserRepo(pool)
        repo = KnowledgeRepo(pool)
        admin = users.create(username="admin", role="admin")
        employee = users.create(username="employee", role="user")
        other = users.create(username="other", role="user")
        base = repo.create_base(owner_user_id=admin, name="Company knowledge")
        with pool.transaction() as conn:
            conn.execute("DROP TABLE employee_knowledge_bases")
            conn.execute("UPDATE _schema_version SET version = 29")
        run_migrations(pool)
        access = KnowledgeAccess(SimpleNamespace(user_repo=users, knowledge_repo=repo))
        access.set(employee, [base.id], actor="admin")
        assert {row.id for row in repo.list_visible(employee)} == {base.id}
        assert repo.list_visible(other) == []
        assert repo.has_assignment(employee, base.id)
        access.set(employee, [], actor="admin")
        assert repo.list_visible(employee) == []
        access.set(employee, [base.id], actor="admin")
        repo.delete_base(base.id)
        assert repo.assigned_base_ids(employee) == []
    finally:
        pool.close()
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
