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

## Milestone M2 — SQLite and durable queue

Implemented:

- independent versioned SQLite migration ledger and startup write lock;
- UTC-aware timestamp type for PostgreSQL and SQLite;
- durable single-worker claim metadata, heartbeat and terminal timing;
- atomic queue claim protected against concurrent worker loops;
- startup recovery to `INTERRUPTED` with partial report preservation;
- one in-process desktop worker alongside FastAPI;
- automatic creation of the first local administrator without a CLI;
- queued automatic/manual project-run merging;
- scheduler re-anchor from a finished manual project refresh;
- migration, concurrency, recovery, UTC and scheduler unit tests.

Validation still required before M2 is complete:

- run the complete Python suite when the dependency environment or Docker
  daemon is available (network installation is unavailable in this sandbox);
- exercise process-crash recovery against a compiled sidecar;
- stress long browser scans and confirm transactions stay short;
- validate the PostgreSQL compatibility migration in Docker.

## Next milestone

Finish M2 runtime validation, then implement the read-only PostgreSQL-to-SQLite
converter and the verified `.searchcar-backup` export/restore foundation.
