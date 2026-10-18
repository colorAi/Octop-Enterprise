"""SSE / WebSocket frame formatting helpers."""

from __future__ import annotations

import json
from typing import Any


def json_chunk_default(obj: Any) -> Any:
    """Fallback serializer for types json.dumps can't handle natively."""
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    return repr(obj)


def format_sse(event: str, data: Any) -> str:
    if isinstance(data, str):
        payload = data
    else:
        try:
            payload = json.dumps(data, ensure_ascii=False, default=json_chunk_default)
        except Exception:
            payload = json.dumps({"type": "error", "message": "chunk not serializable"})
    return f"event: {event}\ndata: {payload}\n\n"
