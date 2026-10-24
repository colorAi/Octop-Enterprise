"""Composer context for dashboard chat turns."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from octop.api.routers.chat.turn import resolve_thread_id
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.process.message_keys import build_composer_context


class _ThreadRegistry:
    def __init__(self, row: object) -> None:
        self.row = row
        self.rebound = False

    def get_thread(self, thread_id: str) -> object:
        assert thread_id == "owner-thread"
        return self.row

    async def rebind(self, **_kwargs: object) -> None:
        self.rebound = True


async def test_resolve_thread_id_rejects_another_users_thread() -> None:
    registry = _ThreadRegistry(SimpleNamespace(agent_id="shared-agent", user_id=1))

    with pytest.raises(OctopError) as exc_info:
        await resolve_thread_id(
            agent_id="shared-agent",
            user_id=2,
            thread_registry=registry,
            thread_id="owner-thread",
            session_key=None,
        )

    assert exc_info.value.code == ErrorCode.FORBIDDEN
    assert registry.rebound is False


def test_build_composer_context_omits_default_model() -> None:
    ctx = build_composer_context(
        mcp_servers=["github"],
        skills=["docx"],
        target_agent_ids=["agent-b"],
        model_ref="openai/gpt-4o",
        default_model="openai/gpt-4o",
    )
    assert ctx == {
        "connectors": ["github"],
        "skills": ["docx"],
        "targetAgents": ["agent-b"],
    }


def test_build_composer_context_includes_model_override() -> None:
    ctx = build_composer_context(
        mcp_servers=None,
        skills=None,
        target_agent_ids=None,
        model_ref="openai/gpt-4o-mini",
        default_model="openai/gpt-4o",
    )
    assert ctx == {"model": "openai/gpt-4o-mini"}


@pytest.mark.parametrize("selection", [None, []])
async def test_plain_worker_turn_can_queue_without_a_sandbox(monkeypatch, selection):
    from unittest.mock import AsyncMock, Mock

    from octop.api.routers.chat.models import ChatTurnBody
    from octop.api.routers.chat.turn import prepare_dashboard_turn

    access = SimpleNamespace(assert_use=Mock(), assigned_only=lambda _: False)
    monkeypatch.setattr("octop.infra.users.expert_access.ExpertAccess", lambda _: access)
    model_access = SimpleNamespace(resolve=Mock(return_value="test/model"))
    monkeypatch.setattr("octop.infra.users.model_access.ModelAccess", lambda _: model_access)
    monkeypatch.setattr(
        "octop.api.routers.chat.turn.require_agent_row",
        lambda *a, **kw: SimpleNamespace(default_model="test/model"),
    )
    monkeypatch.setattr(
        "octop.api.routers.chat.turn.resolve_thread_id",
        AsyncMock(return_value=("thread", "session")),
    )
    registry = SimpleNamespace(start=AsyncMock(side_effect=AssertionError("no sandbox available")))
    server = SimpleNamespace(
        role="worker",
        services=SimpleNamespace(),
        app_runtime=SimpleNamespace(
            agent_registry=registry, gateway=SimpleNamespace(thread_registry=object())
        ),
    )
    prepared = await prepare_dashboard_turn(
        server,
        agent_id="expert",
        user=SimpleNamespace(id=7, is_admin=False),
        turn=ChatTurnBody(text="hello", skills=selection, mcp_servers=selection),
    )
    assert prepared.thread_id == "thread"
    assert prepared.skills == selection
    access.assert_use.assert_called_once_with(7, "expert")
    model_access.resolve.assert_called_once_with(7)
    registry.start.assert_not_called()
