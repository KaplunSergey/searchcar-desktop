# Phase 9 acceptance: backup and device transfer

This runbook completes the installed macOS ↔ Windows validation for Phase 9.
Use a real source computer and a clean target computer. Do not delete the source
application or its data until every check passes.

## 1. Deploy the Worker update

No D1 migration is required for this Phase 9 increment. From the repository
root on macOS, run:

```bash
unset CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN
export WRANGLER_LOG_PATH="$PWD/.wrangler/wrangler.log"
pnpm dlx --yes wrangler@4.123.0 deploy \
  --config license-service/wrangler.jsonc
```

The command must finish with the production URL:

```text
https://searchcar-license-service.mapatar23.workers.dev
```

Open `/health` and then `/owner`. The owner panel must contain the updated
`Перенос устройства` card with old/new device confirmation and the backup
warning.

## 2. Build both applications

Build a fresh macOS application from the repository root on macOS:

```bash
SEARCHCAR_NO_PAUSE=1 zsh scripts/build_mac_fixed.command
```

The result is `SearchCar Desktop Fixed.app` in the parent `Codex` directory.

On the Windows computer, pull the same commit and run a complete build in
PowerShell 7:

```powershell
git pull
& ".\Build SearchCar for Windows.ps1" -NoPause
```

Do not use `-ResumeAfterSidecar` for this Phase 9 build: both the backend and
the frontend changed. The installer is created at
`work\windows-artifact\SearchCar-Desktop-Windows-x64-setup.exe`.

## 3. Prepare the source computer

1. Open SearchCar and select `Settings`.
2. Record the current license ID, device ID and project/car counts.
3. Click `Create backup`, save the `.searchcar-backup` file outside the
   SearchCar data directory and keep the application open.
4. Copy the archive to the target computer without modifying it.

## 4. Prepare the clean target computer

1. Install the newest SearchCar build.
2. Start it with a clean SearchCar data directory. The first screen must show
   both `Новый код` and `Перенос лицензии`.
3. Select `Перенос лицензии`, click `Создать запрос переноса` and send only the
   displayed `TR-...` code to the owner. Never request or copy a claim token.
4. Keep this window open until approval is complete.

## 5. Approve in the owner panel

1. Open `/owner` and sign in.
2. In `Перенос устройства`, select the pending request and the source
   computer's license.
3. Confirm that the summary contains the expected customer, old device and new
   device. Do not continue if any of them is wrong.
4. Click `Одобрить перенос`, read the final confirmation and approve it.

## 6. Claim and restore on the target

1. Return to the target application and click `Завершить перенос`.
2. Confirm that the same license ID is active and the device ID is new.
3. Import the `.searchcar-backup` archive through Settings.
4. Restart if requested and compare project/car counts with the source.
5. Reopen License settings. The target license ID and new device ID must be
   unchanged after restore. This proves that the archive did not carry source
   license state.

## 7. Verify the old binding

1. On the source computer click `Проверить сейчас` in License settings.
2. The server must refuse a new lease for the old device. The already cached
   offline lease may remain usable until its signed expiration, currently at
   most 48 hours; this is expected.
3. In `/owner`, confirm that the source device is inactive and the target
   device is the only active device for the license.
4. Run a search on the target and verify a normal result.

## 8. Test both directions

Repeat sections 3–7 for both combinations:

- macOS source → Windows target;
- Windows source → macOS target.

Phase 9 is complete only after both rows pass on installed applications.

| Direction | Backup restored | License unchanged after restore | Old renewal rejected | Target search |
| --- | --- | --- | --- | --- |
| macOS → Windows | ☐ | ☐ | ☐ | ☐ |
| Windows → macOS | ☐ | ☐ | ☐ | ☐ |
