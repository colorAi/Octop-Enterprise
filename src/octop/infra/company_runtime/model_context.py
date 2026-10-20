"""Carry non-secret model capacities into the local compaction engine."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.messages.utils import count_tokens_approximately

from octop.infra.agents.settings.runtime_limits import agent_max_input_tokens, agent_model_settings


def model_token_limits(profile: Any) -> dict[str, int]:
    if not isinstance(profile, dict):
        return {}
    return {
        key: value
        for key in ("max_input_tokens", "context_window", "max_output_tokens")
        if type(value := profile.get(key)) is int and value > 0
    }


def company_model_profile(limits: dict[str, int], settings: dict[str, Any]) -> dict[str, int]:
    """Use the smaller configured/model prompt cap and reserve configured output."""
    profile = model_token_limits(limits)
    caps = [profile[key] for key in ("max_input_tokens", "context_window") if key in profile]
    configured = agent_max_input_tokens(settings)
    if configured:
        caps.append(configured)
    output = agent_model_settings(settings).get("max_tokens")
    context = profile.get("context_window")
    if context and type(output) is int and output > 0:
        caps.append(max(1, context - output))
    # Match the host's existing unknown-capacity fallback, while still enabling
    # fraction-based compaction instead of deepagents' 170k-token fallback.
    profile["max_input_tokens"] = min(caps) if caps else 128_000
    return profile


def calibrated_prompt_tokens(messages: Any, tools: Any) -> int | None:
    """Calibrate estimates against the last measured request, including image costs.

    Retain the old request's estimate as the denominator: a compacted prompt must
    shrink its budget instead of inheriting a stale high provider token count.
    """
    estimated = count_tokens_approximately(messages, tools=tools)
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        stamp = message.additional_kwargs.get("context_usage")
        if not isinstance(stamp, dict) or stamp.get("source") != "model_request":
            continue
        segments = stamp.get("segments")
        if not isinstance(segments, dict):
            continue
        previous_estimate = sum(
            value for value in segments.values() if type(value) is int and value > 0
        )
        measured = message.usage_metadata["input_tokens"] if message.usage_metadata else 0
        if previous_estimate > 0 and type(measured) is int and measured > 0:
            return max(
                estimated, (estimated * measured + previous_estimate - 1) // previous_estimate
            )
    return None
