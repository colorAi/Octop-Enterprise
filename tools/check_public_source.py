#!/usr/bin/env python3
"""Check a source export's hashes, maintained documentation and expert references."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

LIBRARY = Path("src/octop/infra/agents/experts/library")
LINK = re.compile(r"!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")
FENCE = re.compile(r"^(`{3,}|~{3,}).*?\n.*?^\1\s*$", re.MULTILINE | re.DOTALL)
SOURCE_ONLY_EXCLUSIONS = (
    "fnos/",
    "scripts/build-fpk.sh",
    "scripts/fnos/",
    "tests/unit/test_fnos_docker_persist.py",
    "scripts/release_download_links.py",
    "tests/unit/test_release_download_links.py",
    "docs/octop-introduction.html",
)
INHERITED_INSTALLERS = {
    "scripts/install.sh",
    "scripts/install-octop.sh",
    "scripts/install.ps1",
    "scripts/install.bat",
}
UPSTREAM_DISTRIBUTION = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:ghcr\.io|docker\.io)/tencentcloud/(?:octop|orca)\b",
        r"https://github\.com/TencentCloud/(?:Octop|orca)/releases/(?:download|latest)\b",
        r"(?:GITHUB_REPO|OCTOP_REPO)\s*=\s*[\"']?(?:https://github\.com/)?TencentCloud/(?:Octop|orca)\b",
        r"com\.tencent\.(?:octop|orca)\b",
        r"maintainer\s*=\s*[\"']?TencentCloud\b",
        r"OrcaKit Team|https://github\.com/TencentCloud/orca\b",
    )
)


def disabled_installer(name: str, text: str) -> bool:
    lines = text.splitlines()
    notice = "upstream installer is disabled for this distribution."
    if name in {"scripts/install.sh", "scripts/install-octop.sh"}:
        return (
            len(lines) >= 3
            and lines[0] == "#!/usr/bin/env bash"
            and lines[1].startswith("printf ")
            and notice in lines[1]
            and lines[2] == "exit 1"
        )
    if name == "scripts/install.ps1":
        return bool(
            lines
            and lines[0].startswith(('throw "Octop Enterprise:', 'throw "Octop Ent:'))
            and notice in lines[0]
        )
    if name == "scripts/install.bat":
        return (
            len(lines) >= 3
            and lines[0] == "@echo off"
            and lines[1].startswith(("echo Octop Enterprise:", "echo Octop Ent:"))
            and notice in lines[1]
            and lines[2] == "exit /b 1"
        )
    return False


def check_distribution(root: Path) -> list[str]:
    errors: list[str] = []
    for name in SOURCE_ONLY_EXCLUSIONS:
        if (root / name).exists():
            errors.append(f"unsupported distribution material: {name}")
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if not (
            name.startswith(
                ("fnos/", "scripts/", "desktop/", "docker/", "docs/", ".github/workflows/")
            )
            or name in {"README.md", "README_CN.md", "README_EN.md"}
        ):
            continue
        if path.suffix not in {
            ".md",
            ".py",
            ".sh",
            ".ps1",
            ".bat",
            ".yml",
            ".yaml",
            ".json",
            ".plist",
            ".manifest",
            ".nsh",
            ".nsi",
            ".html",
            ".toml",
        } and path.name not in {"Dockerfile", "manifest"}:
            continue
        if "/tests/" in name:
            continue  # Regression fixtures intentionally contain forbidden examples.
        text = path.read_text()
        if name in INHERITED_INSTALLERS:
            if disabled_installer(name, text):
                continue
            errors.append(f"upstream installer entry guard missing: {name}")
        for line_number, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line.replace(r"\.", ".")) for pattern in UPSTREAM_DISTRIBUTION):
                errors.append(f"upstream distribution reference: {name}:{line_number}")
    return errors


def anchors(path: Path) -> set[str]:
    text = FENCE.sub("", path.read_text())
    result = set(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', text))
    counts: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        heading = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", heading)
        heading = re.sub(r"<[^>]*>", "", heading)
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        index = counts.get(slug, 0)
        counts[slug] = index + 1
        result.add(f"{slug}-{index}" if index else slug)
    return result


def check(root: Path) -> list[str]:
    root = root.resolve()
    errors = check_distribution(root)
    manifest_path = root / "PUBLIC_SOURCE.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        expected = {item["path"]: item for item in manifest["files"]}
        actual = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() or path.is_symlink()
        } - {"PUBLIC_SOURCE.json"}
        for name in sorted(expected.keys() - actual):
            errors.append(f"missing manifest file: {name}")
        for name in sorted(actual - expected.keys()):
            errors.append(f"unlisted export file: {name}")
        for name in sorted(expected.keys() & actual):
            path = root / name
            if not path.resolve().is_relative_to(root):
                errors.append(f"file leaves export: {name}")
                continue
            data = str(path.readlink()).encode() if path.is_symlink() else path.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected[name]["sha256"]:
                errors.append(f"hash mismatch: {name}")
    else:
        errors.append("PUBLIC_SOURCE.json missing; run against an exported source snapshot")

    docs = set(root.glob("*.md")) | set((root / "docs").glob("*.md"))
    docs |= {
        root / name
        for name in (
            "docker/README.md",
            "docker/README_CN.md",
            "docker/enterprise/README.md",
            "docker/enterprise/README_EN.md",
            "desktop/README.md",
            "desktop/portable/README.md",
            "services/communication/README.md",
            "scripts/README.md",
            "fnos/README.md",
        )
    }
    for path in sorted(docs):
        if not path.is_file():
            continue
        text = FENCE.sub("", path.read_text())
        for match in LINK.finditer(text):
            target = urlsplit(match.group(1).strip("<>"))
            if target.scheme or target.netloc:
                continue
            local = (path.parent / unquote(target.path)).resolve() if target.path else path
            if not local.is_relative_to(root) or not local.exists():
                errors.append(f"broken local link/image: {path.relative_to(root)} -> {target.path}")
            elif (
                target.fragment
                and local.suffix == ".md"
                and unquote(target.fragment) not in anchors(local)
            ):
                errors.append(f"broken anchor: {path.relative_to(root)} -> {match.group(1)}")

    library = root / LIBRARY
    for path in sorted(library.glob("*/manifest.json")):
        manifest = json.loads(path.read_text())
        for name in manifest.get("prompt_files", []):
            target = (path.parent / name).resolve()
            if not target.is_relative_to(path.parent.resolve()) or not target.is_file():
                errors.append(f"missing expert prompt: {path.parent.name}/{name}")
    for path in sorted(library.rglob("*.md")):
        for expert_id in re.findall(
            r"octop agent from-expert\s+([a-z][a-z0-9-]*)", path.read_text()
        ):
            if not (library / expert_id / "manifest.json").is_file():
                errors.append(f"missing example expert: {path.relative_to(root)} -> {expert_id}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    errors = check(args.root)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print("Source manifest, documentation, expert references and distribution metadata: OK")


if __name__ == "__main__":
    main()
