"""Tests for REPL session state helpers."""

from __future__ import annotations

from octop.cli.repl.session import ReplSession
from octop.cli.repl.toolbar import format_repl_toolbar


def test_apply_slash_text_sets_model_from_zh_reply() -> None:
    state = ReplSession(agent_id="ag1", session_key="cli")
    state.apply_slash_text("此话题模型 → openai:gpt-4o。")
    assert state.model == "openai:gpt-4o"


def test_apply_slash_text_sets_model_from_en_reply() -> None:
    state = ReplSession(agent_id="ag1", session_key="cli")
    state.apply_slash_text("Model for this thread → anthropic:claude.")
    assert state.model == "anthropic:claude"


def test_apply_slash_text_clears_model() -> None:
    state = ReplSession(agent_id="ag1", session_key="cli", model="x:y")
    state.apply_slash_text("Model override cleared.")
    assert state.model is None


def test_thread_id_for_send_only_when_pinned() -> None:
    state = ReplSession(agent_id="ag1", session_key="cli", thread_id="t1")
    assert state.thread_id_for_send() == "t1"
    state.on_new_chat("t2")
    assert state.thread_id_for_send() is None
    assert state.thread_id == "t2"


def test_format_repl_toolbar_includes_model_and_elapsed() -> None:
    state = ReplSession(
        agent_id="agent-abcdefghij", session_key="cli-session", model="p:m", last_elapsed=1.2
    )
    bar = format_repl_toolbar(state)
    assert "model: p:m" in bar
    assert "last: 1.2s" in bar
    assert "agent-abcde" in bar
