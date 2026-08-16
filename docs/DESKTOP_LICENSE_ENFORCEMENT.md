# Desktop license enforcement

Phase 7 adds defense in depth around every operation that can start the Encar
scanner. The backend enforcement, device identity, Cloudflare synchronization,
first activation UI and native preflight are implemented locally. The bundled
pilot configuration now uses `enforcement: required`.

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
├── device-key.macos  # macOS fallback for unauthorized ad-hoc builds; mode 0600
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

The macOS preview configuration explicitly sets
`"device_key_storage": "file-preview"`, so local ad-hoc builds never depend
on Keychain authorization. Its device seed is an excluded mode-0600 file in
the license directory. A production macOS configuration must remove that
setting (or set `"keychain"`) and use the user's Keychain instead. On Windows,
the license directory contains only a current-user DPAPI ciphertext.
The Worker receives the public key and its domain-separated SHA-256 fingerprint;
raw hardware identifiers are not collected. Trial subject text is normalized
and hashed locally before transmission.

An existing binding is checked shortly after application startup and every six
hours. Each response lease is verified locally before `binding.json` or
`lease.json` is replaced.

The current pilot requires an active signed license for every search. A
brand-new desktop may still open the activation screen, but it cannot queue or
start a scan before receiving a valid lease. The unsigned macOS preview keeps
its file-preview identity only for this pilot; a signed production release
will use Keychain storage.

`GET /api/desktop/license` exposes the non-secret entitlement decision to an
authenticated desktop user. Administrator-only CSRF-protected endpoints start
a trial, redeem an activation code, refresh an existing lease and create or
claim a transfer from the destination device. A transfer request displays only
the short owner code; its claim token stays in memory and is never written to
the local license files or a backup. The owner approves the short code through
the Phase 8 owner admin surface (the bootstrap admin API remains the pilot
fallback).

## Remaining Phase 7 work

1. Rebuild and manually validate the required pilot configuration with an
   active license, a fresh unlicensed data directory and a deliberately
   tampered lease.
2. Complete the owner approval/recovery surface in Phase 8. Destination-device
   request and claim controls are implemented locally.
3. Repeat lease verification in Rust before starting the sidecar. This native
   gate is implemented and covered by a Rust unit test.
4. Validate Keychain, DPAPI, offline grace, copied state and fingerprint
   behavior in installed macOS and Windows applications. Windows build and
   pilot verification are explicitly deferred until after the current macOS
   license-service rollout.

The native preflight permits a brand-new desktop with no binding, and an
authentic but expired lease, to reach the activation/refresh UI. It rejects an
incomplete state, a changed binding, an unknown signing key or an invalid
signature before launching the sidecar. The backend remains the authoritative
scan gate for expiry, offline grace and clock rollback.
