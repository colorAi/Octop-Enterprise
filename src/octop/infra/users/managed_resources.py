"""Source-owned expert definitions, separate from employee data."""

from __future__ import annotations

import ntpath
import posixpath
from pathlib import Path
from typing import Any

DEFINITION_FILES = (
    "AGENTS.md",
    "SOUL.md",
    "IDENTITY.md",
    "TOOLS.md",
    "HEARTBEAT.md",
    "PROACTIVE.md",
)
DEFINITION_FIELDS = (
    "name",
    "description",
    "system_prompt",
    "default_model",
    "icon",
    "icon_name",
    "icon_url",
    "template_name",
    "persona_mbti",
    "color",
    "welcome_message",
)


def workspace_relative_path(workspace: Any, path: str) -> str:
    """Resolve API aliases before checking a managed resource path."""
    raw = path.replace("\\", "/")
    root = str(workspace.workspace_dir).replace("\\", "/").rstrip("/")
    if raw == root or raw.startswith(root + "/"):
        raw = raw[len(root) :].lstrip("/")
    elif raw.startswith("/") or ntpath.isabs(raw):
        raise ValueError("path outside managed workspace")
    normalized = posixpath.normpath(raw)
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError("path outside managed workspace")
    local_root = Path(workspace.workspace_dir)
    if local_root.is_dir():
        try:
            normalized = (
                (local_root / normalized).resolve().relative_to(local_root.resolve()).as_posix()
            )
        except ValueError:
            raise ValueError("path outside managed workspace") from None
    return normalized


def protected_definition_path(path: str) -> bool:
    parts = path.casefold().split("/")
    return (
        path == "."
        or parts[0].startswith(".")
        or parts[0]
        in {
            "skills",
            "_builtin_skills",
            "agents",
            "bootstrap.md",
            *(x.casefold() for x in DEFINITION_FILES),
        }
    )
