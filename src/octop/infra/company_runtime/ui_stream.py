"""Project graph state to the metadata consumed by the desktop chat page."""

from __future__ import annotations

import math
from typing import Any

from langchain_core.messages import BaseMessage
from octop_harness.context_usage import SEGMENT_KEYS


def _counts(source: Any, keys: tuple[str, ...]) -> dict[str, int | float]:
    if not isinstance(source, dict):
        return {}
    return {
        key: value
        for key in keys
        if isinstance(value := source.get(key), (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    }


def project_ui_chunk(chunk: dict[str, Any]) -> dict[str, Any] | None:
    """Keep live output intact; state frames carry only the latest AI usage.

    Graph/checkpoint messages remain untouched. Never dump a whole BaseMessage
    to extract metadata: its content may contain many megabytes of images.
    """
    if chunk.get("type") not in {"state_update", "state_snapshot"}:
        return chunk
    data = chunk.get("data")
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        return None
    for wire in reversed(messages):
        role: Any
        extra: Any
        if isinstance(wire, BaseMessage):
            role = wire.type
            usage = getattr(wire, "usage_metadata", None)
            extra = wire.additional_kwargs
        elif isinstance(wire, dict):
            message = wire.get("data", wire.get("kwargs", wire))
            if not isinstance(message, dict):
                continue
            role = wire.get("type") or message.get("type") or message.get("role")
            usage = message.get("usage_metadata")
            extra = message.get("additional_kwargs")
        else:
            continue
        if role not in {None, "ai", "AIMessage", "AIMessageChunk", "assistant"}:
            continue
        usage = _counts(usage, ("input_tokens", "prompt_tokens"))
        stamp = extra.get("context_usage") if isinstance(extra, dict) else None
        compact_stamp: dict[str, Any] = _counts(
            stamp, ("input_tokens", "used_tokens", "max_tokens")
        )
        if isinstance(stamp, dict):
            compact_stamp["segments"] = _counts(stamp.get("segments"), SEGMENT_KEYS)
        if not usage and not compact_stamp:
            continue
        projected = {
            "type": "ai",
            "usage_metadata": usage,
            "additional_kwargs": {"context_usage": compact_stamp} if compact_stamp else {},
        }
        return {**chunk, "data": {"messages": [projected]}}
    return None
