import json

import pytest
from starlette.requests import Request

from octop.api.common.execution import execution_error_response
from octop.infra.db.repos.execution import ExecutionConflict


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ["runtime_owner_mismatch", "executor_fenced", "executor_stop_unconfirmed"]
)
async def test_runtime_conflicts_are_unavailability_not_permission_denials(reason):
    request = Request({"type": "http", "headers": [(b"accept-language", b"zh")]})
    response = await execution_error_response(request, ExecutionConflict(reason))
    body = json.loads(response.body)
    assert response.status_code == 503
    assert body["error"]["code"] == "RUNTIME_UNAVAILABLE"
    assert "没有权限" not in body["error"]["message"]
    assert response.headers["Retry-After"] == "1"
