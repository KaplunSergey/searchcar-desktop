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
pnpm desktop:tauri:build -- --debug --bundles app --no-sign
```

The pilot build is intentionally unsigned. Production release automation will
add platform signing and updater signatures in a later milestone.

## Required smoke tests

- compiled sidecar `check` creates SQLite with FK, WAL and busy timeout;
- compiled sidecar starts `/api/health` and serves the SPA only after bootstrap;
- browser check creates a non-empty PNG using the bundled headless shell;
- Tauri bundle contains `searchcar-core`, `desktop-ui` and `browsers`;
- closing the desktop process also terminates the sidecar.

Windows and macOS artifacts must be tested on clean machines without Python,
Node.js, Rust, Docker or a separately installed browser before release.
