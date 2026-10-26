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

Auction-time normalization uses the pinned `tzdata` package from
`backend/requirements.txt` on both platforms rather than whichever IANA rules
the host has installed. The sidecar builder explicitly includes its Python
package and zoneinfo data. Source-level tests with `PYTHONTZPATH=""` verify the
package without a system database. The UI now reads a fresh Intl computer
timezone per auction request and refreshes auction queries after wake/focus or
periodically while visible. Compiled timezone-data checks and installed-app
timezone-change/wake checks on both platforms remain the MSA-07 platform gate;
source/Node tests do not close it.

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
platform Node executable, the full JavaScript driver package and a
size/checksum manifest. Tauri passes this path to the backend, which validates
it and starts Playwright from this signed resource instead of Nuitka's
temporary onefile directory.

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

The local macOS launcher builds the auction preview by default. It embeds
`SEARCHCAR_SOURCE_TEST_MODE=1` in the native shell, which passes
`--source-test-mode` to the sidecar even when Finder supplies no environment.
Both native license preflight and the backend use the existing
`<app-data>/source-tests` profile. This keeps its projects, captured filters,
storage and device binding together. Nothing is copied or merged into the
ordinary profile. Set `SEARCHCAR_SOURCE_TEST_MODE=0` on the local launcher
to deliberately build the ordinary profile. For a direct Tauri invocation,
set `SEARCHCAR_SOURCE_TEST_MODE=1` on that build command for an auction preview;
the sidecar itself supports both profiles. Production CI retains its ordinary
profile unless explicitly configured otherwise.

For Windows CI, dispatch `windows-desktop.yml` with `auction_preview=true`
to build this isolated profile with the staging Telegram delivery pilot.
The option defaults to false and is applied only when compiling the native
shell, not to the test suite. `installer.json` records the selected profile.
This dispatch produces test artifacts only; it does not publish a release.

The preview's existing native `--source-test-mode` flag also pins the sidecar's
Telegram origin to `https://searchcar-telegram-relay-staging.mapatar23.workers.dev`
and forces `SEARCHCAR_TELEGRAM_DELIVERY_ENABLED=0`, overriding inherited values.
It enables `SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED=1` only in that preview so
private, group and channel recipient binding can be tested against staging.
This works from Finder without custom shell variables and bundles no bot secret.
Ordinary-profile builds retain their existing trusted configuration; this does
not set a production relay default or enable message delivery. Source tests and
compiled preview acceptance cover missing/synthetic device binding, cached
recipient reads, and delivery staying off without contacting Telegram. A real
Start/check/confirm flow still needs explicit interactive acceptance.

For the separately authorized manual-delivery pilot, compile the native shell
with both `SEARCHCAR_SOURCE_TEST_MODE=1` and
`SEARCHCAR_TELEGRAM_DELIVERY_PREVIEW=1`. The shell passes the explicit
`--telegram-delivery-preview` flag to the sidecar, enabling the local dispatcher
against the pinned staging relay in the existing source-tests profile. The
25 October candidate also supports automatic Today posting through that dispatcher,
but only after a separate per-project opt-in (default OFF) and the next fully
successful search. Schedule-change notices have a separate per-project option,
default ON: they require a confirmed initial publication to that recipient before
the observed change and a new fully successful search. Enabling or reconnecting
does not replay historical changes. Without
that extra build flag, the pairing preview still forces delivery off; the
sidecar rejects the delivery-preview flag outside source-test mode. Production
defaults are unchanged. Connecting a recipient does not publish a vehicle's
initial message. With Today OFF, initial publications require an explicit
single-car/bulk action; schedule-change notices follow their independent setting.
With Today ON, the local dispatcher can recover fresh work from a committed full
successful run; any incomplete/cancelled project suppresses that whole run.
Queue work
continues while the app is open. The staging relay must independently enable
delivery; this does not distribute a bot secret or add a server scheduler.

An auction preview acceptance check must open a previously populated profile,
verify its projects remain visible, verify Copart and IAA have `can_search=true`,
and create a project in disposable data. A successful `/api/health` alone does
not verify the selected profile or auction availability.

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

The Windows onefile payload uses a product-version and payload-fingerprint
directory below the current user's cache and validates the cached files before
reuse. The fingerprint prevents two pilot commits with the same visible
product version from trying to overwrite DLLs that an older installed or
orphaned sidecar still has open. The shared source metadata file
`backend/app/sources/catalog.json` is explicitly bundled as
`app/sources/catalog.json` and included in that fingerprint. Adding a source
must invalidate the cached payload even when the visible version is unchanged.
Verify catalog loading from the actual compiled sidecar, not only Python source
or the command-construction tests, before publishing. The first `check` invocation primes that
cache; the Chromium check and server startup no longer extract the same runtime
into disposable directories again. Inner onefile
compression is disabled to keep the initial extraction predictable while the
outer NSIS installer still provides distribution compression. Loopback smoke
requests use .NET `HttpClient` with its proxy explicitly disabled instead of
PowerShell-version-specific `Invoke-WebRequest` switches. On failure the script
prints the tails of stdout, stderr and the structured backend log; the database
and browser CLI checks also keep their own stdout/stderr files under
`work\windows-smoke\results`. It then stops the complete Nuitka process tree.
Windows Nuitka build intermediates are
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

### Auction workspace UI (local fixtures)

Run `pnpm desktop:frontend:dev`, then in a separate terminal:

```bash
.desktop-build-venv/bin/python scripts/auction_workspace_ui_smoke.py \
  --base-url http://127.0.0.1:1420/ \
  --report-dir work/auction-ui-smoke
```

This uses the existing bundled Playwright runtime and real frontend, but all
API/media responses are fixtures. It checks create-to-queue behavior, denied
search/queue failure, photo preview/keyboard focus, captured conditions and
explicit confirmation, incomplete-selection protection, IAA replay gating and
responsive project/form layout. JSON results/screenshots go to the report folder.
No user database, activation, live auction or Telegram requests are used.
This is browser UI evidence, not a native installed-app or live-source check.

The selection transport also has an opt-in bundled Chromium fixture test:
from `backend/`, with disposable `STORAGE_ROOT` and `DATABASE_URL`, run
`RUN_SOURCE_SELECTION_BROWSER=1 ../.desktop-build-venv/bin/python -m pytest -q tests/test_source_selection_browser.py tests/test_source_selection_checkbox_browser.py`.
All IAA/Copart responses are intercepted; this checks real browser request
events, checkbox/label/keyboard activation, duplicate events, pending/stale
requests, edits and closing, not current live-site access or native Windows.

### Windows update installation continuity

The Windows updater passes the running executable's parent directory as the
final NSIS `/D=...` argument (unquoted even for spaces). Without this argument,
NSIS uses the saved registry location or the default LocalAppData directory;
stale registration can update a different copy while a shortcut opens the old
one. Keep the product name, publisher, binary name and install scope stable.

The Windows CI workflow runs `scripts/windows_installer_update_smoke.ps1` on
its disposable runner: install in a custom Unicode path, point registration
elsewhere, update with `/D`, and check the executable, registration, Desktop
and Start menu shortcuts. This test must never run on a user's computer.

For a reported old version after update, run `Diagnose SearchCar for Windows.cmd`
on the affected Windows computer while SearchCar is open. It saves
`SearchCar-installation-*.json` on the Desktop. Run it once after updater restart
and once after launch through the problematic shortcut. The report contains
process paths, registry locations, shortcut targets, executable versions and
hashes, including local usernames in paths. The `findings` section highlights
multiple copies, different versions, stale shortcuts and registry/path conflicts.
It does not change installations
or collect database, license secrets or cookies.

Compare the two reports before removing anything. A second backend extraction
cache is not a second installed desktop application. If duplicate installed
apps are confirmed, back up the workspace and identify the active installation
and its shortcuts before using the obsolete installation's uninstaller; do not
delete app data. Arbitrary old directories, copied executables, other users'
installations and pinned shortcuts are not automatically deleted or retargeted.

The fix controls updates initiated by a build containing it. It cannot change
the updater in an already installed old release; a one-time manual install into
the verified existing path may be needed to repair an affected machine.

### Runtime checks

Before compiling, both native workflows run
`python scripts/multisource_acceptance_smoke.py --report-dir work/queue-smoke`.
It checks the shared sequential queue with fake sources, the HTTP API and
prepared native Chromium/Node resources using disposable databases/storage.
JSON resource reports and JUnit XML are saved as a separate artifact even
when a later build step fails. This is source-runtime evidence, not a compiled
or installed-app smoke and not live Copart/IAA access. See
[MULTISOURCE_ACCEPTANCE.md](MULTISOURCE_ACCEPTANCE.md) for commands and open gates.

- compiled sidecar `check` creates SQLite with FK, WAL and busy timeout;
- compiled sidecar starts `/api/health` and serves the SPA only after bootstrap;
- browser check creates a non-empty PNG using the bundled headless shell;
- Tauri bundle contains `searchcar-core`, `desktop-ui` and `browsers`;
- closing the desktop process asks active work to cancel cooperatively, waits
  for the partial report to be committed and then terminates the sidecar;
- Encar and Telegram links open in the operating-system browser.
- complete native close/relaunch restores the last section of every site,
  including project/car IDs and application settings. Test across different
  localhost ports: native startup uses an ephemeral port, so reloading the same
  origin is insufficient. Desktop navigation is stored per authenticated owner
  in `ui/workspace-{user_id}.json` under the data directory, not only localStorage;
  a write failure must be visible and preserve the previous valid snapshot.

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

Both workflows also run compiled navigation and auction-time acceptance after compilation:

```bash
python scripts/compiled_navigation_smoke.py \
  --sidecar desktop/src-tauri/binaries/searchcar-core-aarch64-apple-darwin \
  --frontend desktop/dist \
  --report-dir work/macos-smoke
```

On Windows use `searchcar-core-x86_64-pc-windows-msvc.exe` and
`work/windows-smoke`. To check the packaged macOS resources, pass
`<app>/Contents/MacOS/searchcar-core` and `<app>/Contents/Resources/desktop-ui`.
Use the build Python environment containing pytest/backend dependencies.
The runner owns disposable SQLite/storage and test owner, starts the compiled
HTTP stack three times on different localhost ports and verifies catalog
validation, protected bootstrap, CSRF, saved per-site routes and zero jobs.
Another disposable process checks the auction read API using bundled timezone
data with `PYTHONTZPATH=""` and without external `PYTHONPATH`/`PYTHONHOME`.
Fixed independent expectations cover UTC/Warsaw/Los Angeles/Tokyo across US
spring/fall DST transitions; START/END/UNKNOWN, date-only, unconfirmed CST,
today filtering and unchanged rounds/statuses are also checked. An open SSE
subscription is held through each shutdown. This runner checks compiled
projections, not a source adapter's parsing or a real OS timezone/wake change.
It never uses the installed application's data, accesses live sources or
sends Telegram messages. `compiled-navigation.json`, `compiled-auction-time.json`, JUnit XML and
startup logs are uploaded separately, even if a later build step fails.
Success JSON markers from earlier runs are removed before validation/testing;
failures retain their original logs/JUnit instead of presenting stale green.
Unrelated reports are preserved. These tests are opt-in and skipped in the ordinary backend suite.
This proves compiled HTTP behavior, not native WebView or installed-app behavior.

Product TypeScript checks exclude disposable `work/` build outputs. Nuitka may
recreate Playwright driver declarations there during compilation; these are not
product sources and must not enter `tsc --noEmit`. The real app and desktop
frontend remain covered; `tests/typecheck-scope.test.mjs` verifies that boundary.
The license and Telegram Workers also have separate TypeScript programs so their
generated runtime globals cannot override browser DOM types. Both remain checked
through their own targets; the root web Worker owns its lightweight D1 contract
instead of relying on a license-service declaration leaking into the frontend.

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

Desktop shutdown explicitly ends every live `/api/events` subscription,
including a full subscriber queue, before waiting for the scan worker. Late
publishes cannot replace that end marker and new subscriptions finish immediately.
This prevents the native window and HTTP server waiting for each other to close.
The worker's 35-second persistence grace remains unchanged; HTTP draining has
its own 2-second safety bound. An idle exit should not consume the native
40-second fallback. `backend/tests/test_desktop_shutdown.py` checks a real
source-runtime server with the SSE connection held open until after process exit,
data lock release, and cooperative partial-report persistence on disposable data.
Compiled navigation acceptance also keeps SSE open across each shutdown.
These tests do not replace installed macOS/Windows GUI acceptance.

### Local API contention and error recovery

Authentication activity timestamps are best-effort. A busy SQLite writer must
not turn otherwise valid project/status reads into failures or wait five seconds
for an optional activity update. Only SQLite BUSY/LOCKED is skipped; expiry,
revocation, account status and CSRF checks remain mandatory. Other storage
failures are not hidden.

The desktop session guard and request logger use direct ASGI wrappers, avoiding
the compiled `BaseHTTPMiddleware` exception-group generator seen in the macOS
25 October crash report. `test_auth_contention.py` covers real file-backed
contention and access controls; `test_http_stack.py` covers guard, logging and
streaming contracts. Compiled navigation acceptance also holds a real writer
lock while reading projects/status/runtime, then separately exercises 3,000
concurrent healthy/500 requests on a deliberately broken disposable schema.
The service must recover after schema repair and shut down with SSE still open.
Passing this stress test is not a guarantee against every native crash.

The frontend's runtime probe has a bounded timeout. Losing the previously
connected desktop service marks the worker unavailable, makes the active count
unknown, disables the global new-search button and offers an explicit connection
recheck without starting another scan. Cached projects remain visible. The
`backend disconnection` scenario in the existing auction UI fixture runner
checks loss/recovery in RU/UK at desktop widths without using customer data.

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
