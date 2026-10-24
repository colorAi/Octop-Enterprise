"""Read continuity for employee-owned experts after managed assignments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def personal_predecessors(services: Any, user_id: int, agent_id: str) -> list[str]:
    """Only an unambiguous earlier private instance of the same expert qualifies.

    Never inherit an administrator's memory, match display names, or move data.
    Ambiguous templates remain separately available in the historical expert list.
    """
    repo = getattr(services, "expert_assignment_repo", None)
    mapping = repo.instance(agent_id) if repo is not None else None
    current = services.agent_repo.get(agent_id)
    if not mapping or mapping["user_id"] != user_id or not current or current.user_id != user_id:
        return []
    assert repo is not None
    source = services.agent_repo.get(mapping["source_agent_id"])
    if source is None or source.id != mapping["source_row_id"]:
        return []
    candidates = [
        row
        for row in services.agent_repo.list_by_user(user_id)
        if row.kind == "expert" and row.id < current.id and repo.instance(row.agent_id) is None
    ]
    linked = [
        row.agent_id
        for row in candidates
        if json.loads(row.config_json or "{}").get("shared_template_id") == source.agent_id
    ]
    if linked:
        return linked if len(linked) == 1 else []
    if not source.template_name:
        return []
    sources = [
        row
        for row in services.agent_repo.list_by_user(source.user_id)
        if row.kind == "expert" and row.template_name == source.template_name
    ]
    matches = [row.agent_id for row in candidates if row.template_name == source.template_name]
    return matches if len(sources) == 1 and len(matches) == 1 else []


def legacy_personal_files(services: Any, user_id: int, agent_id: str) -> dict[str, str]:
    """Read only the owner's pre-isolation profile, never a configured host path.

    The control-plane migration pins these agents to SQLite. Their profile
    files predate the Docker volumes, so opening an empty volume loses them.
    """
    row = services.agent_repo.get(agent_id)
    if row is None or row.user_id != user_id:
        return {}
    cfg = json.loads(row.config_json or "{}")
    memory_cfg = cfg.get("memory")
    backend = memory_cfg.get("backend") if isinstance(memory_cfg, dict) else None
    if not isinstance(backend, dict) or backend.get("type") != "sqlite":
        return {}
    root = services.paths.agents_dir / agent_id
    if root.is_symlink() or not root.is_dir():
        return {}
    result = {}
    for name in ("USER.md", "MEMORY.md"):
        path: Path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 262144:
            continue
        result[name] = path.read_text(encoding="utf-8")
    return result
