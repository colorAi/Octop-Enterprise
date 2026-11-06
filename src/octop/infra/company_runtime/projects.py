"""Project catalog access through an authenticated employee desktop session."""

from __future__ import annotations

import hashlib
from typing import Any

from octop.infra.company_runtime.service import CompanyRuntimeService, denied
from octop.infra.db.repos.company_runtime import RuntimeConflict
from octop.infra.utils.asyncio import offload

PROJECT_FIELDS = ("project_id", "name", "created_at", "updated_at")


class CompanyProjectService:
    def __init__(self, runtime: CompanyRuntimeService, installation_token: str) -> None:
        self.runtime = runtime
        self.repo = runtime.services.company_project_repo
        self.owner = runtime.owner
        self.installation = hashlib.sha256(installation_token.encode()).hexdigest()

    async def save(self, project_id: str, name: str, *, rename: bool = False) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise denied("project_name_required", 422)
        try:
            row = await offload(
                self.repo.rename if rename else self.repo.register,
                project_id,
                self.owner,
                self.installation,
                name,
            )
        except RuntimeConflict as exc:
            raise denied(str(exc), 403) from exc
        return {key: row[key] for key in PROJECT_FIELDS}

    async def list(self, limit: int, offset: int) -> dict[str, Any]:
        rows = await offload(
            self.repo.list_projects, self.owner, self.installation, limit + 1, offset
        )
        return {
            "items": [{key: r[key] for key in PROJECT_FIELDS} for r in rows[:limit]],
            "has_more": len(rows) > limit,
        }

    async def create_thread(
        self, project_id: str, thread_id: str, agent_id: str, title: str
    ) -> dict[str, str]:
        await self.runtime.authorize(agent_id)
        try:
            return await offload(
                self.repo.create_thread,
                project_id,
                self.owner,
                self.installation,
                thread_id=thread_id,
                agent_id=agent_id,
                device_id=self.runtime.identity["device_id"],
                title=title,
            )
        except RuntimeConflict as exc:
            raise denied(str(exc), 409 if str(exc) == "thread_project_conflict" else 403) from exc

    async def fork(
        self,
        project_id: str,
        source_id: str,
        thread_id: str,
        message_id: str | None,
        turns: int | None,
    ) -> dict[str, Any]:
        from langchain_core.messages import messages_from_dict

        from octop.infra.agents.threads.fork import find_assistant_fork_index
        from octop.infra.company_runtime.history import edit_checkpoint

        try:
            source = await offload(
                self.repo.fork_context, project_id, self.owner, self.installation, source_id
            )
            await self.runtime.authorize(source["agent_id"], source_id)
            index = find_assistant_fork_index(
                messages_from_dict(source["messages"]),
                message_id=message_id,
                assistant_turns_from_end=turns,
            )
            prefix = source["messages"][: index + 1]
            checkpoint = await offload(edit_checkpoint, thread_id, prefix, None)
            created = await offload(
                self.repo.create_thread,
                project_id,
                self.owner,
                self.installation,
                thread_id=thread_id,
                agent_id=source["agent_id"],
                device_id=self.runtime.identity["device_id"],
                title=(source["title"] or "新对话") + " · 分支",
                fork={
                    "source": source_id,
                    "head": source["head"],
                    "messages": prefix,
                    "checkpoint": checkpoint,
                },
            )
        except RuntimeConflict as exc:
            raise denied(str(exc), 409) from exc
        return {**created, "copied_messages": len(prefix)}

    async def adopt(self, project_id: str, thread_id: str) -> dict[str, str]:
        try:
            await offload(self.repo.adopt, project_id, self.owner, self.installation, thread_id)
        except RuntimeConflict as exc:
            raise denied(str(exc), 409) from exc
        return {"thread_id": thread_id, "project_id": project_id}

    async def continue_thread(
        self, project_id: str, source_id: str, thread_id: str, agent_id: str
    ) -> dict[str, Any]:
        from octop.infra.company_runtime.history import edit_checkpoint
        from octop.infra.history.continuation import owned_transcript

        await self.runtime.authorize(agent_id)
        # Validate the destination installation before reading an old transcript.
        await self.threads(project_id, 1, 0)
        source, wires = await owned_transcript(self.runtime.server, self.owner, source_id)
        checkpoint = await offload(edit_checkpoint, thread_id, wires, None)
        try:
            created = await offload(
                self.repo.create_thread,
                project_id,
                self.owner,
                self.installation,
                thread_id=thread_id,
                agent_id=agent_id,
                device_id=self.runtime.identity["device_id"],
                title=source.title or "",
                continuation={
                    "source": source_id,
                    "source_agent_id": source.agent_id,
                    "messages": wires,
                    "checkpoint": checkpoint,
                },
            )
        except RuntimeConflict as exc:
            raise denied(str(exc), 409) from exc
        return {**created, "copied_messages": len(wires)}

    async def threads(self, project_id: str, limit: int, offset: int) -> dict[str, Any]:
        try:
            rows = await offload(
                self.repo.threads, project_id, self.owner, self.installation, limit + 1, offset
            )
        except RuntimeConflict as exc:
            raise denied(str(exc), 403) from exc
        return {"items": rows[:limit], "has_more": len(rows) > limit}
