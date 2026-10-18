"""Cache-Control for dashboard SPA shell vs hashed Vite assets."""

from __future__ import annotations

from pathlib import Path

from octop.api.app import (
    _dashboard_fallback,
    dashboard_cache_control,
    dashboard_directory,
    is_dashboard_asset_path,
)


def test_shell_files_are_revalidated() -> None:
    assert dashboard_cache_control("") == "no-cache"
    assert dashboard_cache_control("index.html") == "no-cache"
    assert dashboard_cache_control("sw.js") == "no-cache"
    assert dashboard_cache_control("manifest.json") == "no-cache"
    assert dashboard_cache_control("desktop-login.html") == "no-cache"


def test_hashed_assets_are_immutable() -> None:
    assert (
        dashboard_cache_control("assets/index.abc123.js") == "public, max-age=31536000, immutable"
    )
    assert (
        dashboard_cache_control("assets/vendor-utils.Xyz.css")
        == "public, max-age=31536000, immutable"
    )


def test_other_static_files_leave_cache_unset() -> None:
    assert dashboard_cache_control("logo_vertical_dark.svg") is None
    assert dashboard_cache_control("offline.html") is None


def test_only_hashed_assets_skip_the_spa_fallback() -> None:
    assert is_dashboard_asset_path("assets/index.abc123.js") is True
    assert is_dashboard_asset_path("chat") is False
    assert is_dashboard_asset_path("") is False


def test_public_cdn_build_preserves_lan_and_unknown_hosts(tmp_path: Path) -> None:
    cdn = tmp_path / "cdn"
    cdn.mkdir()
    (cdn / "index.html").write_text("CDN build")
    hosts = ["octop.example.com"]
    assert dashboard_directory(tmp_path, "octop.example.com", hosts) == cdn
    assert dashboard_directory(tmp_path, "OCTOP.EXAMPLE.COM.", hosts) == cdn
    for host in ["192.0.2.28", "localhost", "other.example.com", None]:
        assert dashboard_directory(tmp_path, host, hosts) == tmp_path
    assert dashboard_directory(tmp_path, "octop.example.com", ()) == tmp_path


def test_missing_cdn_build_falls_back_to_local_build(tmp_path: Path) -> None:
    assert dashboard_directory(tmp_path, "octop.example.com", ["octop.example.com"]) == tmp_path


def test_public_shell_edge_cache_keeps_browser_revalidation(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("Public shell")
    local = _dashboard_fallback(index, "chat")
    public = _dashboard_fallback(index, "chat", public_edge_cache=True)
    assert local.headers["Cache-Control"] == public.headers["Cache-Control"] == "no-cache"
    assert "Cloudflare-CDN-Cache-Control" not in local.headers
    assert public.headers["Cloudflare-CDN-Cache-Control"] == "public, max-age=60"
