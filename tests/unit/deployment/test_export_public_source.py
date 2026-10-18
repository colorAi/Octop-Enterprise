"""Public exports include current source while excluding private material and automation."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[3] / "tools/export_public_source.py"
_SPEC = importlib.util.spec_from_file_location("export_public_source", _PATH)
assert _SPEC and _SPEC.loader
exporter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(exporter)

_CHECK_SPEC = importlib.util.spec_from_file_location(
    "check_public_source", _PATH.with_name("check_public_source.py")
)
assert _CHECK_SPEC and _CHECK_SPEC.loader
checker = importlib.util.module_from_spec(_CHECK_SPEC)
_CHECK_SPEC.loader.exec_module(checker)


def _repo(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Export Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    (root / "README.md").write_text("test\n")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)


def test_export_includes_dirty_and_untracked_source_but_not_private_material(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _repo(root)
    (root / "README.md").write_text("current content\n")
    files = {
        "src/new.py": "current source\n",
        "services/communication/server.go": "package main\n",
        "dashboard/vendor/required.tgz": "required dependency\n",
        "dashboard/src/components/ClientConfigUpdatePrompt/index.tsx": "export default function Prompt() {}\n",
        "desktop/src/company_ui.go": "package main\n",
        "desktop/build-dashboard.py": "# Build assets from source\n",
        "desktop/src/assets/company-dashboard.zip": "generated dashboard\n",
        "desktop/src/assets/company-dashboard.json": "generated asset hashes\n",
        "desktop/src/assets/login.bundle.js": "generated login script\n",
        "desktop/src/assets/login.bundle.css": "generated login styles\n",
        ".github/workflows/release.yml": "name: upstream release\n",
        ".env": "private configuration\n",
        "output/receipt.json": "private receipt\n",
        "docs/company-desktop-runtime-delivery.md": "internal deployment\n",
        "docs/upstream-sync-20261017.md": "internal delivery receipt\n",
        "docs/user-guide.html": "unmaintained generated guide\n",
        "docs/octop-introduction.html": "upstream personal edition introduction\n",
        "fnos/docker/app/docker/docker-compose.yaml": "image: ghcr.io/tencentcloud/octop:1.0.2b5\n",
        "scripts/build-fpk.sh": "# upstream FPK packager\n",
        "scripts/fnos/common.sh": "# upstream FPK helper\n",
        "tests/unit/test_fnos_docker_persist.py": "# upstream FPK tests\n",
        "scripts/release_download_links.py": 'GITHUB_REPO = "TencentCloud/Octop"\n',
        "tests/unit/test_release_download_links.py": "# upstream binary assets\n",
        "dashboard/public/experts/avatars/office-automation.svg": "excluded portrait\n",
        ".playwright-cli/session.yaml": "private browser state\n",
        "src/octop/infra/agents/experts/library/office-automation/skills/docx/SKILL.md": "restricted\n",
    }
    for name, data in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
    target = tmp_path / "public"
    exporter.export(root, target)
    assert (target / "README.md").read_text() == "current content\n"
    for name in (
        "src/new.py",
        "services/communication/server.go",
        "dashboard/vendor/required.tgz",
        "dashboard/src/components/ClientConfigUpdatePrompt/index.tsx",
        "desktop/src/company_ui.go",
        "desktop/build-dashboard.py",
    ):
        assert (target / name).is_file()
    for name in files:
        if exporter.excluded(name) or name.startswith(".github/workflows/"):
            assert not (target / name).exists()
    assert not (target / ".git").exists()
    assert (target / ".github/upstream-workflows/release.yml.disabled").exists()
    manifest = json.loads((target / "PUBLIC_SOURCE.json").read_text())
    assert manifest["source_worktree_dirty"] is True
    assert manifest["git_history_included"] is False
    assert str(root) not in json.dumps(manifest)
    with pytest.raises(ValueError, match="must not exist"):
        exporter.export(root, target)


def test_export_rejects_private_key_and_does_not_write_output(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    (root / "key.txt").write_text("-----BEGIN " + "PRIVATE KEY-----\n" + "A" * 64 + "\n")
    target = tmp_path / "public"
    with pytest.raises(ValueError, match="value redacted"):
        exporter.export(root, target)
    assert not target.exists()


def test_export_cannot_target_its_own_repository(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    with pytest.raises(ValueError, match="outside"):
        exporter.export(root, root / "public")


def test_clean_import_can_be_reexported_without_nested_manifest_or_duplicate_records(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _repo(root)
    imported = tmp_path / "imported"
    exporter.export(root, imported)
    subprocess.run(["git", "init", "-q", str(imported)], check=True)
    subprocess.run(["git", "-C", str(imported), "config", "user.name", "Export Test"], check=True)
    subprocess.run(
        ["git", "-C", str(imported), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(imported), "add", "."], check=True)
    subprocess.run(["git", "-C", str(imported), "commit", "-qm", "clean import"], check=True)
    final = tmp_path / "final"
    exporter.export(imported, final)
    manifest = json.loads((final / "PUBLIC_SOURCE.json").read_text())
    paths = [record["path"] for record in manifest["files"]]
    assert manifest["source_worktree_dirty"] is False
    assert "PUBLIC_SOURCE.json" not in paths
    assert len(paths) == len(set(paths))
    assert checker.check(final) == []


def test_export_validation_catches_missing_images_experts_and_modified_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _repo(root)
    (root / "README.md").write_text("# Guide\n[Start](#guide)\n![Missing](docs/missing.png)\n")
    skill = root / "src/octop/infra/agents/experts/library/example/skills/help/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("octop agent from-expert missing-expert\n")
    target = tmp_path / "public"
    exporter.export(root, target)
    errors = checker.check(target)
    assert any("broken local link/image" in error for error in errors)
    assert any("missing example expert" in error for error in errors)
    assert not any("broken anchor" in error for error in errors)
    (target / "README.md").write_text("[Bad](#absent)\n")
    errors = checker.check(target)
    assert any("hash mismatch: README.md" in error for error in errors)
    assert any("broken anchor" in error for error in errors)


@pytest.mark.parametrize(
    "name,content",
    [
        (
            "fnos/docker/app/docker/docker-compose.yaml",
            "image: ghcr.io/tencentcloud/octop:1.0.2b5\n",
        ),
        ("scripts/downloads.py", 'GITHUB_REPO = "TencentCloud/Octop"\n'),
        ("desktop/src/build/config.yml", 'productIdentifier: "com.tencent.octop"\n'),
        ("desktop/src/build/stamp_version.py", r'identity = "com\.tencent\.octop"'),
        (
            "docker/Dockerfile",
            'LABEL org.opencontainers.image.source="https://github.com/TencentCloud/orca"\n',
        ),
    ],
)
def test_distribution_check_rejects_upstream_installation_targets(tmp_path, name, content):
    target = tmp_path / name
    target.parent.mkdir(parents=True)
    target.write_text(content)
    errors = checker.check_distribution(tmp_path)
    assert any("upstream distribution reference" in error for error in errors)


def test_distribution_check_preserves_attribution_but_requires_installer_entry_guard(tmp_path):
    (tmp_path / "README.md").write_text("Based on https://github.com/TencentCloud/Octop\n")
    script = tmp_path / "scripts/install.sh"
    script.parent.mkdir()
    upstream = 'OCTOP_REPO="https://github.com/TencentCloud/Octop.git"\n'
    script.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' 'Octop Ent: upstream installer is disabled for this distribution.' >&2\n"
        "exit 1\n" + upstream
    )
    assert checker.check_distribution(tmp_path) == []
    script.write_text("#!/usr/bin/env bash\n" + upstream)
    assert any(
        "upstream distribution reference" in error for error in checker.check_distribution(tmp_path)
    )


@pytest.mark.parametrize(
    "name,content",
    [
        (
            "install-octop.sh",
            '#!/usr/bin/env bash\nOCTOP_REPO="${OCTOP_REPO:-https://github.com/TencentCloud/Octop.git}"\n',
        ),
        (
            "install.ps1",
            '$OctopRepo = if ($env:OCTOP_REPO) { $env:OCTOP_REPO } else { "https://github.com/TencentCloud/Octop.git" }\n',
        ),
    ],
)
def test_legacy_installers_cannot_lose_their_guard_by_obscuring_the_upstream_url(
    tmp_path, name, content
):
    script = tmp_path / "scripts" / name
    script.parent.mkdir()
    script.write_text(content)
    assert any("entry guard missing" in error for error in checker.check_distribution(tmp_path))


@pytest.mark.skipif(os.name != "posix", reason="POSIX symbolic-link fixture")
def test_export_preserves_internal_links_but_rejects_external_links(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    (root / "alias.md").symlink_to("README.md")
    target = tmp_path / "public"
    exporter.export(root, target)
    assert (target / "alias.md").is_symlink()
    assert (target / "alias.md").read_text() == "test\n"
    (root / "external").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="leaves public source"):
        exporter.export(root, tmp_path / "blocked")
