"""Seed a fresh local turn from the messages preceding an edited question."""

from __future__ import annotations

import base64
from typing import Any
from uuid import UUID

from langchain_core.messages import messages_from_dict
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def edit_checkpoint(
    thread_id: str, messages: list[dict[str, Any]], previous_id: str | None
) -> dict[str, Any]:
    serde = JsonPlusSerializer(pickle_fallback=False)

    def pack(value: Any) -> list[str]:
        kind, data = serde.dumps_typed(value)
        return [kind, base64.b64encode(data).decode()]

    checkpoint = empty_checkpoint()
    # Native checkpoints use the employee PC's clock. A server timestamp may
    # sort before that PC's history, or ahead of its next normal checkpoint.
    checkpoint["id"] = str(UUID(int=UUID(previous_id).int + 1 if previous_id else 1))
    checkpoint["channel_values"] = {"messages": messages_from_dict(messages)}
    checkpoint["channel_versions"] = {"messages": 1}
    return {
        "config": {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint["id"],
            }
        },
        "checkpoint": pack(checkpoint),
        "metadata": pack({"source": "update", "step": -1, "parents": {}}),
        # Do not resume pending tools or walk back into the replaced branch.
        "parent_config": None,
    }
