# SearchCar Desktop — release runbook

This is the operational checklist for an owner or future coding agent. Follow
the steps in order. The current process produces **pilot artifacts**: macOS
uses an ad-hoc OS signature and Windows uses an OS-unsigned NSIS installer.
Both workflows additionally create updater artifacts protected by the separate
Tauri updater signature. A third manual workflow publishes only verified
artifacts to a public releases-only repository; product source remains private.

## Safety rules

- All GitHub workflows are manual. A push must not deploy a Worker, create a
  backup, or build an installer by itself.
- Never place Cloudflare tokens, private signing keys, owner passwords or
  generated activation codes in Git, issues, workflow files or release notes.
- Do not run the License service workflow with `deploy: true` unless the
  release changes `license-service/` or `license-service/migrations/`.
- Stop at the first failed check. Fix it in a new commit; do not distribute an
  artifact from a failed or partially rerun job.

## 1. Prepare the release commit

Work from the repository root on the branch that is intended for release.

```bash
git status --short
git pull --ff-only
pnpm install --frozen-lockfile
pnpm version:check
pnpm test
pnpm lint
```

`git status --short` must be empty before changing the version. Lint currently
may show known `next/image` warnings, but it must show no errors.

Choose a [SemVer](https://semver.org/) version:

- patch (`0.1.1`) — bug fix without user-visible functionality changes;
- minor (`0.2.0`) — backward-compatible user-visible functionality;
- major (`1.0.0`) — incompatible data, license or workflow change.

Edit only the `version` field in `package.json`, then synchronize the generated
metadata:

```bash
pnpm version:sync
pnpm version:check
pnpm build
pnpm desktop:frontend:build
```

`package.json` is the only authoritative version. `version:sync` updates the
Tauri bundle, Cargo package, backend health endpoint and frontend label. Do
not hand-edit their version strings.

Run relevant Python tests when Python 3.12 and project dependencies are
available:

```bash
export PYTHONPATH="$PWD/backend"
export STORAGE_ROOT="/private/tmp/searchcar-release-storage"
export DATABASE_URL="sqlite+pysqlite:////private/tmp/searchcar-release.sqlite3"
python -m pytest backend/tests -q
```

Commit and push the completed release candidate:

```bash
git add -A
git commit -m "release: vX.Y.Z"
git push
git tag -a vX.Y.Z -m "SearchCar Desktop vX.Y.Z"
git push origin vX.Y.Z
```

Replace `X.Y.Z` with the selected version. Do not tag until the commit has
passed local checks.

## 2. Run Cloudflare checks only when needed

If the release contains no changes below `license-service/`, skip this section.

1. In GitHub, open **Actions → Backup license service → Run workflow**.
   Wait for it to complete and download/store the encrypted backup artifact.
2. Open **Actions → License service → Run workflow** with `deploy` set to
   `false`. It runs typechecking and Worker/D1 tests only.
3. If that run is green, start the same workflow again with `deploy` set to
   `true`. It applies pending D1 migrations, then deploys the Worker.
4. Open the Worker `/health` endpoint and perform one owner-panel smoke check.

Run the backup before every remote D1 migration. Never use `deploy: true` just
to build desktop installers.

## Updater signing key setup (one time)

The Tauri updater key is independent from the Cloudflare license signing key.
Generate it once on the owner's trusted Mac:

```bash
zsh scripts/generate_tauri_updater_key.command
```

The script asks for a strong password and writes the pair outside the Git
repository, by default to the sibling directory
`.searchcar-release-secrets`. The private file is mode `600`; the directory is
mode `700`. Back up the private key and password separately. Losing either one
prevents future installed applications from accepting new updates.

In GitHub open **Settings → Secrets and variables → Actions → New repository
secret** and add:

- `TAURI_SIGNING_PRIVATE_KEY`: the complete contents of
  `searchcar-updater.key`;
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`: the password chosen during generation.

Copy the private key to the clipboard without printing it:

```bash
pbcopy < ../.searchcar-release-secrets/searchcar-updater.key
```

The `.pub` file is not secret. Its checked copy is
`desktop/updater-public-key.txt`, and tests require `tauri.conf.json` to contain
the same value. Do not replace it with a placeholder or with the Cloudflare
license public key.

## Public releases-only repository setup (one time)

Create a public GitHub repository named
`KaplunSergey/searchcar-desktop-releases`. It contains only installers,
updater signatures, release notes and `latest.json`; do not copy the product
source into it. Initialize it with a README so it has a default branch.

Create a fine-grained personal access token restricted to that repository with
**Contents: Read and write**. In the private source repository open
**Settings → Secrets and variables → Actions** and save the token as
`RELEASES_REPO_TOKEN`. Never paste this token into a workflow input or log.

Installed applications use the anonymous endpoint:

```text
https://github.com/KaplunSergey/searchcar-desktop-releases/releases/latest/download/latest.json
```

## 3. Build the installers in GitHub Actions

Open **Actions** in the GitHub repository and start both workflows from the
release tag or the release commit:

1. **macOS desktop pilot → Run workflow**
2. **Windows desktop pilot → Run workflow**

Leave `include_diagnostic_standalone` disabled. It is a slow troubleshooting
build, not a normal release prerequisite. Each normal workflow runs tests,
checks version metadata, creates its native installer and updater artifact,
requires both updater signing secrets, verifies each `.sig` cryptographically
against `desktop/updater-public-key.txt`, and uploads a SHA-256 checksum.

The Windows workflow keeps one bounded Nuitka compiler cache shared across
commits with unchanged desktop build requirements. It stores compiler objects
only: never installers, updater bundles, signatures, browser files or a new
cache for every commit. The first cache-seeding build can still take a long
time; later builds print a heartbeat every two minutes and upload a small
Nuitka compilation report with the normal pilot artifact.

Wait for both runs to be green. A workflow blocked by billing or a cancelled
run is not a successful build and must be re-run after the account issue is
resolved.

## 4. Verify and distribute artifacts

Download the artifacts from the successful workflow runs:

- macOS: `SearchCar-Desktop-macOS-arm64.zip`,
  `SearchCar-Desktop-macOS-arm64.app.tar.gz`, its `.sig`, and
  `SHA256SUMS.txt`;
- Windows: `SearchCar-Desktop-Windows-x64-setup.exe`, its `.sig`, and
  `SHA256SUMS.txt` plus smoke metadata.

On macOS, verify the archive before distribution:

```bash
shasum -a 256 SearchCar-Desktop-macOS-arm64.zip
cat SHA256SUMS.txt
```

The calculated value must match the artifact checksum. Use the corresponding
SHA-256 command on Windows or compare it in PowerShell:

```powershell
Get-FileHash .\SearchCar-Desktop-Setup.exe -Algorithm SHA256
```

Install and smoke-test each artifact on a clean target machine before sending
it to customers:

1. Start the application.
2. Enter a newly issued activation code on a clean local database.
3. Create a project and perform one real Encar scan.
4. Verify that the license panel displays the expected license and source
   entitlement.
5. Create a local backup and verify it.
6. Close the application during a scan and confirm that it asks for exit and
   saves a partial/cancelled result safely.

Because pilot artifacts are not commercially signed/notarized, macOS Gatekeeper
and Windows SmartScreen may show warnings. Do not advise customers to disable
system security globally. Commercial signing is a later phase.

After both clean-machine checks pass, copy each run ID from the number at the
end of its URL (`.../actions/runs/123456789`). Open
**Actions → Publish desktop release → Run workflow** and enter the version
without `v`, both successful run IDs and concise customer-facing release
notes.

The workflow downloads those exact Actions artifacts, verifies checksums and
requires both updater signature files, then binds their contents into the
manifest and uploads everything to a draft release. It also requires that the
workflow itself was launched from the immutable matching source tag and checks
the exact final asset list, including the public `SHA256SUMS.txt`. The installed Tauri client
performs the cryptographic signature verification. Only after all assets and
`latest.json` exist does the workflow publish the release as `Latest`. A failed
publication therefore leaves installed clients on the previous release.

### Publish the updater manifest

The repository includes a strict generator for the static `latest.json` format
used by the enabled native Tauri updater. The application checks at startup,
then every six hours, and manually from the tray menu or the button next to the
sidebar version. A native dialog appears when an update is found. Never publish
a manifest until both platform artifacts and signatures are from successful
workflows.

The normal owner path is the publication workflow above. For offline recovery,
generate `latest.json` manually only after both signed updater bundles and
their `.sig` files are available:

```bash
pnpm release:updater-manifest -- \
  --version X.Y.Z \
  --notes-file release-notes.md \
  --darwin-aarch64-url "https://github.com/KaplunSergey/searchcar-desktop-releases/releases/download/vX.Y.Z/SearchCar-Desktop-macOS-arm64.app.tar.gz" \
  --darwin-aarch64-signature-file SearchCar-Desktop-macOS-arm64.app.tar.gz.sig \
  --windows-x86_64-url "https://github.com/KaplunSergey/searchcar-desktop-releases/releases/download/vX.Y.Z/SearchCar-Desktop-Windows-x64-setup.exe" \
  --windows-x86_64-signature-file SearchCar-Desktop-Windows-x64-setup.exe.sig \
  --output latest.json
```

The generator refuses non-HTTPS URLs, malformed versions and missing
signatures. Upload the resulting `latest.json` to that same public release as
the final step. Never point installed applications back to the private source
repository because customers cannot anonymously download its assets.

## 5. Rollback

There is no automatic updater rollback yet. If an installer is defective:

1. Remove or mark the GitHub Release as a bad release; do not delete its audit
   trail or checksums.
2. Stop distributing its files.
3. Keep Worker/D1 unchanged unless the fault is in the license service.
4. Give pilot customers the last verified installer and document the affected
   version.
5. Fix the issue, increment the patch version and repeat this runbook.

If a Worker/D1 migration is involved, restore only through the documented D1
backup and recovery procedure in `docs/LICENSE_SERVICE.md`; do not attempt SQL
changes by guesswork during an incident.

## Completion record

For every released version, record in the GitHub Release notes or internal
release issue:

- version, commit SHA and tag;
- links to the green macOS and Windows workflow runs;
- checksums of the distributed artifacts;
- whether a Worker/D1 deploy occurred and its Worker version ID;
- names of the clean test machines and the result;
- known limitations or required customer instructions.
