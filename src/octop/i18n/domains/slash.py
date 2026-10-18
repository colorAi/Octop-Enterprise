"""``slash.*`` — slash command responses and catalog labels."""

from __future__ import annotations

from octop.i18n.loader import tr as _tr
from octop.infra.utils.locale import Locale

__all__ = ["field_label", "localized_rows", "tr"]


def tr(key: str, locale: Locale, **kwargs: object) -> str:
    return _tr(f"slash.{key}", locale, **kwargs)


def field_label(key: str, locale: Locale) -> str:
    try:
        return _tr(f"slash.fields.{key}", locale)
    except KeyError:
        return key


def localized_rows(rows: list[tuple[str, str]], locale: Locale) -> list[tuple[str, str]]:
    return [(field_label(k, locale), v) for k, v in rows]
