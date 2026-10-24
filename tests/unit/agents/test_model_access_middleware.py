from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from octop_harness import HarnessAgent, HarnessAgentConfig
from octop_harness.config import ModelConfig, ProviderConfig
from octop_harness.llm.factory import ChatModelFactory
from tests.unit.users.test_model_access import grant
from tests.unit.users.test_model_access import model_env as model_env

from octop.infra.agents.middleware import model_access as middleware
from octop.infra.errors import OctopError


def request(ref):
    return SimpleNamespace(runtime=SimpleNamespace(config={"configurable": {"model": ref}}))


async def test_acting_user_grants_override_admin_owned_agent_and_recheck_revocation(
    model_env, monkeypatch
):
    e = model_env
    e.services.agent_repo.create(agent_id="shared", user_id=e.admin, name="Shared")
    guard = middleware.ModelAccessMiddleware(e.services.repos, "shared")
    monkeypatch.setattr(middleware, "configurable_user_id", lambda: e.employee)
    handler = AsyncMock(return_value="ok")
    with pytest.raises(OctopError):
        await guard.awrap_model_call(request("alpha/chat"), handler)
    handler.assert_not_awaited()
    grant(e, ["alpha/chat"])
    assert await guard.awrap_model_call(request("alpha/chat"), handler) == "ok"
    grant(e, [])
    with pytest.raises(OctopError):
        await guard.awrap_model_call(request("alpha/chat"), handler)
    assert handler.await_count == 1


def test_sync_calls_use_owner_policy_when_no_caller_context(model_env, monkeypatch):
    e = model_env
    e.services.agent_repo.create(agent_id="private", user_id=e.employee, name="Private")
    guard = middleware.ModelAccessMiddleware(e.services.repos, "private")
    monkeypatch.setattr(middleware, "configurable_user_id", lambda: None)
    handler = Mock(return_value="ok")
    with pytest.raises(OctopError):
        guard.wrap_model_call(request("beta/chat"), handler)
    grant(e, ["beta/chat"])
    assert guard.wrap_model_call(request("beta/chat"), handler) == "ok"


async def test_resume_uses_employee_thread_owner_before_shared_expert_owner(model_env, monkeypatch):
    e = model_env
    e.services.agent_repo.create(agent_id="shared", user_id=e.admin, name="Shared")
    e.services.thread_repo.insert(
        thread_id="employee-thread",
        agent_id="shared",
        user_id=e.employee,
        channel_type="dashboard",
        session_key="employee-thread",
        last_active=0,
    )
    monkeypatch.setattr(middleware, "configurable_user_id", lambda: None)
    guard = middleware.ModelAccessMiddleware(e.services.repos, "shared")
    call = request("alpha/chat")
    call.runtime.config["configurable"]["thread_id"] = "employee-thread"
    handler = AsyncMock(return_value="ok")
    with pytest.raises(OctopError):
        await guard.awrap_model_call(call, handler)
    handler.assert_not_awaited()
    grant(e, ["alpha/chat"])
    assert await guard.awrap_model_call(call, handler) == "ok"


class AssignedTestModel(BaseChatModel):
    calls: int = 0

    @property
    def _llm_type(self):
        return "assigned-model-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Allowed reply"))])


async def test_real_harness_routes_before_grant_guard_and_denies_revoked_model(model_env, tmp_path):
    e = model_env
    grant(e, ["alpha/chat"])
    e.services.agent_repo.create(agent_id="harness", user_id=e.admin, name="Harness")
    provider = ProviderConfig(
        id="alpha", api_key="fake", base_url="http://invalid", models=[ModelConfig(id="chat")]
    )
    factory = ChatModelFactory([provider])
    model = AssignedTestModel()
    factory._cache["alpha/chat"] = model
    agent = HarnessAgent(
        HarnessAgentConfig(
            name="grant-test",
            workspace_dir=str(tmp_path / "workspace"),
            providers=[provider],
            default_model="alpha/chat",
            bootstrap_enabled=False,
            memory_enabled=False,
            session_log_enabled=False,
            subagents_auto_load=False,
            ask_user_enabled=False,
            tool_guard_enabled=False,
            model_retry_enabled=False,
            middleware=[middleware.ModelAccessMiddleware(e.services.repos, "harness")],
        ),
        model_factory=factory,
    )
    try:
        chunks = [
            chunk
            async for chunk in agent.stream(
                {
                    "thread_id": "thread",
                    "messages": "Hello",
                    "model": "alpha/chat",
                    "user": str(e.employee),
                }
            )
        ]
        assert chunks and model.calls == 1
        grant(e, [])
        with pytest.raises(OctopError):
            async for _ in agent.stream(
                {
                    "thread_id": "thread",
                    "messages": "Again",
                    "model": "alpha/chat",
                    "user": str(e.employee),
                }
            ):
                pass
        assert model.calls == 1
    finally:
        await agent.aclose()
