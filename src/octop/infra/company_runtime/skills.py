"""Immutable company skill assets; task snapshots pin hashes before any download."""

from __future__ import annotations

import base64
from pathlib import Path, PurePosixPath
from typing import Any

from octop.infra.utils.immutable_files import read_object, store_object


def store_asset(root: Path, data: bytes) -> str:
    try:
        return store_object(root, data)
    except ValueError:
        raise ValueError("skill_cache_corrupt") from None


def read_asset(root: Path, digest: str) -> bytes:
    try:
        return read_object(root, digest)
    except (OSError, ValueError):
        raise ValueError("skill_checksum_mismatch") from None


async def snapshot_skills(agent: Any, cache: Path) -> list[dict[str, Any]]:
    from octop.infra.company_runtime.service import denied
    from octop.infra.utils.asyncio import offload

    skills: list[dict[str, Any]] = []
    total = 0
    summaries = await agent.list_skill_summaries()
    for summary in summaries:
        if not summary.get("enabled", True):
            continue
        slug = str(summary.get("slug") or "")
        if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
            continue
        roots = agent.workspace.skill_catalog_roots(extra=agent.config.skills_dir)
        for root, _ in reversed(roots):
            prefix = f"{root.rstrip('/')}/{slug}"
            content = await agent.workspace.adownload_bytes(prefix + "/SKILL.md")
            if content is None:
                continue
            files = [("SKILL.md", content)]
            listing = await agent.workspace.aglob("**/*", prefix)
            if listing and getattr(listing, "truncated", False):
                raise denied("expert_skill_listing_truncated")
            for entry in (listing.matches or []) if listing else []:
                path = str(entry.get("path", ""))
                if entry.get("is_dir") or not path.startswith(prefix + "/"):
                    continue
                relative = path[len(prefix) + 1 :]
                if relative == "SKILL.md" or any(
                    p.startswith(".") or p in {"node_modules", "__pycache__"}
                    for p in PurePosixPath(relative).parts
                ):
                    continue
                data = await agent.workspace.adownload_bytes(path)
                if data is not None:
                    files.append((relative, data))
            for relative, data in files:
                total += len(data)
                if (
                    total > 512 * 1024 * 1024
                    or len(skills) >= 20000
                    or len(data) > 64 * 1024 * 1024
                ):
                    raise denied("expert_skills_too_large")
                digest = await offload(store_asset, cache, data)
                item = {"path": f"skills/{slug}/{relative}", "sha256": digest, "size": len(data)}
                if relative == "SKILL.md":
                    item.update(
                        content=base64.b64encode(data).decode(),
                        description=summary.get("description", ""),
                    )
                skills.append(item)
            break
    return skills
