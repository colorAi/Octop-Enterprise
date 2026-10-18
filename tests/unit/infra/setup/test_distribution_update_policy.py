"""A fork must not download or install the upstream distribution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from click.testing import CliRunner

from octop.api.routers import update as update_router
from octop.api.routers import update_store
from octop.cli.main import cli
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.setup import self_update


def test_distribution_disables_upstream_network_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    assert self_update.UPSTREAM_SELF_UPDATE_ENABLED is False
    load = Mock(side_effect=AssertionError("unexpected network request"))
    monkeypatch.setattr(self_update, "_load_pypi_json", load)
    assert self_update.fetch_pypi_info() is None
    load.assert_not_called()


@pytest.mark.parametrize("kind", ["normal", "portable", "fpk"])
def test_direct_upgrade_is_blocked_before_any_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path, kind: str
) -> None:
    monkeypatch.delenv("OCTOP_FPK_SITE_PACKAGES", raising=False)
    monkeypatch.delenv("OCTOP_GREEN_PACKAGES", raising=False)
    if kind != "normal":
        env_name = "OCTOP_FPK_SITE_PACKAGES" if kind == "fpk" else "OCTOP_GREEN_PACKAGES"
        monkeypatch.setenv(env_name, str(tmp_path))
    install = Mock(side_effect=AssertionError("unexpected installation"))
    stash = Mock(side_effect=AssertionError("unexpected launcher mutation"))
    monkeypatch.setattr(self_update, "_run_managed_upgrade", install)
    monkeypatch.setattr(self_update, "_run_fpk_upgrade", install)
    monkeypatch.setattr(self_update, "stash_console_scripts", stash)
    result = self_update.run_upgrade(version="999.0.0", allow_prerelease=True)
    assert result.success is False
    assert "Octop Ent" in (result.error or "")
    install.assert_not_called()
    stash.assert_not_called()


def test_cli_update_reports_manual_policy_without_probing(monkeypatch: pytest.MonkeyPatch) -> None:
    import octop.cli.commands.update as update_cmd

    probe = Mock(side_effect=AssertionError("unexpected upstream check"))
    monkeypatch.setattr(update_cmd, "fetch_pypi_info", probe)
    result = CliRunner().invoke(cli, ["update", "--yes"])
    assert result.exit_code != 0
    assert "Octop Ent" in result.output
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_update_api_discards_cached_upstream_release(monkeypatch: pytest.MonkeyPatch) -> None:
    update_store.clear_cached_status()
    probe = Mock(side_effect=AssertionError("unexpected upstream check"))
    monkeypatch.setattr(update_router, "fetch_pypi_info", probe)
    server = SimpleNamespace(
        services=SimpleNamespace(settings_repo=SimpleNamespace(get=lambda _: None))
    )
    update_store.cache_status({"latest_any": "999.0.0", "has_update": True})
    try:
        cached = await update_router.update_status(_=None, server=server)
        checked = await update_router.check_for_updates(_=None, server=server)
        for result in (cached, checked):
            assert result["has_update"] is False
            assert result["latest_version"] is None
            assert result["source"] is None
            assert result["error_code"] == "manual_update_required"
        probe.assert_not_called()
    finally:
        update_store.clear_cached_status()


@pytest.mark.asyncio
async def test_update_api_rejects_pinned_upgrade_before_queueing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = AsyncMock()
    probe = Mock(side_effect=AssertionError("unexpected upstream check"))
    monkeypatch.setattr(update_router, "create_task", create)
    monkeypatch.setattr(update_router, "fetch_pypi_info", probe)
    with pytest.raises(OctopError) as exc:
        await update_router.trigger_upgrade(
            body=update_router.UpgradeBody(version="999.0.0"), server=None, _=None
        )
    assert exc.value.code == ErrorCode.FORBIDDEN
    create.assert_not_awaited()
    probe.assert_not_called()
