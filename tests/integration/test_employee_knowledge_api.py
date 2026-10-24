"""Administrator knowledge assignment through the same API used by employee clients."""

from octop.api.app import build_app
from octop.infra.knowledge.service import KnowledgeService
from tests.support.auth import create_user


async def test_employee_knowledge_assignment_browsing_revocation_and_acl(env):
    client, server, admin_auth = env
    employee_auth = await create_user(
        client, admin_auth, username="knowledge_employee", permissions=["knowledge_bases"]
    )
    other_auth = await create_user(client, admin_auth, username="knowledge_other")
    manager_auth = await create_user(
        client, admin_auth, username="knowledge_user_manager", permissions=["users"]
    )
    services = server.services
    admin_id = services.user_repo.get_by_username("admin").id
    employee_id = services.user_repo.get_by_username("knowledge_employee").id
    base = services.knowledge_repo.create_base(owner_user_id=admin_id, name="Staff procedures")
    shared = services.knowledge_repo.create_base(
        owner_user_id=admin_id, name="All staff", shared=True
    )
    doc = services.knowledge_repo.create_document(
        kb_id=base.id, filename="procedures.md", content_type="text/markdown", byte_size=0
    )

    access_url = f"/api/users/{employee_id}/knowledge-bases"
    assert (await client.get(access_url, headers=manager_auth)).status_code == 403
    assert (
        await client.patch(
            f"/api/users/{employee_id}",
            headers=manager_auth,
            json={"knowledge_base_ids": [base.id]},
        )
    ).status_code == 403
    denied = await client.post("/api/knowledge-bases", headers=employee_auth, json={"name": "Mine"})
    assert denied.status_code == 403
    for method in (client.get, client.put):
        kwargs = {"json": {"knowledge_base_ids": [base.id]}} if method == client.put else {}
        response = await method(access_url, headers=employee_auth, **kwargs)
        assert response.status_code == 403
    options = await client.get(access_url, headers=admin_auth)
    assert options.status_code == 200, options.text
    assert options.json()["knowledge_base_ids"] == []
    assert {item["id"] for item in options.json()["items"]} == {base.id, shared.id}
    before = (await client.get("/api/client-config/status", headers=employee_auth)).json()[
        "revision"
    ]
    assigned = await client.put(
        access_url, headers=admin_auth, json={"knowledge_base_ids": [base.id]}
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["knowledge_base_ids"] == [base.id]
    after = (await client.get("/api/client-config/status", headers=employee_auth)).json()[
        "revision"
    ]
    assert before != after
    for auth, expected in ((employee_auth, {base.id, shared.id}), (other_auth, {shared.id})):
        listed = await client.get("/api/knowledge-bases", headers=auth)
        assert listed.status_code == 200
        assert {row["id"] for row in listed.json()} == expected
    documents_url = f"/api/knowledge-bases/{base.id}/documents"
    documents = await client.get(documents_url, headers=employee_auth)
    assert documents.status_code == 200
    assert documents.json()[0]["id"] == doc.id
    assert (await client.get(documents_url, headers=other_auth)).status_code == 403
    assert (
        await client.patch(
            f"/api/knowledge-bases/{base.id}", headers=employee_auth, json={"name": "Changed"}
        )
    ).status_code == 403
    assert (
        await client.delete(f"/api/knowledge-bases/{base.id}", headers=employee_auth)
    ).status_code == 403

    invalid = await client.patch(
        f"/api/users/{employee_id}",
        headers=admin_auth,
        json={"display_name": "Should not change", "knowledge_base_ids": ["missing"]},
    )
    assert invalid.status_code == 404
    assert services.user_repo.get(employee_id).display_name is None
    assert services.knowledge_repo.assigned_base_ids(employee_id) == [base.id]
    revoked = await client.patch(
        f"/api/users/{employee_id}", headers=admin_auth, json={"knowledge_base_ids": []}
    )
    assert revoked.status_code == 200, revoked.text
    assert (await client.get(documents_url, headers=employee_auth)).status_code == 403
    assert (
        KnowledgeService(services).get_readable_base(shared.id, actor_user_id=employee_id).id
        == shared.id
    )

    spec = build_app(server).openapi()
    for method in ("get", "put"):
        operation = spec["paths"]["/api/users/{user_id}/knowledge-bases"][method]
        assert operation["summary"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]
