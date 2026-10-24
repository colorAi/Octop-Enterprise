"""Check current employee assignments before every controlled model/tool call."""

from __future__ import annotations

import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from octop.infra.agents.middleware.token_quota import configurable_user_id
from octop.infra.users.capabilities import TOOL_CAPABILITIES
from octop.infra.users.expert_access import ExpertAccess, denied
from octop.infra.utils.asyncio import offload


class ExpertAccessMiddleware(AgentMiddleware[Any, Any]):
    def __init__(self, repos: Any, agent_id: str, resources: Any = None) -> None:
        super().__init__()
        self.access = ExpertAccess(repos)
        self.agent_id = agent_id
        self.resources = resources

    async def capabilities(self, resources: Any) -> set[str]:
        row = await offload(self.access.repos.agent_repo.get, self.agent_id)
        caller = configurable_user_id()
        actor = caller if caller is not None else row.user_id if row else None
        return await resources.granted_capabilities(actor) if actor is not None else set()

    async def enforce(self) -> None:
        from octop.infra.execution.service import current_execution

        execution = current_execution.get()
        if execution is not None:
            await execution.check()
        # Read the invocation context here; executor threads do not inherit it.
        caller = configurable_user_id()
        resources = self.resources() if self.resources else None
        if resources is not None:
            owner = await offload(self.access.repos.agent_repo.get, self.agent_id)
            actor = caller if caller is not None else owner.user_id if owner else None
            if actor is not None:
                await resources.authorize(actor, self.agent_id, prepared=True)
                return
        if caller is None:
            await offload(self.access.assert_owner_execution, self.agent_id)
        else:
            await offload(self.access.assert_use, caller, self.agent_id)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        await self.enforce()
        resources = self.resources() if self.resources else None
        if resources is not None:
            granted = await self.capabilities(resources)
            request = request.override(
                tools=[
                    tool
                    for tool in request.tools
                    if TOOL_CAPABILITIES.get(getattr(tool, "name", "")) in {None, *granted}
                ]
            )
        if resources is not None and self.access.repo.instance(self.agent_id) is not None:
            from octop_harness.skills.catalog import skill_slug_from_path

            agent = resources.registry.get_agent(self.agent_id)
            allowed: set[str] = getattr(agent, "_octop_allowed_skills", set())
            state = getattr(request, "state", {})
            metadata = state.get("skills_metadata")
            if isinstance(metadata, list):
                request = request.override(
                    state={
                        **state,
                        "skills_metadata": [
                            item
                            for item in metadata
                            if (item.get("slug") or skill_slug_from_path(item.get("path")))
                            in allowed
                        ],
                    }
                )
        return await handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        await self.enforce()
        resources = self.resources() if self.resources else None
        call = getattr(request, "tool_call", {})
        capability = TOOL_CAPABILITIES.get(str(call.get("name", "")).rsplit("/", 1)[-1])
        if (
            resources is not None
            and capability
            and capability not in await self.capabilities(resources)
        ):
            raise denied("company_capability_not_assigned", 403)
        if resources is not None and self.access.repo.instance(self.agent_id) is not None:
            agent = resources.registry.get_agent(self.agent_id)
            allowed: set[str] = getattr(agent, "_octop_allowed_skills", set())
            # Keep the effective denylist current even when a new folder appears
            # after preparation. The model filter above is a positive allowlist.
            summaries = await agent.list_skill_summaries()
            agent.set_skills_disabled(
                {str(s["slug"]) for s in summaries if s["slug"] not in allowed}
            )
            call = getattr(request, "tool_call", {})
            base = str(call.get("name", "")).rsplit("/", 1)[-1]
            if base in {"write_file", "edit_file", "delete"}:
                args = call.get("args", {})
                for key in ("path", "file_path"):
                    if isinstance(args.get(key), str):
                        mapping = self.access.repo.instance(self.agent_id)
                        assert mapping is not None
                        await resources.workspace_access(
                            mapping["user_id"],
                            self.agent_id,
                            agent.workspace,
                            args[key],
                            write=True,
                        )
            if str(call.get("name", "")).rsplit("/", 1)[-1] in {
                "ls",
                "read_file",
                "glob",
                "grep",
                "write_file",
                "edit_file",
                "delete",
                "execute",
                "bash",
                "shell",
            }:
                raw = json_strings(call.get("args", {}))
                for value in raw:
                    slugs = re.findall(
                        r"(?:^|[/\\])(?:skills|_builtin_skills)[/\\]([^/\\\s\"']+)", value
                    )
                    if any(slug not in allowed for slug in slugs):
                        raise denied("resource_not_assigned")
        if resources is not None and resources.publication is not None:
            return await resources.publication.tool(self.agent_id, request, handler)
        return await handler(request)


def json_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in json_strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in json_strings(item)]
    return []
