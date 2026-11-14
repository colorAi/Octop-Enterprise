"""Regression coverage for bounded group execution and durable delivery."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import AIMessage
from tests.unit.db.test_execution import fence, submit
from tests.unit.db.test_group_experts import context as call_context
from tests.unit.db.test_group_experts import group_env as group_env
from tests.unit.db.test_group_experts import group_pool as group_pool
from tests.unit.db.test_group_experts import run_call

from octop.infra.db.repos.execution import ExecutionConflict
from octop.infra.db.repos.group_experts import enqueue
from octop.infra.db.repos.token_accounting import quota_tokens
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.execution.service import ActiveExecution, ExecutionService
from octop.infra.group_experts.executor import GroupExpertExecutor, model_not_executed
from octop.infra.group_experts.service import GroupExpertService
from octop.infra.utils.bounded_io import BoundedIO


def setup_runner(e, model, *, context_hook=None):
    run = run_call(e)
    service = ExecutionService(
        e.execution, node=run["node_id"], incarnation=run["incarnation"], role="worker"
    )
    io = BoundedIO(wait=True)

    async def context(call):
        if context_hook:
            await context_hook()
        return {**e.group.execution_input(call), "question": "Question", "quote": ""}

    groups = SimpleNamespace(context=context, io=io, services=e.repos, repo=e.group)
    return run, service, io, GroupExpertExecutor(groups, model_factory=lambda *args: model)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [400, 429, "connect"])
async def test_known_model_rejection_releases_reservation(group_env, failure):
    e = group_env

    class Model:
        async def ainvoke(self, messages):
            request = httpx.Request("POST", "https://model.invalid")
            if failure == "connect":
                raise httpx.ConnectError("not connected", request=request)
            raise httpx.HTTPStatusError(
                "rejected", request=request, response=httpx.Response(failure, request=request)
            )

    run, service, io, runner = setup_runner(e, Model())
    try:
        await runner(ActiveExecution(service, run))
        assert e.execution.get(run["run_id"])["state"] == "failed"
        with e.pool.connect() as conn:
            hold = conn.execute(
                "SELECT state,actual FROM execution_reservations WHERE run_id=?", (run["run_id"],)
            ).fetchone()
            assert hold["state"] == "settled" and hold["actual"] == 0
            assert quota_tokens(conn, e.user) == 0
    finally:
        await io.drain()
        await service.io.drain()


@pytest.mark.asyncio
async def test_transient_context_failure_keeps_the_model_receipt(group_env):
    e = group_env
    checks = 0

    async def check():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "communication unavailable", status=503)

    class Model:
        async def ainvoke(self, messages):
            await asyncio.sleep(2.2)
            return AIMessage(
                content="Answer",
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            )

    run, service, io, runner = setup_runner(e, Model(), context_hook=check)
    try:
        await asyncio.wait_for(runner(ActiveExecution(service, run)), 5)
        assert checks >= 3
        assert e.execution.get(run["run_id"])["state"] == "completed"
        with e.pool.connect() as conn:
            assert (
                conn.execute(
                    "SELECT actual FROM execution_reservations WHERE run_id=?", (run["run_id"],)
                ).fetchone()[0]
                == 15
            )
    finally:
        await io.drain()
        await service.io.drain()


@pytest.mark.asyncio
async def test_cancel_preserves_unknown_accounting_without_permanent_global_occupancy(
    group_env, monkeypatch
):
    e = group_env
    e.execution.configure(running=1, models=1, queued=10, user_queued=5)
    started = asyncio.Event()

    class Model:
        async def ainvoke(self, messages):
            started.set()
            await asyncio.Event().wait()

    run, service, io, runner = setup_runner(e, Model())
    queued = e.group.admit(call_context(e, "already-queued"))
    assert queued["admission_state"] == "accepted"
    task = asyncio.create_task(runner(ActiveExecution(service, run)))
    try:
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert e.execution.get(run["run_id"])["state"] == "unknown"
        refused = e.group.admit(call_context(e, "another-call"))
        assert (
            refused["admission_state"] == "rejected"
            and refused["reason_code"] == "group_expert_usage_unknown"
        )
        e.group.control("already-queued", sequence=1, kind="dispatch")
        personal = submit(e.execution, e.user, key="after-group-cancel")
        assert e.execution.claim(run["node_id"], run["incarnation"]) is None
        # Even when global capacity is available, the cooldown leaves accepted
        # questions queued rather than cancelling them across other groups.
        e.execution.configure(running=2, models=1, queued=10, user_queued=5)
        assert (
            e.execution.claim(run["node_id"], run["incarnation"], run_id=queued["run_id"]) is None
        )
        assert e.execution.get(queued["run_id"])["state"] == "queued"
        with e.pool.transaction() as conn:
            now = e.execution._now(conn)
            conn.execute("UPDATE execution_nodes SET lease_until=?", (now + 120,))
            reserved = conn.execute(
                "SELECT reserved FROM execution_reservations WHERE run_id=?", (run["run_id"],)
            ).fetchone()[0]
            assert quota_tokens(conn, e.user) == reserved
        monkeypatch.setattr(e.execution, "_now", lambda conn: now + 31)
        monkeypatch.setattr(e.group.execution, "_now", lambda conn: now + 31)
        assert e.group.admit(call_context(e, "after-cooldown"))["admission_state"] == "accepted"
        resumed = e.execution.claim(run["node_id"], run["incarnation"], run_id=queued["run_id"])
        assert resumed and resumed["run_id"] == queued["run_id"]
        e.execution.reserve_model(
            *fence(resumed), key="resumed-model", provider="example", tokens=1, quota_remaining=None
        )
        e.execution.transition(
            *fence(resumed),
            "failed",
            model_receipt=("resumed-model", 0),
            group_reason="group_expert_model_unavailable",
        )
        claimed = e.execution.claim(run["node_id"], run["incarnation"], run_id=personal["run_id"])
        assert claimed and claimed["run_id"] == personal["run_id"]
        e.repos.user_policy_repo.set(e.user, "token_quota", str(reserved))
        with pytest.raises(ExecutionConflict, match="token_quota_exceeded"):
            e.execution.reserve_model(
                *fence(claimed),
                key="personal-model",
                provider="example",
                tokens=1,
                quota_remaining=None,
            )
        e.repos.user_policy_repo.set(e.user, "token_quota", str(reserved + 10))
        e.execution.reserve_model(
            *fence(claimed),
            key="personal-model",
            provider="example",
            tokens=1,
            quota_remaining=None,
        )
        assert e.execution.get(run["run_id"])["state"] == "unknown"
    finally:
        if not task.done():
            task.cancel()
        await io.drain()
        await service.io.drain()


@pytest.mark.parametrize("state", ["failed", "cancelled", "unknown"])
def test_execution_reason_does_not_change_the_admission_receipt(group_env, state):
    e = group_env
    first = e.group.admit(call_context(e))
    run = run_call(e)
    e.execution.transition(*fence(run), state, group_reason="group_expert_timed_out")
    assert e.group.admit(call_context(e)) == first
    result = json.loads(e.group.delivery("call-one")["result_wire"])
    assert result["reason_code"] == "group_expert_timed_out"


def test_unknown_model_hold_expires_without_resolving_old_usage(group_env, monkeypatch):
    e = group_env
    e.execution.configure(running=4, models=4, queued=10, user_queued=5)
    first = run_call(e)
    second = run_call(e, "second")
    e.execution.reserve_model(
        *fence(first), key="unconfirmed", provider="example", tokens=100, quota_remaining=None
    )
    e.execution.transition(*fence(first), "unknown")
    reserve = {"key": "next", "provider": "example", "tokens": 5, "quota_remaining": None}
    with e.pool.transaction() as conn:
        now = conn.execute(
            "SELECT updated_at FROM execution_runs WHERE run_id=?", (first["run_id"],)
        ).fetchone()[0]
        conn.execute("UPDATE execution_nodes SET lease_until=?", (now + 120,))
    monkeypatch.setattr(e.execution, "_now", lambda conn: now + 29)
    with pytest.raises(ExecutionConflict, match="group_expert_usage_unknown"):
        e.execution.reserve_model(*fence(second), **reserve)
    monkeypatch.setattr(e.execution, "_now", lambda conn: now + 30)
    e.execution.reserve_model(*fence(second), **reserve)
    assert e.execution.get(first["run_id"])["state"] == "unknown"
    with e.pool.connect() as conn:
        assert quota_tokens(conn, e.user) == 100


def test_revision_probes_detect_live_changes_and_recreation(group_env):
    e = group_env
    publication = e.group.publication_revision()
    refresh = e.group.refresh_revision()
    assert e.group.publication_revision() == publication
    assert e.group.refresh_revision() == refresh
    # A provider's original fields can return to the same values; its immutable
    # authority revision must still force a rebuild.
    e.repos.provider_repo.update(e.provider, base_url="https://changed.invalid")
    e.repos.provider_repo.update(e.provider, base_url="https://model.invalid/v1")
    assert e.group.publication_revision() != publication
    assert e.group.refresh_revision() != refresh
    publication = e.group.publication_revision()
    e.repos.provider_repo.delete(e.provider)
    with e.pool.transaction() as conn:
        conn.execute(
            "INSERT INTO providers(id,name,kind,created_at,updated_at) VALUES(?,'model-source','openai',0,0)",
            (e.provider,),
        )
    assert e.group.publication_revision() != publication
    # Source identity edits also matter before any group projection exists.
    e.repos.agent_repo.create(agent_id="unpublished-source", user_id=e.admin, name="New")
    publication = e.group.publication_revision()
    e.repos.agent_repo.set_enabled("unpublished-source", False)
    assert e.group.publication_revision() != publication
    publication = e.group.publication_revision()
    with e.pool.transaction() as conn:
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (e.admin,))
        conn.execute("UPDATE users SET disabled=0 WHERE id=?", (e.admin,))
    assert e.group.publication_revision() != publication
    refresh = e.group.refresh_revision()
    with e.pool.transaction() as conn:
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (e.user,))
    assert e.group.refresh_revision() != refresh


def test_output_limit_change_does_not_change_admission_identity(group_env):
    e = group_env
    identity = call_context(e)
    first = e.group.admit(identity)
    assert e.group.admit({**identity, "max_text_chars": 7999}) == first
    e.group.control("call-one", sequence=1, kind="dispatch")
    assert e.group.execution_input("call-one")["identity"]["max_text_chars"] == 8000


def test_pending_authority_for_another_group_does_not_block_invitation(group_env):
    e = group_env
    with e.pool.transaction() as conn:
        conn.execute("UPDATE group_expert_outbox SET acknowledged_at=1")
        enqueue(
            conn,
            1,
            operation="scope:unrelated:1",
            kind="scope",
            target="unrelated",
            payload={"revision": 1, "sources": []},
        )
    assert e.group.syncing()
    assert not e.group.syncing(
        conversation_id="group-one", source=e.source.agent_id, source_row=e.source.id
    )
    assert not e.group.pending(conversation_id="group-one")
    assert e.group.syncing(conversation_id="unrelated")


def test_disabled_source_does_not_roll_back_other_source_publication(group_env):
    e = group_env
    e.repos.agent_repo.create(agent_id="second-source", user_id=e.admin, name="Second expert")
    second = e.repos.agent_repo.get("second-source")
    first_object = {
        "source_agent_id": e.source.agent_id,
        "source_row_id": e.source.id,
        "snapshot": e.snapshot,
        "display": {"name": "Original"},
    }
    second_object = {
        "source_agent_id": "second-source",
        "source_row_id": second.id,
        "snapshot": {**e.snapshot, "source_agent_id": "second-source", "source_row_id": second.id},
        "display": {"name": "Second"},
    }
    e.group.refresh_publication([first_object, second_object], source_manifest="a" * 64)
    with e.pool.transaction() as conn:
        conn.execute("UPDATE agents SET enabled=0 WHERE agent_id='second-source'")
    e.group.refresh_publication(
        [{**first_object, "display": {"name": "Updated"}}, second_object], source_manifest="a" * 64
    )
    with e.pool.connect() as conn:
        row = conn.execute(
            "SELECT display_json FROM group_expert_source_versions WHERE source_agent_id=?",
            (e.source.agent_id,),
        ).fetchone()
        assert json.loads(row[0])["name"] == "Updated"
    assert not any(row["source_agent_id"] == "second-source" for row in e.group.available_sources())


@pytest.mark.asyncio
async def test_unchanged_publication_skips_snapshot_and_avatar_work(group_env, monkeypatch):
    e = group_env
    objects = [
        {
            "source_agent_id": e.source.agent_id,
            "source_row_id": e.source.id,
            "snapshot": e.snapshot,
            "display": {"name": "Expert"},
        }
    ]
    prepare = AsyncMock(return_value=objects)
    monkeypatch.setattr("octop.infra.group_experts.service.prepare_publication", prepare)
    publication = SimpleNamespace(current=AsyncMock(return_value=("a" * 64, {})))
    groups = GroupExpertService(
        SimpleNamespace(
            services=e.repos,
            communication=SimpleNamespace(enabled=False),
            employee_resources=SimpleNamespace(publication=publication),
        )
    )
    try:
        await groups.refresh()
        await groups.refresh()
        assert prepare.await_count == 1
        e.repos.provider_repo.update(e.provider, base_url="https://changed.invalid")
        await groups.refresh()
        assert prepare.await_count == 2
    finally:
        await groups.close()


@pytest.mark.asyncio
async def test_heartbeat_renews_while_reconciliation_is_blocked():
    groups = GroupExpertService(
        SimpleNamespace(
            services=SimpleNamespace(group_expert_repo=None),
            communication=SimpleNamespace(enabled=True),
        )
    )
    heartbeats = asyncio.Queue()

    async def heartbeat():
        heartbeats.put_nowait(True)

    async def blocked_tick():
        await asyncio.Event().wait()

    groups.runtime = heartbeat
    groups.tick = blocked_tick
    try:
        await groups.start(background=True)
        await asyncio.wait_for(heartbeats.get(), 1)
        await asyncio.wait_for(heartbeats.get(), 4)
        assert groups._task and not groups._task.done()
    finally:
        await groups.close()


@pytest.mark.asyncio
async def test_unpublished_delivery_keeps_the_authority_reason(group_env):
    e = group_env
    run = run_call(e)
    e.execution.transition(*fence(run), "completed", group_result={"text": "Answer"})
    captured = []

    async def request(method, path, body=None):
        if path.endswith("/receipt"):
            return {"receipt": None}
        if path.endswith("/context"):
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "revoked",
                status=409,
                details={"reason": "group_expert_quote_audience"},
            )
        if path.endswith("/result"):
            captured.append(json.loads(body["result_wire"]))
            return {
                "call_id": "call-one",
                "result_digest": body["result_digest"],
                "delivery_state": "unpublished",
            }
        return {"saved": True}

    groups = GroupExpertService(
        SimpleNamespace(
            services=e.repos, communication=SimpleNamespace(enabled=False, group_request=request)
        )
    )
    try:
        await groups.deliver(e.group.result_obligation("call-one"))
        assert (
            captured[0]["result"] is None
            and captured[0]["reason_code"] == "group_expert_quote_audience"
        )
    finally:
        await groups.close()


@pytest.mark.asyncio
async def test_response_received_during_cancel_check_keeps_known_usage():
    finished = asyncio.Event()
    response = AIMessage(
        content="Answer",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )

    async def model():
        await finished.wait()
        return response

    async def check():
        finished.set()
        await asyncio.sleep(0)
        raise ExecutionConflict("execution_cancel_requested")

    executor = GroupExpertExecutor(SimpleNamespace())
    result = await executor.watch(model(), SimpleNamespace(check=check), "call", timeout=2)
    assert result is response and result.usage_metadata["total_tokens"] == 15


def test_server_error_is_not_zero_settled_from_an_earlier_connection_error():
    request = httpx.Request("POST", "https://model.invalid")
    error = httpx.HTTPStatusError(
        "unknown server outcome", request=request, response=httpx.Response(500, request=request)
    )
    error.__cause__ = httpx.ConnectError("earlier failure", request=request)
    assert not model_not_executed(error)
