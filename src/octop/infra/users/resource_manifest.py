"""Immutable resource manifests shared by server preparation and device exports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from octop.infra.utils.immutable_files import read_object, store_object

FORMAT = "octop.employee-resources.v1"
MAX_MANIFEST_BYTES = 32 * 1024 * 1024


def store_manifest(root: Path, manifest: dict[str, Any]) -> str:
    payload = json.dumps(
        {"validation": "legacy_content", **manifest, "format": FORMAT},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("resource_manifest_too_large")
    return store_object(root, payload)


def read_manifest(root: Path, digest: str) -> dict[str, Any]:
    value = json.loads(read_object(root, digest, limit=MAX_MANIFEST_BYTES))
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        raise ValueError("resource_manifest_invalid")
    return value
