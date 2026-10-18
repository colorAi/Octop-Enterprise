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
) -> str:
    data = {
        "role": role,
        "permissions": sorted(set(permissions)),
        "policies": sorted((p.name, p.enabled, p.value) for p in policies),
        "release": release.get("id"),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
