"""Credential-free projection of an expert's own tool switches."""

from typing import Any

from octop.infra.agents.settings.tool_catalog import builtin_tool_available, tools_disabled_set

SEARCH_TOOLS = (
    "tavily_search",
    "brave_search",
    "google_search",
    "kimi_search",
    "searchfree_search",
)


def source_tool_policy(config: dict[str, Any]) -> dict[str, Any]:
    disabled = tools_disabled_set(config)
    searches = [
        name
        for name in SEARCH_TOOLS
        if name not in disabled and builtin_tool_available(name, agent_cfg=config)
    ]
    requested = config.get("web_search_tools")
    if isinstance(requested, list):
        searches = [name for name in searches if name.removesuffix("_search") in requested]
    tools = ["company_web_search"] if searches else []
    for kind in ("image", "video"):
        name = "generate_" + kind
        if name not in disabled and builtin_tool_available(name, agent_cfg=config):
            tools.append("company_" + name)
    return {"tools": tools, "search_tools": searches}
