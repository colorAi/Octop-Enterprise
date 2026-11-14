import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from tests.unit.db.test_group_experts import group_env as group_env
from tests.unit.db.test_group_experts import group_pool as group_pool
from tests.unit.db.test_group_experts import run_call

from octop.infra.execution.service import ActiveExecution, ExecutionService
from octop.infra.group_experts.executor import GroupExpertExecutor, build_model
from octop.infra.group_experts.snapshots import build_snapshot
from octop.infra.utils.bounded_io import BoundedIO


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["completed", "empty", "unknown", "revoked", "budget", "cancel"]
)
async def test_one_shot_execution_and_atomic_accounting(group_env, outcome):
    e = group_env
    run = run_call(e)
    execution = ExecutionService(
        e.execution, node=run["node_id"], incarnation=run["incarnation"], role="worker"
    )
    io = BoundedIO(wait=True)
    seen = []
    started = asyncio.Event()

    async def context(call):
        value = e.group.execution_input(call)
        return {
            **value,
            "question": "Current question" if outcome != "budget" else "a" * 20000,
            "quote": "One direct quote",
        }

    class Model:
        async def ainvoke(self, messages):
            seen.append(messages)
            started.set()
            if outcome == "unknown":
                raise TimeoutError("provider disconnected")
            if outcome == "cancel":
                await asyncio.Event().wait()
            if outcome == "revoked":
                with e.pool.transaction() as conn:
                    conn.execute("UPDATE users SET disabled=1 WHERE id=?", (e.user,))
            return AIMessage(
                content="" if outcome == "empty" else "One final answer",
                usage_metadata={"input_tokens": 30, "output_tokens": 10, "total_tokens": 40},
            )

    groups = SimpleNamespace(context=context, io=io, services=e.repos, repo=e.group)
    runner = GroupExpertExecutor(groups, model_factory=lambda provider, snapshot, tools: Model())
    task = asyncio.create_task(runner(ActiveExecution(execution, run)))
    if outcome == "cancel":
        await asyncio.wait_for(started.wait(), 2)
        e.group.control("call-one", sequence=2, kind="cancel")
    try:
        await asyncio.wait_for(task, 5)
        final = e.execution.get(run["run_id"])
        expected = {
            "completed": "completed",
            "empty": "failed",
            "unknown": "unknown",
            "revoked": "completed",
            "budget": "failed",
            "cancel": "unknown",
        }[outcome]
        assert final["state"] == expected
        assert len(seen) == (0 if outcome == "budget" else 1)
        if seen:
            assert len(seen[0]) == 2
            assert seen[0][1].content.endswith("Current question")
            assert "One direct quote" in seen[0][1].content
            assert not any(
                secret in seen[0][0].content
                for secret in ("private user", "[MEMORY.md]", "private tool secret")
            )
        with e.pool.connect() as conn:
            holds = conn.execute(
                "SELECT * FROM execution_reservations WHERE run_id=?", (run["run_id"],)
            ).fetchall()
            usage = conn.execute(
                "SELECT total_tokens,source,thread_id FROM usage_log WHERE execution_run_id=?",
                (run["run_id"],),
            ).fetchall()
            call = conn.execute(
                "SELECT * FROM group_expert_calls WHERE call_id='call-one'"
            ).fetchone()
            assert call["result_digest"]
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM group_expert_outbox WHERE operation_id='result:call-one'"
                ).fetchone()[0]
                == 1
            )
            if outcome in {"completed", "empty", "revoked"}:
                assert holds[0]["actual"] == 40 and holds[0]["state"] == "settled"
                assert (
                    len(usage) == 1
                    and usage[0]["total_tokens"] == 40
                    and usage[0]["source"] == "group_expert"
                    and usage[0]["thread_id"] == ""
                )
                assert json.loads(call["usage_json"])["input_tokens"] == 30
            elif outcome in {"unknown", "cancel"}:
                assert holds[0]["state"] == "unknown" and not usage
            else:
                assert not holds and not usage
        if outcome == "revoked":
            delivery = e.group.delivery("call-one")
            assert delivery["unpublished"] and json.loads(delivery["result_wire"])["result"] is None
            assert e.execution.get(run["run_id"])["state"] == "completed"
    finally:
        await io.drain()
        await execution.io.drain()


def test_model_factory_has_no_retries_or_tools(group_env):
    e = group_env
    model = build_model(e.repos.provider_repo.get(e.provider), e.snapshot)
    assert model.bound.max_retries == 0
    assert not model.kwargs.get("tools")
    assert model.kwargs["max_tokens"] == e.snapshot["model_settings"]["max_tokens"]


@pytest.mark.asyncio
async def test_small_context_uses_remaining_output_budget_and_reserves_the_same_amount(group_env):
    e = group_env
    e.repos.provider_repo.update(
        e.provider, models_json=json.dumps([{"id": "example", "context_window": 4096}])
    )
    definition = {**e.definition, "runtime_settings": {}}
    snapshot = build_snapshot(
        source_agent_id=e.source.agent_id,
        definition=definition,
        provider=e.repos.provider_repo.get(e.provider),
        provider_version=e.group.provider_version(e.provider),
        global_model=("model-source", "example"),
    )
    e.group.publish(snapshot, source_manifest="a" * 64, display={"name": "Group expert"})
    with e.pool.connect() as conn:
        e.versions.update(
            {
                domain: conn.execute(
                    "SELECT revision FROM group_expert_authorizations WHERE domain_key=?", (domain,)
                ).fetchone()[0]
                for domain in e.versions
            }
        )
    run = run_call(e)
    execution = ExecutionService(
        e.execution, node=run["node_id"], incarnation=run["incarnation"], role="worker"
    )
    io = BoundedIO(wait=True)
    model_settings = []

    async def context(call):
        return {**e.group.execution_input(call), "question": "Hi", "quote": ""}

    class Model:
        async def ainvoke(self, messages):
            assert messages[-1].content == "Hi"
            return AIMessage(
                content="Hello",
                usage_metadata={"input_tokens": 30, "output_tokens": 10, "total_tokens": 40},
            )

    def factory(provider, current, tools):
        model_settings.append(current["model_settings"])
        return Model()

    groups = SimpleNamespace(context=context, io=io, services=e.repos, repo=e.group)
    try:
        await GroupExpertExecutor(groups, model_factory=factory)(ActiveExecution(execution, run))
        assert e.execution.get(run["run_id"])["state"] == "completed"
        assert 0 < model_settings[0]["max_tokens"] < 4095
        assert e.group.execution_input("call-one")["snapshot"]["model_settings"] == {
            "max_tokens": 4095
        }
        with e.pool.connect() as conn:
            reservation = conn.execute(
                "SELECT reserved,actual,state FROM execution_reservations WHERE run_id=?",
                (run["run_id"],),
            ).fetchone()
        assert reservation["reserved"] == 4096
        assert reservation["actual"] == 40
        assert reservation["state"] == "settled"
    finally:
        await io.drain()
        await execution.io.drain()
