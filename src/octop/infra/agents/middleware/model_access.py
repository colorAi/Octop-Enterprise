"""Recheck the acting employee's model grant before every routed model call."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from octop_harness.middleware.runtime import runtime_config

from octop.infra.agents.middleware.token_quota import configurable_user_id
from octop.infra.users.model_access import ModelAccess
from octop.infra.utils.asyncio import offload


class ModelAccessMiddleware(AgentMiddleware[Any, Any]):
    def __init__(self, repos: Any, agent_id: str) -> None:
        super().__init__()
        self.access = ModelAccess(repos)
        self.agent_id = agent_id

    def _actor(self, actor: int | None, thread_id: str | None) -> int | None:
        if actor is None:
            # HITL resume may contain only the thread id, without the original caller.
            thread = self.access.services.thread_repo.get(thread_id) if thread_id else None
            actor = thread.user_id if thread is not None else None
        if actor is None:
            owner = self.access.services.agent_repo.get(self.agent_id)
            actor = owner.user_id if owner is not None else None
        return actor

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        configurable = runtime_config(request).get("configurable") or {}
        actor = self._actor(configurable_user_id(), configurable.get("thread_id"))
        if actor is not None and not self.access.is_admin(actor):
            self.access.require(actor, str(configurable.get("model") or ""))
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        configurable = runtime_config(request).get("configurable") or {}
        actor = await offload(self._actor, configurable_user_id(), configurable.get("thread_id"))
        if actor is not None:
            ref = str(configurable.get("model") or "")
            if not await offload(self.access.is_admin, actor):
                await offload(self.access.require, actor, ref)
        return await handler(request)
