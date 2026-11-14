"""Prepare group projections from an already captured immutable source manifest."""

from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from octop.infra.db.services import SharedServices
from octop.infra.group_experts.snapshots import ROLE_FILES, build_snapshot, snapshot_digest
from octop.infra.utils.immutable_files import read_object


def _portrait(root: Path, avatar: dict[str, Any]) -> dict[str, str]:
    """Keep a small, self-contained portrait for group history."""
    try:
        raw = read_object(root, avatar["sha256"], limit=128 * 1024)
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > 8_000_000:
                return {}
            if max(image.size) > 64 or len(raw) > 1024:
                thumbnail = image.convert("RGBA")
                thumbnail.thumbnail((64, 64), Image.Resampling.LANCZOS)
                with BytesIO() as output:
                    thumbnail.save(output, "WEBP", quality=70)
                    raw = output.getvalue()
                media_type = "image/webp"
            else:
                media_type = avatar["media_type"]
    except (OSError, ValueError, Image.DecompressionBombError):
        return {}
    return {"url": f"data:{media_type};base64," + base64.b64encode(raw).decode()}


async def prepare_publication(
    services: SharedServices, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    group = services.group_expert_repo
    previous = {
        (row["source_agent_id"], row["source_row_id"]): row
        for row in await asyncio.to_thread(group.sources)
    }
    providers = {row.name: row for row in await asyncio.to_thread(services.provider_repo.list_all)}
    global_model = await asyncio.to_thread(services.settings_repo.get_active_model)
    objects = []
    for source, definition in manifest["definitions"].items():
        fields = definition["fields"]
        projection = {
            "fields": {key: fields.get(key) for key in ("default_model", "system_prompt")},
            "files": {key: definition.get("files", {}).get(key) for key in ROLE_FILES},
            "runtime_settings": definition.get("runtime_settings", {}),
            "group_tool_policy": definition.get("group_tool_policy", {}),
            "group_tool_revision": definition.get("group_tool_revision"),
        }
        definition_digest = snapshot_digest(projection)
        old = previous.get((source, definition["source_row_id"]))
        execution_definition = definition
        # A display edit does not re-resolve AUTO against a changed global model.
        if old and old["definition_digest"] == definition_digest:
            old_snapshot = json.loads(old["snapshot_json"])
            if old_snapshot.get("model_ref"):
                execution_definition = {
                    **definition,
                    "fields": {**fields, "default_model": old_snapshot["model_ref"]},
                }
        preferred = execution_definition["fields"].get("default_model") or "AUTO"
        name = global_model[0] if preferred.lower() == "auto" else preferred.partition("/")[0]
        provider = providers.get(name)
        version = await asyncio.to_thread(group.provider_version, provider.id) if provider else None
        value: dict[str, Any] = {
            "source_agent_id": source,
            "source_row_id": definition["source_row_id"],
            "definition_digest": definition_digest,
            "display": {"name": fields.get("name") or source, "avatar": {}},
        }
        # Store the approved small avatar with the binding so source deletion
        # cannot turn history into a broken workspace URL.
        avatar = definition.get("avatar")
        if avatar and str(avatar.get("media_type", "")) in {
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/gif",
        }:
            value["display"]["avatar"] = await asyncio.to_thread(
                _portrait, services.paths.root / "company-runtime-skills", avatar
            )
        try:
            value["snapshot"] = build_snapshot(
                source_agent_id=source,
                definition=execution_definition,
                provider=provider,
                provider_version=version or {},
                global_model=global_model,
            )
        except ValueError as exc:
            reason = str(exc)
            value["invalid_reason"] = (
                reason
                if reason.startswith("group_expert_")
                else "group_expert_configuration_changed"
            )
        objects.append(value)
    return objects
