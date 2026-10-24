"""Describe company tools without the harness's local-provider assumption."""

from typing import Any

from langchain.agents.middleware import AgentMiddleware

from octop.infra.users.capabilities import CAPABILITIES

# Harness 1.0.3 emits this whenever local media credentials are absent. Company
# clients deliberately have no provider keys; generation is a server capability.
_LOCAL_MEDIA_UNCONFIGURED = (
    " Image and video generation tools are not configured in this runtime. "
    "If the user asks for generated media, state that configuration is required; "
    "do not claim that media was generated."
)


class CompanyCapabilitiesMiddleware(AgentMiddleware):
    def __init__(self, *, capabilities: dict[str, bool]) -> None:
        self.capabilities = capabilities

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        tools = [item[4] for item in CAPABILITIES.values() if self.capabilities.get(item[0], False)]
        instruction = (
            " Company service tools assigned to this employee: "
            + (", ".join(tools) or "none")
            + ". Provider credentials stay on the company server, not this computer. "
            "Use an assigned tool and report its actual result; do not infer unavailability "
            "from missing local credentials. For an unassigned capability, ask the company "
            "administrator to grant it. Historical tool availability is not current authorization."
        )
        message = request.system_message
        content = message.content
        if isinstance(content, str):
            content = content.replace(_LOCAL_MEDIA_UNCONFIGURED, "") + instruction
        else:
            content = [
                {**block, "text": block["text"].replace(_LOCAL_MEDIA_UNCONFIGURED, "")}
                if isinstance(block, dict) and isinstance(block.get("text"), str)
                else block
                for block in content
            ] + [{"type": "text", "text": instruction}]
        return await handler(
            request.override(system_message=message.model_copy(update={"content": content}))
        )
