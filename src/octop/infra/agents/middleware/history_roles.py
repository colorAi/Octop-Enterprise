"""Keep conversation context compatible with strict chat-role templates."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage, SystemMessage


def normalize_history_roles(request: ModelRequest[Any]) -> ModelRequest[Any]:
    # The authoritative system prompt is request.system_message. Older team
    # checkpoints also contain dispatch context as SystemMessage in history.
    # Keep that context at conversation privilege without rewriting saved data.
    if not any(isinstance(message, SystemMessage) for message in request.messages):
        return request
    return request.override(
        messages=[
            HumanMessage(content=message.content, id=message.id)
            if isinstance(message, SystemMessage)
            else message
            for message in request.messages
        ]
    )


class HistoryRolesMiddleware(AgentMiddleware[Any, Any]):
    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(normalize_history_roles(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(normalize_history_roles(request))
