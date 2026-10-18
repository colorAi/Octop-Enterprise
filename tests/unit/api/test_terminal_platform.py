"""Unit tests for terminal platform support."""

from __future__ import annotations

import os
import shutil
from unittest.mock import patch

from octop.api.routers.terminal import (
    _ZSH_WEB_TERMINAL_RC,
    _zsh_web_zdotdir,
    terminal_supported,
)


def test_terminal_supported_on_posix() -> None:
    if os.name != "posix":
        return
    supported, reason = terminal_supported()
    assert supported is True
    assert reason == ""


def test_terminal_unsupported_on_windows() -> None:
    with patch("octop.api.routers.terminal.os.name", "nt"):
        supported, reason = terminal_supported()
    assert supported is False
    assert "Windows" in reason


def test_zsh_web_zdotdir_sources_user_rc_and_clears_prompt_markers() -> None:
    if os.name != "posix":
        return
    zdotdir = _zsh_web_zdotdir()
    try:
        rc_path = os.path.join(zdotdir, ".zshrc")
        assert os.path.isfile(rc_path)
        with open(rc_path, encoding="utf-8") as f:
            text = f.read()
        assert text == _ZSH_WEB_TERMINAL_RC
        assert "PROMPT_EOL_MARK" in text
        assert "prompt_sp" in text
        assert 'source "${HOME}/.zshrc"' in text
    finally:
        shutil.rmtree(zdotdir, ignore_errors=True)
