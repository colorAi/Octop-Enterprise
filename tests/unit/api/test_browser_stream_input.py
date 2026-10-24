"""Browser control must support IME commits and real editing key events."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from octop.api.routers.browser.stream import _handle_client_event


def _session() -> SimpleNamespace:
    return SimpleNamespace(_internal=SimpleNamespace(client=SimpleNamespace(send=AsyncMock())))


@pytest.mark.parametrize("text", ["中文输入", "hello", "👩‍💻", "第一行\nsecond line"])
async def test_text_is_inserted_as_one_unicode_commit(text: str) -> None:
    sess = _session()
    await _handle_client_event(sess, {"type": "type", "text": text})
    sess._internal.client.send.assert_awaited_once_with("Input.insertText", {"text": text})


@pytest.mark.parametrize(
    ("key", "key_code", "command"),
    [
        ("Backspace", 8, "deleteBackward"),
        ("Delete", 46, "deleteForward"),
        ("ArrowLeft", 37, "moveLeft"),
        ("ArrowRight", 39, "moveRight"),
        ("Home", 36, "moveToBeginningOfLine"),
        ("End", 35, "moveToEndOfLine"),
    ],
)
async def test_editing_keys_dispatch_press_and_release(
    key: str, key_code: int, command: str
) -> None:
    sess = _session()
    await _handle_client_event(sess, {"type": "keydown", "key": key, "code": key})
    await _handle_client_event(sess, {"type": "keyup", "key": key, "code": key})
    down, up = [call.args for call in sess._internal.client.send.await_args_list]
    assert down[0] == up[0] == "Input.dispatchKeyEvent"
    assert down[1]["type"] == "rawKeyDown"
    assert down[1]["windowsVirtualKeyCode"] == key_code
    assert down[1]["commands"] == [command]
    assert up[1]["type"] == "keyUp"
    assert "commands" not in up[1]


@pytest.mark.parametrize(("modifier", "mask"), [("ctrlKey", 2), ("metaKey", 4)])
@pytest.mark.parametrize(
    ("key", "shift", "command"),
    [("a", False, "selectAll"), ("z", False, "undo"), ("z", True, "redo"), ("y", False, "redo")],
)
async def test_editing_shortcuts_preserve_modifiers(
    modifier: str, mask: int, key: str, shift: bool, command: str
) -> None:
    sess = _session()
    await _handle_client_event(
        sess,
        {
            "type": "keydown",
            "key": key,
            "code": f"Key{key.upper()}",
            modifier: True,
            "shiftKey": shift,
        },
    )
    method, params = sess._internal.client.send.await_args.args
    assert method == "Input.dispatchKeyEvent"
    assert params["modifiers"] == mask | (8 if shift else 0)
    assert params["commands"] == [command]
    assert "text" not in params


async def test_shift_arrow_extends_selection() -> None:
    sess = _session()
    await _handle_client_event(
        sess, {"type": "keydown", "key": "ArrowLeft", "code": "ArrowLeft", "shiftKey": True}
    )
    params = sess._internal.client.send.await_args.args[1]
    assert params["commands"] == ["moveLeftAndModifySelection"]
    assert params["modifiers"] == 8


async def test_enter_uses_a_key_event_to_submit_or_insert_a_newline() -> None:
    sess = _session()
    await _handle_client_event(sess, {"type": "keydown", "key": "Enter", "code": "Enter"})
    params = sess._internal.client.send.await_args.args[1]
    assert params["type"] == "keyDown"
    assert params["text"] == "\r"
    assert params["windowsVirtualKeyCode"] == 13


async def test_tab_repeat_and_key_location_are_forwarded() -> None:
    sess = _session()
    await _handle_client_event(
        sess,
        {
            "type": "keydown",
            "key": "Tab",
            "code": "Tab",
            "shiftKey": True,
            "repeat": True,
            "location": 1,
        },
    )
    params = sess._internal.client.send.await_args.args[1]
    assert params["windowsVirtualKeyCode"] == 9
    assert params["modifiers"] == 8
    assert params["autoRepeat"] is True
    assert params["location"] == 1


async def test_legacy_keydown_still_sends_a_complete_press() -> None:
    sess = _session()
    await _handle_client_event(sess, {"type": "keydown", "key": "Backspace"})
    assert [call.args[1]["type"] for call in sess._internal.client.send.await_args_list] == [
        "rawKeyDown",
        "keyUp",
    ]


@pytest.mark.parametrize("key", ["", "Process", "Dead", "Unidentified"])
async def test_ime_placeholder_keys_are_not_dispatched(key: str) -> None:
    sess = _session()
    await _handle_client_event(sess, {"type": "keydown", "key": key, "code": key})
    sess._internal.client.send.assert_not_awaited()
