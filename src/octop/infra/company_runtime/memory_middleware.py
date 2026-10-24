"""Recall through the server; the native harness never opens a memory store."""

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from octop_harness.middleware.memory_recall import (
    has_recall_snapshot,
    replay_recall_snapshots,
    stamp_recall_snapshot,
)


class CompanyMemoryMiddleware(AgentMiddleware):
    def __init__(self, client: Any, task_id: str) -> None:
        self.client, self.task_id = client, task_id

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], HumanMessage):
            return None
        message = messages[-1]
        if has_recall_snapshot(message):
            return None
        content = message.content
        query = (
            content
            if isinstance(content, str)
            else "\n".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        )
        result = await self.client.request(
            f"tasks/{self.task_id}/memory/recall", {"query": query[:10000]}
        )
        return {"messages": [stamp_recall_snapshot(message, result.get("rendered", ""))]}

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(request.override(messages=replay_recall_snapshots(request.messages)))
