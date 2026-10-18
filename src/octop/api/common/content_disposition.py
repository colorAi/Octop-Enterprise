"""Build latin-1-safe Content-Disposition values (RFC 5987)."""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote


def _basename(filename: str) -> str:
    """Strip directory components from POSIX or Windows-style paths."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name or "download"


def _ascii_fallback(base: str) -> str:
    """ASCII-only placeholder that keeps a safe file extension when possible."""
    suffix = PurePosixPath(base).suffix
    if suffix.isascii() and all(c.isalnum() or c == "." for c in suffix):
        return f"download{suffix}"
    return "download"


def content_disposition(filename: str, *, disposition: str = "attachment") -> str:
    """Return a ``Content-Disposition`` header value safe for Starlette/ASGI.

    HTTP header values must encode as latin-1. Non-ASCII names use RFC 5987
    ``filename*`` plus an ASCII ``filename`` fallback (``download`` + extension).
    """
    base = _basename(filename)
    starred = quote(base, safe="")
    if base.isascii():
        escaped = base.replace("\\", "\\\\").replace('"', '\\"')
        return f'{disposition}; filename="{escaped}"'
    fallback = _ascii_fallback(base)
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{starred}"
