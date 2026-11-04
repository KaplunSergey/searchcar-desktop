# SearchCar license service

This is the Phase 6 Cloudflare Worker and D1 foundation. Client-side key
storage, offline lease verification and entitlement enforcement belong to
Phase 7. The Phase 6 admin endpoints use a bootstrap bearer secret. Phase 8
adds a short-session owner login; the bootstrap secret is then retained only
for the one-time creation of the first owner and emergency recovery.

## Private Telegram authorization

`TelegramAuthorization` is a named WorkerEntrypoint reachable only through a
service binding, not a public HTTP route. `authorize(signedEnvelope)` returns
only a boolean after independent device-proof/freshness/Telegram-audience and
current license/device checks. It issues no lease, activates nothing, and runs
only SELECT; no database migration or extra secret is needed. The relay owns
request replay protection and never receives a license DB binding or signing key.
Service failure is not an allow decision and must not be cached as one.

On2026-10-22 this addition was deployed over the downloaded production bundle,
preserving the existing HTTP/scheduled implementation rather than deploying
unrelated dirty-checkout edits. Evidence and rollback IDs are in
`work/license-auth-20261022/REPORT.md`. This does not enable Telegram delivery.

## Security and protocol invariants

- Ed25519 device proofs authenticate every public mutation and check.
- Ed25519 leases are signed over canonical JSON with server-provided time.
- Request timestamps have a five-minute acceptance window.
- Every POST requires an idempotency key and a unique request UUID.
- Trial uniqueness is enforced independently for the privacy-safe subject hash
  and fingerprint hash.
- Activation codes are persisted only as stable, domain-separated SHA-256 hashes.
  They contain 100 bits of random entropy, so their digest is not practically
  brute-forceable and remains valid if an unrelated server secret is rotated.
  Transfer codes and tokens remain peppered.
- Idempotency responses are AES-GCM encrypted with a domain-separated key
  derived from the code pepper, so cached delivery never persists plaintext
  activation codes or transfer claim tokens.
- D1 unique constraints and atomic batches make code redemption and transfer
  approval single-winner operations.
- A lease lasts 48 hours, but `subscription_expires_at` remains the hard search
  boundary. Network grace never extends a subscription.
- No raw hardware identifier or application data is accepted by the API.

The signed device message is:

```text
SEARCHCAR-DEVICE-REQUEST-V1
<canonical JSON of the complete request without proof>
```

The signed lease message is:

```text
SEARCHCAR-LICENSE-LEASE-V1
<canonical JSON of lease.payload>
```

Canonical JSON recursively sorts object keys, keeps array order, emits no
whitespace and uses normal JSON primitive encoding. Binary values use unpadded
base64url.

## Public API

All POST requests use `Content-Type: application/json` and an
`Idempotency-Key` header. Common signed fields:

```json
{
  "protocol_version": 1,
  "request_id": "018f6ac2-8c44-7df0-8f6d-2d34af37b337",
  "requested_at": "2026-08-03T12:00:00.000Z",
  "app_version": "0.1.0",
  "device": {
    "public_key": "<32-byte Ed25519 public key as base64url>",
    "fingerprint_hash": "<64 lowercase hexadecimal characters>",
    "label": "MacBook"
  },
  "proof": "<64-byte Ed25519 signature as base64url>"
}
```

Endpoints and additional fields:

- `POST /v1/trials/activate`: `subject_hash`;
- `POST /v1/licenses/check`: `license_id`, `device_id`;
- `POST /v1/licenses/redeem`: `activation_code`;
- `POST /v1/transfers/request`: no additional fields;
- `POST /v1/transfers/claim`: `transfer_code`, `claim_token`;
- `GET /v1/releases/latest`;
- `GET /health`.

The claim token is high entropy and stays on the new device. The short transfer
code can be sent to the owner. A pending transfer must be approved by the owner
before claim. Approval atomically deactivates the old device and activates the
new one; the old device may only use its previously issued lease.

## Bootstrap admin API

These temporary Phase 6 endpoints require `Authorization: Bearer
<ADMIN_API_TOKEN>`, `Idempotency-Key` and a UUID in `X-Request-Id`:

- `POST /v1/admin/customers` with `display_name` and optional `contact`;
- `POST /v1/admin/licenses` with `customer_id`;
- `POST /v1/admin/activation-codes` with `license_id` and one of `P1M`, `P3M`,
  `P6M`, `P12M`, `PERPETUAL`, `CURRENT`;
- `POST /v1/admin/transfers/approve` with `license_id` and `transfer_code`.

Plain activation and transfer codes are returned exactly once. Audit rows never
contain those plaintext values.

### Current-term recovery (`CURRENT` / «Текущая»)

#### Local compatibility gate — 2026-11-04

Prepared on `codex/multisource-auctions`; no production migration/deployment.
`protocol-v1 legacy clients keep check and renewal semantics after CURRENT recovery`
in `license-service/tests/license-service.test.mjs` exercises the locally built
Worker with synthetic signed protocol-v1 requests. With
`SEARCHCAR_COMPAT_BACKENDS` set to a JSON array of absolute backend directories,
it additionally runs `scripts/check_license_lease_compatibility.py` against each
real historical Python consumer. `SEARCHCAR_COMPAT_PYTHON` selects the interpreter.
Without the array the Worker assertions run, but historical consumers are NOT tested.

Verified consumers: `v0.3.1` (`3a033cff37b1e8610ffb1f0a0dd79a69df7b60e5`),
`v0.4.1` (`4d574b6de87b5599c2920ca05daaae136257775f`) and the working backend.
The historical backends were extracted with `git archive` into disposable
`work/license-compat.D2nMtP/` directories. They accepted the new Worker's signed
CURRENT/check leases and retained the same expiry instant. The Worker preserved
the complete license row during recovery/check, emitted no recovery marker on
ordinary check/renewal and applied a subsequent P1M renewal once. This verifies
the historical entitlement reader, NOT the entire installed client activation
flow, keychain/DPAPI, updater or Windows/macOS GUI.

`backend/tests/test_license_service_upgrade.py` checks applying missing migration
0007 AFTER 0008 on a populated disposable SQLite database: exact existing
licenses, source restrictions, devices, codes and renewal rows survive, foreign
keys remain valid, and the legacy activation-code table/update/renewal SQL still
works. This is SQLite schema compatibility, not a rehearsal on a production D1
backup or an API test of a rolled-back Worker. A rolled-back old Worker cannot
redeem newly created CURRENT codes; do not present rollback as full feature parity.

Results: Worker dry build + TypeScript pass; 20 Worker tests pass (no skips),
including all three historical/current consumers; 194 targeted backend tests
pass, 2 existing opt-in cases skipped. An initial date-format-only assertion was
corrected to compare instants; an initially shared synthetic subject hash was
replaced with a unique hash. No product behavior was changed to pass these tests.

Before rollout: rehearse backup/restore and the actual pending migration against
a disposable D1 copy, then installed old-client check/ordinary redemption and
new-client recovery on Windows/macOS. Keep signing keys, key IDs, protocol-v1
endpoints and existing entitlement policies unchanged. Deploy only after explicit
approval and a fresh encrypted backup. Stop CURRENT issuance if rolling back the
Worker; preserve additive schema instead of dropping recovery tables or restoring
an old database over intervening renewals. Recovery does not repair missing device
identity; that still requires the existing owner-approved transfer flow.

Continuation 2026-11-04: the SQLite upgrade test now logically exports/restores
the populated upgraded database, comparing all legacy rows plus a recovery code,
checking the restored view and foreign keys. Together with current client tests:
34 passed. This is NOT an encrypted production backup or a D1 export rehearsal.

`scripts/license_server_compatibility_smoke.mjs LEGACY_BUNDLE REPORT_JSON` prepares
a stronger opt-in HTTP test: real current Python `LicenseServiceClient` trial,
check and P1M redemption against the old v0.4.1 Worker, then CURRENT/P1M against
the current Worker, then ordinary check/P1M after code rollback. Miniflare owns
one temporary persistent D1 simulation, the proxy binds only 127.0.0.1, keys and
codes are synthetic; app data and production are never used. The Python probe
uses a fixture identity store, so this does not test keychain/DPAPI. Select the
Python interpreter with `SEARCHCAR_COMPAT_PYTHON`; report paths must be disposable.
Initial execution was blocked by loopback permissions. After permission was
granted, the tag v0.4.1 Worker proved unsuitable as the old-server baseline:
that tag already contains recovery logic, unlike the actual deployed candidate.
Use the exact preserved deployment bundle described below, not the app version
as a proxy for deployed server code.

#### HTTP rollout verification — completed locally 2026-11-04

**Production rollout completed 2026-11-04:** after explicit user authorization,
fresh encrypted GitHub backup run `41911502024` completed; downloaded archive and
encrypted SQL checksums verified. Remote 0001–0008 were already applied, including
0007's exact recovery table/view, so NO migration was executed. Deployed tested
Worker version `13a582c3-f948-4d98-b3dc-ac706c505a51` with `--keep-vars`, preserving
the signing identity, secrets, source policies and existing cron. /health and
/owner pass; owner HTML includes CURRENT. Before/after aggregate license/device/
renewal/source-policy totals match. Signed synthetic unknown-device checks for
protocol-v1 client versions 0.3.1/0.4.1 fail closed with LICENSE_NOT_AVAILABLE.
No customer code was issued/redeemed and no installed-client positive recovery
is claimed. Details, checksums, backup and rollback IDs:
`work/license-recovery-rollout.K5oRrP/REPORT.md`. Old-client local compatibility
evidence remains below. Earlier local-only/no-remote statements are historical.
The previous macOS release-config app excluded the new recovery handler; rebuild
it before testing repair of corrupt trusted-time. No desktop release was published.

Baseline: `work/license-sources-rollout-20261030.JMMsbx/license-service/dist/index.js`,
SHA256 `a49332fb5b3f51c413ae162ff34fa6079821ee85b811b48c295ee835eeb12ba8`,
matching the preserved production deployment report (Worker
`e83734b5-ba3f-435f-8a63-f62efae8c316`). That report says schema migration 0007
was ALREADY present before the source-only rollout. Current production state has
not been queried by this test: check applied migrations before any future rollout;
do not blindly reapply 0007 or treat it as definitely pending.

The runner now passed with current, v0.3.1 and v0.4.1 Python client implementations
over real loopback HTTP, each with its own disposable Miniflare D1/identity:
old server trial/P1M → current server check/CURRENT/P1M → old server check/P1M.
It redeems codes issued before each server switch, asserts exactly one unchanged
device/public key and one license across phases, and exactly one renewal per P1M
(counts 1→2→3, no extra renewal for CURRENT). The current client additionally
repairs a corrupt trusted-time file through signed CURRENT, preserves the expiry
and device identity, and retains an exact backup of the invalid file. Old-client
runs deliberately do not corrupt their time state or claim that repair feature.
Both already-present and late-0007 schema variants passed for the current client.
The fixture store originally serialized seed bytes as JSON; it was corrected to
binary storage. No product behavior changed for these checks.

Reports: `work/license-compat.D2nMtP/rollout-existing.json`, `rollout-late.json`,
`rollout-client-v031.json`, `rollout-client-v041.json`. Each records server/client
hashes, schema mode and completed phases. Reproduce with
`SEARCHCAR_COMPAT_PYTHON=<python>` and `SEARCHCAR_COMPAT_RECOVERY_SCHEMA=existing`
using `node scripts/license_server_compatibility_smoke.mjs <baseline-bundle> <report>`.
For historical clients add `SEARCHCAR_COMPAT_CLIENT_BACKEND=<archived-backend>`
and `SEARCHCAR_COMPAT_REPAIR_TIME=0`. A failure removes the old success marker.

This verifies source-client/Worker API and migration compatibility, not installed
executables, native secret stores, actual production D1 migration/backup or live
service availability. The tested rollback is ONLY to that exact deployed bundle;
arbitrary earlier Workers may not preserve all-source policies. Stop CURRENT
issuance on rollback: ordinary checks/renewals survive, but the old Worker cannot
redeem recovery codes. No remote state was changed and no app was installed.

The owner duration selector includes **«Текущая»**. This is a recovery operation
on an already device-bound license, not a new subscription or a zero-month
renewal. It preserves the exact current server expiry, license kind (including
trial/perpetual), activation count and source policy. A paid renewal performed
after recovery-code creation is not overwritten: the latest server expiry is
used. Expired subscriptions remain expired; suspended/deleted licenses and
foreign devices cannot use the code. A missing device identity needs the
separate owner-approved transfer flow.

Migration `0007_license_recovery_codes.sql` adds a separate recovery-code table
and the `all_activation_codes` read view, leaving renewal history and existing
foreign keys intact. New codes expire after 30 days before first use. Redemption
is audited once, without a renewal; retries from the same active device return
a new signed lease without applying a term. The owner code list displays
**«Текущая · восстановление»**.

Only a verified lease from that redemption authorizes local recovery. Its
signed `recovery` marker contains the request nonce and device public key. The
desktop checks these, the signature and a five-minute server-time window before
attempting recovery. Unparsable `trusted-time.json` is copied to an excluded
local `trusted-time.json.invalid-<uuid>` file before fresh server time replaces
it. The device secret is not changed. Readable clock guards (including rollback
errors), permission/locking errors, links and oversized files are not reset.
Unsigned response flags cannot authorize recovery.

Rollout order: back up D1, check applied migrations and apply **0007** only if pending, deploy the Worker, then
distribute a desktop build containing the recovery handler. Older clients can
accept the signed current-term lease without extending the subscription, but
**0.3.1 cannot repair a corrupt trusted-time file**. The external Windows
diagnostic remains read-only and independent of this feature. Implementation
and local tests do not perform a production migration, deployment or release.

## Owner management panel

The Worker now provides the Phase 8 owner-session foundation:

- `POST /v1/owner/bootstrap` creates the first owner once and requires the
  bootstrap `ADMIN_API_TOKEN` bearer secret;
- `POST /v1/owner/login` creates a 12-hour `HttpOnly`, `Secure`,
  `SameSite=Strict` owner session;
- `GET /v1/owner/session` returns the currently authenticated owner;
- `GET /v1/owner/dashboard` returns the owner-visible customers, licenses,
  activation codes, source grants and transfer requests;
- `POST /v1/owner/license-sources` replaces the allowed sources for one
  license. Its JSON body is `{ "license_id": "…", "source_keys": ["encar"] }`;
  an empty array deliberately disables all parser sources for that license;
  `["*"]` restores all current and future active sites (wildcard cannot be mixed
  with explicit keys). The owner panel exposes “Все сайты, включая новые”;
- `POST /v1/owner/licenses/delete` irreversibly revokes one license. It
  requires `{ "license_id": "…", "confirmation": "DELETE" }`, deactivates
  its device and unused codes, and records `LICENSE_DELETED` in the audit log;
- `POST /v1/owner/activation-codes/diagnose` checks a supplied code against
  the current D1 binding and hash formats without exposing stored hashes;
- `POST /v1/owner/logout` requires a same-origin request and clears the
  session.

Passwords use a unique random salt plus the separate
`OWNER_PASSWORD_PEPPER` Worker secret. The owner routes enforce a strict
login rate limit; this intentionally avoids a high-iteration KDF that exceeds
the 10 ms CPU allowance on a Workers Free plan. `GET /owner` serves the
same-origin owner panel: it creates customers and subscription placeholders,
generates one-time activation codes, approves pending device transfers, and
shows recent device history plus the audit journal. Each mutation is
CSRF-protected and idempotent; the plaintext activation code is displayed only
at creation time. Do not send an owner password or either secret in chat,
source control or browser query parameters.

## Source entitlements

Migration `0003_source_entitlements.sql` creates `source_catalog` and
`license_sources`. Originally it granted only `encar` by default.
Migration `0006_default_source_access.sql` makes new trial/paid licenses default
to all active sites (`licenses.sources_all = 1`). Existing owner restrictions,
including empty grants, retain their explicit policy via the successful
`LICENSE_SOURCES_UPDATED` audit record. Default legacy Encar grants remain
compatible but no longer limit an all-sites policy. An all-policy lease still
contains explicit signed active catalog keys, not a wildcard: older applications
can read it, and normal refresh picks up newly activated catalog sites. Only
shipped/approved sources should be activated in the production catalog.
Migration `0008_auction_source_catalog.sql` registers the shipped Copart and IAA
preview sites without replacing grants or overriding an inactive catalog entry.

Production rollout completed with explicit approval on 2026-10-30: encrypted D1
backup, migrations 0006 and 0008, then Worker version
`e83734b5-ba3f-435f-8a63-f62efae8c316`. Encar, Copart and IAA are active;
existing explicit restrictions remain intact. The `/owner` panel now exposes
“Доступ к источникам”: select a license, keep “Все сайты, включая новые” or
choose a subset, then save. The installed application picks up changes through
“Проверить сейчас” or its next license check; no new activation code is needed.
This does not add auction adapters to a desktop build that omits them. The
isolated rollout did not include uncommitted license-recovery changes; evidence
is in `work/license-sources-rollout-20261030.JMMsbx/REPORT.md`.

Deploy separately through the existing authorized license-service workflow:
back up D1, apply migration 0006 **before** deploying the new Worker, then smoke
trial/paid check, subset/empty/all and owner dashboard. Local implementation and
Miniflare tests do not change production rights. Rolling back only the Worker
to the old grants-only implementation loses all-policy behavior; do not remove
the migration or assume an old Worker honors `sources_all`.

Source access and license lifecycle are deliberately independent. Saving an
empty source list does not suspend, unbind or delete the license. The next
license check returns a valid signed lease with `search: false` and
`sources: []`; the desktop keeps the license and device identifiers while
blocking new searches. Re-enabling `encar` restores access on the next check
without a new activation code.

Migration `0004_license_deletion.sql` adds deletion audit fields. A deleted
license is retained as a tombstone rather than physically removed, so its
history and foreign-key integrity remain available. `/v1/licenses/check`
returns HTTP 410 with `LICENSE_DELETED`; desktop then removes only its local
binding, lease and trusted-time files. The Keychain/DPAPI device identity is
retained, and a replacement owner-issued activation code may bind a new
license to that device.

Migration `0005_device_rebinding.sql` makes that replacement flow possible
without losing device history: inactive rows may retain the same public key,
while a partial unique index still permits only one active binding for that
key at a time.

Before deploying this version, apply the migration once to the production D1
database, then deploy the Worker:

```bash
pnpm dlx --yes wrangler@4.123.0 d1 migrations apply searchcar-license-production \
  --remote --config license-service/wrangler.jsonc
pnpm exec wrangler deploy --config license-service/wrangler.jsonc
```

After changing a source grant, the customer uses **«Проверить сейчас»** in the
desktop license settings to receive a fresh signed lease. Existing cached
leases remain valid only until their normal short lease expiry.

After adding migration `0004`, apply migrations before deploying the Worker:

```bash
unset CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN
pnpm dlx --yes wrangler@4.123.0 d1 migrations apply searchcar-license-production \
  --remote --config license-service/wrangler.jsonc
pnpm dlx --yes wrangler@4.123.0 deploy --config license-service/wrangler.jsonc
```

## Local setup

1. Replace `REPLACE_WITH_D1_DATABASE_ID` in
   `license-service/wrangler.jsonc` after creating the database.
2. Generate an Ed25519 key:

   ```bash
   node scripts/generate_license_signing_key.mjs
   ```

3. Store the private value and other secrets:

   ```bash
   pnpm exec wrangler secret put LICENSE_SIGNING_PRIVATE_KEY --config license-service/wrangler.jsonc
   pnpm exec wrangler secret put CODE_PEPPER --config license-service/wrangler.jsonc
   pnpm exec wrangler secret put RATE_LIMIT_PEPPER --config license-service/wrangler.jsonc
   pnpm exec wrangler secret put ADMIN_API_TOKEN --config license-service/wrangler.jsonc
   pnpm exec wrangler secret put OWNER_PASSWORD_PEPPER --config license-service/wrangler.jsonc
   ```

   `wrangler.jsonc` declares these five names as required secrets, so a future
   deployment fails before publishing if any of them is missing. Do not rotate
   `CODE_PEPPER` during normal operation: it invalidates unused legacy activation
   codes, transfer codes and cached idempotency responses. Activation codes issued
   by the current service use a stable versioned hash and survive this rotation.

4. Keep `license-signing-public-key.json` for the Phase 7 client. Never commit
   the private `.key` file. Copy `public_key` into the non-secret
   `LICENSE_SIGNING_PUBLIC_KEY` variable in `license-service/wrangler.jsonc`.
   Copy its `key_id` and the same `public_key`, together with the deployed
   Worker URL, into `desktop/license-service.json`. The Worker signs and
   immediately verifies every lease against this value before consuming a
   trial or activation code. Keep `enforcement` disabled for pilot builds until
   a remote trial/check/redeem smoke test succeeds; change it to `required` for
   the production build.
5. Apply migrations and run locally:

   ```bash
   pnpm license:migrate:local
   pnpm exec wrangler dev --config license-service/wrangler.jsonc
   ```

6. Validate:

   ```bash
   pnpm license:typecheck
   pnpm license:test
   ```

Miniflare tests cover trial reuse, signed server-time leases, concurrent
one-time code redemption, transfer approval/claim and old-device rejection.
They skip only when the execution sandbox prohibits loopback sockets; the
GitHub workflow runs them in a normal runner.

## Deploy

Create a D1 database named `searchcar-license-production`, update its ID in the
Wrangler config and set all five Worker secrets. Add
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` to the GitHub environment
`license-production`. Run the `License service` workflow with `deploy=true`.
Tests complete before migrations and deployment.

## Backup and recovery

Run the `Backup license service` workflow. It exports D1, encrypts the SQL dump
with AES-256-CBC/PBKDF2 using the environment secret
`LICENSE_BACKUP_PASSPHRASE`, stores only the encrypted artifact and writes a
SHA-256 checksum. Copy the artifact to offline storage before its 30-day
retention expires. Keep an offline copy of the signing private key and peppers
separately from the database archive.

To recover:

1. Verify the encrypted artifact checksum.
2. Decrypt it with the same passphrase:

   ```bash
   openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
     -in license-service.sql.enc -out license-service.sql \
     -pass env:LICENSE_BACKUP_PASSPHRASE
   ```

3. Create a new empty D1 database.
4. Import the SQL:

   ```bash
   pnpm exec wrangler d1 execute NEW_DATABASE_NAME --remote --file=license-service.sql
   ```

5. Change only the D1 `database_id` binding, restore the original Worker
   secrets, deploy, then verify `/health` and a signed license check.
6. Securely remove the decrypted SQL after verification.

Changing the signing key during database recovery invalidates outstanding
leases. Preserve the key unless rotation is intentional and coordinated with a
client release.
