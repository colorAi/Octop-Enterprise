"""Keep conversation context compatible with strict chat-role templates."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage


def normalize_history_roles(request: ModelRequest[Any]) -> ModelRequest[Any]:
    # The authoritative system prompt is request.system_message. Older team
    # checkpoints also contain dispatch context as SystemMessage in history.
    # Keep that context at conversation privilege without rewriting saved data.
    messages: list[AnyMessage] = []
    for message in request.messages:
        if isinstance(message, SystemMessage):
            message = HumanMessage(content=message.content, id=message.id)
        elif isinstance(message, AIMessage):
            # Harness names use Octop's agent_<id> memory namespace. They are
            # routing metadata, not model-facing speaker names. Some older
            # messages also keep the name in additional_kwargs.
            updates: dict[str, Any] = {}
            if message.name and message.name.startswith("agent_"):
                updates["name"] = None
            legacy_name = message.additional_kwargs.get("name")
            if isinstance(legacy_name, str) and legacy_name.startswith("agent_"):
                updates["additional_kwargs"] = {
                    key: value for key, value in message.additional_kwargs.items() if key != "name"
                }
            if updates:
                message = message.model_copy(update=updates)
        messages.append(message)
    if all(
        current is original for current, original in zip(messages, request.messages, strict=True)
    ):
        return request
    return request.override(messages=messages)


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
