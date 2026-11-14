"""Invoked by the Go opt-in test, using two freshly created disposable databases."""

import asyncio
import base64
import json
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
from langchain_core.messages import AIMessage

from octop.infra.communication.bridge import CommunicationBridge
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import PostgresPool
from octop.infra.db.services import RepoBundle, SharedServices
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.execution.service import ActiveExecution, ExecutionService
from octop.infra.group_experts.executor import GroupExpertExecutor
from octop.infra.group_experts.policy import source_tool_policy
from octop.infra.group_experts.service import GroupExpertService
from octop.infra.group_experts.tools import GroupTools
from tests.unit.db.test_group_experts import group_env


def fixture(config):
    # Only the fresh testdb database allocated by the Go parent may be reset.
    assert urlsplit(config["dsn"]).path.startswith("/octop_im_test_testgroupexpertpythonroundtrip_")
    pool = PostgresPool(config["dsn"], max_size=4)
    with pool.transaction() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    run_migrations(pool)
    RepoBundle.from_pool(pool).user_repo.create(username="go-admin", role="admin")
    e = group_env.__wrapped__(pool)
    e.snapshot.update(
        **source_tool_policy({}),
        tool_revision=e.group.tool_revision(e.source.agent_id, e.source.id),
    )
    e.group.publish(e.snapshot, source_manifest="a" * 64, display={"name": "Group expert"})
    assert e.user == 2
    services = SharedServices(repos=e.repos, paths=None, config=None)
    return pool, e, services


async def main(config):
    pool, e, services = fixture(config)
    try:
        await roundtrip(config, pool, e, services)
    finally:
        pool.close()


async def roundtrip(config, pool, e, services):
    bridge = CommunicationBridge(services)
    bridge.url = config["url"]
    bridge.enabled = True
    bridge._key = config["key"].encode()
    bridge._instance = "installation"
    bridge._http = httpx.AsyncClient(timeout=10, trust_env=False)
    groups = GroupExpertService(SimpleNamespace(services=services, communication=bridge))
    group = config["group"]
    with pool.transaction() as conn:
        conn.execute("DELETE FROM group_expert_scopes")
        conn.execute(
            "INSERT INTO department_resource_pools(scope_id,revision,expert_ids) VALUES(?,1,?)",
            (config["department"], json.dumps([e.source.agent_id])),
        )
    await groups.reconcile_user(e.user)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO expert_assignments(user_id,source_agent_id) VALUES(?,?) ON CONFLICT DO NOTHING",
            (e.user, e.source.agent_id),
        )
    e.group.save_scope(
        actor=1,
        request_id="scope",
        conversation_id=group,
        department_id=None,
        sources=[{"source_agent_id": e.source.agent_id, "source_row_id": e.source.id}],
        expected_revision=0,
    )
    await groups.runtime()
    await groups.flush()
    await bridge.group_request(
        "PUT",
        f"/groups/{group}/enabled",
        {
            "actor_id": bridge.mapped_id(1),
            "organization": True,
            "request_id": "enable",
            "enabled": True,
        },
        actor=1,
    )
    binding = (
        await bridge.group_request(
            "POST",
            f"/groups/{group}/bindings",
            {
                "actor_id": bridge.mapped_id(1),
                "organization": True,
                "request_id": "invite",
                "revision": e.group.scope(group)["revision"],
                "source_agent_id": e.source.agent_id,
                "source_row_id": e.source.id,
            },
            actor=1,
        )
    )["binding"]
    calls = []

    class Model:
        async def ainvoke(self, messages):
            calls.append(messages)
            assert len(messages) == 2 and messages[1].content == "Question only"
            assert not any(
                v in messages[0].content
                for v in ("private user", "[MEMORY.md]", "private tool secret")
            )
            return AIMessage(
                content="Controlled final answer",
                usage_metadata={"input_tokens": 12, "output_tokens": 7, "total_tokens": 19},
            )

    async def ask(key):
        response = await bridge._http.post(
            config["url"] + f"/api/app/v1/conversations/{group}/messages",
            headers={"Authorization": "Bearer " + config["token"]},
            json={
                "client_message_id": key,
                "text": "Question only",
                "mention_expert_id": binding["binding_id"],
            },
        )
        assert 200 <= response.status_code < 300, response.text
        return response.json()["data"]["expert_call"]["call_id"]

    call = await ask("once")
    assert await ask("once") == call
    await groups.poll()
    await groups.poll()
    receipt = e.group.get(call)
    assert receipt["admission_state"] == "accepted", receipt
    node = e.execution.worker_for(e.user)
    boot = "boot1" if node == "worker" else "boot2"
    run = e.execution.claim(node, boot)
    assert run and run["run_id"] == receipt["run_id"]
    executor = ExecutionService(e.execution, node=node, incarnation=boot, role="worker")
    await GroupExpertExecutor(groups, model_factory=lambda provider, snapshot, tools: Model())(
        ActiveExecution(executor, run)
    )
    assert e.execution.get(run["run_id"])["state"] == "completed", e.group.get(call)
    assert e.execution.get(run["run_id"], user=e.user) is None
    send = bridge.group_request
    lost = False

    async def lose_response(method, path, body=None, **kwargs):
        nonlocal lost
        if path.endswith("/result"):
            state = await send("GET", f"/calls/{call}")
            assert state["call"]["call_state"] == "awaiting_delivery"
        result = await send(method, path, body, **kwargs)
        if path.endswith("/result") and not lost:
            lost = True
            raise OctopError(ErrorCode.INTERNAL_ERROR, "simulated lost receipt", status=503)
        return result

    bridge.group_request = lose_response
    await groups.flush()
    assert lost and e.group.get(call)["delivery_state"] == "pending"
    bridge.group_request = send
    # A new service instance recovers the receipt before asking for any answer.
    recovered = GroupExpertService(SimpleNamespace(services=services, communication=bridge))
    with pool.connect() as conn:
        row = dict(
            conn.execute(
                "SELECT * FROM group_expert_outbox WHERE operation_id=?", ("result:" + call,)
            ).fetchone()
        )
    await recovered.deliver(row)
    await recovered.deliver(row)
    assert e.group.get(call)["delivery_state"] == "confirmed" and len(calls) == 1
    history = await bridge._http.get(
        config["url"] + f"/api/app/v1/conversations/{group}/messages",
        headers={"Authorization": "Bearer " + config["token"]},
    )
    answers = [v for v in history.json()["data"]["items"] if v["sender_type"] == "group_expert"]
    assert len(answers) == 1 and answers[0]["text"] == "Controlled final answer"
    assert answers[0]["expert"]["binding_id"] == binding["binding_id"]
    assert answers[0]["reply_to_message_id"]
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM threads WHERE thread_id LIKE 'group-expert:%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT actual FROM execution_reservations WHERE run_id=?", (run["run_id"],)
            ).fetchone()[0]
            == 19
        )
    image_calls = []

    class ImageModel:
        async def ainvoke(self, messages):
            image_calls.append(messages)
            if len(image_calls) == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "company_generate_image",
                            "args": {"prompt": "a cat"},
                            "id": "image-tool",
                        }
                    ],
                    usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
                )
            return AIMessage(
                content="Generated image attached",
                usage_metadata={"input_tokens": 40, "output_tokens": 5, "total_tokens": 45},
            )

    class ImageTools(GroupTools):
        async def generate(self, kind, **body):
            assert kind == "image" and body["prompt"] == "a cat"
            png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg=="
            )
            await self.workspace.aupload_many([("generated/cat.png", png)])
            stored = await self.workspace.adownload_bytes("generated/cat.png")
            asset = await self.upload(stored, "image/png", "cat.png")
            return {"status": "completed", "attachments": [asset]}

    image_call = await ask("image")
    await groups.poll()
    await groups.poll()
    image_run = e.execution.claim(node, boot)
    assert image_run
    await GroupExpertExecutor(
        groups, model_factory=lambda p, s, t: ImageModel(), tools_factory=ImageTools
    )(ActiveExecution(executor, image_run))
    assert e.execution.get(image_run["run_id"])["state"] == "completed", e.group.get(image_call)
    await groups.flush()
    assert e.group.get(image_call)["delivery_state"] == "confirmed"
    image_receipt = (await bridge.group_request("GET", f"/calls/{image_call}/receipt"))["receipt"]
    response = await bridge._http.get(
        config["url"] + f"/api/app/v1/conversations/{group}/messages/{image_receipt['message_id']}",
        headers={"Authorization": "Bearer " + config["token"]},
    )
    artifact = response.json()["data"]["attachments"][0]
    download = await bridge._http.get(
        config["url"] + "/api/app/v1/media/" + artifact["asset_id"],
        headers={"Authorization": "Bearer " + config["token"]},
    )
    assert download.status_code == 200 and download.headers["content-type"] == "image/png"
    assert len(image_calls) == 2
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT SUM(total_tokens) FROM usage_log WHERE execution_run_id=?",
                (image_run["run_id"],),
            ).fetchone()[0]
            == 70
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM execution_tools WHERE run_id=? AND state='completed'",
                (image_run["run_id"],),
            ).fetchone()[0]
            == 1
        )
    await groups.close()
    await recovered.close()
    await executor.io.drain()
    await bridge.stop()
    pool.close()
    print(
        "real Python/Go roundtrip passed: text + image tool loop, authorized attachment download, one reply each, lost receipt recovered, durable per-round usage and no personal thread"
    )


if __name__ == "__main__":
    asyncio.run(main(json.load(sys.stdin)))
