"""Employee browser conversations are account-owned and separate from local projects."""

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from octop.infra.history.projection import message_inputs
from tests.support.auth import auth_header, create_agent, create_user


@pytest.fixture
async def web_chats(env_with_provider):
    client, server, admin = env_with_provider
    employee = await create_user(client, admin, username="web-employee")
    agent = await create_agent(client, employee, name="Web expert")
    owner = (await client.get("/api/auth/me", headers=employee)).json()["id"]
    base = f"/api/agents/{agent}"
    created = await client.post(f"{base}/threads?surface=web", headers=employee)
    created.raise_for_status()
    web_thread = created.json()["thread_id"]
    server.services.thread_message_repo.replace_all(
        web_thread,
        message_inputs(
            [
                HumanMessage(content="Remember this web conversation", id="web-user"),
                AIMessage(content="Saved on the server", id="web-reply"),
            ]
        ),
    )
    server.services.thread_repo.update_title(web_thread, "Web conversation")
    # More local conversations than the browser page size, all pinned first.
    for index in range(12):
        thread = f"local-{index}"
        server.services.thread_repo.insert(
            thread_id=thread,
            agent_id=agent,
            user_id=owner,
            channel_type="dashboard",
            session_key=f"local:{index}",
            title="Local project",
        )
        server.services.thread_repo.set_pinned(thread, True)
        with server.services.db.transaction() as conn:
            conn.execute(
                "INSERT INTO company_runtime_threads VALUES(?,?,?,?,?,?,?)",
                (thread, owner, agent, "desktop", thread, "installation", 1),
            )
    return client, server, admin, employee, owner, base, web_thread


async def test_web_chats_follow_account_across_browser_logins(web_chats):
    client, server, _, employee, _, base, thread = web_chats
    first = await client.get(f"{base}/threads?surface=web&limit=1", headers=employee)
    assert first.status_code == 200
    assert [row["thread_id"] for row in first.json()] == [thread]

    # A second browser has its own login and no local device or project grants.
    async with httpx.AsyncClient(transport=client._transport, base_url=client.base_url) as browser:
        other_login = await auth_header(browser, username="web-employee")
        listed = await browser.get(f"{base}/threads?surface=web&limit=1", headers=other_login)
        assert listed.json() == first.json()
        history = await browser.get(
            f"{base}/threads/{thread}/history?surface=web", headers=other_login
        )
        assert history.status_code == 200
        assert [row["id"] for row in history.json()["messages"]] == ["web-user", "web-reply"]
        renamed = await browser.patch(
            f"{base}/threads/{thread}?surface=web",
            headers=other_login,
            json={"title": "Continued from another browser"},
        )
        assert renamed.status_code == 200
    assert server.services.thread_repo.get(thread).title == "Continued from another browser"
    assert server.services.company_runtime_repo.binding(thread) is None


async def test_employee_web_blocks_local_links_without_changing_desktop_or_admin(web_chats):
    client, server, admin, employee, owner, base, _ = web_chats
    local = f"{base}/threads/local-0"
    for method, path, body in [
        ("GET", f"{local}/history", None),
        ("PATCH", local, {"title": "Should not change"}),
        ("DELETE", local, None),
        ("POST", f"{local}/fork", {"assistant_turns_from_end": 1}),
        ("PATCH", f"{base}/session", {"thread_id": "local-0"}),
    ]:
        response = await client.request(method, f"{path}?surface=web", headers=employee, json=body)
        assert response.status_code == 404, (path, response.text)
    assert server.services.thread_repo.get("local-0").title == "Local project"
    native = await client.get(f"{base}/threads", headers=employee)
    assert len(native.json()) == 13
    assert (await client.get(f"{local}/history", headers=employee)).status_code == 200
    admin_list = await client.get(f"{base}/threads?surface=web&as_user={owner}", headers=admin)
    assert len(admin_list.json()) == 13
    assert (
        await client.get(f"{local}/history?surface=web&as_user={owner}", headers=admin)
    ).status_code == 200


async def test_web_scope_keeps_account_ownership_checks(web_chats):
    client, _, admin, _, _, base, thread = web_chats
    other = await create_user(client, admin, username="other-employee")
    for path in (f"{base}/threads", f"{base}/threads/{thread}/history"):
        response = await client.get(f"{path}?surface=web", headers=other)
        assert response.status_code == 403
