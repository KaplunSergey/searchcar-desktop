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
- unsigned macOS debug `.app` bundle containing UI, backend and Chromium;
- Windows x64 GitHub Actions build and compiled-runtime smoke-test workflow;
- offline WebView2 NSIS configuration for installation without network access.

Still required before M1 is complete:

- validate Chromium launch from the native macOS `.app` outside the Codex
  process sandbox;
- run the complete desktop window flow against the compiled sidecar;
- obtain the first successful Windows x64 CI artifact and run it on Windows 10
  and Windows 11 pilot machines;
- generate unsigned `.dmg` and `.exe` installers and document OS warnings.

## Current stage

The Windows x64 build pipeline is ready for its first run in GitHub Actions.
Milestone M1 remains open until the generated installer is exercised on clean
Windows 10 and Windows 11 pilot machines.

## Next milestone

Introduce versioned SQLite migrations, a persistent update-job queue and
recovery rules for searches interrupted by an application or system restart.
