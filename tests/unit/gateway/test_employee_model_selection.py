from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tests.unit.users.test_model_access import grant
from tests.unit.users.test_model_access import model_env as model_env

from octop.infra.agents.providers.store import ProviderStore
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.process.processor import GlobalProcessor


@pytest.fixture
def selection(model_env):
    e = model_env
    manager = MagicMock()
    manager._repos = e.services.repos
    manager.providers = ProviderStore(e.services.provider_repo)
    manager.get_thread_model.return_value = None
    manager.resolve_fallback_model_ref.return_value = "alpha/chat"
    threads = MagicMock()
    threads.get_thread.return_value = None
    processor = GlobalProcessor(
        agent_manager=manager,
        thread_registry=threads,
        audit_repo=e.services.audit_repo,
        agent_repo=e.services.agent_repo,
        user_repo=e.services.user_repo,
        connector_repo=MagicMock(),
        dispatcher=MagicMock(),
        gateway=MagicMock(),
    )
    return SimpleNamespace(env=e, manager=manager, processor=processor)


def test_auto_and_stale_sticky_selection_fall_back_within_employee_grants(selection):
    s = selection
    grant(s.env, ["beta/chat"])
    s.manager.get_thread_model.return_value = "alpha/chat"
    assert (
        s.processor._resolve_harness_model(
            "agent",
            "thread",
            None,
            needs_multimodal=False,
            user_id=s.env.employee,
        )
        == "beta/chat"
    )
    with pytest.raises(OctopError) as exc:
        s.processor._resolve_harness_model(
            "agent",
            "thread",
            {"model": "alpha/chat"},
            needs_multimodal=False,
            user_id=s.env.employee,
        )
    assert exc.value.code == ErrorCode.MODEL_NOT_ASSIGNED
    grant(s.env, [])
    with pytest.raises(OctopError):
        s.processor._resolve_harness_model(
            "agent",
            "thread",
            None,
            needs_multimodal=False,
            user_id=s.env.employee,
        )


def test_vision_upgrade_cannot_use_an_unassigned_vision_model(selection):
    s = selection
    grant(s.env, ["alpha/chat"])
    with pytest.raises(OctopError):
        s.processor._resolve_harness_model(
            "agent",
            "thread",
            None,
            needs_multimodal=True,
            user_id=s.env.employee,
        )
    grant(s.env, ["alpha/chat", "alpha/vision"])
    assert (
        s.processor._resolve_harness_model(
            "agent",
            "thread",
            None,
            needs_multimodal=True,
            user_id=s.env.employee,
        )
        == "alpha/vision"
    )
