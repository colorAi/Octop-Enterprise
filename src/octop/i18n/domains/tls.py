"""TLS preflight message helpers."""

from __future__ import annotations

from octop.i18n import tr


def preflight_message(check_id: str, *, variant: str, locale: str, **fmt: object) -> str:
    return tr(f"tls.preflight.{check_id}.{variant}", locale, **fmt)
