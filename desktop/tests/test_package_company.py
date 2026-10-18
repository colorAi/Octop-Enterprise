"""Targeted offline packaging contract tests; no installers or downloads."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location(
    "package_company", Path(__file__).resolve().parents[1] / "package-company.py"
)
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


class CompanyPackageTest(unittest.TestCase):
    def test_uninstall_preserves_employee_workspace_files(self):
        script = (
            Path(__file__).resolve().parents[1] / "src/build/windows/nsis/project.nsi"
        ).read_text()
        uninstall = script.split('Section "uninstall"', 1)[1]
        self.assertNotRegex(uninstall, r'(?i)RMDir\s+/r\s+"?\$INSTDIR')
        self.assertIn('Delete "$INSTDIR\\${PRODUCT_EXECUTABLE}"', uninstall)
        self.assertIn('RMDir "$INSTDIR"', uninstall)

    def setUp(self):
        self.release = {
            "octop_version": "1.0.0",
            "source_commit": "fixture",
            "source_dirty": False,
            "device_protocol_version": 2,
            "company_protocol_version": 1,
            "python_source_sha256": "",
        }
        mock = patch.object(module, "release_identity", return_value=self.release)
        mock.start()
        self.addCleanup(mock.stop)

    def payload(self, root: Path) -> Path:
        source = root / "darwin-arm64"
        names = [
            "launch.py",
            "runtime/bin/python3",
            "packages/octop/company_runtime.py",
            "packages/octop_harness/__init__.py",
            "tools/ffmpeg",
            "tools/ffprobe",
            "packages/octop_harness-1.0.3.dist-info/METADATA",
        ]
        names += [f"packages/{m}/__init__.py" for m in module.RUNTIME_MODULES]
        for name in names:
            item = source / name
            item.parent.mkdir(parents=True, exist_ok=True)
            if name in ("runtime/bin/python3", "tools/ffmpeg", "tools/ffprobe"):
                item.write_bytes(
                    b"\xcf\xfa\xed\xfe" + (0x100000C).to_bytes(4, "little") + b"\0" * 56
                )
            elif name.endswith("METADATA"):
                item.write_text("Version: 1.0.3\n")
            else:
                item.write_text("company-runtime")
        app_metadata = source / "packages/octop-1.0.0.dist-info/METADATA"
        app_metadata.parent.mkdir()
        app_metadata.write_text("Version: 1.0.0\n")
        names.append(app_metadata.relative_to(source).as_posix())
        self.release["python_source_sha256"] = module.python_source_digest(
            source / "packages/octop"
        )
        (source / "company-runtime-build.json").write_text(
            json.dumps(
                {
                    "platform": "darwin",
                    "arch": "arm64",
                    "protocol_version": 1,
                    "harness_version": "1.0.3",
                    "octop_version": "1.0.0",
                    "smoke_test_passed": True,
                    "file_hashes": {
                        name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                        for name in names
                    },
                }
            )
        )
        return source

    def test_manifest_freezes_all_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.payload(root)
            archive = module.package_runtime(source, root / "payload.zip", "darwin", "arm64")
            with zipfile.ZipFile(archive) as bundle:
                manifest = json.loads(
                    bundle.read("Octop-company-darwin-arm64/company-runtime-manifest.json")
                )
                self.assertEqual(manifest["arch"], "arm64")
                self.assertEqual(manifest["octop_version"], "1.0.0")
                self.assertEqual(manifest["device_protocol_version"], 2)
                for name, digest in manifest["files"].items():
                    self.assertEqual(
                        hashlib.sha256(
                            bundle.read("Octop-company-darwin-arm64/" + name)
                        ).hexdigest(),
                        digest,
                    )

    def test_missing_tools_and_wrong_arch_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.payload(root)
            with self.assertRaisesRegex(ValueError, "different platform"):
                module.package_runtime(source, root / "bad.zip", "darwin", "amd64")
            (source / "tools/ffmpeg").unlink()
            with self.assertRaisesRegex(ValueError, "Missing offline runtime dependency"):
                module.package_runtime(source, root / "bad.zip", "darwin", "arm64")

    def test_changes_after_smoke_test_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.payload(root)
            (source / "packages/octop/company_runtime.py").write_text("modified source")
            with self.assertRaisesRegex(ValueError, "differs from"):
                module.package_runtime(source, root / "bad.zip", "darwin", "arm64")

    def test_unverified_payload_is_marked_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.payload(root)
            (source / "company-runtime-build.json").unlink()
            archive = module.package_runtime(source, root / "untested.zip", "darwin", "arm64")
            with zipfile.ZipFile(archive) as bundle:
                manifest = json.loads(
                    bundle.read("Octop-company-darwin-arm64/company-runtime-manifest.json")
                )
                self.assertFalse(manifest["target_smoke_verified"])

    def test_old_application_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.payload(root)
            next((source / "packages").glob("octop-*.dist-info/METADATA")).write_text(
                "Version: 0.0.1\n"
            )
            with self.assertRaisesRegex(ValueError, "version must match"):
                module.package_runtime(source, root / "bad.zip", "darwin", "arm64")


if __name__ == "__main__":
    unittest.main()
