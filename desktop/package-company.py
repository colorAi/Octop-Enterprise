#!/usr/bin/env python3
"""Build offline company clients on macOS with architecture-matched tools.

Requires Go, the project's pinned wails3 CLI, NSIS, and Apple's command-line tools.
Windows installers include Microsoft architecture-matched offline WebView2 installers.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path


def run(*args: str | Path, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True)


RUNTIME_MODULES = ("openpyxl", "docx", "pptx", "pypdf", "PIL", "pandas")


def python_source_digest(package: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(package).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def protocol_version(path: Path) -> int:
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PROTOCOL_VERSION"
            for target in node.targets
        ):
            return int(ast.literal_eval(node.value))
    raise ValueError(f"Protocol version missing: {path}")


def release_identity() -> dict:
    root = Path(__file__).resolve().parent.parent
    package = root / "src/octop"
    return {
        "octop_version": tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"],
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True)
        ),
        "python_source_sha256": python_source_digest(package),
        "device_protocol_version": protocol_version(package / "infra/devices/protocol.py"),
        "company_protocol_version": protocol_version(package / "infra/company_runtime/__init__.py"),
    }


def native_binary_target(path: Path) -> tuple[str, str]:
    with path.open("rb") as stream:
        header = stream.read(64)
        if header[:4] == b"\xcf\xfa\xed\xfe":
            cpu = struct.unpack("<I", header[4:8])[0]
            return "darwin", {0x100000C: "arm64", 0x1000007: "amd64"}.get(cpu, "unknown")
        if header[:2] == b"MZ" and len(header) == 64:
            stream.seek(struct.unpack("<I", header[60:64])[0])
            pe = stream.read(6)
            if pe[:4] == b"PE\0\0":
                return "windows", {0xAA64: "arm64", 0x8664: "amd64"}.get(
                    struct.unpack("<H", pe[4:])[0], "unknown"
                )
    raise ValueError(f"Not a supported native executable: {path}")


def package_runtime(source: Path, destination: Path, platform: str, arch: str) -> Path:
    """Validate a supplied offline payload and freeze its complete file manifest.

    No pip, download or ambient interpreter fallback is permitted during install.
    Target smoke tests are optional during cross-packaging and remain explicitly unverified.
    """
    source = source.resolve()
    release = release_identity()
    distributions = list((source / "packages").glob("octop-*.dist-info/METADATA"))
    if (
        len(distributions) != 1
        or Parser().parsestr(distributions[0].read_text()).get("Version")
        != release["octop_version"]
    ):
        raise ValueError("Offline Octop version must match the shell release version")
    if python_source_digest(source / "packages/octop") != release["python_source_sha256"]:
        raise ValueError("Offline Octop source differs from the current release source")
    suffix = ".exe" if platform == "windows" else ""
    required = [
        "launch.py",
        "packages/octop/company_runtime.py",
        "packages/octop_harness/__init__.py",
        "runtime/python.exe" if platform == "windows" else "runtime/bin/python3",
        "tools/ffmpeg" + suffix,
        "tools/ffprobe" + suffix,
    ]
    required += [f"packages/{module}/__init__.py" for module in RUNTIME_MODULES]
    for relative in required:
        if not (source / relative).is_file():
            raise ValueError(f"Missing offline runtime dependency: {source / relative}")
    for binary in (required[3], "tools/ffmpeg" + suffix, "tools/ffprobe" + suffix):
        if native_binary_target(source / binary) != (platform, arch):
            raise ValueError(
                f"Runtime native binary has a different platform or architecture: {binary}"
            )
    metadata = source / "packages/octop_harness-1.0.3.dist-info/METADATA"
    if not metadata.is_file() or "Version: 1.0.3\n" not in metadata.read_text():
        raise ValueError("Runtime requires the locked octop-harness 1.0.3 distribution")
    attestation_file = source / "company-runtime-build.json"
    attestation = json.loads(attestation_file.read_text()) if attestation_file.is_file() else {}
    if attestation:
        if attestation.get("octop_version") != release["octop_version"]:
            raise ValueError("Runtime smoke-test record has a different Octop version")
        if (
            attestation.get("platform"),
            attestation.get("arch"),
            attestation.get("protocol_version"),
        ) != (platform, arch, 1):
            raise ValueError(
                "Runtime smoke-test record has a different platform, architecture or protocol"
            )
        if (
            attestation.get("harness_version") != "1.0.3"
            or attestation.get("smoke_test_passed") is not True
        ):
            raise ValueError("Invalid optional runtime smoke-test record")
    if "company-runtime" not in (source / "launch.py").read_text():
        raise ValueError("Portable launcher does not implement the company-runtime entry point")
    files: dict[str, str] = {}
    entries: list[tuple[str, Path]] = []
    for item in sorted(source.rglob("*")):
        if item.is_symlink() and item.is_dir():
            raise ValueError(f"Materialize directory symlinks before packaging: {item}")
        if not item.is_file():
            continue
        if not item.resolve().is_relative_to(source):
            raise ValueError(f"Runtime symlink escapes package: {item}")
        relative = item.relative_to(source).as_posix()
        if relative == "company-runtime-manifest.json" or "__pycache__" in item.parts:
            continue
        with item.open("rb") as stream:
            files[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
        entries.append((relative, item))
    if attestation and attestation.get("file_hashes") != {
        key: value for key, value in files.items() if key != "company-runtime-build.json"
    }:
        raise ValueError(
            "Runtime payload changed after its target smoke test; run verify_company_runtime.py again"
        )
    manifest = {
        **release,
        "shell_version": release["octop_version"],
        "protocol_version": release["company_protocol_version"],
        "platform": platform,
        "arch": arch,
        "harness_version": "1.0.3",
        "target_smoke_verified": bool(attestation),
        "files": files,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"Octop-company-{platform}-{arch}"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, item in entries:
            # ZipFile.write follows file symlinks: installed packages have no links.
            archive.write(item, f"{prefix}/{relative}")
        archive.writestr(
            f"{prefix}/company-runtime-manifest.json", json.dumps(manifest, sort_keys=True)
        )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--webview2-dir", required=True, type=Path)
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        help="Offline payloads: darwin-arm64/, darwin-amd64/, windows-amd64/, windows-arm64/",
    )
    parser.add_argument(
        "--legacy-shell-only",
        action="store_true",
        help="Build an explicitly incomplete legacy shell without local task execution",
    )
    parser.add_argument("--wails", default="wails3")
    parser.add_argument("--sign-identity", help="Optional Developer ID Application identity")
    parser.add_argument("--platform", choices=("all", "darwin", "windows"), default="all")
    args = parser.parse_args()
    if not args.legacy_shell_only and not args.runtime_dir:
        parser.error(
            "Complete company packages require --runtime-dir; --legacy-shell-only produces an incomplete client"
        )
    root = Path(__file__).resolve().parent.parent
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage = output.parent / "build-company"
    if stage.exists():
        raise SystemExit(f"Build staging already exists: {stage}; use a fresh output parent")
    payloads: dict[tuple[str, str], Path] = {}
    if not args.legacy_shell_only:
        for platform in ("darwin", "windows"):
            if args.platform not in ("all", platform):
                continue
            for arch in ("arm64", "amd64"):
                payloads[platform, arch] = package_runtime(
                    (
                        args.runtime_dir / f"{platform}-{arch}"
                        if (args.runtime_dir / f"{platform}-{arch}").is_dir()
                        else args.runtime_dir / f"Octop-{platform}-{arch}"
                    ),
                    stage / "payloads" / f"Octop-{platform}-{arch}.zip",
                    platform,
                    arch,
                )
    run("node", root / "desktop/login/build.mjs", cwd=root)
    run(sys.executable, root / "desktop/build-dashboard.py", cwd=root)
    source = stage / "src"
    shutil.copytree(
        root / "desktop/src",
        source,
        ignore=shutil.ignore_patterns("bin", "bundled", "*.syso", ".DS_Store"),
    )
    build = source / "build"
    stamp = build / "stamp_version.py"
    run(
        args.wails,
        "generate",
        "icons",
        "-input",
        build / "appicon.png",
        "-windowsfilename",
        build / "windows/icon.ico",
        "-macfilename",
        build / "darwin/icons.icns",
        cwd=source,
    )
    run(
        args.wails,
        "generate",
        "icons",
        "-input",
        build / "appicon-macos.png",
        "-windowsfilename",
        stage / "mac-unused.ico",
        "-macfilename",
        build / "darwin/icons.icns",
        cwd=source,
    )
    common = ["go", "build", "-tags", "production,company", "-trimpath", "-buildvcs=false"]
    artifacts: list[Path] = []
    if args.platform in ("all", "darwin"):
        for arch in ("arm64", "amd64"):
            env = dict(
                os.environ,
                GOOS="darwin",
                GOARCH=arch,
                CGO_ENABLED="1",
                CGO_CFLAGS="-mmacosx-version-min=13.0",
                CGO_LDFLAGS="-mmacosx-version-min=13.0",
            )
            run(*common, "-ldflags=-w -s", "-o", stage / f"Octop-{arch}", cwd=source, env=env)
        dmgroot = stage / "dmg"
        app = dmgroot / "Octop.app"
        macos = app / "Contents/MacOS"
        resources = app / "Contents/Resources"
        macos.mkdir(parents=True)
        resources.mkdir()
        for arch in ("arm64", "amd64"):
            if ("darwin", arch) in payloads:
                shutil.copy2(payloads["darwin", arch], resources / f"Octop-darwin-{arch}.zip")
        run(
            "lipo",
            "-create",
            stage / "Octop-arm64",
            stage / "Octop-amd64",
            "-output",
            macos / "Octop",
            cwd=source,
        )
        info = plistlib.loads((build / "darwin/Info.plist").read_bytes())
        info.update(
            CFBundleVersion=version,
            CFBundleShortVersionString=version,
            LSMinimumSystemVersion="13.0",
            CFBundleGetInfoString="Octop company client",
        )
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        shutil.copy2(build / "darwin/icons.icns", resources / "icons.icns")
        os.symlink("/Applications", dmgroot / "Applications")
        if args.sign_identity:
            run(
                "codesign",
                "--force",
                "--options",
                "runtime",
                "--timestamp",
                "--sign",
                args.sign_identity,
                app,
                cwd=source,
            )
            run("codesign", "--verify", "--deep", "--strict", "--verbose=2", app, cwd=source)
        dmg = (
            output
            / f"Octop-company{'-legacy-shell' if args.legacy_shell_only else ''}-macos-universal-{version}.dmg"
        )
        run(
            "hdiutil",
            "create",
            "-volname",
            "Octop Company",
            "-srcfolder",
            dmgroot,
            "-format",
            "UDZO",
            dmg,
            cwd=source,
        )
        if args.sign_identity:
            run("codesign", "--timestamp", "--sign", args.sign_identity, dmg, cwd=source)
        run("hdiutil", "verify", dmg, cwd=source)
        artifacts.append(dmg)
    if args.platform in ("all", "windows"):
        nsis = build / "windows/nsis"
        run(
            sys.executable, stamp, "nsis-defines", version, nsis / "version_defines.nsh", cwd=source
        )
        run(
            sys.executable,
            stamp,
            "json",
            build / "windows/info.json",
            build / "windows/info.generated.json",
            version,
            cwd=source,
        )
        run(
            sys.executable,
            stamp,
            "manifest",
            build / "windows/wails.exe.manifest",
            build / "windows/wails.exe.generated.manifest",
            version,
            cwd=source,
        )
        for arch, suffix in (("amd64", "X64"), ("arm64", "ARM64")):
            webview = (
                args.webview2_dir.resolve() / f"MicrosoftEdgeWebView2RuntimeInstaller{suffix}.exe"
            )
            if not webview.is_file() or webview.stat().st_size < 50_000_000:
                raise SystemExit(f"Missing full offline WebView2 installer: {webview}")
            syso = source / f"wails_windows_{arch}.syso"
            run(
                args.wails,
                "generate",
                "syso",
                "-arch",
                arch,
                "-icon",
                build / "windows/icon.ico",
                "-info",
                build / "windows/info.generated.json",
                "-manifest",
                build / "windows/wails.exe.generated.manifest",
                "-out",
                syso,
                cwd=source,
            )
            binary = stage / f"Octop-windows-{arch}.exe"
            env = dict(os.environ, GOOS="windows", GOARCH=arch, CGO_ENABLED="0")
            try:
                run(*common, "-ldflags=-w -s -H windowsgui", "-o", binary, cwd=source, env=env)
            finally:
                syso.unlink(missing_ok=True)
            installer = (
                output
                / f"Octop-company{'-legacy-shell' if args.legacy_shell_only else ''}-windows-{arch}-{version}.exe"
            )
            runtime_args = []
            if ("windows", arch) in payloads:
                runtime_args = [
                    f"-DOCTOP_COMPANY_RUNTIME_ZIP={payloads['windows', arch]}",
                    f"-DOCTOP_COMPANY_RUNTIME_NAME=Octop-windows-{arch}.zip",
                ]
            run(
                "makensis",
                "-V2",
                *runtime_args,
                f"-DARG_WAILS_{arch.upper()}_BINARY={binary}",
                "-DWAILS_INSTALL_SCOPE=user",
                "-DOCTOP_COMPANY_OFFLINE",
                f"-DOCTOP_WEBVIEW2_OFFLINE_INSTALLER={webview}",
                f"-DINSTALLER_OUTFILE={installer}",
                "project.nsi",
                cwd=nsis,
            )
            artifacts.append(installer)
    (output / "SHA256SUMS.txt").write_text(
        "".join(
            f"{hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()}  {p.name}\n"
            for p in artifacts
        )
    )
    (output / "company-package-status.json").write_text(
        json.dumps(
            {
                **release_identity(),
                "version": version,
                "protocol_version": 1,
                "local_runtime_included": not args.legacy_shell_only,
                "dashboard_included": True,
                "dashboard_zip_sha256": hashlib.sha256((root / "desktop/src/assets/company-dashboard.zip").read_bytes()).hexdigest(),
                "built": True,
                "deployed": False,
                "real_device_verified": False,
                "payloads": [
                    {
                        "platform": key[0],
                        "arch": key[1],
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for key, path in payloads.items()
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    for artifact in artifacts:
        print(artifact, flush=True)


if __name__ == "__main__":
    main()
