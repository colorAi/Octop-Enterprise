"""Tests for runtime optional Python package installation."""

from __future__ import annotations

import subprocess
import sys

import pytest
from tests.support.fakes import fake_bin_path

from octop.infra.utils import runtime_packages as mod


def test_find_uv_binary_from_path(monkeypatch) -> None:
    monkeypatch.setenv("OCTOP_UV_BIN", "")
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    assert mod.find_uv_binary() == "/usr/bin/uv"


def test_find_uv_binary_from_env_override(monkeypatch, tmp_path) -> None:
    uv = tmp_path / "custom-uv"
    uv.write_text("", encoding="utf-8")
    monkeypatch.setenv("OCTOP_UV_BIN", str(uv))
    monkeypatch.setattr(mod.shutil, "which", lambda _name: None)
    assert mod.find_uv_binary() == str(uv)


def test_build_install_commands_prefers_uv(monkeypatch) -> None:
    monkeypatch.setattr(mod, "find_uv_binary", lambda: "/usr/bin/uv")
    cmds = mod.build_install_commands(["pkg>=1"])
    assert cmds[0] == [
        "/usr/bin/uv",
        "pip",
        "install",
        "--python",
        sys.executable,
        "pkg>=1",
    ]
    assert cmds[1][:4] == [sys.executable, "-m", "pip", "install"]


def test_install_packages_noop_when_satisfied() -> None:
    assert mod.install_packages(["pkg"], is_satisfied=lambda: True) == "ready"


def test_install_packages_uses_uv_then_pip(monkeypatch) -> None:
    calls: list[list[str]] = []
    state = {"ready": False}

    class FakeProc:
        def __init__(self, code: int, stderr: str = "") -> None:
            self.returncode = code
            self.stderr = stderr
            self.stdout = ""

    def fake_run(cmd, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        if cmd[0].endswith("uv"):
            state["ready"] = True
            return FakeProc(0)
        return FakeProc(1, "should not reach pip")

    monkeypatch.setattr(mod, "find_uv_binary", lambda: "/usr/bin/uv")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert mod.install_packages(["pkg>=1"], is_satisfied=lambda: state["ready"]) == "installed"
    assert calls[0][0] == "/usr/bin/uv"


def test_install_packages_bootstraps_pip_on_missing_module(monkeypatch) -> None:
    calls: list[list[str]] = []
    state = {"pip": False, "ready": False}

    class FakeProc:
        def __init__(self, code: int, stderr: str = "") -> None:
            self.returncode = code
            self.stderr = stderr
            self.stdout = ""

    def fake_run(cmd, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        if cmd[:3] == [sys.executable, "-m", "ensurepip"]:
            state["pip"] = True
            return FakeProc(0)
        if cmd[:3] == [sys.executable, "-m", "pip"]:
            if not state["pip"]:
                return FakeProc(1, "No module named pip")
            state["ready"] = True
            return FakeProc(0)
        return FakeProc(1, "unexpected")

    monkeypatch.setattr(mod, "find_uv_binary", lambda: None)
    monkeypatch.setattr(mod, "pip_importable", lambda: state["pip"])
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert mod.install_packages(["pkg>=1"], is_satisfied=lambda: state["ready"]) == "installed"
    assert any(cmd[:3] == [sys.executable, "-m", "ensurepip"] for cmd in calls)


def test_install_packages_extra_fallback(monkeypatch) -> None:
    calls: list[list[str]] = []

    class FakeProc:
        returncode = 1
        stderr = "network error"
        stdout = ""

    monkeypatch.setattr(mod, "find_uv_binary", lambda: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **_kw: calls.append(list(cmd)) or FakeProc())
    spec = mod.PackageInstallSpec(packages=("fastembed>=0.4",), extra_fallback="local-embedding")
    with pytest.raises(RuntimeError, match="Could not install optional Python components"):
        mod.install_packages(spec, is_satisfied=lambda: False)
    assert any("octop[local-embedding]" in cmd for cmd in calls)


def test_find_uv_binary_uses_custom_search_paths(monkeypatch, tmp_path) -> None:
    uv = tmp_path / "uv"
    uv.write_text("", encoding="utf-8")
    monkeypatch.setenv("OCTOP_UV_BIN", "")
    monkeypatch.setattr(mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(mod, "_uv_search_paths", lambda: (uv,))
    assert mod.find_uv_binary() == str(uv)


@pytest.mark.parametrize(
    ("uv_error", "expected_reason"),
    [
        ("Permission denied (os error 13)", "permission_denied"),
        ("Read-only file system (os error 30)", "permission_denied"),
        ("Access is denied (os error 5)", "permission_denied"),
        ("Failed to connect to package index", "failed"),
        ("timed out", "failed"),
    ],
)
def test_pip_fallback_does_not_hide_uv_failure(
    monkeypatch: pytest.MonkeyPatch, uv_error: str, expected_reason: str
) -> None:
    uv = fake_bin_path("uv")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        detail = uv_error if cmd[0] == uv else "No module named pip"
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=detail)

    monkeypatch.setattr(mod, "find_uv_binary", lambda: uv)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "pip_importable", lambda: False)
    spec = mod.PackageInstallSpec(packages=("fastembed>=0.4",), extra_fallback="local-embedding")
    with pytest.raises(mod.PackageInstallError) as raised:
        mod.install_packages(spec, is_satisfied=lambda: False)

    assert raised.value.reason == expected_reason
    assert sum("ensurepip" in cmd for cmd in calls) == 1
    assert uv_error not in str(raised.value)


def test_ensurepip_permission_failure_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        detail = (
            "PermissionError: environment is not writable"
            if "ensurepip" in cmd
            else "No module named pip"
        )
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=detail)

    monkeypatch.setattr(mod, "find_uv_binary", lambda: None)
    monkeypatch.setattr(mod, "pip_importable", lambda: False)
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(mod.PackageInstallError) as raised:
        mod.install_packages(["fastembed>=0.4"])
    assert raised.value.reason == "permission_denied"


def test_missing_installer_is_reported_when_no_strategy_can_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod, "find_uv_binary", lambda: None)
    monkeypatch.setattr(mod, "ensure_pip_bootstrapped", lambda: (False, ""))
    monkeypatch.setattr(
        mod, "_run_install_command", lambda *_args, **_kwargs: (False, "No module named pip")
    )
    with pytest.raises(mod.PackageInstallError) as raised:
        mod.install_packages(["fastembed>=0.4"])
    assert raised.value.reason == "installer_missing"


def test_successful_fallback_still_installs_after_uv_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uv = fake_bin_path("uv")

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == uv:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Permission denied")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod, "find_uv_binary", lambda: uv)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert mod.install_packages(["fastembed>=0.4"]) == "installed"
