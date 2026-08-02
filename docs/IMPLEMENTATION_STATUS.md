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
- native macOS Apple Silicon Rust compilation check.

Still required before M1 is complete:

- compile the complete Python backend into the target-specific Nuitka sidecar;
- bundle Chromium and validate Playwright from an installed application;
- run the complete desktop window flow against the compiled sidecar;
- add Windows x64 build and smoke-test automation;
- generate unsigned `.dmg` and `.exe` installers and document OS warnings.

## Next milestone

Package the backend sidecar, then run the existing application end-to-end from
Tauri without requiring Python, Node.js, Rust or Docker on the customer device.
