# Desktop license enforcement

Phase 7 adds defense in depth around every operation that can start the Encar
scanner. The backend enforcement, OS-protected device identity, Cloudflare
synchronization and first activation UI are implemented locally.

## Current enforcement points

- manual project and car enqueue requests;
- automatic scheduler enqueue;
- worker start immediately before Playwright/Chromium launches.

When enforcement is required and no valid entitlement exists, existing cars,
reports and backups remain readable. New searches are blocked. A scheduled run
is not queued and its next check is re-anchored by the configured interval,
preventing a tight retry loop.

## Signed state

License state is deliberately stored outside SQLite under the desktop data
directory:

```text
license/
├── binding.json
├── lease.json
├── trusted-time.json
└── device-key.dpapi  # Windows only; current-user encrypted
```

`binding.json` contains only the assigned `license_id` and `device_id`.
`lease.json` is the complete `{ payload, signature }` response from the
Cloudflare service. `trusted-time.json` records the highest authenticated server
time and highest observed local time. All files are excluded from
`.searchcar-backup`, so restoring or copying an application backup cannot copy
or extend a license.

The backend verifies the Ed25519 signature over the exact Phase 6 canonical
message, checks the binding, and uses the greatest trusted time seen. A local
clock rollback greater than five minutes blocks search until a newer online
lease repairs trusted time. Offline use ends at the earlier of
`lease_expires_at` and `subscription_expires_at`; perpetual licenses have no
subscription boundary but still require periodic signed leases.

## Configuration

Production desktop releases will set:

- `SEARCHCAR_LICENSE_ENFORCEMENT=required`;
- `SEARCHCAR_LICENSE_PUBLIC_KEY_FILE` to the bundled public key document, or
  `SEARCHCAR_LICENSE_PUBLIC_KEYS_JSON` to a key-id/base64url map during key
  rotation.

Installed applications read the equivalent non-secret values from
`desktop/license-service.json`, bundled as `license-service.json`:

```json
{
  "protocol_version": 1,
  "service_url": "https://your-worker.workers.dev",
  "public_keys": { "production-v1": "<base64url Ed25519 public key>" },
  "enforcement": "required"
}
```

The device seed is stored in the user's macOS Keychain. On Windows, the
license directory contains only a current-user DPAPI ciphertext. The Worker
receives the public key and its domain-separated SHA-256 fingerprint; raw
hardware identifiers are not collected. Trial subject text is normalized and
hashed locally before transmission.

An existing binding is checked shortly after application startup and every six
hours. Each response lease is verified locally before `binding.json` or
`lease.json` is replaced.

The pilot currently defaults to `disabled`, which reports status `pilot` and
keeps search available. This transition mode must not be used for a commercial
release. Enabling `required` without a binding, lease or known signing key is
fail-closed.

`GET /api/desktop/license` exposes the non-secret entitlement decision to an
authenticated desktop user. Administrator-only CSRF-protected endpoints start
a trial, redeem an activation code and refresh an existing lease.

## Remaining Phase 7 work

1. Deploy Phase 6 and bundle the production Worker URL and lease-verification
   public key, then enable required mode.
2. Add transfer request/claim controls and owner-facing recovery copy.
3. Repeat lease verification in Rust before starting the sidecar.
4. Validate Keychain, DPAPI, offline grace, copied state and fingerprint
   behavior in installed macOS and Windows applications.
