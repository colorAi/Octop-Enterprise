import json

import pytest

from octop.infra.company_runtime.client import CompanyServiceError
from octop.infra.users.model_access import MODEL_ACCESS_POLICY

from .test_runtime import acquire, install_agent


async def test_native_snapshot_contains_only_assigned_models(company):
    server, client, _, _ = company
    install_agent(server)
    agent = server.app_runtime.agent_registry.get_agent("expert")
    agent.model_factory.list_refs = lambda: ["test/other", "test/model"]
    agent.config.pick_default_model_ref = lambda: "test/other"
    server.app_runtime.agent_registry.resolve_fallback_model_ref = lambda: "test/other"
    result = await acquire(client)
    assert result["expert"]["models"] == ["test/model"]
    assert result["expert"]["default_model"] == "test/model"
    assert set(result["expert"]["model_profiles"]) == {"test/model"}


async def test_native_employee_without_models_cannot_start(company):
    server, client, _, owner = company
    install_agent(server)
    server.services.user_policy_repo.set(owner, MODEL_ACCESS_POLICY, None)
    with pytest.raises(CompanyServiceError) as exc:
        await acquire(client)
    assert exc.value.status == 403
    assert str(exc.value) == "MODEL_NOT_ASSIGNED"
    assert server.services.company_runtime_repo.task("task1") is None


async def test_native_model_proxy_rechecks_grants_after_snapshot(company):
    server, client, _, owner = company
    install_agent(server)
    await acquire(client)
    server.services.user_policy_repo.set(owner, MODEL_ACCESS_POLICY, None)
    response = await client.http.post(
        "tasks/task1/model", json={"model": "test/model", "messages": []}
    )
    assert response.status_code == 200
    error = json.loads(response.text)
    assert error == {"error": "MODEL_NOT_ASSIGNED", "retryable": False}
