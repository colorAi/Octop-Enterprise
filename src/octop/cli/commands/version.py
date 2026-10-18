"""octop version command."""

from __future__ import annotations

import click


@click.command("version")
def version() -> None:
    """Show the installed octop version."""
    try:
        from importlib.metadata import version as _v

        v = _v("octop")
    except Exception:
        v = "unknown"
    click.echo(f"octop v{v}")
