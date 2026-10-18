#!/usr/bin/env python3
"""Build a complete same-origin desktop UI, including all lazy CSS/JS modules."""
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path


def build_dashboard(root: Path) -> dict:
    dashboard = root / "dashboard"
    destination = root / "desktop/src/assets/company-dashboard.zip"
    environment = dict(os.environ, VITE_DESKTOP_BUNDLE="1", VITE_STATIC_CDN_BASE="")
    with tempfile.TemporaryDirectory(prefix="octop-company-ui-") as temporary:
        output = Path(temporary) / "dashboard"
        subprocess.run(["node", "node_modules/vite/bin/vite.js", "build", "--outDir", str(output)],
                       cwd=dashboard, env=environment, check=True)
        index = (output / "index.html").read_text()
        if "assets.phantomai.top" in index or "https://h3c." in index:
            raise ValueError("Packaged UI must not depend on public static resources")
        files = {}
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(output.rglob("*")):
                if not path.is_file() or path.name in {"sw.js", "workbox.js"}:
                    continue
                relative = path.relative_to(output).as_posix()
                data = path.read_bytes()
                files[relative] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                entry = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, data)
    result = {"resources": len(files), "source_bytes": sum(item["bytes"] for item in files.values()),
              "zip_bytes": destination.stat().st_size,
              "zip_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "files": files}
    destination.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    result = build_dashboard(Path(__file__).resolve().parent.parent)
    print(json.dumps({key: value for key, value in result.items() if key != "files"}))
