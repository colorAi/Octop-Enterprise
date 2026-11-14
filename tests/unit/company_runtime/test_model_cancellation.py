import time
from threading import Event
from types import SimpleNamespace

import anyio
import httpx
import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, messages_to_dict

from octop.infra.company_runtime.service import CompanyRuntimeService
from octop.infra.utils.bounded_io import BoundedIO

from .test_runtime import acquire, install_agent


class ReplyModel:
    def bind(self, **kwargs):
        return self

    async def astream(self, messages):
        yield AIMessageChunk(
            content="Reply",
            usage_metadata={"input_tokens": 7, "output_tokens": 2, "total_tokens": 9},
        )


@pytest.mark.parametrize("with_image", [False, True])
@pytest.mark.parametrize("reply_format", ["text", "text_blocks", "thinking_blocks", "tool_calls"])
async def test_completed_model_without_usage_charges_for_request_and_reply(
    company, with_image, reply_format
):
    server, client, _, owner = company
    install_agent(server)
    await acquire(client)
    repo = server.services.execution_repo
    repo.register("worker", "boot", "worker")
    repo.configure(running=2, models=1, queued=8, user_queued=4)
    io = BoundedIO(workers=2, capacity=32, wait=True)
    server.execution = SimpleNamespace(repo=repo, io=io, node="worker", incarnation="boot")

    class UnreportedModel(ReplyModel):
        async def astream(self, messages):
            for index, part in enumerate(reply):
                if reply_format == "text":
                    yield AIMessageChunk(content=part)
                elif reply_format == "text_blocks":
                    yield AIMessageChunk(content=[{"type": "text", "text": part, "index": 0}])
                elif reply_format == "thinking_blocks":
                    yield AIMessageChunk(
                        content=[{"type": "thinking", "thinking": part, "index": 0}]
                    )
                else:
                    # Anthropic exposes the same tool arguments in both fields.
                    yield AIMessageChunk(
                        content=[{"type": "input_json_delta", "partial_json": part, "index": 0}],
                        tool_call_chunks=[
                            {"name": "answer" if index == 0 else None, "args": part, "index": 0}
                        ],
                    )

    agent = server.app_runtime.agent_registry.get_agent("expert")
    agent.model_factory.get_chat_model = lambda ref: UnreportedModel()
    svc = CompanyRuntimeService(server, {"owner_user_id": owner, "device_id": "device0"})
    message = HumanMessage(
        content=[
            {"type": "text", "text": "Hello"},
            {"type": "image_url", "image_url": {"url": "https://company.test/image.png"}},
        ]
        if with_image
        else "Hello"
    )
    body = {"model": "test/model", "messages": messages_to_dict([message])}

    try:
        reply = ('{"text":"Hi"}',) if reply_format == "tool_calls" else ("Hi",)
        assert [chunk async for chunk in svc.model("task1", body)]
        with server.services.db.connect() as conn:
            receipt = conn.execute(
                "SELECT state,actual,reserved FROM execution_reservations"
            ).fetchone()
        charge = receipt["actual"]
        assert receipt["state"] == "settled"
        assert 0 < charge < 1000 < receipt["reserved"]
        assert server.services.usage_repo.total_tokens_for_user(owner) == charge

        server.services.user_policy_repo.set(owner, "token_quota", str(receipt["reserved"] + 1000))
        reply = (reply[0][:1], reply[0][1:])
        with anyio.fail_after(2):
            assert len([chunk async for chunk in svc.model("task1", body)]) == 2
        assert len(repo.list_runs(state="completed")) == 2
        assert server.services.usage_repo.total_tokens_for_user(owner) == 2 * charge
    finally:
        await io.drain()
        await client.close()


@pytest.mark.parametrize("usage_reported", [False, True])
async def test_disconnected_model_stream_settles_reported_usage(
    company, monkeypatch, usage_reported
):
    server, client, _, owner = company
    install_agent(server)
    await acquire(client)
    repo = server.services.execution_repo
    repo.register("worker", "boot", "worker")
    io = BoundedIO(workers=2, capacity=32, wait=True)
    server.execution = SimpleNamespace(repo=repo, io=io, node="worker", incarnation="boot")
    waiting = anyio.Event()

    class StreamingModel:
        def bind(self, **kwargs):
            return self

        async def astream(self, messages):
            usage = {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}
            yield AIMessageChunk(
                content="Partial reply", usage_metadata=usage if usage_reported else None
            )
            waiting.set()
            await anyio.sleep_forever()

    agent = server.app_runtime.agent_registry.get_agent("expert")
    agent.model_factory.get_chat_model = lambda ref: StreamingModel()
    original = repo.transition

    def transition(*args, **kwargs):
        original(*args, **kwargs)
        # A committed DB operation can outlive its cancelled awaiter.
        time.sleep(0.03)

    monkeypatch.setattr(repo, "transition", transition)
    svc = CompanyRuntimeService(server, {"owner_user_id": owner, "device_id": "device0"})

    async def consume():
        async for _ in svc.model("task1", {"model": "test/model", "messages": []}):
            pass

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(consume)
            await waiting.wait()
            group.cancel_scope.cancel()
        await io.drain()
        assert repo.list_runs(state="running") == []
        expected = "cancelled" if usage_reported else "unknown"
        runs = repo.list_runs(state=expected)
        assert len(runs) == 1
        run_id = runs[0]["run_id"]
        with server.services.db.connect() as conn:
            receipt = conn.execute(
                "SELECT state,actual FROM execution_reservations WHERE run_id=?", (run_id,)
            ).fetchone()
        assert tuple(receipt) == (("settled", 9) if usage_reported else ("unknown", None))
        if not usage_reported:
            with server.services.db.connect() as conn:
                estimate = conn.execute(
                    "SELECT estimated,reserved FROM execution_reservations WHERE run_id=?",
                    (run_id,),
                ).fetchone()
            assert 0 < estimate["estimated"] < estimate["reserved"]
            assert server.services.usage_repo.total_tokens_for_user(owner) == estimate["estimated"]
        assert len(repo.events(run_id, user=owner)) == 1
    finally:
        await io.drain()
        await client.close()


@pytest.mark.parametrize(
    ("status", "run_state", "receipt"),
    [
        (429, "failed", ("settled", 0)),
        (503, "unknown", ("unknown", None)),
    ],
)
async def test_model_http_failure_keeps_only_unconfirmed_usage(company, status, run_state, receipt):
    server, client, _, owner = company
    install_agent(server)
    await acquire(client)
    repo = server.services.execution_repo
    repo.register("worker", "boot", "worker")
    repo.configure(running=2, models=1, queued=8, user_queued=4)
    io = BoundedIO(workers=2, capacity=32, wait=True)
    server.execution = SimpleNamespace(repo=repo, io=io, node="worker", incarnation="boot")

    class FailingModel:
        def bind(self, **kwargs):
            return self

        async def astream(self, messages):
            request = httpx.Request("POST", "https://model.invalid")
            raise httpx.HTTPStatusError(
                "provider error", request=request, response=httpx.Response(status)
            )
            yield

    agent = server.app_runtime.agent_registry.get_agent("expert")
    agent.model_factory.get_chat_model = lambda ref: FailingModel()
    svc = CompanyRuntimeService(server, {"owner_user_id": owner, "device_id": "device0"})
    body = {"model": "test/model", "messages": []}
    try:
        with pytest.raises(httpx.HTTPStatusError):
            async for _ in svc.model("task1", body):
                pass
        runs = repo.list_runs(state=run_state)
        assert len(runs) == 1
        with server.services.db.connect() as conn:
            row = conn.execute(
                "SELECT state,actual FROM execution_reservations WHERE run_id=?",
                (runs[0]["run_id"],),
            ).fetchone()
        assert tuple(row) == receipt
        if status == 429:
            agent.model_factory.get_chat_model = lambda ref: ReplyModel()
            with anyio.fail_after(2):
                assert [chunk async for chunk in svc.model("task1", body)]
    finally:
        await io.drain()
        await client.close()


async def test_disconnect_during_reservation_releases_before_calling_model(company, monkeypatch):
    server, client, _, owner = company
    install_agent(server)
    await acquire(client)
    repo = server.services.execution_repo
    repo.register("worker", "boot", "worker")
    io = BoundedIO(workers=2, capacity=32, wait=True)
    server.execution = SimpleNamespace(repo=repo, io=io, node="worker", incarnation="boot")
    agent = server.app_runtime.agent_registry.get_agent("expert")
    agent.model_factory.get_chat_model = lambda ref: ReplyModel()
    committed = Event()
    release = Event()
    original = repo.reserve_model

    def reserve(*args, **kwargs):
        original(*args, **kwargs)
        committed.set()
        assert release.wait(timeout=3)

    monkeypatch.setattr(repo, "reserve_model", reserve)
    svc = CompanyRuntimeService(server, {"owner_user_id": owner, "device_id": "device0"})

    async def consume():
        async for _ in svc.model("task1", {"model": "test/model", "messages": []}):
            pytest.fail("a cancelled request should not stream output")

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(consume)
            try:
                assert await anyio.to_thread.run_sync(committed.wait, 3)
                group.cancel_scope.cancel()
            finally:
                release.set()
        runs = repo.list_runs(state="cancelled")
        assert len(runs) == 1
        with server.services.db.connect() as conn:
            receipt = conn.execute(
                "SELECT state,actual FROM execution_reservations WHERE run_id=?",
                (runs[0]["run_id"],),
            ).fetchone()
        assert tuple(receipt) == ("settled", 0)
    finally:
        await io.drain()
        await client.close()
