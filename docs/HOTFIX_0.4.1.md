# License recovery hotfix 0.4.1

Release candidate based on main, excluding the multisource-auction branch.

The owner may generate CURRENT (Текущая) for the original device-bound license.
Redemption returns a signed recovery marker bound to the request nonce and device
public key. It does not renew the license, change its expiry or grant new sources.
Expired/suspended/deleted and foreign-device cases remain restricted.

The client verifies signature, nonce, identity and fresh server time before
backing up and rebuilding an unparsable trusted-time.json. A readable clock
guard, permission/locking failure, link or oversized file is not reset.
Existing device keys, projects and portable database backups are preserved.
Clients 0.3.1 cannot repair this failure with only a server update.

Rollout: encrypted D1 backup, migration0007 and Worker deployment; then signed
macOS/Windows installers and clean-machine smoke before public publication.
After installing, the owner supplies a CURRENT code for the existing license.

Windows run36739028851 failed after executable/directory/registry checks passed.
Probe38589559241 established a test-reader defect: WScript.Shell reports '?' in
place of the Cyrillic 'Т', whereas IShellLinkW reads the correct shortcut target.
Both real Desktop/Start Menu shortcuts, registry and executable hash passed
before/after update in Rename and Overwrite scenarios on Windows. The rename
hypothesis was not reproduced. Preserve Rename as the default regression and
use the Unicode Windows API; log the legacy reader only for diagnosis. Both
probe scenarios are strict (no accepted shortcut failures).

Local evidence: recovery regressions RED on main (7 fail,4pass), then45 license
tests GREEN; full backend224pass. Worker/D1 recovery and existing invariants:
16pass. No production mutation is implied by these local checks.

Frontend20pass after generating the production web bundle; desktop frontend
build and typechecks passed. Two Rust updater tests passed. Rust
`cargo check --offline --locked` passed with
packaging-only resources excluded from that compile check (not an installer
smoke). Lint has no errors and six pre-existing image warnings.

Production rollout is currently held: backup run38587693605 exported and
encrypted D1 but upload failed with GitHub Actions artifact storage quota.
The database has not been migrated and the Worker has not been deployed.
Do not count this failed run as a retained backup or publish until the backup,
native builds and installed-machine checks pass.

Server CI38588806437 passed against the hotfix implementation. This is a check,
not a production deployment. The Windows probe used the checksum-verified
published0.3.1 installer; the0.4.1 installer still needs its native build/smoke.
