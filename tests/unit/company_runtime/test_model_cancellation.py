import time
from types import SimpleNamespace

import anyio
from langchain_core.messages import AIMessageChunk

from octop.infra.company_runtime.service import CompanyRuntimeService
from octop.infra.utils.bounded_io import BoundedIO

from .test_runtime import acquire, install_agent


async def test_disconnected_model_stream_finishes_unknown_record(company, monkeypatch):
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
            yield AIMessageChunk(content="Partial reply")
            waiting.set()
            await anyio.sleep_forever()

    agent = server.app_runtime.agent_registry.get_agent("expert")
    agent.model_factory.get_chat_model = lambda ref: StreamingModel()
    original = repo.settle_model

    def settle(*args, **kwargs):
        original(*args, **kwargs)
        # A committed DB operation can outlive its cancelled awaiter.
        time.sleep(0.03)

    monkeypatch.setattr(repo, "settle_model", settle)
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
        unknown = repo.list_runs(state="unknown")
        assert len(unknown) == 1
        record = repo.reconciliation(unknown[0]["run_id"])
        assert len(record["models"]) == 1
        assert record["models"][0]["state"] == "unknown"
        assert len(repo.events(unknown[0]["run_id"], user=owner)) == 1
    finally:
        await io.drain()
        await client.close()
