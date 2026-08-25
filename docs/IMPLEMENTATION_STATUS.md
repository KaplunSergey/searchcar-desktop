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

- validate Chromium launch from the installed native macOS `.app` outside the
  Codex process sandbox. macOS blocks that native launch inside Codex, while
  the same browser logic has already passed in an allowed environment;
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

- run the installed macOS `.app` manually and verify that its bundled Chromium
  completes an actual application scan; this is part of the current validation
  step because the Codex macOS sandbox cannot perform the native launch;
- run the complete Python suite when the dependency environment or Docker
  daemon is available (network installation is unavailable in this sandbox);
- exercise process-crash recovery against a compiled sidecar;
- stress long browser scans and confirm transactions stay short;
- validate the PostgreSQL compatibility migration in Docker.

## Milestone M3 — converter and backup foundation

Implemented:

- versioned `.searchcar-backup` format with manifest and SHA-256 checksums;
- SQLite Online Backup API export and integrity/foreign-key validation;
- cross-platform archive traversal and symlink protection;
- maintenance lock that prevents searches from starting during export;
- offline startup restore with a verified automatic rollback backup;
- reset of authentication sessions and interruption of unfinished jobs;
- read-only, consistent PostgreSQL-to-SQLite converter;
- preserved IDs/JSON/relationships, UTC normalization and portable paths;
- storage copy with source/target checksums and table-count comparison;
- administrator Settings UI for backup, restore and migration staging;
- command-line diagnostics and automated unit coverage.

Validation still required before M3 is complete:

- run the full Python suite in the desktop Python 3.12 dependency environment;
- migrate a copy of the real PostgreSQL volume and review every count;
- execute backup/restore from installed macOS and Windows applications;
- rebuild and visually verify the Settings wizard in the native desktop shell;
- simulate a restore failure and confirm the automatic rollback on both OSes.

## Milestone M4 — sidecar and browser packaging hardening

Implemented:

- generated browser manifest with the exact relative executable path, byte
  length and SHA-256 checksum;
- startup validation that rejects a missing, modified or path-traversing
  bundled Chromium executable;
- explicit Playwright launch through the validated packaged executable instead
  of host-browser discovery;
- authenticated graceful-shutdown endpoint used by the Tauri lifecycle;
- cooperative cancellation of queued and running scans before the sidecar
  exits, preserving work already written to the report;
- bounded graceful wait with a force-kill fallback in the native shell;
- allowlisted Encar and Telegram links opened by the operating-system browser;
- compact globe action for opening a listing directly from each car row;
- macOS and Windows compiled-runtime smoke scripts covering SQLite, bundled
  Chromium, protected bootstrap session and graceful shutdown;
- compiled sidecar/browser/frontend size and smoke-duration reporting;
- Windows CI smoke artifacts include the packaging measurements.

Validation still required before M4 is complete:

- run `scripts/macos_desktop_smoke.sh` against a newly compiled sidecar outside
  the Codex macOS sandbox; native Chromium process creation is blocked inside
  that sandbox;
- complete one real project scan from the installed `.app`, including images
  and a listing screenshot;
- obtain a green Windows x64 workflow artifact and repeat the real scan on
  clean Windows 10 and Windows 11 machines;
- measure installer size, installed size and cold/warm startup time on the
  three pilot systems;
- run the complete backend suite in the Python 3.12 build environment.

## Milestone M5 — background scheduler and tray foundation

Implemented:

- versioned SQLite/PostgreSQL migration for scheduler pause and catch-up mode;
- persistent per-user pause without disabling manual refresh actions;
- optional wake/restart catch-up with a strict maximum of one queued run;
- stale scheduled runs are skipped and re-anchored when catch-up is disabled;
- existing automatic/manual merge and manual-refresh re-anchor remain active;
- authenticated localhost tray-status and pause/resume control endpoints;
- Tauri system tray with Open, next-run status, pause/resume and Exit actions;
- closing the window hides it when a scheduler is enabled and keeps the
  sidecar working; left-clicking the tray icon restores the window;
- closing without an enabled scheduler or selecting Exit performs graceful
  sidecar shutdown;
- Russian and Ukrainian settings UI for pause and catch-up behavior;
- unit scenarios for stale wake-up, single catch-up and tray pause state.
- deterministic sleep/resume handling: an active scan is marked
  `INTERRUPTED_SLEEP` with its partial report preserved; the next automatic
  scan is made immediately eligible exactly once when the completed-run
  interval is overdue;
- Tauri resume signal forwarded to the protected sidecar on macOS and Windows,
  plus startup reconciliation for an automatic scan abandoned before restart.

Still required to complete M5 validation/polish:

- add native completion/error notifications with a user setting;
- refresh tray text in the selected application language;
- execute sleep/wake, timezone/DST and long-running scan tests in installed
  macOS and Windows applications;
- compile the new tray code on both targets and validate Windows 10/11 tray
  behavior; Rust tooling is unavailable in the current Codex environment;
- add license/network/captcha guards before automatic enqueue once the local
  license enforcement layer exists.

## Milestone M6 — Cloudflare license service

Implemented:

- isolated Cloudflare Worker project and versioned D1 migration;
- privacy-safe trial uniqueness for both subject and device fingerprint;
- signed Ed25519 device requests and server-time license leases;
- trial, check, redeem, transfer request and transfer claim endpoints;
- one-time activation and transfer codes stored only as peppered hashes;
- atomic single-winner redemption and owner-approved device transfer;
- request freshness, replay protection, idempotency and D1 rate limits;
- structured audit events and aggregated device/version checks;
- temporary secret-protected bootstrap admin endpoints for Phase 8 to replace;
- Miniflare/D1 concurrency and transfer tests;
- manual deploy workflow plus manually started encrypted D1 backup workflow;
- documented key generation, deployment, backup and clean-database recovery.

Validation still required before M6 is deployed:

- create the production Cloudflare account resources and replace the placeholder
  D1 database ID;
- set the signing, pepper and bootstrap admin secrets outside the repository;
- run the Miniflare suite on GitHub or another environment that permits
  loopback sockets (the Codex sandbox blocks Miniflare's local listener);
- perform the first remote D1 migration, test deployment and encrypted
  backup/restore drill.

## Next milestone

Deploy and externally validate M6, then continue Phase 7 client enforcement.
The first two M7 increments are implemented locally:

- Ed25519 verification of cached Phase 6 leases using canonical JSON;
- separate binding, lease and trusted-time state outside SQLite backups;
- hard lease/subscription boundaries and clock-rollback protection;
- read-only safe mode with guards at manual enqueue, scheduler enqueue and
  worker start;
- authenticated desktop license-status endpoint and automated tests for valid,
  expired, tampered, device-mismatched, offline and rolled-back-clock states.
- persistent Ed25519 device identity in macOS Keychain or a Windows
  current-user DPAPI-protected blob, never in SQLite or portable backups;
- privacy-preserving fingerprint derived from the device public key rather
  than a hardware serial number;
- signed trial, check and activation-code requests with strict HTTPS,
  idempotency, bounded responses and signed-lease validation before storage;
- startup and six-hour lease synchronization for existing bindings;
- Russian/Ukrainian activation, trial, expiry and manual-refresh controls in
  desktop settings;
- bundled public license configuration that remains disabled until the
  production Worker URL and verification key are supplied.

M7 is not complete: transfer UI, Rust verification, production keys/enforcement
and installed macOS/Windows secure-store validation remain. The Windows x64
build and installed-OS pilot are deliberately deferred until after the current
macOS license-service rollout.
Native tray notifications and installed-OS scheduler tests remain tracked as M5
polish. OS autostart is explicitly out of scope.

## Milestone M8 — owner license administration

Implemented:

- D1 singleton guard for one-time owner bootstrap;
- salted, peppered owner password records designed to stay within the
  Cloudflare Workers Free CPU budget;
- 12-hour `HttpOnly`, `Secure`, `SameSite=Strict` owner sessions;
- owner bootstrap, login, session inspection and same-origin logout endpoints;
- same-origin `/owner` panel for login, first-owner setup, customer creation,
  subscription placeholders, activation-code creation and transfer approval;
- owner dashboard API for recent customers, licenses, code history and device
  transfer requests, device history and audit events;
- integration coverage for bootstrap-once, failed login, session, logout and
  authenticated owner administration.

Production validation completed:

- migration `0002_owner_admin.sql`, owner bootstrap and the basic owner
  workflow were verified manually against the production Worker and D1.

Still required before sales beyond the pilot:

- searchable device history,
  audit export, suspension/revocation и owner-account recovery.

## Next milestone M8.5 — unified owner administration and source entitlements

Approved product direction:

- one client equals one active device in the first commercial model;
- the client has no Cloudflare password account: the first run consumes an
  invite/activation code and creates one local workspace automatically;
- the Cloudflare owner panel is the source of truth for customer, license,
  active device and source access;
- projects, listings and images remain local. Cloud synchronization is an
  explicitly deferred later phase;
- each future parser source receives a stable source key. A license receives
  an allow-list of keys, included in the signed lease and enforced before a
  scan starts.

Implemented locally (deployment still required):

- a first-run device accepts an owner-issued activation code, creates a single
  passwordless local workspace and immediately signs it in;
- existing local installations keep their existing login and data rather than
  being silently rewritten;
- migration `0003_source_entitlements.sql` creates the source catalog and
  grants `encar` to all current active licenses;
- new trials and owner-created licenses receive `encar` by default;
- signed leases carry an explicit source allow-list; the Python API and worker
  reject Encar scans when it is absent;
- `/owner` displays sources per license and lets the owner grant or revoke
  active catalog sources, with an audit event for each change.
- desktop hides the legacy local user-administration screen, including for an
  existing local administrator; customer, license and source management are
  centralised in Cloudflare `/owner`.

Still required to complete M8.5:

- apply migration `0003_source_entitlements.sql` and deploy the Worker;
- rebuild and test a fresh macOS and Windows installer against that Worker;
- perform the documented pilot-local-user migration and transfer checks;
- add the next parser only together with a catalog migration and its explicit
  desktop scan guard.

## Milestone M10 — release foundation (in progress)

Implemented:

- `package.json` is the canonical SemVer source for the desktop product;
- `pnpm version:sync` synchronizes the Tauri bundle, Cargo package, backend
  health version and frontend version label;
- `pnpm version:check` runs in every manually launched GitHub workflow and
  rejects a build or Worker operation when version metadata differs;
- the current product version and a desktop-only manual update button are
  visible together in the application sidebar;
- a strict generator and tests exist for the signed static Tauri updater
  manifest;
- the native shell checks the HTTPS manifest after startup, every six hours,
  from the tray and from the sidebar button, displays a native notification
  when an update is available, shows release notes, verifies the updater
  signature before stopping the sidecar, then installs and restarts;
- the updater public key is embedded and checked against a repository copy;
- manual macOS and Windows workflows require the updater secrets and reject a
  build without a non-empty matching `.sig` artifact.

Still required: a public releases-only download endpoint (or public GitHub
release assets), first end-to-end old-to-new update test, pre-update data
backup/active-scan coordination, richer in-window progress states, and
commercial OS signing/notarization.
