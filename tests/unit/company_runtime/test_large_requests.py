"""Image-heavy turns must cross the complete native/server JSON transport."""

import json

import pytest
from langchain_core.messages import AIMessageChunk

from .test_runtime import acquire, install_agent


@pytest.mark.asyncio
async def test_large_model_checkpoint_writes_and_history_requests(company):
    server, client, _, _ = company
    install_agent(server)
    # A single image or several retained tool images exceed the former 16 MiB cap.
    image = "data:image/png;base64," + "A" * (20 * 1024 * 1024)
    messages = [
        {
            "type": "human",
            "data": {
                "id": "user1",
                "content": [{"type": "image_url", "image_url": {"url": image}}],
            },
        }
    ]
    received = []

    class ImageModel:
        async def astream(self, inputs):
            received.append(inputs[0].content[0]["image_url"]["url"])
            yield AIMessageChunk(content="image received", id="reply1")

    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: ImageModel()
    )
    await acquire(client)
    try:
        response = await client.http.post(
            "tasks/task1/model", json={"model": "test/model", "messages": messages}
        )
        assert response.status_code == 200, response.text
        assert json.loads(response.text)["data"]["content"] == "image received"
        assert received == [image]

        checkpoint = {"checkpoint_id": "cp1", "payload": {"image": image}}
        assert await client.request("tasks/task1/checkpoint/put", checkpoint) == {"saved": True}
        assert await client.request(
            "tasks/task1/checkpoint/writes",
            {
                "checkpoint_id": "cp1",
                "task_key": "tool1",
                "writes": [{"idx": 0, "value": image}],
            },
        ) == {"saved": True}
        saved = await client.request("tasks/task1/checkpoint/get", {"checkpoint_id": "cp1"})
        assert saved["image"] == image
        assert saved["pending_writes"] == [image]

        assert await client.request(
            "tasks/task1/events",
            {
                "event_id": "event1",
                "seq": 1,
                "payload": {"kind": "messages", "messages": messages},
            },
        ) == {"accepted": True, "seq": 1}
        events = await client.request("tasks/task1/events")
        assert events[0]["payload"]["messages"] == messages
        assert server.services.thread_message_repo.head("thread1") > 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_large_requests_still_require_native_device_and_task_ownership(company):
    server, client, credentials, _ = company
    install_agent(server)
    await acquire(client)
    body = {
        "model": "test/model",
        "messages": [{"type": "human", "data": {"content": "A" * (17 * 1024 * 1024)}}],
    }
    try:
        response = await client.http.post(
            "tasks/task1/model", json=body, headers={"Origin": "https://browser.test"}
        )
        assert response.status_code == 403
        response = await client.http.post(
            "tasks/task1/model", json=body, headers={"X-Octop-Device-Credential": "invalid"}
        )
        assert response.status_code == 401
        other = server.services.user_repo.create(username="other", role="user")
        with server.services.db.transaction() as conn:
            conn.execute(
                "UPDATE desktop_devices SET owner_user_id=? WHERE device_id='device1'", (other,)
            )
        client.http.headers["X-Octop-Device-Credential"] = credentials[1]
        response = await client.http.post("tasks/task1/model", json=body)
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "task_not_owned"
    finally:
        await client.close()
