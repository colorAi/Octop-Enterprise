"""Gateway message processing — GlobalProcessor and harness turn helpers."""

from octop.infra.gateway.process.agent_resolve import (
    harness_workspace_for_agent,
    media_backend_for_agent,
)
from octop.infra.gateway.process.harness_request import (
    build_content,
    build_content_from_message,
    build_harness_request,
    content_from_parts,
)


def __getattr__(name: str) -> object:
    if name == "GlobalProcessor":
        from octop.infra.gateway.process.processor import GlobalProcessor

        return GlobalProcessor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "GlobalProcessor",
    "build_content",
    "build_content_from_message",
    "build_harness_request",
    "content_from_parts",
    "harness_workspace_for_agent",
    "media_backend_for_agent",
]
