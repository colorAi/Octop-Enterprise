import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from tests.unit.db.test_group_experts import group_env as group_env
from tests.unit.db.test_group_experts import group_pool as group_pool
from tests.unit.db.test_group_experts import run_call

from octop.infra.db.repos.execution import ExecutionConflict
from octop.infra.execution.service import ActiveExecution, ExecutionService
from octop.infra.group_experts.executor import GroupExpertExecutor, build_model
from octop.infra.group_experts.policy import source_tool_policy
from octop.infra.group_experts.tools import GroupTools
from octop.infra.utils.bounded_io import BoundedIO


def publish_tools(e, config=None):
    policy = source_tool_policy(config or {})
    e.snapshot.update(**policy, tool_revision=e.group.tool_revision(e.source.agent_id, e.source.id))
    published = e.group.publish(
        e.snapshot, source_manifest="a" * 64, display={"name": "Group expert"}
    )
    e.versions[f"source:{e.source.agent_id}:{e.source.id}"] = published["revision"]


def test_source_tool_switches_are_the_authority():
    policy = source_tool_policy(
        {
            "tools_disabled": ["generate_image", "searchfree_search"],
            "web_search_tools": ["searchfree", "brave"],
        }
    )
    assert policy["tools"] == ["company_web_search", "company_generate_video"]
    assert policy["search_tools"] == ["brave_search"]
    assert source_tool_policy({"web_search_tools": False, "media_generation": False})["tools"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "duplicate", "unknown_model", "source_revoked"])
async def test_group_uses_expert_tools_and_preserves_each_model_receipt(
    group_env, monkeypatch, outcome
):
    e = group_env
    publish_tools(e)
    run = run_call(e)
    service = ExecutionService(
        e.execution, node=run["node_id"], incarnation=run["incarnation"], role="worker"
    )
    io = BoundedIO(wait=True)
    invocations, model_calls, roots = [], [], []

    async def context(call):
        return {**e.group.execution_input(call), "question": "Find today's weather", "quote": ""}

    @tool
    async def searchfree_search(query: str) -> str:
        """A controlled search service."""
        invocations.append(query)
        if outcome == "source_revoked":
            with e.pool.transaction() as conn:
                conn.execute(
                    "UPDATE agents SET config_json=? WHERE agent_id=?",
                    (json.dumps({"tools_disabled": ["searchfree_search"]}), e.source.agent_id),
                )
                # Re-enabling immediately must still revoke this execution.
                conn.execute(
                    "UPDATE agents SET config_json='{}' WHERE agent_id=?", (e.source.agent_id,)
                )
            await asyncio.Event().wait()
        return "A controlled weather result"

    monkeypatch.setattr(
        "octop_harness.builtin.tools.web_search.load_web_search_tools",
        lambda policy: [searchfree_search],
    )

    class Runtime(GroupTools):
        async def open(self):
            await super().open()
            roots.append(self.workspace.workspace_dir)
            assert not list(self.workspace.workspace_dir.iterdir())

    class Model:
        async def ainvoke(self, messages):
            index = len(model_calls)
            model_calls.append(list(messages))
            if index and outcome == "unknown_model":
                raise TimeoutError("result lost")
            if index == 0 or (outcome == "duplicate" and index == 1):
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "company_web_search",
                            "args": {"query": "weather"},
                            "id": f"search-{index}",
                        }
                    ],
                    usage_metadata={"input_tokens": 30, "output_tokens": 10, "total_tokens": 40},
                )
            assert "controlled weather result" in messages[-1].content
            return AIMessage(
                content="The weather result",
                usage_metadata={"input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
            )

    # There is deliberately no employee_resources/has_capability on this server.
    # A caller without personal search grants still invokes the expert's tool.
    assert e.repos.employee_resource_repo.skills(e.user) == []
    groups = SimpleNamespace(context=context, io=io, services=e.repos, repo=e.group)
    try:
        await asyncio.wait_for(
            GroupExpertExecutor(
                groups, model_factory=lambda p, s, t: Model(), tools_factory=Runtime
            )(ActiveExecution(service, run)),
            5,
        )
        final = e.execution.get(run["run_id"])
        assert final["state"] == (
            "unknown" if outcome in {"unknown_model", "source_revoked"} else "completed"
        )
        assert invocations == ["weather"]
        assert roots and all(not root.exists() for root in roots)
        with e.pool.connect() as conn:
            usage = conn.execute(
                "SELECT SUM(total_tokens) FROM usage_log WHERE execution_run_id=?", (run["run_id"],)
            ).fetchone()[0]
            assert (
                usage
                == {"completed": 100, "duplicate": 140, "unknown_model": 40, "source_revoked": 40}[
                    outcome
                ]
            )
            tools = conn.execute(
                "SELECT state FROM execution_tools WHERE run_id=?", (run["run_id"],)
            ).fetchall()
            assert len(tools) == 1 and tools[0][0] == (
                "unknown" if outcome == "source_revoked" else "completed"
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM group_expert_outbox WHERE operation_id='result:call-one'"
                ).fetchone()[0]
                == 1
            )
    finally:
        await io.drain()
        await service.io.drain()


def test_employee_grant_cannot_enable_an_expert_disabled_tool(group_env):
    e = group_env
    publish_tools(e, {"web_search_tools": False})
    run = run_call(e)
    with e.pool.transaction() as conn:
        conn.execute(
            "INSERT INTO employee_skill_assignments(user_id,resource_id) VALUES(?,?)",
            (e.user, "capability:web_search"),
        )
    with pytest.raises(ExecutionConflict, match="tool_not_authorized"):
        e.group.begin_tool(
            run["run_id"],
            run["node_id"],
            run["incarnation"],
            run["token"],
            operation="not-allowed",
            request={"name": "company_web_search", "args": {"query": "weather"}},
        )


@pytest.mark.asyncio
async def test_call_workspaces_cannot_read_each_others_files(tmp_path):
    value = {"identity": {"call_id": "one", "user_id": 1}, "snapshot": {"tools": []}}
    first, second = GroupTools(None, None, value), GroupTools(None, None, value)
    try:
        await first.open()
        await second.open()
        await first.workspace.aupload_many([("generated/test.txt", b"first call output")])
        assert await second.workspace.adownload_bytes("generated/test.txt") is None
        assert first.workspace.workspace_dir != second.workspace.workspace_dir
        assert not first.tools and not second.tools
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_real_factory_binds_only_the_experts_selected_tools(group_env):
    e = group_env
    publish_tools(e, {"tools_disabled": ["generate_video"], "web_search_tools": False})
    runtime = GroupTools(
        None, None, {"identity": {"call_id": "one", "user_id": 1}, "snapshot": e.snapshot}
    )
    try:
        await runtime.open()
        model = build_model(
            e.repos.provider_repo.get(e.provider), e.snapshot, list(runtime.tools.values())
        )
        assert [item["function"]["name"] for item in model.kwargs["tools"]] == [
            "company_generate_image"
        ]
        assert model.bound.max_retries == 0
    finally:
        await runtime.close()
