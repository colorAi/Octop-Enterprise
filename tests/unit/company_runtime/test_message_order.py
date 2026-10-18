"""Late cumulative receipts retain their original conversation boundaries."""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict

from .test_runtime import acquire, install_agent


async def send_messages(client, task, seq, messages):
    return await client.request(
        f"tasks/{task}/events",
        {
            "event_id": f"{task}_{seq}",
            "seq": seq,
            "payload": {"kind": "messages", "messages": messages_to_dict(messages)},
        },
    )


def history(server):
    with server.services.db.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT seq,message_id,message_json FROM thread_messages WHERE thread_id=? ORDER BY seq",
                ("thread1",),
            ).fetchall()
        ]


async def test_failed_turn_and_steer_are_filled_before_the_retry_question(company):
    server, client, _, _ = company
    install_agent(server)
    await acquire(client)
    first = HumanMessage(content="generate", id="task1_user")
    steer = HumanMessage(content="use the image tool", id="steer1")
    await send_messages(client, "task1", 1, [first])
    await send_messages(client, "task1", 2, [steer])
    await client.request("tasks/task1/status", {"status": "failed"})
    await acquire(client, "task2")
    retry = HumanMessage(content="try again", id="task2_user")
    await send_messages(client, "task2", 1, [retry])
    process = AIMessage(content="preparing the image", id="process1")
    failure = ToolMessage(content="gateway failed", tool_call_id="image1", id="failure1")
    answer = AIMessage(content="saved", id="answer2")
    snapshot = [first, process, steer, failure, retry, answer]
    assert (await send_messages(client, "task2", 2, snapshot))["accepted"]
    assert [row["message_id"] for row in history(server)] == [m.id for m in snapshot]
    before = history(server)
    assert not (await send_messages(client, "task2", 2, snapshot))["accepted"]
    assert history(server) == before
    # Replaying the old task remains an audit receipt, not a history rewrite.
    await send_messages(client, "task1", 3, [first, AIMessage(content="stale", id="stale")])
    assert history(server) == before
    assert json.loads(before[2]["message_json"])["data"]["content"] == steer.content
    await client.close()


async def test_cumulative_receipt_repairs_prior_append_order_without_dropping_rows(company):
    server, client, _, _ = company
    install_agent(server)
    await acquire(client)
    first = HumanMessage(content="first", id="task1_user")
    retry = HumanMessage(content="retry", id="retry")
    process = AIMessage(content="first process", id="process")
    extra = AIMessage(content="retained receipt", id="extra")
    # Reproduce the order stored by older servers after a failed turn.
    await send_messages(client, "task1", 1, [first, retry, process, extra])
    await send_messages(client, "task1", 2, [first, process, retry])
    assert [row["message_id"] for row in history(server)] == [
        first.id,
        process.id,
        extra.id,
        retry.id,
    ]
    assert [row["seq"] for row in history(server)] == [1, 2, 3, 4]
    await client.close()
