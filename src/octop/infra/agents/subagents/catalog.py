"""Subagent catalog — bundled agency-agents definitions.

Templates live on disk under ``library/<locale>/<division>/*.md`` and are
discovered at server start by :class:`SubagentCatalog`. Slugs are derived
from the file stem (not the ``name`` frontmatter field), which guarantees
stable pairing between translated and source files. Agents without a
translation for the requested locale are omitted from :meth:`SubagentCatalog.list_summaries`
results — there is no fallback to another locale.
"""
# ruff: noqa: UP006,UP007  (we keep modern X | None annotations everywhere)

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from octop.infra.utils.frontmatter import is_agent_file, parse_frontmatter
from octop.infra.utils.locale import DEFAULT_LOCALE, SUPPORTED_LOCALES, Locale, normalize_locale

logger = logging.getLogger(__name__)

FALLBACK_LOCALE: Locale = "en"
# Sentinel stored in the YAML ``name`` field of freshly-generated zh
# placeholder files. The catalog treats entries with this name as
# "not yet translated" and excludes them from the in-memory catalog so
# they don't collide on the same ``TODO_TRANSLATE`` slug. Translators
# replace the name with the real Chinese agent name once they finish.
TRANSLATION_PLACEHOLDER_NAME: str = "TODO_TRANSLATE"


@dataclass(frozen=True)
class DivisionMeta:
    id: str
    label: str
    icon: str | None
    color: str | None
    available_locales: tuple[Locale, ...] = ()


@dataclass(frozen=True)
class SubagentSummary:
    slug: str
    division: str
    name: str
    description: str
    emoji: str | None = None
    color: str | None = None
    source_path: str = ""
    available_locales: tuple[Locale, ...] = ()


@dataclass(frozen=True)
class SubagentDefinition:
    summary: SubagentSummary
    # Per-locale text. ``content`` is the authoritative-locale body (kept for
    # backward compatibility with callers that only care about a single locale);
    # the dictionary holds the markdown body for every locale that ships a file
    # on disk. Missing locales are not represented here — :meth:`content_for`
    # returns an empty string when the requested locale is not available.
    content: str = ""
    contents: dict[Locale, str] = field(default_factory=dict)
    # Per-locale frontmatter-derived text. Missing locales return empty string
    # at lookup time — there is no fallback to another locale.
    names: dict[Locale, str] = field(default_factory=dict)
    descriptions: dict[Locale, str] = field(default_factory=dict)

    def name_for(self, locale: Locale | str) -> str:
        """Return the name for *locale*; return ``""`` when not available."""
        return self.names.get(_norm(locale), "")

    def description_for(self, locale: Locale | str) -> str:
        """Return the description for *locale*; return ``""`` when not available."""
        return self.descriptions.get(_norm(locale), "")

    def content_for(self, locale: Locale | str) -> str:
        """Return the content for *locale*; return ``""`` when not available."""
        return self.contents.get(_norm(locale), "")


def _norm(locale: Locale | str | None) -> Locale:
    return normalize_locale(locale) if locale is not None else FALLBACK_LOCALE


def slugify(name: str) -> str:
    """``Software Architect`` -> ``software-architect``."""
    text = name.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _read_divisions(path: Path) -> dict[str, DivisionMeta]:
    if not path.exists():
        return {}
    try:
        data = cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("subagent divisions.json unreadable: %s", exc)
        return {}
    raw = data.get("divisions")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, DivisionMeta] = {}
    for div_id, node in raw.items():
        if not isinstance(node, dict):
            continue
        out[str(div_id)] = DivisionMeta(
            id=str(div_id),
            label=str(node.get("label") or div_id),
            icon=node.get("icon") if node.get("icon") else None,
            color=node.get("color") if node.get("color") else None,
        )
    return out


def _merge_divisions(per_locale: dict[Locale, dict[str, DivisionMeta]]) -> dict[str, DivisionMeta]:
    """Combine per-locale division metadata; first locale wins for icon/color."""
    merged: dict[str, DivisionMeta] = {}
    ordered = sorted(per_locale.items(), key=lambda kv: kv[0])
    # We iterate locales in a deterministic order, and per-division we let
    # the first available locale set icon/color. Labels are localizable
    # but the dashboard reads ``label`` as a single string; we keep the
    # default-locale label on the merged entry and expose other locales
    # via the per-locale divisions map consumed by the API.
    available: dict[str, set[Locale]] = {}
    labels: dict[str, dict[Locale, str]] = {}
    icons: dict[str, str | None] = {}
    colors: dict[str, str | None] = {}
    for locale, divs in ordered:
        for div_id, meta in divs.items():
            available.setdefault(div_id, set()).add(locale)
            labels.setdefault(div_id, {})[locale] = meta.label
            if div_id not in icons:
                icons[div_id] = meta.icon
            if div_id not in colors:
                colors[div_id] = meta.color

    for div_id in sorted({d for divs in per_locale.values() for d in divs}):
        # Default-label picker: default locale first, then English, then
        # whatever is available, in the iteration order.
        candidates = labels.get(div_id, {})
        chosen_label = (
            candidates.get(DEFAULT_LOCALE)
            or candidates.get(FALLBACK_LOCALE)
            or next(iter(candidates.values()), div_id)
        )
        merged[div_id] = DivisionMeta(
            id=div_id,
            label=chosen_label,
            icon=icons.get(div_id),
            color=colors.get(div_id),
            available_locales=tuple(sorted(available.get(div_id, set()))),
        )
    return merged


class SubagentCatalog:
    """Loads bundled subagent definitions from a directory tree."""

    def __init__(self, package_root: Path) -> None:
        self._root = package_root
        self._library_root = package_root / "library"
        self._divisions: dict[str, DivisionMeta] = {}
        self._subagents: dict[str, SubagentDefinition] = {}
        self._divisions_by_locale: dict[Locale, dict[str, DivisionMeta]] = {}

    @property
    def root(self) -> Path:
        return self._root

    def refresh(self) -> None:
        """Re-scan the library directory; quietly skip malformed entries.

        Reads ``library/<locale>/divisions.json`` for every locale that has
        one, then merges the per-locale subagent definitions by slug. The
        authoritative entry is the one from the default locale (or the
        first locale in alphabetical order when the default locale is
        missing); other locales are stored on
        :attr:`SubagentDefinition.contents` and exposed via
        ``name_for``/``description_for``/``content_for``.
        """
        self._divisions = {}
        self._subagents = {}
        self._divisions_by_locale = {}

        if not self._library_root.exists():
            return

        # Per-locale discovery ------------------------------------------------
        per_locale_subagents: dict[Locale, dict[str, SubagentDefinition]] = {}
        per_locale_divisions: dict[Locale, dict[str, DivisionMeta]] = {}
        locales_present: list[Locale] = []

        for locale in SUPPORTED_LOCALES:
            locale_root = self._library_root / locale
            if not locale_root.is_dir():
                continue
            locales_present.append(locale)
            per_locale_divisions[locale] = _read_divisions(locale_root / "divisions.json")
            per_locale_subagents[locale] = self._scan_locale(
                locale, locale_root, per_locale_divisions[locale]
            )

        if not locales_present:
            # Legacy flat layout fallback: scan <library_root>/<division>/*.md
            # directly. This keeps a single-process developer workflow
            # possible during the migration window.
            legacy = self._scan_legacy_flat_layout()
            if legacy:
                self._subagents = legacy
                logger.info(
                    "subagent catalog loaded (legacy flat layout): %d agents",
                    len(legacy),
                )
            return

        self._divisions_by_locale = per_locale_divisions
        self._divisions = _merge_divisions(per_locale_divisions)

        # Merge subagents ----------------------------------------------------
        # Authoritative locale order: default first, then alphabetical.
        priority = [
            DEFAULT_LOCALE,
            *(loc for loc in locales_present if loc != DEFAULT_LOCALE),
        ]
        # Re-order to honor priority.
        ordered_locales = [loc for loc in priority if loc in locales_present]

        merged: dict[str, SubagentDefinition] = {}
        # First pass: seed the catalog with the authoritative locale so
        # ``name`` / ``description`` / ``content`` carry a stable value.
        for locale in ordered_locales:
            if locale == DEFAULT_LOCALE:
                merged.update(per_locale_subagents.get(locale, {}))
                break
        if not merged and ordered_locales:
            # Default locale missing — fall back to the first available
            # locale for the headline fields, then merge the rest as
            # locale-specific overrides.
            first = ordered_locales[0]
            merged.update(per_locale_subagents[first])

        # Second pass: for every locale, attach the per-locale body /
        # frontmatter text to each known slug. New slugs (only present in
        # a non-default locale) are added too.
        # Track which locales each slug appears in while iterating.
        locales_per_slug: dict[str, set[Locale]] = {}
        for locale in ordered_locales:
            for slug, defn in per_locale_subagents.get(locale, {}).items():
                locales_per_slug.setdefault(slug, set()).add(locale)
                if slug in merged:
                    target = merged[slug]
                    merged[slug] = self._extend_with_locale(target, defn, locale)
                else:
                    merged[slug] = defn
                    if locale == FALLBACK_LOCALE:
                        logger.info(
                            "subagent %r only present in locale %r; using it as authoritative",
                            slug,
                            locale,
                        )

        # Stamp available_locales onto each summary.
        rebuilt: dict[str, SubagentDefinition] = {}
        for slug, defn in merged.items():
            available = tuple(sorted(locales_per_slug.get(slug, ())))
            new_summary = SubagentSummary(
                slug=defn.summary.slug,
                division=defn.summary.division,
                name=defn.summary.name,
                description=defn.summary.description,
                emoji=defn.summary.emoji,
                color=defn.summary.color,
                source_path=defn.summary.source_path,
                available_locales=available,
            )
            rebuilt[slug] = SubagentDefinition(
                summary=new_summary,
                content=defn.content,
                contents=dict(defn.contents),
                names=dict(defn.names),
                descriptions=dict(defn.descriptions),
            )
        self._subagents = rebuilt

        logger.info(
            "subagent catalog loaded: %d agents across %d divisions (locales: %s)",
            len(rebuilt),
            len(self._divisions),
            ",".join(locales_present),
        )

    def _scan_legacy_flat_layout(self) -> dict[str, SubagentDefinition]:
        """Scan the pre-i18n layout (library/<division>/*.md) once."""
        self._divisions = _read_divisions(self._root / "divisions.json")
        out: dict[str, SubagentDefinition] = {}
        for div_id in sorted(self._divisions):
            div_dir = self._library_root / div_id
            if not div_dir.is_dir():
                continue
            for fpath in sorted(div_dir.glob("*.md")):
                defn = self._parse_agent_file(div_id, fpath, FALLBACK_LOCALE)
                if defn is None:
                    continue
                slug = defn.summary.slug
                if slug in out:
                    slug = self._resolve_collision(out, defn, fpath, div_id)
                    defn = self._with_slug(defn, slug)
                out[slug] = defn
        return out

    def _scan_locale(
        self,
        locale: Locale,
        locale_root: Path,
        divisions: dict[str, DivisionMeta],
    ) -> dict[str, SubagentDefinition]:
        out: dict[str, SubagentDefinition] = {}
        if not divisions:
            # Without a divisions.json for this locale we cannot resolve
            # which top-level subdirectories to scan. Skip quietly.
            return out
        for div_id in sorted(divisions):
            div_dir = locale_root / div_id
            if not div_dir.is_dir():
                continue
            for fpath in sorted(div_dir.glob("*.md")):
                defn = self._parse_agent_file(div_id, fpath, locale)
                if defn is None:
                    continue
                slug = defn.summary.slug
                if slug in out:
                    slug = self._resolve_collision(out, defn, fpath, div_id)
                    defn = self._with_slug(defn, slug)
                out[slug] = defn
        return out

    def _parse_agent_file(
        self, div_id: str, fpath: Path, locale: Locale
    ) -> SubagentDefinition | None:
        try:
            text = fpath.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("subagent %s unreadable: %s", fpath, exc)
            return None
        if not is_agent_file(text):
            return None
        meta, _body = parse_frontmatter(text)
        name = str(meta.get("name") or "").strip()
        if not name:
            logger.warning("subagent %s: missing name frontmatter", fpath)
            return None
        if name == TRANSLATION_PLACEHOLDER_NAME:
            # Auto-generated zh placeholder — keep the file on disk so
            # translators can pick it up, but do not register it in the
            # in-memory catalog. ``content_for`` will fall back to the
            # English version for the affected slug. Logged at debug to
            # avoid spam (hundreds of placeholders are normal during the
            # translation window).
            logger.debug(
                "subagent %s: skipping translation placeholder (locale=%s)",
                fpath,
                locale,
            )
            return None
        # Always derive the slug from the file stem so that zh and en files
        # with the same filename (e.g. ``academic-anthropologist.md``) map to
        # the same slug regardless of what language the ``name`` field uses.
        # This is the only reliable way to pair translated files with their
        # English counterparts when CJK characters make slugify unusable.
        slug = fpath.stem
        if not slug:
            logger.warning("subagent %s: empty slug from stem", fpath)
            return None
        try:
            rel = fpath.relative_to(self._library_root).as_posix()
        except ValueError:  # pragma: no cover - fpath always under _library_root
            rel = fpath.as_posix()
        description = str(meta.get("description") or "").strip()
        emoji = str(meta.get("emoji")).strip() if meta.get("emoji") else None
        color = str(meta.get("color")).strip() if meta.get("color") else None
        summary = SubagentSummary(
            slug=slug,
            division=div_id,
            name=name,
            description=description,
            emoji=emoji,
            color=color,
            source_path=rel,
            available_locales=(locale,),
        )
        return SubagentDefinition(
            summary=summary,
            content=text,
            contents={locale: text},
            names={locale: name},
            descriptions={locale: description},
        )

    def _resolve_collision(
        self,
        existing: dict[str, SubagentDefinition],
        new: SubagentDefinition,
        fpath: Path,
        div_id: str,
    ) -> str:
        base = new.summary.slug
        prefixed = f"{div_id}-{base}"
        if prefixed not in existing:
            return prefixed
        stem = fpath.stem
        if stem not in existing:
            return stem
        fallback = f"{div_id}-{stem}"
        if fallback not in existing:
            return fallback
        return f"{base}-{len(existing)}"

    def _with_slug(self, defn: SubagentDefinition, slug: str) -> SubagentDefinition:
        return SubagentDefinition(
            summary=SubagentSummary(
                slug=slug,
                division=defn.summary.division,
                name=defn.summary.name,
                description=defn.summary.description,
                emoji=defn.summary.emoji,
                color=defn.summary.color,
                source_path=defn.summary.source_path,
                available_locales=defn.summary.available_locales,
            ),
            content=defn.content,
            contents=dict(defn.contents),
            names=dict(defn.names),
            descriptions=dict(defn.descriptions),
        )

    def _extend_with_locale(
        self,
        target: SubagentDefinition,
        locale_defn: SubagentDefinition,
        locale: Locale,
    ) -> SubagentDefinition:
        contents = dict(target.contents)
        contents[locale] = locale_defn.content
        names = dict(target.names)
        names[locale] = locale_defn.summary.name
        descriptions = dict(target.descriptions)
        descriptions[locale] = locale_defn.summary.description
        return SubagentDefinition(
            summary=target.summary,
            content=target.content,
            contents=contents,
            names=names,
            descriptions=descriptions,
        )

    def list_divisions(self, locale: Locale | str | None = None) -> list[dict[str, Any]]:
        """Return divisions, optionally localized.

        ``locale`` picks which division label to expose. When the
        requested locale does not ship a ``divisions.json`` the default
        locale is used (with English as a final fallback). The full
        per-locale label set is also returned under ``labels`` so the
        dashboard can show the user-preferred string client-side.
        """
        key = _norm(locale)
        per_locale = self._divisions_by_locale or {DEFAULT_LOCALE: self._divisions}
        locale_divs = (
            per_locale.get(key)
            or per_locale.get(DEFAULT_LOCALE)
            or per_locale.get(FALLBACK_LOCALE, {})
        )
        counts: dict[str, int] = {}
        for item in self._subagents.values():
            counts[item.summary.division] = counts.get(item.summary.division, 0) + 1
        rows: list[dict[str, Any]] = []
        for div_id in sorted(self._divisions):
            meta = self._divisions[div_id]
            localized = locale_divs.get(div_id) or meta
            labels: dict[str, str] = {}
            for loc, divs in per_locale.items():
                if div_id in divs:
                    labels[loc] = divs[div_id].label
            labels[key] = labels.get(key, localized.label)
            rows.append(
                {
                    "id": div_id,
                    "label": localized.label,
                    "icon": meta.icon,
                    "color": meta.color,
                    "count": counts.get(div_id, 0),
                    "available_locales": list(meta.available_locales),
                    "labels": labels,
                },
            )
        return rows

    def list_summaries(
        self,
        *,
        division: str | None = None,
        query: str | None = None,
        locale: Locale | str | None = None,
    ) -> list[SubagentSummary]:
        rows = [d.summary for d in self._subagents.values()]
        if division:
            rows = [s for s in rows if s.division == division]
        q = (query or "").strip().lower()
        if q:
            rows = [
                s
                for s in rows
                if q in s.name.lower()
                or q in s.description.lower()
                or q in s.slug.lower()
                or q in s.division.lower()
            ]
        rows.sort(key=lambda s: (s.division, s.name.lower()))
        if not locale:
            return rows
        key = _norm(locale)
        out: list[SubagentSummary] = []
        for s in rows:
            # Only include agents that have a translation for the requested locale.
            if key not in s.available_locales:
                continue
            defn = self._subagents.get(s.slug)
            if defn is None:
                out.append(s)
                continue
            out.append(
                SubagentSummary(
                    slug=s.slug,
                    division=s.division,
                    name=defn.name_for(key),
                    description=defn.description_for(key),
                    emoji=s.emoji,
                    color=s.color,
                    source_path=s.source_path,
                    available_locales=s.available_locales,
                ),
            )
        return out

    def get(self, slug: str) -> SubagentDefinition | None:
        return self._subagents.get(slug)

    def get_division(self, division_id: str) -> DivisionMeta | None:
        return self._divisions.get(division_id)


def default_package_root() -> Path:
    """Return the in-package subagents directory."""
    return Path(__file__).parent
