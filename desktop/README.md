# SearchCar Desktop development

This directory contains the first desktop implementation milestone:

- a Vite SPA build of the existing React interface;
- a Tauri 2 shell with single-instance handling;
- a localhost sidecar lifecycle and health gate;
- platform data directories and a one-time browser session secret;
- a SQLite runtime with foreign keys, WAL and a busy timeout.

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
in `desktop/src-tauri/binaries`. Nuitka packaging and installer automation are
the next implementation step; customers will not need Rust, Python, Node,
Docker or these commands.
