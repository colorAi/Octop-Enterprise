"""Employee read access to administrator-maintained knowledge bases."""

from __future__ import annotations

from typing import Any

from octop.infra.errors import ErrorCode, OctopError


class KnowledgeAccess:
    def __init__(self, services: Any) -> None:
        self.services = services

    def _employee(self, user_id: int) -> None:
        user = self.services.user_repo.get(user_id)
        if user is None:
            raise OctopError(ErrorCode.NOT_FOUND, "user not found")
        if user.role == "admin":
            raise OctopError(
                ErrorCode.FORBIDDEN, "administrators already have full knowledge access"
            )

    def get(self, user_id: int) -> dict[str, Any]:
        self._employee(user_id)
        return {
            "knowledge_base_ids": self.services.knowledge_repo.assigned_base_ids(user_id),
            "items": [
                {
                    "id": base.id,
                    "name": base.name,
                    "description": base.description,
                    "shared": base.shared,
                }
                for base in self.services.knowledge_repo.list_managed()
            ],
        }

    def validate(self, user_id: int, kb_ids: list[str]) -> list[str]:
        self._employee(user_id)
        selected = list(dict.fromkeys(kb_ids))
        available = {base.id for base in self.services.knowledge_repo.list_managed()}
        if not set(selected).issubset(available):
            raise OctopError(
                ErrorCode.KNOWLEDGE_NOT_FOUND,
                "only administrator-maintained knowledge bases can be assigned",
            )
        return selected

    def set(self, user_id: int, kb_ids: list[str], *, actor: str) -> dict[str, Any]:
        selected = self.validate(user_id, kb_ids)
        self.services.knowledge_repo.replace_user_bases(user_id, selected, actor=actor)
        return self.get(user_id)
