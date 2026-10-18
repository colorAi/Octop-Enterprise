#!/usr/bin/env python3
"""Local, focused enterprise checks; optional native payload manifests remain separate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_TESTS = [
    "tests/unit/backup",
    "tests/unit/deployment/test_enterprise_backup.py",
    "tests/unit/company_runtime",
    "tests/unit/backend/test_isolation.py",
    "tests/unit/agents/test_private_instance.py",
    "tests/unit/agents/test_media_generation_settings.py",
    "tests/unit/agents/test_media_openai.py",
    "tests/unit/api/test_enterprise_health.py",
    "tests/unit/communication",
    "desktop/tests/test_package_company.py",
]
FRONTEND_TESTS = [
    "src/api/desktopRuntime.test.ts",
    "src/api/communicationRealtime.test.ts",
    "src/pages/Messaging/hooks/communicationClient.test.ts",
    "src/components/DocumentPreviewCore.docxSanitize.test.ts",
    "src/components/DocumentPreviewCore.pdfSkeleton.test.tsx",
    "src/components/documentDependencyCompatibility.test.ts",
    "src/routes/controlAdminPath.test.ts",
]


def run(args: list[str], cwd: Path = ROOT) -> None:
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def check_manifests(root: Path) -> None:
    expected = {(p, a) for p in ("darwin", "windows") for a in ("amd64", "arm64")}
    spec = importlib.util.spec_from_file_location(
        "package_company", ROOT / "desktop/package-company.py"
    )
    assert spec is not None and spec.loader is not None
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    expected_release = package.release_identity()
    manifests = list(root.glob("*/company-runtime-manifest.json"))
    seen = set()
    identities = set()
    for path in manifests:
        record = json.loads(path.read_text())
        target = (record["platform"], record["arch"])
        if target in seen or target not in expected:
            raise ValueError(f"Unexpected or duplicate target: {target}")
        seen.add(target)
        identity = tuple(
            record[key]
            for key in (
                "octop_version",
                "shell_version",
                "source_commit",
                "python_source_sha256",
                "harness_version",
                "device_protocol_version",
                "company_protocol_version",
            )
        )
        if identity[0] != identity[1] or identity[4:] != ("1.0.2", 2, 1):
            raise ValueError(f"Incompatible release manifest: {path}")
        for key in ("octop_version", "source_commit", "python_source_sha256"):
            if record[key] != expected_release[key]:
                raise ValueError(f"Manifest differs from current source: {key}: {path}")
        for relative, digest in record["files"].items():
            payload_file = (path.parent / relative).resolve()
            if not payload_file.is_relative_to(path.parent.resolve()):
                raise ValueError(f"Payload path escapes its root: {relative}")
            with payload_file.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError(f"Payload changed: {relative}")
        identities.add(identity)
        print(
            f"{target}: target_smoke_verified={record['target_smoke_verified']}; native installation/signing must be recorded separately"
        )
    if seen != expected or len(identities) != 1:
        raise ValueError("Require all four targets from one source release")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--section",
        choices=("all", "backend", "frontend", "desktop", "communication", "packaging"),
        default="all",
    )
    parser.add_argument("--manifest-dir", type=Path)
    args = parser.parse_args()
    if args.section in ("all", "backend"):
        run(["uv", "run", "pytest", "-q", *BACKEND_TESTS])
    if args.section in ("all", "frontend"):
        run(["npm", "exec", "--", "tsc", "-b"], ROOT / "dashboard")
        run(["npm", "exec", "--", "vitest", "run", *FRONTEND_TESTS], ROOT / "dashboard")
    if args.section in ("all", "desktop"):
        run(["node", "desktop/login/build.mjs"])
        run(["go", "test", "./internal/company", "./internal/companyruntime"], ROOT / "desktop/src")
    if args.section in ("all", "communication"):
        run(
            [
                "go",
                "test",
                "./internal/itest",
                "./internal/auth",
                "./internal/mediasvc",
                "./internal/store",
                "./internal/syncsrv",
            ],
            ROOT / "services/communication",
        )
    if args.section in ("all", "packaging"):
        run(
            [
                "uv",
                "run",
                "ruff",
                "check",
                "desktop/package-company.py",
                "desktop/portable/verify_company_runtime.py",
                "desktop/tests/test_package_company.py",
                "docker/enterprise",
                "tools/check_enterprise.py",
            ]
        )
        run(["uv", "run", "pytest", "-q", "desktop/tests/test_package_company.py"])
    if args.manifest_dir:
        check_manifests(args.manifest_dir)
    else:
        print(
            "Native four-architecture payload manifests not supplied; release acceptance remains unverified."
        )


if __name__ == "__main__":
    main()
