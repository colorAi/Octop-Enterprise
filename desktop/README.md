# Octop Enterprise desktop (Wails v3 + green portable)

> For employee setup and everyday use, see [the employee guide](../docs/enterprise-client_EN.md). This document covers building the enterprise client and the base-mode desktop packages from source.

All desktop-client code lives here. This is **not** `src/octop/infra/desktop`
(remote desktop streaming).

| Path | Role |
|------|------|
| [`portable/`](portable/) | Portable Python runtime and application packaging |
| [`src/`](src/) | Wails v3 shell: load bundled zip, spawn Octop, tray/settings |
| [`package-release.sh`](package-release.sh) | Native end-to-end portable + Wails release build |

## Data directory

The full local edition shares the Octop CLI/server defaults. Enterprise runtime data is separate:

- `OCTOP_HOME` → `~/.octop` (or the existing `OCTOP_HOME` env)
- Green runtime extract → `~/.octop/portable/`
- Shell prefs → `~/.octop/desktop-settings.json`
- Enterprise verified tool payloads → `~/.octop/company-runtime/tools/<payload-sha256>/`
- Enterprise account cache/receipts → `~/.octop/company-runtime/accounts/<company-and-owner-sha256>/`
- Company mode never boots an independent local Python backend or uses `~/.octop/portable/`; the loopback UI listener only serves the packaged dashboard and proxies company APIs.

## Build green zip

From repo root:

```bash
make -f desktop/portable/Makefile green
```

This builds the base-mode portable server package. Use `package-company.py`
below to build enterprise employee installers. Run packaging commands locally;
the reference workflows in `.github/upstream-workflows/` are inactive.

## Build a complete desktop release

Run the end-to-end script on the matching native host. It builds the Dashboard,
creates and verifies the portable runtime, embeds it into Wails, and produces
the final native package:

```bash
desktop/package-release.sh
# Reuse an existing desktop/portable/release/Octop-portable-<plat>-<version>.zip:
desktop/package-release.sh darwin-arm64 --reuse-portable
```

Base-mode release scripts run on the matching native host. For enterprise
packaging, `package-company.py` can also assemble Windows installers on macOS
using supplied Windows runtime payloads.
Windows packaging also needs [NSIS](https://nsis.sourceforge.io/) (`makensis`)
so the `.exe` is an installer rather than a portable single-file binary.

## Build the Wails shell

Build the bundled employee login assets with `node desktop/login/build.mjs` and the complete dashboard with `python3 desktop/build-dashboard.py` from
the repository root before a direct Go build. `package-company.py` does both automatically.

### Employee workspace

Windows and macOS use the same configurable client. A fresh installation opens
**Employee sign in**, with account/password fields and a **Server address**
accordion below the login button. The address expands inside that same card.
Enter the administrator-provided address and sign in; there is no separate
connection screen or server-settings window. Addresses are saved automatically.
Bare IP/hostname:port values use HTTPS; intranet HTTP requires an explicit `http://` prefix. Different
companies use the same installer. Complete company packages contain an architecture-matched portable Python, pinned harness, document libraries and FFmpeg. Company mode starts only the stdio enterprise worker, including when an older settings file selected independent local mode.

The server must enable `device_channel_enabled` and `company_runtime_enabled`
(or `OCTOP_DEVICE_CHANNEL_ENABLED=1` and `OCTOP_COMPANY_RUNTIME_ENABLED=1`)
for company desktop tasks. Both HTTP/WS and HTTPS/WSS
are supported. HTTP needs no certificate; HTTPS uses normal system certificate
validation. Neither mode requires a public address. Contacts and employee messaging are
available after signing in even when the local runtime is unavailable. Password/LDAP authentication and the server's
configured CAPTCHA are checked by the existing authentication endpoint.
Login errors stay on the same form. A successful sign-in transfers the session
only to the selected origin and a one-use navigation nonce, then enters `/chat`.
The complete dashboard, including lazy-loaded JS/CSS, fonts and public images, is embedded in `assets/company-dashboard.zip`. The native shell serves it on a company-specific `127.0.0.1` origin and forwards only `/api/` traffic (including WebSockets and streamed bodies) to the selected company server with normal TLS validation. No dashboard resources are fetched from R2 or the company server, and the desktop build does not register a Service Worker.

Remembered sessions use that company's stable loopback WebView origin. Upgrading from the remote-page client requires signing in once because the browser origin changed; passwords are never saved. Origin/port records under `~/.octop/company-ui/` contain no account credentials. The listener binds only loopback, requires a native bootstrap capability cookie, rejects foreign Host/Origin requests, and is closed with its streaming connections on a server switch or exit. An offline saved server returns to the editable login page.

The bootstrap commits a local HTML document before navigating to the login handoff page. Do not replace it with an HTTP redirect: macOS WebKit can retain the initial `wails://` navigation's cross-site context and omit the Strict cookie, showing `Forbidden` instead of restoring a valid saved session. The bootstrap preserves the handoff fragment and keeps the existing cookie, Host and Origin checks.

After sign-in the dashboard automatically pairs this computer with the employee
session. On first use of an account it creates and authorizes a default folder:
Windows first tries `Workspace` under the executable directory, then falls back
to `Octop Workspace` under the user home; macOS uses the latter. Each company
account gets its own subdirectory.

In the desktop client, open **Local working folder** in the left sidebar's
**Settings** group to view the path or use **Change folder** / **Choose folder**
and the native directory picker.
Folder preferences and execution consent are saved under `~/.octop/workspaces/`,
scoped to the company origin and employee ID. Device credentials remain in memory;
the separate **Remember me** login option can retain the account login token in
the company's WebView storage. Passwords are never saved.

Restarting with the same account creates a new device session and restores its
folder. Switching accounts or servers does not inherit another identity's folder.
**Remove folder** revokes its grant and saves an explicit empty preference;
it does not delete project files or automatically recreate a default on restart.
An unavailable saved folder is reported rather than silently replaced.

Company expert chat executes through `window.__octopRuntime` and the native
stdio worker (`launch.py company-runtime`). Employees see the working folder,
not a local/auto/server selector. Runtime failure never falls back to server
execution; contacts and employee messaging are independent. Browser clients keep
the existing device-tool flow. Existing server-executed histories remain readable
and are rejected for local continuation; start a new expert conversation.
The local runtime excludes autonomous expert teams and external connectors.
Compatible clients import uploaded expert attachments and forwarded company
attachments into the local task folder before starting a turn. Arbitrary external
attachment URLs are not accepted. Mid-task steering currently supports text only.
Selected company knowledge bases are retrieved through the server.

The automatically created default folder, or a folder selected through the native
picker, authorizes terminal/Python execution using the current OS user's
permissions: the working folder is **not a security sandbox**.
Legacy folder-only preferences require a fresh native selection before execution.
File names, relevant excerpts, tool output and results may reach company models
and authoritative history; the entire project is not automatically synchronized.
The project directory is not initialized as another expert/memory database.
The settings show runtime/tool diagnostics, and result cards offer native file
reveal for artifacts actually reported by the current account's worker.

Native runtime IPC uses an explicit allowlist with the selected page nonce:
`status`, `refresh_status`, `start`, `cancel`, `reveal`, `reconcile`,
`preview_image`, `steer`, `steer_remove`, `upload_artifact` and `import_attachment`.
Device credentials are sent to the child only through stdin, never argv, URLs,
page properties or logs. Account/server/folder changes terminate the worker
process tree, clear artifact permissions and invalidate its queued events.
The local UI listener only serves packaged files and forwards company APIs; it exposes no native file or execution endpoints. A crash or lost result is never automatically
replayed; receipts remain available for reconciliation.

The dashboard and desktop shell must both be rebuilt for this workflow. The
native bridge is injected only into the selected company UI origin, uses a session
nonce, accepts a short-lived pairing code (never the account token or password),
and opens a native picker rather than accepting paths supplied by the page.
Signing out stops the local connection. The legacy native device panel remains
for compatibility with older dashboards and explicit browser-based pairing.

No per-company rebuild or employee environment variable is required. An optional
`OCTOP_COMPANY_URL` or compiled `main.companyDefaultURL` still supplies a deployment
default; the address saved in the panel takes precedence. Compile the NSIS installer
with `-DOCTOP_COMPANY_OFFLINE`; optionally supply
`-DOCTOP_WEBVIEW2_OFFLINE_INSTALLER=<full architecture-matched installer>`.
Without WebView2 already installed or that full offline payload, the company
installer stops and asks for IT assistance rather than downloading a bootstrapper.
These options are explicit; the ordinary package task does not enable them.

To produce company installers from macOS (Node with dashboard dependencies installed,
Python 3.11+, Go, the pinned Wails CLI,
NSIS and Xcode command-line tools):

`package-company.py` requires a Git checkout with a resolvable `HEAD` to record
the source commit and local changes. Build from a checkout of the target release.
For an exported source archive, initialize a local Git repository and commit the
imported sources before packaging. Server Docker builds do not require Git history.

```bash
python3 desktop/package-company.py \
  --output /absolute/fresh-release/client \
  --webview2-dir /absolute/microsoft-offline-installers \
  --runtime-dir /absolute/verified-offline-payloads \
  --sign-identity 'Developer ID Application: Your Name (TEAMID)'
```

`--runtime-dir` contains `darwin-arm64/`, `darwin-amd64/`, `windows-amd64/`,
and `windows-arm64/` (only requested platforms are needed). Each directory must
contain `runtime/`, `packages/`, `launch.py`, and `tools/ffmpeg[.exe]` plus
`tools/ffprobe[.exe]`. Install dependencies while preparing the payload, never on
the employee machine. The package requires octop-harness 1.0.3, pandas, openpyxl,
python-docx, python-pptx, pypdf and Pillow. Run the supplied
`desktop/portable/verify_company_runtime.py <payload>` **using that payload's
Python on its target OS/architecture**; it imports the engine and exercises
Excel/Word/PPT/PDF/images/FFmpeg/terminal, producing `company-runtime-build.json`.
The packager refuses missing dependencies and mismatched verification records. Target smoke-test records are optional for cross-packaging; their absence is recorded as unverified, never as passed. It generates SHA-256 manifests; the shell verifies
all listed files before first launch. Tool payload updates use content-addressed
directories, preserving account receipts and project files during rollback.

The runtime requires Harness 1.0.3 and Gateway 1.0.2 or later, as specified by
the source dependencies. Rebuild the company runtime payload and native shell
together; Harness 1.0.2 payloads fail the server/client version check.
The shell supports text steering and withdrawal over nonce-bound IPC. While a
task is running, queue a draft and choose **插入 / Steer** to add it at the next
completed tool batch (or the end of a text-only reply). Older shells retain queued
drafts and show an update hint.

`--legacy-shell-only` is an explicit compatibility option and labels its artifacts
`legacy-shell`; it is not a complete local execution installer. The package output
records `built`, `deployed` and `real_device_verified` separately in
`company-package-status.json`. Building or unit-testing does not establish real
Windows/macOS installation acceptance.

Supply `MicrosoftEdgeWebView2RuntimeInstallerX64.exe` and
`MicrosoftEdgeWebView2RuntimeInstallerARM64.exe` from Microsoft's official
WebView2 download page. The script builds a macOS 13+ universal DMG and separate
Windows amd64/arm64 installers, includes offline WebView2, and writes SHA-256
checksums. Build staging is outside the repository, beside the output directory.
Employees configure the server address at sign-in. Developer ID signing is
optional; notarization and Windows Authenticode signing are separate release
steps. Windows installation still requires validation on a Windows machine.
See [the enterprise client guide](../docs/enterprise-client_EN.md)
for setup, supported workflows and data boundaries.

### Development build

Run the following from the repository root; **`desktop/src`** contains the Wails
project (`Taskfile.yml` and `build/config.yml`). Requires **Go 1.25+**, [Wails v3](https://v3.wails.io/)
`v3.0.0-beta.13`.

```bash
go install github.com/wailsapp/wails/v3/cmd/wails3@v3.0.0-beta.13
cd desktop/src
go mod tidy
wails3 build            # development binary under desktop/src/bin/
wails3 task package ARCH=arm64 VERSION="<version>" \
  PORTABLE_ZIP="../portable/release/Octop-portable-darwin-arm64-<version>.zip"
```

Dev against an already-running Octop (skips the bundled green zip):

```bash
cd desktop/src
OCTOP_DESKTOP_URL=http://127.0.0.1:8088 wails3 dev
```

The following portable-runtime behavior applies only to a base-mode shell
explicitly using independent local mode. Enterprise clients default to company
sign-in and do not start this server. In independent local mode, launch uses `~/.octop/portable/` if valid,
otherwise extracts the matching zip shipped with the desktop package (embedded
in the Windows and Linux binaries, under `Contents/Resources` on macOS). The
Wails shell never downloads Octop. For local runtime debugging, set
`OCTOP_DESKTOP_PORTABLE_ZIP=/absolute/path/Octop-portable-<plat>-<version>.zip`.
On later launches, a newer bundled portable version replaces the extracted
runtime after creating a consistent SQLite backup under `~/.octop/backups/`.
The upgraded Octop process then applies the normal database migrations during
startup. Newer extracted runtimes are never downgraded; PostgreSQL remains
externally managed and is not copied by the desktop shell.

GitHub Release names follow `Octop-<kind>-<os>-<arch>-<version>.<ext>`:

- Desktop GUI: `Octop-desktop-<plat>-<version>.dmg` (macOS; open and drag
  `Octop.app` into Applications), `.exe` (Windows NSIS installer — copies
  into the configured product directory and creates Start Menu + desktop shortcuts),
  `.tar.gz` (Linux)
- Green runtime zip: `Octop-portable-<plat>-<version>.zip`
- PyPI wheels stay `octop-<version>-py3-none-any.whl` (PEP 427)

The Linux tar.gz contains only the GUI binary; it has no separate portable zip or
server terminal process. Octop Enterprise uses [manual upgrades](../docs/updates_EN.md);
the CLI/API online update paths are disabled for this distribution.

The base-mode Wails shell requires macOS 12+; enterprise employee installers
built by `package-company.py` require macOS 13+. Linux also needs GTK4 + WebKitGTK 6 to link.

## Icons

| File | Used for | Rule |
|------|----------|------|
| `src/build/appicon.png` | Windows `.ico`, Linux | Full-bleed 512x512 artwork |
| `src/build/appicon-macos.png` | macOS `.icns` | 1024x1024 canvas, artwork 824x824 centred |
| `src/assets/tray-icon.png` | Tray + app icon on Windows/Linux | Full-bleed |
| `src/assets/tray-icon-template.png` | macOS menu bar | 88px canvas, 64px black-on-transparent glyph |

macOS sizes both surfaces to a fixed box, so the padding has to live in the
artwork: the Dock follows Apple's 824/1024 icon grid, and Wails scales the menu
bar image to the full `NSStatusBar` thickness (22pt) where the glyph should be
~16pt. Full-bleed sources on either surface render a size bigger than every
other app. On macOS the Dock icon comes from the bundle's `icons.icns` only —
see `applyAppIcon` in `src/icons_darwin.go`.

Enterprise source/manifest checks and the native acceptance matrix: [deployment guide](../docker/enterprise/README_EN.md). Before local Wails builds, run `npm ci --prefix dashboard` from the repository root; the shared build task then generates and verifies login assets.

## Independent distribution identity

The macOS bundle and Windows assembly identity is `io.github.colorai.octop.enterprise`. Product metadata is configured in [`src/build/config.yml`](src/build/config.yml) and the platform-specific files under [`src/build/`](src/build/). Artifact filenames follow the conventions above; the executable is named `Octop`.

Preserve working folders, account settings and task receipts before replacing an older client. Check the target release's upgrade instructions, signing status and notarization status before distribution.
