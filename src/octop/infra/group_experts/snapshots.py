"""Project an approved source definition into a credential-free execution snapshot.

This never opens an agent runtime or workspace. Callers supply a definition from
the committed source publication and persist the result before admitting calls.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from octop_harness.config import ModelConfig

from octop.infra.agents.providers.model_flags import is_chat_eligible_model
from octop.infra.agents.settings.runtime_limits import agent_model_settings
from octop.infra.db.repos.execution import ExecutionConflict, wire

ROLE_FILES = ("AGENTS.md", "SOUL.md", "IDENTITY.md")
DEFAULT_OUTPUT_TOKENS = 4096


def _positive(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def build_snapshot(
    *,
    source_agent_id: str,
    definition: dict[str, Any],
    provider: Any,
    provider_version: dict[str, Any],
    global_model: tuple[str, str],
) -> dict[str, Any]:
    """Resolve AUTO once against the explicit enterprise default, never a caller.

    Provider metadata is reduced to identity, revision, API selection and token
    limits. Secrets, endpoints/headers and arbitrary extras stay in the provider repo.
    """
    fields = definition.get("fields", {})
    preferred = str(fields.get("default_model") or "").strip()
    if not preferred or preferred.lower() == "auto":
        if not all(global_model):
            raise ExecutionConflict("group_expert_model_required")
        preferred = "/".join(global_model)
    provider_name, separator, model_id = preferred.partition("/")
    if (
        not separator
        or not model_id
        or provider is None
        or provider.name != provider_name
        or not provider.enabled
        or not provider.base_url
        or not provider.api_key
        or provider_version.get("deleted")
        or provider_version.get("provider_id") != provider.id
        or not provider_version.get("generation")
        or not _positive(provider_version.get("revision"))
    ):
        raise ExecutionConflict("group_expert_model_unavailable")
    models = [model for model in provider.get_models() if model.get("id") == model_id]
    if len(models) != 1 or not is_chat_eligible_model(
        models[0], provider_name=provider.name, provider_api_key=provider.api_key
    ):
        raise ExecutionConflict("group_expert_model_unavailable")
    model = models[0]
    model_config = {
        "api_mode": model.get("api_mode", "chat_completions"),
        "api_fallback": model.get("api_fallback", "none"),
    }
    ModelConfig(id=model_id, **model_config)
    context = _positive(model.get("context_window"))
    max_input = _positive(model.get("max_input_tokens")) or context
    max_output = _positive(model.get("max_output_tokens")) or _positive(model.get("max_tokens"))
    # Without published model limits a conservative, non-truncating admission
    # decision cannot be made. Ask the administrator to supply the missing cap.
    if max_input is None:
        raise ExecutionConflict("group_expert_model_budget_required")
    settings = agent_model_settings(definition.get("runtime_settings", {}))
    output = min(
        settings.get("max_tokens", DEFAULT_OUTPUT_TOKENS), max_output or DEFAULT_OUTPUT_TOKENS
    )
    if context is not None:
        output = min(output, context - 1)
        # Each model round will cap output against its actual input bound.
        max_input = min(max_input, context - 1)
    override = _positive(definition.get("runtime_settings", {}).get("max_input_length"))
    if override is not None:
        max_input = min(max_input, override)
    if output < 1 or max_input < 1:
        raise ExecutionConflict("group_expert_model_budget_required")
    settings["max_tokens"] = output
    prompts = []
    if prompt := fields.get("system_prompt"):
        if not isinstance(prompt, str):
            raise ExecutionConflict("group_expert_definition_invalid")
        prompts.append(prompt)
    files = definition.get("files", {})
    for name in ROLE_FILES:
        if content := files.get(name):
            if not isinstance(content, str):
                raise ExecutionConflict("group_expert_definition_invalid")
            prompts.append(f"[{name}]\n{content}")
    if not _positive(definition.get("source_row_id")) or not source_agent_id:
        raise ExecutionConflict("group_expert_definition_invalid")
    snapshot = {
        "protocol": 2,
        "source_agent_id": source_agent_id,
        "source_row_id": definition["source_row_id"],
        "system_prompt": "\n\n".join(prompts),
        "model_ref": preferred,
        "model_config": model_config,
        "provider_id": provider.id,
        "provider_generation": provider_version["generation"],
        "provider_revision": provider_version["revision"],
        "model_settings": settings,
        "max_input_tokens": max_input,
        "context_window": context,
        "tools": definition.get("group_tool_policy", {}).get("tools", []),
        "search_tools": definition.get("group_tool_policy", {}).get("search_tools", []),
        "tool_revision": definition.get("group_tool_revision"),
    }
    # Keep caller-owned structures out of the frozen value and enforce the same
    # size/NaN rules as durable execution payloads.
    return json.loads(wire(snapshot))  # type: ignore[no-any-return]


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(wire(snapshot).encode()).hexdigest()


def format_answer(
    text: str, *, max_chars: int, suffix: str, incomplete: bool = False
) -> dict[str, Any]:
    """Freeze the sole reply before delivery; Python len matches Go rune counts."""
    value = text.strip()
    if not value:
        raise ExecutionConflict("group_expert_empty_output")
    truncated = len(value) > max_chars
    if truncated or incomplete:
        if not suffix or len(suffix) >= max_chars:
            raise ExecutionConflict("group_expert_output_limit_invalid")
        value = value[: max_chars - len(suffix)].rstrip() + suffix
    return {"text": value, "truncated": truncated, "incomplete": incomplete}
