"""Format /help output from catalog metadata."""

from __future__ import annotations

from octop.i18n.domains.slash import tr
from octop.infra.gateway.slash.catalog import (
    SlashCommandSpec,
    category_label,
    group_specs_by_category,
)
from octop.infra.gateway.slash.formatting import markdown_grouped_list
from octop.infra.utils.locale import Locale


def format_help(specs: list[SlashCommandSpec], locale: Locale) -> str:
    """Build categorized /help text for the given visible command specs."""
    sections: list[tuple[str, list[str]]] = []
    for cat, items in group_specs_by_category(specs):
        bullets: list[str] = []
        for spec in items:
            usage = spec.usage or spec.command
            desc = spec.description_for(locale)
            bullets.append(f"`{usage}` — {desc}" if desc else f"`{usage}`")
        sections.append((category_label(cat, locale), bullets))
    return markdown_grouped_list(tr("help.title", locale), sections)
