from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO
from uuid import uuid4

from .maintenance import MaintenanceBusyError, maintenance_lock
from .storage_paths import portable_storage_path


BACKUP_FORMAT = "searchcar-backup"
BACKUP_FORMAT_VERSION = 1
DATABASE_ARCHIVE_PATH = "database.sqlite3"
MANIFEST_ARCHIVE_PATH = "manifest.json"
CHECKSUMS_ARCHIVE_PATH = "checksums.json"
ACTIVE_SCAN_STATUSES = ("QUEUED", "RUNNING", "CANCEL_REQUESTED")
MAX_ARCHIVE_FILE_BYTES = 20 * 1024 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 50 * 1024 * 1024 * 1024
COPY_CHUNK_SIZE = 1024 * 1024


class BackupError(RuntimeError):
    pass


class BackupBusyError(BackupError):
    pass


class BackupValidationError(BackupError):
    pass


@dataclass(frozen=True)
class BackupValidation:
    path: str
    format_version: int
    schema_version: int
    created_at: str
    files: int
    bytes: int
    table_counts: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "format_version": self.format_version,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "files": self.files,
            "bytes": self.bytes,
            "table_counts": self.table_counts,
        }


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(COPY_CHUNK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return _sha256_stream(stream)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_archive_path(raw_name: str) -> PurePosixPath:
    if not raw_name or "\\" in raw_name:
        raise BackupValidationError(f"unsafe_archive_path:{raw_name}")
    path = PurePosixPath(raw_name)
    windows_path = PureWindowsPath(raw_name)
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise BackupValidationError(f"unsafe_archive_path:{raw_name}")
    return path


def _validate_zip_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    total_size = 0
    for info in archive.infolist():
        raw_name = info.filename.rstrip("/")
        if not raw_name:
            continue
        path = _safe_archive_path(raw_name)
        name = path.as_posix()
        if name in seen:
            raise BackupValidationError(f"duplicate_archive_path:{name}")
        seen.add(name)
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise BackupValidationError(f"archive_symlink_not_allowed:{name}")
        if info.file_size > MAX_ARCHIVE_FILE_BYTES:
            raise BackupValidationError(f"archive_file_too_large:{name}")
        total_size += info.file_size
        if total_size > MAX_ARCHIVE_TOTAL_BYTES:
            raise BackupValidationError("archive_total_size_too_large")
        if not info.is_dir():
            members[name] = info
    required = {
        DATABASE_ARCHIVE_PATH,
        MANIFEST_ARCHIVE_PATH,
        CHECKSUMS_ARCHIVE_PATH,
    }
    missing = required - members.keys()
    if missing:
        raise BackupValidationError(
            f"backup_required_files_missing:{','.join(sorted(missing))}"
        )
    return members


def _read_json_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> dict:
    try:
        value = json.loads(archive.read(info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupValidationError(f"invalid_json:{info.filename}") from exc
    if not isinstance(value, dict):
        raise BackupValidationError(f"invalid_json_object:{info.filename}")
    return value


def _sqlite_connection(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only=ON")
    else:
        connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def sqlite_schema_version(connection: sqlite3.Connection) -> int:
    table = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'desktop_schema_migrations'
        """
    ).fetchone()
    if not table:
        return 0
    return int(
        connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM desktop_schema_migrations"
        ).fetchone()[0]
    )


def sqlite_table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
              AND name != 'desktop_schema_migrations'
            ORDER BY name
            """
        )
    ]
    result: dict[str, int] = {}
    for table in tables:
        quoted_table = table.replace('"', '""')
        result[table] = int(
            connection.execute(
                f'SELECT COUNT(*) FROM "{quoted_table}"'
            ).fetchone()[0]
        )
    return result


def verify_sqlite(path: Path) -> dict:
    try:
        with _sqlite_connection(path, read_only=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise BackupValidationError(f"sqlite_integrity_failed:{integrity}")
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise BackupValidationError(
                    f"sqlite_foreign_key_failed:{len(foreign_keys)}"
                )
            return {
                "schema_version": sqlite_schema_version(connection),
                "table_counts": sqlite_table_counts(connection),
            }
    except sqlite3.DatabaseError as exc:
        raise BackupValidationError("invalid_sqlite_database") from exc


def _active_scan_count(database_path: Path) -> int:
    if not database_path.is_file():
        return 0
    with _sqlite_connection(database_path, read_only=True) as connection:
        table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'scan_runs'
            """
        ).fetchone()
        if not table:
            return 0
        placeholders = ",".join("?" for _ in ACTIVE_SCAN_STATUSES)
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM scan_runs WHERE status IN ({placeholders})",
                ACTIVE_SCAN_STATUSES,
            ).fetchone()[0]
        )


def _online_sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _sqlite_connection(source, read_only=True) as source_connection:
        with _sqlite_connection(destination) as destination_connection:
            source_connection.backup(destination_connection)


PORTABLE_PATH_KEYS = {"path", "screenshot_path", "main_image_path"}


def _portable_json_value(value, storage_root: Path, *, key: str | None = None):
    if isinstance(value, dict):
        return {
            item_key: _portable_json_value(item, storage_root, key=item_key)
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_portable_json_value(item, storage_root, key=key) for item in value]
    if isinstance(value, str) and key in PORTABLE_PATH_KEYS:
        portable = portable_storage_path(value, storage_root)
        return portable if portable is not None else value
    return value


def prepare_backup_database_copy(database_path: Path, storage_root: Path) -> None:
    """Make path fields portable and remove transferable login sessions."""

    with _sqlite_connection(database_path) as connection:
        # SQLite's online backup preserves the source journal mode. Convert the
        # isolated copy to a single-file journal before mutating it so that the
        # archive cannot omit changes that are still present only in a WAL file.
        connection.execute("PRAGMA journal_mode=DELETE")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if not row[0].startswith("sqlite_")
        ]
        for table in tables:
            quoted_table = table.replace('"', '""')
            json_columns = [
                row[1]
                for row in connection.execute(f'PRAGMA table_info("{quoted_table}")')
                if "JSON" in str(row[2]).upper()
            ]
            for column in json_columns:
                quoted_column = column.replace('"', '""')
                rows = connection.execute(
                    f'SELECT rowid, "{quoted_column}" FROM "{quoted_table}" '
                    f'WHERE "{quoted_column}" IS NOT NULL'
                ).fetchall()
                for rowid, raw_payload in rows:
                    try:
                        payload = (
                            json.loads(raw_payload)
                            if isinstance(raw_payload, str)
                            else raw_payload
                        )
                    except (TypeError, json.JSONDecodeError):
                        continue
                    normalized = _portable_json_value(payload, storage_root)
                    if normalized != payload:
                        connection.execute(
                            f'UPDATE "{quoted_table}" '
                            f'SET "{quoted_column}" = ? WHERE rowid = ?',
                            (json.dumps(normalized, ensure_ascii=False), rowid),
                        )
        if "auth_sessions" in tables:
            connection.execute("DELETE FROM auth_sessions")
        connection.commit()


def _storage_files(storage_root: Path) -> list[tuple[Path, str]]:
    cars_root = storage_root / "cars"
    if not cars_root.exists():
        return []
    result: list[tuple[Path, str]] = []
    for path in sorted(cars_root.rglob("*")):
        if path.is_symlink():
            raise BackupValidationError(f"storage_symlink_not_allowed:{path}")
        if path.is_file():
            relative = path.relative_to(storage_root).as_posix()
            result.append((path, f"storage/{relative}"))
    return result


def export_backup(
    database_path: Path,
    storage_root: Path,
    destination: Path,
    *,
    app_version: str = "0.1.0",
    created_at: datetime | None = None,
) -> BackupValidation:
    database_path = database_path.expanduser().resolve()
    storage_root = storage_root.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not database_path.is_file():
        raise BackupError(f"database_not_found:{database_path}")
    if destination == database_path or storage_root in destination.parents:
        raise BackupError("backup_destination_overlaps_live_data")
    destination.parent.mkdir(parents=True, exist_ok=True)
    timestamp = created_at or _utc_now()

    try:
        lock = maintenance_lock(database_path, "BACKUP_EXPORT")
        with lock, tempfile.TemporaryDirectory(
            prefix="searchcar-backup-",
            dir=destination.parent,
        ) as temporary_name:
            if _active_scan_count(database_path):
                raise BackupBusyError("backup_blocked_by_active_scan")
            temporary = Path(temporary_name)
            database_copy = temporary / DATABASE_ARCHIVE_PATH
            _online_sqlite_backup(database_path, database_copy)
            prepare_backup_database_copy(database_copy, storage_root)
            database_info = verify_sqlite(database_copy)

            files: list[tuple[Path, str]] = [
                (database_copy, DATABASE_ARCHIVE_PATH),
                *_storage_files(storage_root),
            ]
            checksums = {
                archive_name: sha256_file(source)
                for source, archive_name in files
            }
            manifest = {
                "format": BACKUP_FORMAT,
                "format_version": BACKUP_FORMAT_VERSION,
                "app_version": app_version,
                "created_at": timestamp.astimezone(timezone.utc).isoformat(),
                "schema_version": database_info["schema_version"],
                "table_counts": database_info["table_counts"],
                "storage_root": "storage/cars",
            }
            manifest_path = temporary / MANIFEST_ARCHIVE_PATH
            checksums_path = temporary / CHECKSUMS_ARCHIVE_PATH
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            checksums_path.write_text(
                json.dumps(
                    {"algorithm": "sha256", "files": checksums},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            archive_temporary = temporary / "backup.searchcar-backup"
            with zipfile.ZipFile(
                archive_temporary,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                archive.write(manifest_path, MANIFEST_ARCHIVE_PATH)
                archive.write(checksums_path, CHECKSUMS_ARCHIVE_PATH)
                for source, archive_name in files:
                    archive.write(source, archive_name)
            validation = validate_backup(archive_temporary)
            os.replace(archive_temporary, destination)
    except MaintenanceBusyError as exc:
        raise BackupBusyError(str(exc)) from exc
    return BackupValidation(
        path=str(destination),
        format_version=validation.format_version,
        schema_version=validation.schema_version,
        created_at=validation.created_at,
        files=validation.files,
        bytes=validation.bytes,
        table_counts=validation.table_counts,
    )


def _extract_validated_archive_unchecked(
    backup_path: Path,
    destination: Path,
) -> tuple[dict, dict[str, str]]:
    try:
        archive = zipfile.ZipFile(backup_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupValidationError("invalid_backup_archive") from exc
    with archive:
        members = _validate_zip_members(archive)
        manifest = _read_json_member(archive, members[MANIFEST_ARCHIVE_PATH])
        checksum_document = _read_json_member(
            archive,
            members[CHECKSUMS_ARCHIVE_PATH],
        )
        if (
            manifest.get("format") != BACKUP_FORMAT
            or manifest.get("format_version") != BACKUP_FORMAT_VERSION
        ):
            raise BackupValidationError("unsupported_backup_format")
        if not isinstance(manifest.get("schema_version"), int):
            raise BackupValidationError("invalid_backup_schema_version")
        if not isinstance(manifest.get("created_at"), str):
            raise BackupValidationError("invalid_backup_created_at")
        if not isinstance(manifest.get("table_counts"), dict) or any(
            not isinstance(table, str)
            or not isinstance(count, int)
            or count < 0
            for table, count in manifest.get("table_counts", {}).items()
        ):
            raise BackupValidationError("invalid_backup_table_counts")
        if checksum_document.get("algorithm") != "sha256" or not isinstance(
            checksum_document.get("files"),
            dict,
        ):
            raise BackupValidationError("invalid_backup_checksums")
        expected_checksums = checksum_document["files"]
        payload_members = {
            name: info
            for name, info in members.items()
            if name not in {MANIFEST_ARCHIVE_PATH, CHECKSUMS_ARCHIVE_PATH}
        }
        if set(expected_checksums) != set(payload_members):
            raise BackupValidationError("backup_checksum_file_set_mismatch")

        for name, info in payload_members.items():
            expected = expected_checksums.get(name)
            if not isinstance(expected, str) or len(expected) != 64:
                raise BackupValidationError(f"invalid_backup_checksum:{name}")
            with archive.open(info, "r") as source:
                actual = _sha256_stream(source)
            if actual != expected:
                raise BackupValidationError(f"backup_checksum_mismatch:{name}")

        for name, info in payload_members.items():
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=COPY_CHUNK_SIZE)
    return manifest, expected_checksums


def _extract_validated_archive(
    backup_path: Path,
    destination: Path,
) -> tuple[dict, dict[str, str]]:
    try:
        return _extract_validated_archive_unchecked(backup_path, destination)
    except BackupValidationError:
        raise
    except (EOFError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BackupValidationError("invalid_backup_archive") from exc


def validate_backup(backup_path: Path) -> BackupValidation:
    backup_path = backup_path.expanduser().resolve()
    if not backup_path.is_file():
        raise BackupValidationError(f"backup_not_found:{backup_path}")
    with tempfile.TemporaryDirectory(prefix="searchcar-validate-") as temporary_name:
        temporary = Path(temporary_name)
        manifest, checksums = _extract_validated_archive(backup_path, temporary)
        database_info = verify_sqlite(temporary / DATABASE_ARCHIVE_PATH)
        if database_info["schema_version"] != manifest.get("schema_version"):
            raise BackupValidationError("backup_schema_version_mismatch")
        if database_info["table_counts"] != manifest.get("table_counts"):
            raise BackupValidationError("backup_table_counts_mismatch")
        total_bytes = sum(
            path.stat().st_size for path in temporary.rglob("*") if path.is_file()
        )
        return BackupValidation(
            path=str(backup_path),
            format_version=int(manifest["format_version"]),
            schema_version=int(manifest["schema_version"]),
            created_at=str(manifest["created_at"]),
            files=len(checksums),
            bytes=total_bytes,
            table_counts=dict(manifest["table_counts"]),
        )


def interrupt_unfinished_jobs(engine, *, reason: str) -> list[int]:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from .job_queue import recover_interrupted_jobs
    from .models import ProjectScanRun, ScanRun

    recovered_ids = recover_interrupted_jobs(engine, reason=reason)
    with Session(engine) as database:
        queued_jobs = list(
            database.scalars(
                select(ScanRun).where(ScanRun.status == "QUEUED")
            )
        )
        interrupted_at = _utc_now()
        for job in queued_jobs:
            payload = dict(job.payload or {})
            payload.update(
                {
                    "current_project_id": None,
                    "interrupted_at": interrupted_at.isoformat(),
                    "interruption_reason": reason,
                }
            )
            job.status = "INTERRUPTED"
            job.payload = payload
            job.error = job.error or "Queued scan was cancelled by data restore"
            job.finished_at = interrupted_at
            job.worker_id = None
        if queued_jobs:
            queued_ids = [job.id for job in queued_jobs]
            for project_run in database.scalars(
                select(ProjectScanRun).where(
                    ProjectScanRun.scan_run_id.in_(queued_ids),
                    ProjectScanRun.status.in_(("QUEUED", "RUNNING")),
                )
            ):
                project_run.status = "INTERRUPTED"
                project_run.error_code = project_run.error_code or reason[:30]
        database.commit()
    return [*recovered_ids, *(job.id for job in queued_jobs)]


def prepare_portable_database(database_path: Path) -> None:
    from sqlalchemy import text

    from .database import create_database_engine
    from .sqlite_migrations import migrate_sqlite

    engine = create_database_engine(
        f"sqlite+pysqlite:///{database_path.resolve().as_posix()}"
    )
    try:
        migrate_sqlite(engine)
        interrupt_unfinished_jobs(engine, reason="BACKUP_RESTORED")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM auth_sessions"))
    finally:
        engine.dispose()
    verify_sqlite(database_path)


def restore_backup(
    backup_path: Path,
    database_path: Path,
    storage_root: Path,
    rollback_directory: Path,
    *,
    app_version: str = "0.1.0",
) -> dict:
    backup_path = backup_path.expanduser().resolve()
    database_path = database_path.expanduser().resolve()
    storage_root = storage_root.expanduser().resolve()
    rollback_directory = rollback_directory.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    storage_root.mkdir(parents=True, exist_ok=True)
    if _active_scan_count(database_path):
        raise BackupBusyError("restore_blocked_by_active_scan")
    rollback_directory.mkdir(parents=True, exist_ok=True)
    rollback_path = rollback_directory / (
        "pre-restore-"
        f"{_utc_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        ".searchcar-backup"
    )
    if database_path.is_file():
        export_backup(
            database_path,
            storage_root,
            rollback_path,
            app_version=app_version,
        )

    with tempfile.TemporaryDirectory(
        prefix="searchcar-restore-",
        dir=database_path.parent,
    ) as temporary_name:
        extracted = Path(temporary_name) / "extracted"
        extracted.mkdir()
        _extract_validated_archive(backup_path, extracted)
        restored_database = extracted / DATABASE_ARCHIVE_PATH
        prepare_portable_database(restored_database)
        restored_cars = extracted / "storage" / "cars"

        current_cars = storage_root / "cars"
        old_database = database_path.with_name(
            f".{database_path.name}.restore-old-{uuid4().hex}"
        )
        old_cars = storage_root / f".cars.restore-old-{uuid4().hex}"
        database_moved = False
        cars_moved = False
        try:
            if database_path.exists():
                os.replace(database_path, old_database)
                database_moved = True
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{database_path}{suffix}")
                if sidecar.exists():
                    sidecar.unlink()
            if current_cars.exists():
                os.replace(current_cars, old_cars)
                cars_moved = True
            os.replace(restored_database, database_path)
            if restored_cars.exists():
                os.replace(restored_cars, current_cars)
            else:
                current_cars.mkdir(parents=True, exist_ok=True)
            restored_info = verify_sqlite(database_path)
        except Exception:
            if database_path.exists():
                database_path.unlink()
            if current_cars.exists():
                shutil.rmtree(current_cars)
            if database_moved and old_database.exists():
                os.replace(old_database, database_path)
            if cars_moved and old_cars.exists():
                os.replace(old_cars, current_cars)
            raise
        else:
            if old_database.exists():
                old_database.unlink()
            if old_cars.exists():
                shutil.rmtree(old_cars)

    return {
        "status": "restored",
        "backup": str(backup_path),
        "rollback_backup": str(rollback_path) if rollback_path.exists() else None,
        **restored_info,
    }
