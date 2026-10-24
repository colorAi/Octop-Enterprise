"""Deployment-wide model slots and preflight quota reservations."""

from __future__ import annotations

import json
import uuid
from contextlib import suppress
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from octop.infra.db.repos.execution import ExecutionConflict
from octop.infra.execution.service import current_execution


class ExecutionBudgetMiddleware(AgentMiddleware[Any, Any]):
    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        active = current_execution.get()
        if active is None:
            return await handler(request)
        await active.check()
        settings = dict(request.model_settings or {})
        output = int(settings.get("max_tokens") or settings.get("max_completion_tokens") or 8192)
        if not 1 <= output <= 65536:
            raise ExecutionConflict("model_output_limit_exceeded")
        messages = [*request.messages]
        if request.system_message is not None:
            messages.append(request.system_message)
        prompt = json.dumps(
            [getattr(message, "content", "") for message in messages], ensure_ascii=False
        )
        if len(prompt.encode()) > 1024 * 1024:
            raise ExecutionConflict("model_input_limit_exceeded")
        text_only = all(isinstance(getattr(message, "content", None), str) for message in messages)
        reservation = (
            len(prompt.encode()) + len(str(request.tools).encode()) + 4096 if text_only else 128000
        ) + output
        key = uuid.uuid4().hex
        service = active.service
        await service.io.run(
            service.repo.reserve_model,
            *active.fence,
            key=key,
            provider=str(
                getattr(request.model, "model_name", "")
                or getattr(request.model, "model", "default")
            ),
            tokens=reservation,
            quota_remaining=None,
        )
        try:
            setting = (
                "max_completion_tokens" if "max_completion_tokens" in settings else "max_tokens"
            )
            response = await handler(request.override(model_settings={**settings, setting: output}))
        except BaseException:
            with suppress(Exception):
                await service.io.run(service.repo.settle_model, *active.fence, key=key, actual=None)
            raise
        usage = [
            getattr(message, "usage_metadata", None) for message in getattr(response, "result", [])
        ]
        totals = [
            int(item["total_tokens"])
            for item in usage
            if isinstance(item, dict) and isinstance(item.get("total_tokens"), int)
        ]
        # Missing usage retains a conservative charge, never a free request.
        await service.io.run(
            service.repo.settle_model,
            *active.fence,
            key=key,
            actual=sum(totals) if totals else reservation,
        )
        return response
