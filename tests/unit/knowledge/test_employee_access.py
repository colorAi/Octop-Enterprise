"""Employee grants must cover browsing and live retrieval without granting writes."""

from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import OctopError
from octop.infra.knowledge import retrieve as retrieval
from octop.infra.knowledge.access import KnowledgeAccess
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.service import KnowledgeService


@pytest.fixture
def company(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "knowledge.db")
    run_migrations(pool)
    services = SimpleNamespace(
        knowledge_repo=KnowledgeRepo(pool),
        user_repo=UserRepo(pool),
        settings_repo=SettingsRepo(pool),
    )
    admin = services.user_repo.create(username="admin", role="admin")
    employee = services.user_repo.create(username="employee", role="user")
    other = services.user_repo.create(username="other", role="user")
    base = services.knowledge_repo.create_base(owner_user_id=admin, name="Company procedures")
    shared = services.knowledge_repo.create_base(owner_user_id=admin, name="Welcome", shared=True)
    yield pool, services, admin, employee, other, base, shared
    pool.close()


def test_grants_read_only_and_revocation_preserves_shared(company):
    pool, services, admin, employee, other, base, shared = company
    access = KnowledgeAccess(services)
    service = KnowledgeService(services)
    access.set(employee, [base.id, base.id], actor="admin")
    assert access.get(employee)["knowledge_base_ids"] == [base.id]
    assert {row.id for row in services.knowledge_repo.list_visible(employee)} == {
        base.id,
        shared.id,
    }
    assert {row.id for row in services.knowledge_repo.list_visible(other)} == {shared.id}
    assert service.get_readable_base(base.id, actor_user_id=employee).id == base.id
    with pytest.raises(PermissionError):
        service.get_readable_base(base.id, actor_user_id=other)
    with pytest.raises(PermissionError):
        service.get_writable_base(base.id, actor_user_id=employee)
    with pytest.raises(PermissionError):
        service.delete_base(base.id, actor_user_id=employee)
    access.set(employee, [], actor="admin")
    with pytest.raises(PermissionError):
        service.get_readable_base(base.id, actor_user_id=employee)
    assert service.get_readable_base(shared.id, actor_user_id=employee).id == shared.id
    with pool.connect() as conn:
        audit = conn.execute(
            "SELECT actor, payload FROM audit_log WHERE action = 'employee.knowledge_bases' ORDER BY ts"
        ).fetchall()
    assert len(audit) == 2
    assert all(row["actor"] == "admin" for row in audit)


def test_invalid_selection_preserves_grants_and_personal_bases(company):
    _pool, services, _admin, employee, other, base, _shared = company
    access = KnowledgeAccess(services)
    personal = services.knowledge_repo.create_base(owner_user_id=other, name="Private notes")
    access.set(employee, [base.id], actor="admin")
    for ids in (["missing"], [personal.id], [base.id, personal.id]):
        with pytest.raises(OctopError):
            access.set(employee, ids, actor="admin")
        assert access.get(employee)["knowledge_base_ids"] == [base.id]
    assert personal.id not in {item["id"] for item in access.get(employee)["items"]}
    assert services.knowledge_repo.get_base(personal.id) is not None
    services.knowledge_repo.delete_base(base.id)
    assert access.get(employee)["knowledge_base_ids"] == []


@pytest.mark.asyncio
async def test_chat_retrieval_rechecks_grants_after_revocation(company, monkeypatch):
    _pool, services, _admin, employee, other, base, _shared = company
    document = services.knowledge_repo.create_document(
        kb_id=base.id,
        filename="policy.md",
        content_type="text/markdown",
        byte_size=10,
        status="ready",
    )
    KnowledgeIndex(base.id).replace_doc_chunks(document.id, ["Company policy fact"], [[1.0, 0.0]])
    monkeypatch.setattr(retrieval, "assert_knowledge_usable", lambda *_: None)
    monkeypatch.setattr(retrieval, "embed_knowledge_texts", lambda *_: [[1.0, 0.0]])

    async def search(user_id):
        return await retrieval.retrieve_context(
            services, user_id=user_id, is_admin=False, query="policy", knowledge_base_ids=[base.id]
        )

    access = KnowledgeAccess(services)
    access.set(employee, [base.id], actor="admin")
    assert "Company policy fact" in await search(employee)
    assert await search(other) == ""
    access.set(employee, [], actor="admin")
    assert await search(employee) == ""


def test_existing_v29_database_is_upgraded_without_losing_knowledge(company):
    pool, services, _admin, _employee, _other, base, shared = company
    with pool.transaction() as conn:
        conn.execute("DROP TABLE employee_knowledge_bases")
        conn.execute("UPDATE _schema_version SET version = 29")
    run_migrations(pool)
    run_migrations(pool)
    assert {row.id for row in services.knowledge_repo.list_all()} == {base.id, shared.id}
    with pool.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 30
        assert conn.execute("SELECT COUNT(*) FROM employee_knowledge_bases").fetchone()[0] == 0
