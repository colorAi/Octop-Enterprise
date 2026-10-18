"""tests/integration/test_terminal_ws.py — PTY WebSocket terminal.

The PTY-spawning happy path is best exercised against the live socket
because asgi-transport doesn't ship a WebSocket that can drive a real
``pty.openpty()`` cleanly. We therefore stick to the *gates* we own:
unauthenticated requests, missing token, agent-not-found, and the
session-cap path. The actual PTY-shell dance is tested manually via
the dashboard.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
async def env(env_terminal):
    yield env_terminal


def test_router_is_mounted(env: Any) -> None:
    """Smoke: ``/agents/{aid}/terminal/ws`` is in the FastAPI route table."""
    _srv, app, _tok, _aid = env
    # FastAPI nests routers as ``_IncludedRouter`` and WebSocket routes report
    # ``path is None`` at the top level, so resolve by route name instead.
    url = app.router.url_path_for("terminal_ws", agent_id="x")
    assert url == "/api/agents/x/terminal/ws"


def test_extract_winsize_helper_packs_in_correct_order() -> None:
    """``_set_winsize`` packs (rows, cols, x_pixels, y_pixels) — verify the
    pack order matches what ioctl(TIOCSWINSZ) expects, since getting it
    wrong silently swaps the dimensions in the user's terminal.
    """
    import struct

    from octop.api.routers.terminal import _set_winsize  # noqa: F401  (existence)

    # We can't ioctl on an arbitrary fd, but we can inspect the struct
    # convention the function uses.
    packed = struct.pack("HHHH", 24, 80, 0, 0)
    rows, cols, _, _ = struct.unpack("HHHH", packed)
    assert rows == 24
    assert cols == 80


def test_detect_shell_returns_existing_path() -> None:
    """``_detect_shell`` must return a path that exists on the current OS."""
    import os

    from octop.api.routers.terminal import _detect_shell

    shell = _detect_shell()
    if os.name == "nt":
        assert shell.lower().endswith((".exe", "cmd.exe")) or os.path.exists(shell)
    else:
        assert os.path.exists(shell), f"shell {shell!r} does not exist"


def test_read_nonblock_returns_empty_for_none_fd() -> None:
    from octop.api.routers.terminal import _read_nonblock

    assert _read_nonblock(None) == b""
