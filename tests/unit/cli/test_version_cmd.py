"""Tests for `octop version`."""

from __future__ import annotations

from click.testing import CliRunner

from octop.cli.main import cli


def test_version_prints_orca_version() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert "octop" in result.output.lower()
    assert any(ch.isdigit() for ch in result.output)


def test_version_in_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert "version" in result.output


def test_root_version_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["-v"])
    assert result.exit_code == 0
    assert "octop" in result.output.lower()
