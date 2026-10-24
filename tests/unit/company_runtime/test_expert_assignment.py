import pytest

from octop.infra.users.expert_access import ExpertAccess
from tests.unit.company_runtime.test_runtime import acquire, install_agent


@pytest.mark.asyncio
async def test_revoked_desktop_can_finish_but_cannot_start_or_call_model(company):
    server, client, _, owner = company
    install_agent(server)
    await acquire(client)
    admin = server.services.user_repo.create(username="admin-source", role="admin")
    server.services.agent_repo.create(agent_id="source", user_id=admin, name="Source")
    access = ExpertAccess(server.services)
    access.configure_source(admin, admin, 0)
    access.save(admin, owner, [], "assigned_only", 0, 1)
    status = await client.http.get("tasks/task1")
    assert status.status_code == 200
    assert status.json()["status"] == "cancel_requested"
    assert (
        await client.http.post(
            "tasks",
            json={
                "task_id": "new-task",
                "thread_id": "thread2",
                "agent_id": "expert",
                "text": "start",
                "recovery_token": "r" * 32,
                "installation_token": "i" * 32,
            },
        )
    ).status_code == 403
    assert (
        await client.http.post("tasks/task1/model", json={"model": "test/model", "messages": []})
    ).status_code == 403
    events = await client.http.post(
        "tasks/task1/events",
        json={
            "event_id": "final-receipt",
            "seq": 1,
            "payload": {"kind": "note", "text": "cancelled"},
        },
    )
    assert events.status_code == 200
    finished = await client.http.post("tasks/task1/status", json={"status": "cancelled"})
    assert finished.status_code == 200
    assert (await client.http.get("tasks/task1")).json()["status"] == "cancelled"
