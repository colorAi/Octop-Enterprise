"""Shared chat turn result for CLI streaming."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatTurnResult:
    text: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)
    elapsed: float = 0.0
