"""Offline image check: load embedding dependencies and render with the real browser."""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import json
import os
import tempfile
from pathlib import Path


async def verify() -> dict[str, object]:
    dependencies = {}
    for name in ("fastembed", "huggingface_hub", "onnxruntime"):
        importlib.import_module(name)
        dependencies[name] = importlib.metadata.version(name)

    from octop_browser import BrowserSession
    from octop_browser.profile import ProfileManager
    from octop_browser.settings import OctopSettings

    from octop.api.routers.browser.env import _probe_env

    probe = _probe_env()
    if not probe["browsers_ok"] or not probe["harness_browser"]:
        raise RuntimeError(f"Browser environment is incomplete: {probe}")

    with tempfile.TemporaryDirectory(prefix="octop-runtime-check-") as temporary:
        root = Path(temporary)
        settings = OctopSettings(profiles_dir=root / "profiles", screenshots_dir=root / "shots")
        session = await BrowserSession.create(
            profile="image-check",
            headless=True,
            profile_manager=ProfileManager(settings=settings),
            settings=settings,
        )
        try:
            navigation = await session.navigate(
                "data:text/html,<title>Octop runtime check</title><h1>Browser ready</h1>"
            )
            if not navigation.success:
                raise RuntimeError(f"Browser navigation failed: {navigation.error}")
            screenshot = root / "browser.png"
            result = await session.screenshot(path=str(screenshot))
            if not result.success:
                raise RuntimeError(f"Browser screenshot failed: {result.error}")
            if (result.metadata or {}).get("title") != "Octop runtime check":
                raise RuntimeError("Browser did not render the verification page")
            data = screenshot.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 1000:
                raise RuntimeError("Browser did not produce a valid screenshot")
        finally:
            await session.close(kill=True)

    return {
        "uid": os.getuid(),
        "dependencies": dependencies,
        "browser": probe,
        "browser_launch_verified": True,
        "browser_screenshot_bytes": len(data),
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(asyncio.wait_for(verify(), timeout=60))))
