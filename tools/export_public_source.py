#!/usr/bin/env python3
"""Export the current source without local history, receipts or restricted materials."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

EXCLUDED = (
    ".git/",
    "PUBLIC_SOURCE.json",
    ".cursor/",
    ".claude/",
    ".codex/",
    ".playwright-cli/",
    "output/",
    "fnos/",
    "scripts/build-fpk.sh",
    "scripts/fnos/",
    "tests/unit/test_fnos_docker_persist.py",
    "scripts/release_download_links.py",
    "tests/unit/test_release_download_links.py",
    "src/octop/dashboard/",
    "desktop/src/assets/company-dashboard.zip",
    "desktop/src/assets/company-dashboard.json",
    "desktop/src/assets/login.bundle.js",
    "desktop/src/assets/login.bundle.css",
    "src/octop/infra/agents/experts/library/office-automation/",
    "dashboard/public/experts/avatars/office-automation.svg",
    "docs/assets/model-01-custom.png",
    "docs/assets/model-02-manage.png",
    "docs/assets/octop-banner.png",
    "docs/assets/overview.png",
    "docs/assets/qrcode.png",
    "docs/assets/readme-banner-zh.png",
    "docs/assets/readme-banner.png",
    "docs/assets/readme-chat-zh.png",
    "docs/assets/readme-chat.png",
    "docs/assets/setup-01-steps.png",
    "docs/assets/setup-02-admin.png",
    "docs/assets/setup-03-model.png",
    "docs/assets/use-01-login.png",
    "docs/assets/use-02-chat.png",
    "docs/assets/use-03-agent.png",
    "docs/assets/use-04-channels.png",
    "docs/assets/use-05-cron.png",
    "docs/assets/use-06-settings.png",
    "docs/assets/use-07-remote-desktop.png",
    "docs/assets/use-08-browser-ai.png",
    "docs/user-guide.html",
    "docs/octop-introduction.html",
    "docs/company-desktop-hybrid.md",
    "docs/company-desktop-upgrade-plan.md",
    "docs/company-desktop-runtime-delivery.md",
    "docs/company-groups-design-20261015.md",
    "docs/company-bots-design-20261015.md",
    "docs/review-fixes-20261013.md",
    "docs/upstream-sync-20261015.md",
    "docs/upstream-sync-20261017.md",
)
SECRET_PATTERNS = (
    re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----(?:\r?\n|\\n)+[A-Za-z0-9+/]{32,}"
    ),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
)


def excluded(name: str) -> bool:
    if any(name.startswith(item) if item.endswith("/") else name == item for item in EXCLUDED):
        return True
    parts = Path(name).parts
    if any(part in {"__pycache__", ".venv", ".pytest_cache"} for part in parts):
        return True
    if "node_modules" in parts and not name.startswith(
        "src/octop/infra/agents/experts/library/meituan-living-assistant/skills/meituan-deals/scripts/node_modules/"
    ):
        return True
    base = Path(name).name
    return (base == ".env" or base.startswith(".env.")) and base != ".env.example"


def candidates(root: Path) -> list[str]:
    raw = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
    )
    return sorted(set(raw.decode().strip("\0").split("\0")) - {""})


def export(root: Path, destination: Path) -> int:
    root = root.resolve()
    destination = destination.resolve()
    if destination.is_relative_to(root):
        raise ValueError("Choose a destination outside the working repository")
    if destination.exists():
        raise ValueError("Destination must not exist; exports never overwrite a previous snapshot")

    selected: list[tuple[Path, str]] = []
    skipped: list[str] = []
    for name in candidates(root):
        if excluded(name):
            skipped.append(name)
            continue
        source = root / name
        if source.is_symlink():
            resolved = source.resolve()
            if not resolved.is_relative_to(root) or excluded(str(resolved.relative_to(root))):
                raise ValueError(f"Symbolic link leaves public source scope: {name}")
            if source.readlink().is_absolute():
                raise ValueError(f"Absolute symbolic link must be made portable: {name}")
            data = str(source.readlink()).encode()
        elif source.is_file():
            data = source.read_bytes()
        else:
            continue  # Deleted tracked files are intentionally absent from this snapshot.
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):
            raise ValueError(f"Potential credential found; export stopped (value redacted): {name}")
        target_name = name
        if name.startswith(".github/workflows/"):
            target_name = name.replace(".github/workflows/", ".github/upstream-workflows/", 1)
            target_name += ".disabled"
        selected.append((source, target_name))

    destination.mkdir(parents=True)
    records: list[dict[str, str]] = []
    for source, target_name in selected:
        target = destination / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
        data = str(target.readlink()).encode() if target.is_symlink() else target.read_bytes()
        records.append(
            {
                "path": target_name,
                "type": "symlink" if target.is_symlink() else "file",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    archive_note = destination / ".github/upstream-workflows/README.md"
    archive_note.parent.mkdir(parents=True, exist_ok=True)
    archive_note.write_text(
        "# Archived upstream workflows\n\n"
        "These files are reference copies with an inactive .disabled extension. "
        "They do not run in GitHub Actions. Before enabling any release automation, "
        "adapt the package name, repository, images, signing identities and credentials. "
        "See ../../docs/open-source-release.md.\n",
        encoding="utf-8",
    )
    # Re-exporting an imported source repository regenerates this note and its hash.
    records = [
        record for record in records if record["path"] != str(archive_note.relative_to(destination))
    ]
    records.append(
        {
            "path": str(archive_note.relative_to(destination)),
            "sha256": hashlib.sha256(archive_note.read_bytes()).hexdigest(),
        }
    )
    # No absolute paths, original working-tree listings, repository credentials or Git history.
    manifest = {
        "project": "Octop Enterprise",
        "repository_name": "Octop-Enterprise",
        "repository_url": "https://github.com/colorAi/Octop-Enterprise",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_worktree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
        ),
        "git_history_included": False,
        "upstream_release_workflows_enabled": False,
        "excluded_policy": list(EXCLUDED),
        "excluded_file_count": len(skipped),
        "screening": "Limited credential-pattern screening; not a complete security or license audit",
        "files": records,
    }
    (destination / "PUBLIC_SOURCE.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="A new directory outside this repo"
    )
    args = parser.parse_args()
    count = export(Path(__file__).resolve().parent.parent, args.output)
    print(f"Exported {count} files to {args.output.resolve()}")


if __name__ == "__main__":
    main()
