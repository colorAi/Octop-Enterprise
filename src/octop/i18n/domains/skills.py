"""``skills.*`` — built-in skill display names (keyed by slug)."""

from __future__ import annotations

from typing import Any

from octop.i18n.loader import _load_all, lookup
from octop.infra.utils.locale import Locale, normalize_locale

__all__ = ["all_skill_labels", "skill_display_name"]


def skill_display_name(slug: str | None, locale: str | Locale = "en") -> str:
    if not slug:
        return slug or ""
    text = lookup(f"skills.{slug}", locale)
    return text if text is not None else slug


def all_skill_labels(locale: str | Locale = "en") -> dict[str, str]:
    loc = normalize_locale(str(locale))
    tables = _load_all()
    node: Any = tables.get(loc, {}).get("skills")
    if not isinstance(node, dict):
        node = tables.get("en", {}).get("skills")
    if not isinstance(node, dict):
        return {}
    return {str(k): str(v) for k, v in node.items() if isinstance(v, str)}
