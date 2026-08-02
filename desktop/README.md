# SearchCar Desktop development

This directory contains the first desktop implementation milestone:

- a Vite SPA build of the existing React interface;
- a Tauri 2 shell with single-instance handling;
- a localhost sidecar lifecycle and health gate;
- platform data directories and a one-time browser session secret;
- a SQLite runtime with foreign keys, WAL and a busy timeout.
- a Nuitka build path for a single platform-specific backend binary;
- a bundled Chromium headless shell managed outside the Python binary.

## Current milestone commands

Build the desktop frontend:

```bash
pnpm desktop:frontend:build
```

Inspect the native desktop toolchain:

```bash
pnpm desktop:tauri:info
```

Verify the SQLite runtime using an isolated data folder:

```bash
cd backend
python -m app.desktop_runtime check --data-dir ../work/desktop-data
```

Run backend tests:

```bash
cd backend
pytest
```

The Tauri shell requires the free Rust toolchain and a generated sidecar binary
in `desktop/src-tauri/binaries`. Build Chromium and the sidecar with the same
Python environment used for `backend/requirements-desktop-build.txt`:

```bash
python scripts/prepare_desktop_browser.py
python scripts/build_desktop_sidecar.py --mode standalone
python scripts/build_desktop_sidecar.py --mode onefile
```

The standalone build is the diagnostic form recommended before onefile. The
onefile command writes the target-suffixed binary expected by Tauri. Customers
will not need Rust, Python, Node, Docker or these commands.
