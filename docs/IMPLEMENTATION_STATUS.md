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

## Milestone M8.5 — unified owner administration and source entitlements

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

Implemented and deployed:

- a first-run device accepts an owner-issued activation code, creates a single
  passwordless local workspace and immediately signs it in;
- an existing single-user desktop installation keeps all local data while its
  visible legacy account is converted into the hidden workspace;
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
- a legacy desktop database with one local account is converted in place to
  the hidden passwordless `SearchCar` workspace before worker startup, keeping
  all owner IDs, projects, history and scheduler data; desktop never presents
  a login or logout control after the conversion.
- an unexpected legacy multi-user database is exported as a verified local
  backup before the desktop starts with a clean workspace; device and license
  state remain outside that reset.
- production D1 migration `0003_source_entitlements.sql` and the matching
  Worker version were deployed successfully.
- local implementation now separates source access from license lifecycle:
  empty source grants keep the signed license visible while blocking search;
- migration `0004_license_deletion.sql`, the owner delete control and desktop
  `LICENSE_DELETED` cleanup are implemented and verified in the production
  pilot;
- migration `0005_device_rebinding.sql` allows the signing key retained after
  license deletion to activate one replacement license while keeping the old
  device row as inactive history; the owner transfer form now lists only
  licenses that actually have an active source device.

Follow-up validation before sales beyond the pilot:

- rebuild and test a fresh macOS and Windows installer against that Worker;
- perform the documented pilot-local-user migration and transfer checks;
- add the next parser only together with a catalog migration and its explicit
  desktop scan guard.

## Windows startup experience (implemented locally)

- the release Tauri executable now uses the Windows GUI subsystem instead of
  opening a console owned by the application process;
- the production Nuitka sidecar uses hidden-console mode while preserving
  terminal output for developer and smoke-test launches;
- Windows immediately shows a compact startup window while the onefile
  sidecar, local data and HTTP backend are prepared;
- startup detects an early sidecar exit, waits up to five minutes for a cold
  Defender-affected launch and offers `Retry` or a clean `Exit` on failure;
- closing the startup window asks for confirmation and then terminates the
  partially started sidecar rather than leaving an orphan process.

Still required: rebuild the Windows NSIS installer and validate cold and warm
launches on the pilot Windows machine, including retry, startup-window close
and confirmation that no console appears.

## Milestone M9 — backup and device transfer (pilot complete)

Implemented:

- versioned cross-platform `.searchcar-backup` with checksums, SQLite integrity
  validation, portable storage paths and automatic rollback backup;
- staged restore before backend/worker startup; authentication sessions and
  all license/device state stay outside the portable archive;
- transfer request/claim controls in desktop settings and on the first-run
  screen of a replacement computer, without issuing a second license or local
  password;
- Cloudflare owner approval shows the selected client, old device and new
  device, requires an explicit confirmation and explains that local data needs
  a separate backup;
- inactive or deleted licenses are rejected before a transfer can change any
  device binding; a lost old computer does not block an owner-approved license
  transfer;
- an explicit backup regression test proves that archives contain no license
  files and cannot replace the target computer's binding or lease;
- the installed cross-platform acceptance procedure is documented in
  `docs/PHASE9_ACCEPTANCE.md`;
- passwordless desktop workspace can now create, list, validate and stage its
  own backups without receiving legacy local-administrator privileges;
- Settings can download a validated archive and import an externally selected
  archive through the operating-system file chooser; uploads are streamed to
  a temporary file, size-limited, validated and atomically accepted;
- ordinary web users remain unable to access desktop data operations;
- the owner completed the installed macOS → Windows backup, restore, license
  transfer and old-binding rejection scenario on 2026-08-26. The portable
  format is platform-neutral; the reverse direction remains a release
  regression check rather than a blocker for starting M10.

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
  build without a non-empty matching `.sig` artifact;
- immediately before download/install, the native shell asks the local backend
  to create a verified `pre-update-*.searchcar-backup`; an update-install guard
  blocks new manual and scheduled scans until the sidecar stops or preparation
  is aborted;
- an already queued/running/cancelling scan postpones installation with a
  concrete instruction to finish or cancel it first; backup/download failures
  leave the current version running and release the update guard;
- stale update guards are removed safely on the next startup on both POSIX and
  Windows without using a destructive `os.kill(pid, 0)` Windows probe;
- the sidebar now reports `checking`, backup preparation, download progress,
  retry and installation instead of leaving the user with a silent wait;
- a failed download is retried once, then leaves the current version running
  and shows the public manual-download fallback;
- a manual `Publish desktop release` workflow downloads exact successful Mac
  and Windows workflow artifacts, verifies checksums, creates the signed static
  manifest and publishes it atomically through a draft in the public
  `searchcar-desktop-releases` repository;
- the embedded updater endpoint now points only at that releases-only
  repository, so the product source repository can remain private.

External publication setup completed by the owner on 2026-08-26: the public
releases-only repository exists and the private source repository contains the
scoped `RELEASES_REPO_TOKEN` Actions secret.

Still required: publish the first two signed versions, complete an installed
old-to-new update and tampered-artifact acceptance test, and later add
commercial OS signing/notarization.

## Milestone M11 — installer UX and operator support (in progress)

Implemented locally after the first `v0.1.0` publication:

- new projects default to all search-result pages in the frontend, API, ORM
  and legacy importer without rewriting existing project settings;
- a project with no successful scan always performs its initial run as
  `ACCURATE + ALL_PAGES`; failed or cancelled attempts do not disable that
  initial full pass;
- car-level scan failures retain a validated Encar URL and show an `Open
  listing` action in both web and desktop reports; missing or invalid URLs are
  not rendered;
- the new-project form explains that the first run is intentionally more
  thorough than later runs.
- scan reports can be filtered by one or more saved project identities and by
  statuses at the same time; project choices come from the report itself, so a
  deleted project's saved name remains available, and one reset clears both
  filters.
- renamed active projects use their current name in reports and report filters;
  a deleted project's last saved name remains the fallback.
- desktop backend writes rotating JSON logs with a per-request correlation ID;
  `Настройки → Диагностика` creates a redacted ZIP for the last hour, 24 hours,
  3 days or 7 days. It uses the native system file chooser and falls back to a
  protected ZIP download when that WebView command is unavailable. The result
  previews its two archive files and can reveal the report in Finder/Explorer
  through the local authenticated backend.
  The archive contains only `manifest.json` and sanitized `logs.jsonl`; it
  excludes local data, backups, images, license state and cookies.

Still required in M11: add screenshots from the final Windows and macOS
installers to the completed pilot guide and run its independent nontechnical
walkthrough.
