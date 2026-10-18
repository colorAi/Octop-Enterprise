"""Block ``read_file`` on binary inbound attachments (PDF, Office, …)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

_READ_FILE_TOOL = "read_file"
_BLOCKED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".zip",
        ".bin",
        ".gif",
        ".webp",
        ".jpg",
        ".jpeg",
        ".png",
    }
)
_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".csv",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".html",
        ".xml",
        ".yaml",
        ".yml",
        ".sql",
        ".sh",
    }
)


def _read_file_path_from_args(params: dict[str, Any]) -> str:
    for key in ("file_path", "path"):
        raw = params.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _extension(path: str) -> str:
    cleaned = path.split("?", 1)[0].split("#", 1)[0]
    return Path(cleaned).suffix.lower()


def _is_inbound_attachment(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return normalized.startswith("inbound/") or "/inbound/" in normalized


def read_file_block_reason(path: str) -> str | None:
    """Return a rejection message when *path* must not be read via ``read_file``."""
    if not path:
        return None
    ext = _extension(path)
    if ext == ".pdf":
        return (
            f"read_file blocked for binary PDF `{path}`. "
            "Use execute_shell_command with the pdf skill (pdftotext, pdfplumber, etc.) instead."
        )
    if ext in _BLOCKED_EXTENSIONS:
        return (
            f"read_file blocked for binary file `{path}`. "
            "Use execute_shell_command or the matching skill instead."
        )
    if _is_inbound_attachment(path) and ext and ext not in _TEXT_EXTENSIONS:
        return (
            f"read_file blocked for inbound attachment `{path}`. "
            "Use execute_shell_command or the matching skill instead."
        )
    return None


class BinaryReadGuardMiddleware(AgentMiddleware[Any, Any]):
    """Reject read_file on PDF/Office inbound attachments before bytes hit context."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call
        tool_name = str(tool_call.get("name") or "")
        if tool_name != _READ_FILE_TOOL:
            return await handler(request)

        params = tool_call.get("args") or {}
        if not isinstance(params, dict):
            params = {}
        path = _read_file_path_from_args(params)
        reason = read_file_block_reason(path)
        if reason is None:
            return await handler(request)

        logger.info("BinaryReadGuard blocked read_file on %s", path)
        return ToolMessage(
            content=reason,
            tool_call_id=str(tool_call.get("id") or ""),
            status="error",
        )


__all__ = ["BinaryReadGuardMiddleware", "read_file_block_reason"]
