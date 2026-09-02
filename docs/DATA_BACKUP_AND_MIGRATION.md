# Desktop backup and data migration

This guide covers the desktop data tools. They are available to the single
hidden desktop workspace in **Settings → Desktop application data**. A legacy
one-user database is converted in place without changing its projects or
history. If an unexpected multi-user desktop database is found, SearchCar
first saves a verified backup and then starts with a clean workspace; ordinary
web users do not receive desktop data permissions.

## What a backup contains

A `*.searchcar-backup` file is a ZIP-compatible, versioned container with:

- a consistent SQLite copy made through the SQLite Online Backup API;
- locally stored car images and listing screenshots;
- a manifest with the application and database schema versions;
- SHA-256 checksums for every payload file;
- row counts for every application table.

Authentication sessions are removed from the disposable database copy before
it enters the archive and are cleared again during restore. Future license
state and device private keys are deliberately stored outside this format, so
copying a backup cannot copy or extend a license.

Creating a backup temporarily prevents new manual or scheduled searches from
starting. If a search is already queued or running, the application asks the
user to finish or cancel it first.

## Create a backup

1. Open **Settings**.
2. In **Desktop application data**, select **Create backup**.
3. Wait for the success notification. The application validates the new file
   before displaying it in the list.
4. Select **Download** next to the new item and save the complete
   `*.searchcar-backup` file on external storage. Depending on the operating
   system settings, it may be saved directly in **Downloads**.

The exact data directory is still shown for support diagnostics, but a normal
user does not need to open or modify it manually.

## Restore or move to another computer

1. Install SearchCar Desktop on the destination Windows or macOS computer.
2. Open **Settings → Desktop application data**.
3. Select **Import backup** and choose the complete `*.searchcar-backup` file.
4. Wait until the application verifies its format, checksums and SQLite data
   and displays it in the list. A rejected file does not enter local backups.
5. Select **Restore** next to the imported backup and confirm.
6. Close and reopen the application. Restore runs before the backend and scan
   worker start.
7. The passwordless desktop workspace signs in automatically. Authentication
   sessions are intentionally not transferred.

Before replacing anything, restore creates a verified
`pre-restore-*.searchcar-backup` of the current installation. The database and
the `storage/cars` folder are switched only after archive, checksum, SQLite
integrity and foreign-key checks pass. If switching fails, the old database and
storage folder are moved back.

## Migrate the previous Docker/PostgreSQL installation

The source is opened in a PostgreSQL `REPEATABLE READ`, `READ ONLY`
transaction. The converter preserves IDs and relationships, normalizes UTC
timestamps and converts machine-specific storage paths to portable relative
paths. It never writes to the PostgreSQL source.

1. Make sure no search is running in the old installation.
2. From the old project folder start its database:

   ```bash
   docker compose up -d db
   ```

3. Confirm that PostgreSQL is exposed on `127.0.0.1:5432`. The default source
   connection is:

   ```text
   postgresql+psycopg://encar:encar@127.0.0.1:5432/encar
   ```

   If `.env` contains different `POSTGRES_USER`, `POSTGRES_PASSWORD` or
   `POSTGRES_DB` values, update the connection string accordingly.

4. Find the old local `storage` folder. It is the `storage` directory next to
   `docker-compose.yml`, not the `/storage` path inside Docker.
5. In the desktop application open the migration wizard, enter the connection
   string and that local storage path, then select **Create migration copy**.
6. Review the result in the backup list. At this point current desktop data is
   unchanged.
7. Select **Restore** on the `migration-*.searchcar-backup` item and restart the
   application.

The report compares source and target row counts, verifies copied file
checksums, then runs SQLite `integrity_check` and `foreign_key_check`.
Authentication sessions are the only intentionally skipped source table.

## Maintainer command-line checks

The same operations are exposed by the sidecar for diagnostics:

```bash
python -m app.desktop_runtime backup-export \
  --data-dir /path/to/app-data \
  --output /safe/path/backup.searchcar-backup

python -m app.desktop_runtime backup-validate \
  --input /safe/path/backup.searchcar-backup

# Run only while the desktop application is fully stopped.
python -m app.desktop_runtime backup-restore \
  --data-dir /path/to/app-data \
  --input /safe/path/backup.searchcar-backup

python -m app.desktop_runtime convert-database \
  --source-url 'postgresql+psycopg://user:password@127.0.0.1:5432/database' \
  --source-storage /path/to/old/storage \
  --output /safe/path/migration.searchcar-backup
```

Do not paste a real source password into issue reports or diagnostic bundles.
