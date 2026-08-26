# SearchCar license service

This is the Phase 6 Cloudflare Worker and D1 foundation. Client-side key
storage, offline lease verification and entitlement enforcement belong to
Phase 7. The Phase 6 admin endpoints use a bootstrap bearer secret. Phase 8
adds a short-session owner login; the bootstrap secret is then retained only
for the one-time creation of the first owner and emergency recovery.

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
  `P6M`, `P12M`, `PERPETUAL`;
- `POST /v1/admin/transfers/approve` with `license_id` and `transfer_code`.

Plain activation and transfer codes are returned exactly once. Audit rows never
contain those plaintext values.

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
`license_sources`. It grants `encar` to existing active licenses, and new
trials/new owner-created licenses also receive it by default. The current
signed lease contains `entitlements.sources`; the desktop backend permits an
Encar scan only when that signed list contains `encar`.

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
