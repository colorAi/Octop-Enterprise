"""Administrator-assigned chat models; an absent policy grants employees nothing."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from octop.infra.agents.providers.resolved import list_resolved_models
from octop.infra.agents.providers.store import ProviderStore
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_ulid

MODEL_ACCESS_POLICY = "model_access"


class ModelAccess:
    def __init__(self, services: Any) -> None:
        self.services = services

    def _user(self, user_id: int) -> Any:
        row = self.services.user_repo.get(user_id)
        if row is None:
            raise OctopError(ErrorCode.NOT_FOUND, "user not found")
        return row

    def is_admin(self, user_id: int) -> bool:
        row = self._user(user_id)
        return bool(row.role == "admin" and not row.disabled)

    def _stored(self, user_id: int) -> tuple[dict[str, Any], str | None]:
        row = self.services.user_policy_repo.get(user_id, MODEL_ACCESS_POLICY)
        raw = row.value if row is not None and row.enabled else None
        return (json.loads(raw) if raw else {"model_refs": [], "revision": "0"}), raw

    def catalog(self) -> list[dict[str, Any]]:
        providers = ProviderStore(self.services.provider_repo)
        return [
            {**model, "ref": f"{model['provider_name']}/{model['model']}"}
            for model in list_resolved_models(list(providers.iter_usable_rows()))
        ]

    def filter_refs(self, user_id: int, refs: Iterable[str]) -> list[str]:
        row = self._user(user_id)
        if row.disabled:
            return []
        candidates = list(refs)
        if row.role == "admin":
            return candidates
        state, _ = self._stored(user_id)
        granted = set(state["model_refs"])
        return [ref for ref in candidates if ref in granted]

    def require(self, user_id: int, ref: str) -> None:
        if not ref or not self.filter_refs(user_id, [ref]):
            raise OctopError(
                ErrorCode.MODEL_NOT_ASSIGNED,
                "the requested model is not assigned to this employee",
                details={"model": ref},
            )

    def resolve(
        self, user_id: int, preferred: str | None = None, *, needs_multimodal: bool = False
    ) -> str:
        allowed = self.filter_refs(user_id, [model["ref"] for model in self.catalog()])
        if needs_multimodal:
            providers = ProviderStore(self.services.provider_repo)
            allowed = [ref for ref in allowed if providers.is_model_ref_multimodal(ref)]
        if preferred in allowed:
            return str(preferred)
        if not allowed:
            self.require(user_id, "")
        return allowed[0]

    def _employee(self, user_id: int) -> Any:
        row = self._user(user_id)
        if row.role == "admin":
            raise OctopError(ErrorCode.FORBIDDEN, "administrators already have full model access")
        return row

    def get(self, user_id: int) -> dict[str, Any]:
        self._employee(user_id)
        state, _ = self._stored(user_id)
        return {**state, "items": self.catalog()}

    def validate(
        self, user_id: int, model_refs: list[str], expected_revision: str
    ) -> tuple[list[str], str | None]:
        self._employee(user_id)
        selected = sorted(set(model_refs))
        available = {model["ref"] for model in self.catalog()}
        if not set(selected).issubset(available):
            raise OctopError(ErrorCode.MODEL_NOT_ASSIGNED, "select enabled, available models only")
        state, raw = self._stored(user_id)
        if state["revision"] != expected_revision:
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "model assignments have changed; refresh before saving",
                status=409,
                details={"reason": "policy_revision_conflict"},
            )
        return selected, raw

    def set(
        self,
        user_id: int,
        model_refs: list[str],
        expected_revision: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        selected, raw = self.validate(user_id, model_refs, expected_revision)
        value = json.dumps({"model_refs": selected, "revision": new_ulid()}, sort_keys=True)
        if not self.services.user_policy_repo.compare_and_set(
            user_id,
            MODEL_ACCESS_POLICY,
            expected=raw,
            value=value,
            actor=actor,
            action="employee.models",
        ):
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "model assignments have changed; refresh before saving",
                status=409,
                details={"reason": "policy_revision_conflict"},
            )
        return self.get(user_id)
