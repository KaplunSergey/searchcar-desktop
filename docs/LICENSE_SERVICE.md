# SearchCar license service

This is the Phase 6 Cloudflare Worker and D1 foundation. Client-side key
storage, offline lease verification and entitlement enforcement belong to
Phase 7. The Phase 6 admin endpoints use a bootstrap bearer secret; the
short-session owner login and web UI replace that bootstrap in Phase 8.

## Security and protocol invariants

- Ed25519 device proofs authenticate every public mutation and check.
- Ed25519 leases are signed over canonical JSON with server-provided time.
- Request timestamps have a five-minute acceptance window.
- Every POST requires an idempotency key and a unique request UUID.
- Trial uniqueness is enforced independently for the privacy-safe subject hash
  and fingerprint hash.
- Activation and transfer codes are persisted only as peppered SHA-256 hashes.
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
   ```

4. Keep `license-signing-public-key.json` for the Phase 7 client. Never commit
   the private `.key` file.
   Copy its `key_id` and `public_key`, together with the deployed Worker URL,
   into `desktop/license-service.json`. Keep `enforcement` disabled for pilot
   builds until a remote trial/check/redeem smoke test succeeds; change it to
   `required` for the production build.
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
Wrangler config and set all four Worker secrets. Add
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
