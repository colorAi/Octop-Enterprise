# Provenance and third-party licenses

[中文](NOTICE.md)

> This English translation is provided for convenience. If it differs from the Chinese original, the Chinese original controls.

## Octop

Octop Enterprise is developed from [TencentCloud/Octop](https://github.com/TencentCloud/Octop), retaining upstream `Copyright (c) 2026 Octop` and the MIT license text. Enterprise desktop local execution, enterprise service adaptation, internal communication and other changes are maintained by Octop Enterprise contributors; the project is not an official Tencent enterprise release.

This project is based on upstream `eb280112` (1.0.2b6), with selected changes up to `f79015b078c4d5d4c309a1bf6f3cd39ac06cc18f`. `PUBLIC_SOURCE.json` in the source release records the source commit, worktree state and per-file digests at export; upstream commits identify provenance, not a complete merge.

## Own communication service

`services/communication/` derives from Miaox communication code developed by the project rights holder and uses the [MIT License](services/communication/LICENSE) in that directory.

`SOURCE.json` in that directory records the commit and digests from the initial extraction; later file digests in a release follow `PUBLIC_SOURCE.json` in the source package.

## Third-party components

The root MIT license does not replace third-party licenses. The table below indexes the main sources and licenses; it is not a complete dependency SBOM.

| Content | Source or license location |
| --- | --- |
| Octop Harness, Gateway, Memory, Browser | Package metadata, licenses and the corresponding TencentCloud upstream repositories |
| Engineering and design experts | `LICENSE` in each expert directory (Apache-2.0); keep bundled provenance notices |
| SheetJS CE 0.20.3 | `dashboard/vendor/README.md`; Apache-2.0 license and third-party notices in the tarball |
| Python, Node.js, Go and language dependencies | Licenses bundled with the runtimes and dependency packages; versions recorded in uv.lock, package-lock.json and go.sum |
| Wails, system WebView and other desktop dependencies | Each project's license and distribution requirements |

When producing binaries, Docker images or client installers, include the licenses of the components actually shipped. FFmpeg licensing depends on build options; WebView2, Python and other runtimes cannot be replaced by this project's MIT label alone.

## Material not in the public export

The docx, pptx, pdf and xlsx skills in the `office-automation` expert are subject to Anthropic license restrictions on copying, derivation and redistribution and are not included in this project's public source release.

Document tooling in this project is implemented with independent dependencies such as python-docx, python-pptx, pypdf and openpyxl; each dependency follows its own license.
