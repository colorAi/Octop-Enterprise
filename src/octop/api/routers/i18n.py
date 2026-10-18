"""Localized strings API (tool labels, etc.)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from octop.i18n import all_skill_labels, all_tool_labels
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter()


class ToolLabelsOut(BaseModel):
    locale: str = Field(description="Resolved locale (`zh` or `en`).")
    labels: dict[str, str] = Field(description="Tool name → display label.")


class SkillLabelsOut(BaseModel):
    locale: str = Field(description="Resolved locale (`zh` or `en`).")
    labels: dict[str, str] = Field(description="Skill slug → display label.")


@router.get(
    "/i18n/tools", summary="Localized agent tool display names", response_model=ToolLabelsOut
)
async def get_tool_labels(request: Request) -> ToolLabelsOut:
    """Return human-readable tool labels for chat UI and clients.

    Uses ``Accept-Language`` when present; falls back to the server default.
    """
    locale = resolve_request_locale(request)
    return ToolLabelsOut(locale=locale, labels=all_tool_labels(locale))


@router.get(
    "/i18n/skills",
    summary="Localized built-in skill display names",
    response_model=SkillLabelsOut,
)
async def get_skill_labels(request: Request) -> SkillLabelsOut:
    """Return human-readable skill labels keyed by slug.

    Uses ``Accept-Language`` when present; falls back to the server default.
    """
    locale = resolve_request_locale(request)
    return SkillLabelsOut(locale=locale, labels=all_skill_labels(locale))
