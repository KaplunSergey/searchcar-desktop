# SearchCar Desktop build guide

This guide is for maintainers. Customers receive an installer and do not need
any of these tools.

## Build model

Desktop artifacts are target-native. Build macOS Apple Silicon artifacts on an
Apple Silicon Mac and Windows x64 artifacts on Windows x64. The build has four
independent inputs:

1. the Vite frontend;
2. the Nuitka `searchcar-core` sidecar;
3. the Playwright Chromium headless shell.
4. the separately executable Playwright Node driver.

Tauri combines them only after all four are ready.

Playwright's Node driver is kept as a fourth, separately executable resource.
It must not be launched from Nuitka's temporary onefile extraction directory:
macOS application launches may reject that child executable even when direct
terminal checks pass.

## Python environment

Create a Python 3.12 virtual environment and install:

```bash
python -m venv .desktop-build-venv
.desktop-build-venv/bin/python -m pip install \
  -r backend/requirements-desktop-build.txt
```

On Windows, the interpreter path is
`.desktop-build-venv\\Scripts\\python.exe`.

## Browser resources

```bash
.desktop-build-venv/bin/python scripts/prepare_desktop_browser.py
```

Only Chromium's headless shell and its required media helper are retained. A
relative manifest is written into `desktop/runtime/browsers`; it contains no
developer machine path. The manifest records the executable size and SHA-256.
The desktop runtime refuses to start when the executable is missing, modified
or resolves outside the bundled browser directory.

## Backend sidecar

Stage the platform-native Playwright driver before smoke tests or bundling:

```bash
.desktop-build-venv/bin/python scripts/prepare_desktop_playwright_driver.py
```

The resulting `desktop/runtime/playwright-driver` directory contains the
platform Node executable and a size/checksum manifest. Tauri passes this path
to the backend, which validates it and sets `PLAYWRIGHT_NODEJS_PATH` before
Playwright starts.

Build and test standalone first:

```bash
.desktop-build-venv/bin/python scripts/build_desktop_sidecar.py --mode standalone
```

After the standalone runtime works, create the onefile Tauri sidecar:

```bash
.desktop-build-venv/bin/python scripts/build_desktop_sidecar.py --mode onefile
```

The script detects the current target and writes the final executable into
`desktop/src-tauri/binaries` with the target suffix required by Tauri.

## Desktop application

```bash
pnpm install --frozen-lockfile
pnpm desktop:frontend:build
pnpm desktop:tauri:build --debug --bundles app --no-sign
```

For an isolated local Rust installation on macOS, run
`scripts/install_local_rust.command`. It stores rustup and Cargo under
`Documents/Codex/.toolchains` and does not modify the shell profile.

For a local macOS test build, double-click `Build SearchCar for macOS.command`
in Finder or run:

```bash
./"Build SearchCar for macOS.command"
```

The launcher calls `scripts/build_mac_fixed.command` to rebuild the sidecar and
Tauri shell, then installs `SearchCar Desktop Fixed.app`. The previous app
bundle is retained with a UTC timestamp instead of being deleted.

GitHub Actions builds are split by native platform:

- `macos-desktop.yml` produces an ad-hoc-signed Apple Silicon `.app` zip;
- `windows-desktop.yml` produces the unsigned Windows x64 NSIS installer.

Both workflows install pinned Python/Node dependencies, run backend/frontend
tests, stage Chromium plus the external Playwright Node driver, run the compiled
smoke suite and upload checksums with the build artifact. Code-signing and
notarization credentials are intentionally not required for pilot builds.

### Local Windows x64 build without GitHub Actions

The Windows installer must be built inside 64-bit Windows. Use a Windows x64
computer or a Windows virtual machine with x64 application support. Install:

- Git;
- PowerShell 7 x64;
- Python 3.12 x64;
- Node.js 22 x64 and `pnpm@11.17.0`;
- Rustup;
- Visual Studio 2022 Build Tools with **Desktop development with C++** and a
  Windows SDK.

Clone or copy the repository to Windows, close SearchCar Desktop, then
double-click `Build SearchCar for Windows.cmd`. The launcher runs the checked
PowerShell build script and produces:

```text
work\windows-artifact\SearchCar-Desktop-Windows-x64-setup.exe
work\windows-artifact\SHA256SUMS.txt
```

From PowerShell 7 the same build can be started without the final pause:

```powershell
& ".\Build SearchCar for Windows.ps1" -NoPause
```

The slow diagnostic standalone backend is intentionally disabled. Enable it
only for troubleshooting:

```powershell
& ".\Build SearchCar for Windows.ps1" -IncludeDiagnosticStandalone
```

If a previous build already produced the onefile sidecar and stopped later in
the smoke or installer stage, continue without rebuilding Nuitka:

```powershell
& ".\Build SearchCar for Windows.ps1" -ResumeAfterSidecar -NoPause
```

After changing only the sidecar packaging code, rebuild the sidecar and then
continue with smoke testing and NSIS without repeating dependency installation,
the test suites or the Chromium download:

```powershell
& ".\Build SearchCar for Windows.ps1" -RebuildSidecar -NoPause
```

The Windows onefile payload uses a versioned directory below the current
user's cache and validates the cached files before reuse. The first `check`
invocation primes that cache; the Chromium check and server startup no longer
extract the same runtime into disposable directories again. Inner onefile
compression is disabled to keep the initial extraction predictable while the
outer NSIS installer still provides distribution compression. Loopback smoke
requests use .NET `HttpClient` with its proxy explicitly disabled instead of
PowerShell-version-specific `Invoke-WebRequest` switches. On failure the script
prints the tails of stdout, stderr and the structured backend log, then stops
the complete Nuitka process tree. Windows Nuitka build intermediates are
retained under `work/` so later sidecar-only rebuilds can reuse them; delete
that target's `work/nuitka/` directory only when a deliberately clean rebuild
is required.

The installed release uses the Windows GUI subsystem and the production
Nuitka sidecar uses hidden-console mode, so neither executable opens an empty
terminal window. While the cached sidecar is being prepared, SearchCar shows a
small startup window with real stages. The first launch may take longer while
Windows Defender validates the bundled executable. The shell waits up to five
minutes, detects an early sidecar exit, and offers either a clean retry or exit
instead of leaving an empty window running indefinitely.

This produces a pilot installer without a commercial Windows code-signing
certificate, so SmartScreen can warn on a clean computer. When the Tauri
updater signing environment variables are present, Tauri also creates the
separate updater `.sig`; those secrets must never be committed to Git.

The pilot build is intentionally unsigned. Production release automation will
add platform signing and updater signatures in a later milestone.

## Required smoke tests

- compiled sidecar `check` creates SQLite with FK, WAL and busy timeout;
- compiled sidecar starts `/api/health` and serves the SPA only after bootstrap;
- browser check creates a non-empty PNG using the bundled headless shell;
- Tauri bundle contains `searchcar-core`, `desktop-ui` and `browsers`;
- closing the desktop process asks active work to cancel cooperatively, waits
  for the partial report to be committed and then terminates the sidecar;
- Encar and Telegram links open in the operating-system browser.

On an Apple Silicon build machine, run the compiled smoke scenario with:

```bash
scripts/macos_desktop_smoke.sh \
  desktop/src-tauri/binaries/searchcar-core-aarch64-apple-darwin \
  desktop/runtime/browsers \
  desktop/runtime/playwright-driver \
  desktop/dist \
  work/macos-smoke/runtime \
  work/macos-smoke/results
```

On Windows x64, use:

```powershell
scripts/windows_desktop_smoke.ps1 `
  -Sidecar desktop/src-tauri/binaries/searchcar-core-x86_64-pc-windows-msvc.exe `
  -BrowserDir desktop/runtime/browsers `
  -PlaywrightDriverDir desktop/runtime/playwright-driver `
  -FrontendDir desktop/dist `
  -RuntimeRoot work/windows-smoke/runtime `
  -OutputDir work/windows-smoke/results
```

Both scripts write `compiled-smoke.json` containing the sidecar, browser and
frontend footprint plus elapsed smoke time. The Windows workflow also writes
`installer.json` with the final installer size and SHA-256. Record installed
size and cold/warm startup time manually on each clean pilot machine because
CI unpack time is not representative of a customer computer.

The current local Apple Silicon build inputs are approximately 236 MB unpacked
(about 179 MB browser, 54 MB onefile sidecar and 3 MB frontend). Treat this only
as an engineering baseline: the installer is compressed and exact figures are
always taken from the generated JSON reports.

The current validation step also requires launching the installed macOS
`.app`, signing in and completing a real scan with its bundled Chromium. This
specific native launch cannot be executed inside Codex because the macOS system
sandbox blocks it. The browser discovery, launch and screenshot logic has
already passed in an allowed environment; the installed application flow must
therefore be checked manually outside Codex.

The red window close button, the tray `Exit` action and the operating-system
quit command all show the same confirmation dialog. Confirming exit marks
queued work cancelled and active work as cancellation-requested. The worker
retains already processed cars in the report. The native shell waits up to 40
seconds and uses force-kill only if cooperative shutdown does not finish; the
next start then recovers any remaining active record as interrupted. Closing
the window no longer keeps background scheduling alive implicitly.

## Background scheduler and tray checks

Closing the main window must always ask for confirmation. Confirming stops the
sidecar regardless of scheduler state; cancelling leaves the window and all
work running. Use the normal minimize action when the application should keep
working in the background.

Verify the tray menu on every pilot system:

1. `Open SearchCar` restores and focuses the existing window without creating a
   second sidecar.
2. The next-run item changes from `calculating` to the persisted UTC deadline.
3. `Pause automatic updates` changes to `Resume automatic updates`; manual
   refresh remains available in the UI.
4. After sleep, catch-up mode creates at most one automatic run. With catch-up
   disabled, a stale deadline is skipped and re-anchored to the selected
   interval.
5. `Exit` performs the graceful-shutdown flow and leaves no sidecar process.

The tray is currently an English native shell surface. Localized tray labels
and native notifications remain as Phase 5 polish. OS autostart was removed
from the product scope on August 3, 2026.

Windows and macOS artifacts must be tested on clean machines without Python,
Node.js, Rust, Docker or a separately installed browser before release.

## Automated Windows pilot

`.github/workflows/windows-desktop.yml` performs the Windows x64 build on a
native GitHub-hosted runner. It compiles standalone and onefile sidecars, runs
the compiled Chromium and protected-session smoke tests, creates an unsigned
NSIS installer and uploads the installer plus checksums for 14 days.

The Windows installer includes the offline WebView2 installer. This makes the
pilot larger, but installation does not depend on internet access or an
existing WebView2 runtime. The application itself may still require internet
for search and license checks.

On a new local database, the current desktop pilot asks the customer for an
owner-issued one-time activation code. It creates one passwordless local
workspace only after that code is accepted by the license service; no default
customer username or password is embedded in the installer.

The workflow can be started manually from GitHub Actions. A successful CI build
proves Windows Server runner compatibility; Windows 10 and Windows 11 pilot
machines remain mandatory before customer release.

## Product version

`package.json` is the canonical product version. Before making a release,
change its `version` to the required SemVer value and run:

```bash
pnpm version:sync
pnpm version:check
```

The first command synchronizes the Tauri bundle, Rust package, desktop backend
and frontend version metadata. The second command only verifies them. All
manual GitHub build and license-service workflows run the verification before
doing work, so a mismatched desktop build cannot be published. The application
shows the resulting version in its sidebar.
