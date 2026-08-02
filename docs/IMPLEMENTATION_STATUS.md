# SearchCar Desktop implementation status

## Milestone M1 — desktop runtime foundation

Implemented:

- isolated desktop frontend build that reuses the existing React interface;
- Tauri 2 shell with single-instance handling and a localhost health gate;
- sidecar process lifecycle tied to the desktop window;
- one-time bootstrap token and HttpOnly desktop session cookie;
- per-user application data, storage and log directories;
- SQLite runtime with foreign keys, WAL and a busy timeout;
- backend runtime check and automated SQLite tests;
- native macOS Apple Silicon Rust compilation check;
- reproducible Nuitka standalone and onefile sidecar build scripts;
- compiled macOS Apple Silicon backend verified against SQLite and the SPA;
- Playwright Chromium headless-shell download and resource manifest;
- browser launch/screenshot smoke test in the Linux Playwright runtime;
- unsigned macOS debug `.app` bundle containing UI, backend and Chromium.

Still required before M1 is complete:

- validate Chromium launch from the native macOS `.app` outside the Codex
  process sandbox;
- run the complete desktop window flow against the compiled sidecar;
- add Windows x64 build and smoke-test automation;
- generate unsigned `.dmg` and `.exe` installers and document OS warnings.

## Next milestone

Run the complete bundled macOS application flow, then reproduce the same
sidecar/browser/app build on Windows x64 without requiring development tools on
the customer device.
