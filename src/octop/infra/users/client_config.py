"""Durable configuration notices; no software installation or service restart."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Sequence
from typing import Any

from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.user_policies import UserPolicyRow

RELEASE_KEY = "enterprise.client_config_release"


def read_config_state(
    services: Any, user_id: int, resources: Any = None
) -> tuple[dict[str, Any], list[UserPolicyRow], dict[str, Any] | None]:
    """Read revision inputs off the event loop; no provider credentials are included."""
    release = read_release(services.settings_repo)
    policies = services.user_policy_repo.list_for_user(user_id)
    assignments = getattr(services, "expert_assignment_repo", None)
    expert_access = None
    if assignments is not None:
        state = assignments.state(user_id)
        expert_access = {
            "selection": state,
            "source": assignments.source(),
            "available": [
                eid
                for eid in (state or {}).get("expert_ids", [])
                if assignments.granted(user_id, eid)
            ],
        }
        if resources is not None:
            expert_access["resources"] = resources.state(user_id)
            expert_access["default_skill_ids"] = resources.repo.defaults()
        knowledge = getattr(services, "knowledge_repo", None)
        if knowledge is not None:
            expert_access["knowledge_base_ids"] = knowledge.assigned_base_ids(user_id)
    return release, policies, expert_access


def read_release(settings: SettingsRepo) -> dict[str, Any]:
    raw = settings.get(RELEASE_KEY)
    if not raw:
        return {}
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def publish_release(settings: SettingsRepo, message: str) -> dict[str, Any]:
    release = {"id": uuid.uuid4().hex, "published_at": int(time.time()), "message": message}
    settings.set(RELEASE_KEY, json.dumps(release, ensure_ascii=False))
    return release


def config_revision(
    role: str,
    permissions: Sequence[str],
    policies: Sequence[UserPolicyRow],
    release: dict[str, Any],
    expert_access: Any = None,
) -> str:
    data = {
        "role": role,
        "permissions": sorted(set(permissions)),
        "policies": sorted((p.name, p.enabled, p.value) for p in policies),
        "release": release.get("id"),
        "expert_access": expert_access,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
