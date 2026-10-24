# Octop Enterprise source release notes

[中文](open-source-release.md)

This document is for maintainers and describes source export, verification and release requirements for [Octop Enterprise](https://github.com/colorAi/Octop-Enterprise). The current source version is `1.0.1b2`; the main project and own communication module use MIT.

This repository provides source code; server images and Windows/macOS enterprise clients are generated according to the corresponding build documents. Release material is distributed through [this repository's Releases](https://github.com/colorAi/Octop-Enterprise/releases); binary installers require separate target-platform verification and records of signing and notarization status.

## Source scope

- Use Chinese for the default homepage and retain the English edition, English and Chinese employee guides, contribution guide, security policy, and deployment and build instructions.
- Keep the original MIT license and contributor notices in the root and the communication service. See [NOTICE](../NOTICE_EN.md) for third-party sources and distribution scope.
- This distribution disables the online update CLI/API, upstream one-click installers and `make publish` / `publish-test`; install and upgrade from this repository's source.
- The public copy excludes Git history, local tool configuration, runtime environment files, browser sessions, deployment records and build artifacts.
- The `office-automation` expert and its avatars are excluded because bundled document skills restrict redistribution. Tests and examples use experts included publicly; independent document-processing libraries and local tools are unaffected.
- Employee quick start uses [Markdown](user-guide_EN.md); the source release excludes generated `docs/user-guide.html` and the upstream personal-edition introduction `docs/octop-introduction.html`.
- The source release excludes FnOS packaging tools and the upstream release-asset download-table generator.
- Upstream workflows are stored in `.github/upstream-workflows/` with the `.disabled` extension. Before enabling automation, configure this project's repository, package names, images, permissions and signing.

## Export and verification

All changes between formal releases accumulate in the enterprise repository and share one current pending-release entry point. Export candidate source from the complete worktree, including uncommitted changes and new tests from earlier fixes. Record verification status separately instead of splitting fixes into parallel pending releases. Keep older snapshots only for traceability.

Run in the development repository; the output must be a new directory outside the repository that does not already exist:

```sh
python3 tools/export_public_source.py --output /absolute/new-directory/Octop-Enterprise
python3 tools/check_public_source.py --root /absolute/new-directory/Octop-Enterprise
```

The export tool uses Git-tracked and unignored files as candidates and copies the current worktree, including uncommitted changes. It does not modify the Git index, branches or remotes. Use a new output directory for each source snapshot.

To refresh the pending-release entry point, export to a new temporary directory and verify it first, then update the source, archive, change list and checksums at that entry point. Retain the previous snapshot as history. Subsequent changes still belong to the same pending release. Before the final build, compare the complete worktree with the candidate manifest again to confirm that nothing is missing; build installers and runtime payloads from that final source together.

`PUBLIC_SOURCE.json` records the source HEAD, `source_worktree_dirty`, export scope and per-file SHA-256. `source_worktree_dirty=true` means the snapshot includes uncommitted changes and must be identified by the per-file manifest and source archive SHA-256. Formal releases export from a clean worktree at a specified commit and keep the corresponding tag, manifest and checksums.

The verification tool checks manifest digests, local links, anchors and images in the main documents, expert prompt files and experts referenced by command examples, and blocks upstream images, download entry points and packaging identity leakage. Archived inactive upstream workflows, provenance acknowledgments and old installers with valid entry guards are not treated as usable distribution entry points. Symbolic links may only point to relative paths inside the release scope; digests cover link text. Built-in credential pattern screening is limited and is not a complete security or license audit.

## Build prerequisites

The server supports Linux + Docker Engine + Compose; commands are in [Enterprise deployment and recovery](../docker/enterprise/README_EN.md). The build network must reach base images and dependencies; no complete offline server installer is currently provided.

Client builds are in [Desktop builds](../desktop/README.md). Enterprise installers target Windows amd64/arm64 and macOS 13+ universal; support status follows release verification. `desktop/package-company.py` reads Git HEAD and worktree state and requires a local repository with a resolvable `HEAD`. Source archives do not contain `.git`; create a source commit first or use a Git checkout for the corresponding release. Docker builds do not have this Git prerequisite.

Login bundles, `company-dashboard.zip` and generated manifests are not distributed with the source. The full desktop packager builds these from source; building the shell alone requires generating the resources according to the desktop documentation.

## Repository and security entry points

- Issues and suggestions: [this repository's Issues](https://github.com/colorAi/Octop-Enterprise/issues).
- Security reports: follow [SECURITY](../SECURITY_EN.md) for the private channel; do not post vulnerability details in an issue.
- Contribution branches: open pull requests from a working branch against this repository's `main`; see [Contributing](../CONTRIBUTING_EN.md).
- When creating a new distribution repository, use a verified source snapshot; retain provenance through NOTICE and PUBLIC_SOURCE.

## Release checklist

- Identity of the corresponding commit or candidate snapshot, source archive and SHA-256; directory and archive contents match file by file.
- Version, Harness version, main and communication schema, and device/company-runtime protocol requirements.
- Build platforms and architectures, dependency versions, build commands, server deployment and client installation instructions.
- Platforms and architectures actually verified, plus unverified installation, signing, notarization, capacity, upgrade and recovery items.
- Known limitations, data boundaries, pre-upgrade backup and rollback steps; planned capabilities such as group bots are not listed as delivered.
- LICENSE, NOTICE, communication service and third-party licenses. When distributing installers, include the licenses for FFmpeg, WebView2, Python and other components actually shipped; a source-package check does not replace a binary distribution check.
- If images or installers are provided, publish the unique tag/digest, architectures, SHA-256, signing and notarization status; do not reuse the same formal tag for different source.

Desktop bundles and the Windows assembly use `io.github.colorai.octop.enterprise`; Docker image metadata points to this repository. Binary releases must specify image addresses, signing identity and upgrade steps; keep employee working folders, account configuration and task receipts before upgrading. Python import paths, CLI commands, environment variables and database names keep the `octop` convention.
