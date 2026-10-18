"""Unit tests for cross-platform subprocess stdout reads."""

from __future__ import annotations

import json
import subprocess
import sys

from octop.infra.utils.subprocess_io import parse_json_lines, parse_subprocess_json_lines


def test_parse_json_lines_decodes_ndjson() -> None:
    raw = b'{"action":"log","message":"hi"}\nnot-json\n'
    lines = parse_json_lines(raw)
    assert len(lines) == 2
    assert lines[0]["action"] == "log"
    assert lines[1]["step"] == "raw"


def test_parse_subprocess_json_lines_from_echo() -> None:
    payload = {"action": "show_qrcode", "content": "{}"}
    code = f"import json; print(json.dumps({json.dumps(payload)}))"
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=False,
    )
    try:
        proc.wait(timeout=5)
        lines = parse_subprocess_json_lines(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert any(line.get("action") == "show_qrcode" for line in lines)
