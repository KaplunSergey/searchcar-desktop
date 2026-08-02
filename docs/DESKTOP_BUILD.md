# SearchCar Desktop build guide

This guide is for maintainers. Customers receive an installer and do not need
any of these tools.

## Build model

Desktop artifacts are target-native. Build macOS Apple Silicon artifacts on an
Apple Silicon Mac and Windows x64 artifacts on Windows x64. The build has three
independent inputs:

1. the Vite frontend;
2. the Nuitka `searchcar-core` sidecar;
3. the Playwright Chromium headless shell.

Tauri combines them only after all three are ready.

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
developer machine path.

## Backend sidecar

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

The pilot build is intentionally unsigned. Production release automation will
add platform signing and updater signatures in a later milestone.

## Required smoke tests

- compiled sidecar `check` creates SQLite with FK, WAL and busy timeout;
- compiled sidecar starts `/api/health` and serves the SPA only after bootstrap;
- browser check creates a non-empty PNG using the bundled headless shell;
- Tauri bundle contains `searchcar-core`, `desktop-ui` and `browsers`;
- closing the desktop process also terminates the sidecar.

The current validation step also requires launching the installed macOS
`.app`, signing in and completing a real scan with its bundled Chromium. This
specific native launch cannot be executed inside Codex because the macOS system
sandbox blocks it. The browser discovery, launch and screenshot logic has
already passed in an allowed environment; the installed application flow must
therefore be checked manually outside Codex.

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

On a new local database the current pilot creates the agreed initial
administrator `Serhii` with password `sergiokap09` and requires a password
change after the first login.
Development builds can override both bootstrap values with
`SEARCHCAR_INITIAL_ADMIN_USERNAME` and `SEARCHCAR_INITIAL_ADMIN_PASSWORD`.
This temporary bootstrap is replaced by licensed first-run onboarding in the
licensing milestone.

The workflow can be started manually from GitHub Actions. A successful CI build
proves Windows Server runner compatibility; Windows 10 and Windows 11 pilot
machines remain mandatory before customer release.
