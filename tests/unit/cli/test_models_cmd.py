"""Tests for ``octop models`` ollama subcommands."""

from __future__ import annotations

from click.testing import CliRunner

from octop.cli.main import cli


def test_models_help_lists_ollama_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["models", "--help"])
    assert result.exit_code == 0
    for sub in ("ollama-list", "ollama-pull", "ollama-rm"):
        assert sub in result.output
