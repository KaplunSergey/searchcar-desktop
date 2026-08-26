from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.database import create_database_engine
from app.desktop_backup import (
    BackupBusyError,
    BackupValidationError,
    export_backup,
    restore_backup,
    sha256_file,
    validate_backup,
    _safe_archive_path,
)
from app.models import AuthSession, Car, CarImage, Project, ScanRun, User
from app.postgres_converter import convert_to_backup
from app.maintenance import (
    begin_update_install,
    clear_stale_maintenance_lock,
    finish_update_install,
    maintenance_active,
    maintenance_lock,
)
from app.sqlite_migrations import migrate_sqlite
from app.storage_paths import portable_storage_path, resolve_storage_path


def _engine(database_path: Path):
    engine = create_database_engine(
        f"sqlite+pysqlite:///{database_path.resolve().as_posix()}"
    )
    migrate_sqlite(engine)
    return engine


def _seed(database_path: Path, storage_root: Path) -> None:
    engine = _engine(database_path)
    image_path = storage_root / "cars" / "123" / "main-image.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"image-v1")
    with Session(engine) as database:
        user = User(
            username="Owner",
            username_key="owner",
            password_hash="test-only",
            role="ADMIN",
            status="ACTIVE",
            project_limit=None,
        )
        database.add(user)
        database.flush()
        project = Project(
            owner_id=user.id,
            name="Project A",
            name_key="project a",
            search_url="https://example.invalid/search",
        )
        car = Car(
            canonical_encar_id="123",
            url="https://example.invalid/car/123",
            title="Test car",
            details={"checked_at": "2026-08-02T12:00:00+00:00"},
            current_price=25_000_000,
        )
        database.add_all([project, car])
        database.flush()
        database.add(
            CarImage(
                car_id=car.id,
                kind="MAIN",
                payload={"path": str(image_path), "checksum": "test"},
            )
        )
        database.add(
            AuthSession(
                user_id=user.id,
                token_hash="a" * 64,
                csrf_hash="b" * 64,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        database.commit()
    engine.dispose()


def test_backup_round_trip_restores_database_and_storage(tmp_path: Path) -> None:
    database_path = tmp_path / "live" / "data" / "searchcar.sqlite3"
    storage_root = tmp_path / "live" / "storage"
    database_path.parent.mkdir(parents=True)
    _seed(database_path, storage_root)
    backup_path = tmp_path / "owner.searchcar-backup"

    exported = export_backup(database_path, storage_root, backup_path)
    assert exported.files == 2
    assert validate_backup(backup_path).table_counts["cars"] == 1

    engine = _engine(database_path)
    with Session(engine) as database:
        database.add(
            Project(
                owner_id=1,
                name="Project B",
                name_key="project b",
                search_url="https://example.invalid/search-b",
            )
        )
        database.commit()
    engine.dispose()
    (storage_root / "cars" / "123" / "main-image.jpg").write_bytes(b"image-v2")

    result = restore_backup(
        backup_path,
        database_path,
        storage_root,
        tmp_path / "live" / "backups",
    )

    assert result["status"] == "restored"
    assert (storage_root / "cars" / "123" / "main-image.jpg").read_bytes() == b"image-v1"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0
        image_payload = json.loads(
            connection.execute("SELECT payload FROM car_images").fetchone()[0]
        )
        assert image_payload["path"] == "cars/123/main-image.jpg"
    rollback_path = Path(result["rollback_backup"])
    assert rollback_path.is_file()
    assert validate_backup(rollback_path).table_counts["projects"] == 2


def test_backup_never_exports_or_replaces_license_state(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    database_path = source_root / "data" / "searchcar.sqlite3"
    storage_root = source_root / "storage"
    database_path.parent.mkdir(parents=True)
    _seed(database_path, storage_root)
    source_license = source_root / "license"
    source_license.mkdir()
    (source_license / "binding.json").write_text(
        '{"license_id":"source-license","device_id":"source-device"}',
        encoding="utf-8",
    )
    (source_license / "lease.json").write_text("source-secret-state", encoding="utf-8")
    backup_path = tmp_path / "portable.searchcar-backup"

    export_backup(database_path, storage_root, backup_path)
    with zipfile.ZipFile(backup_path) as archive:
        names = set(archive.namelist())
    assert all(not name.startswith("license/") for name in names)
    assert "binding.json" not in names
    assert "lease.json" not in names

    target_root = tmp_path / "target"
    target_database = target_root / "data" / "searchcar.sqlite3"
    target_storage = target_root / "storage"
    target_license = target_root / "license"
    target_database.parent.mkdir(parents=True)
    target_license.mkdir()
    target_binding = target_license / "binding.json"
    target_lease = target_license / "lease.json"
    target_binding.write_text(
        '{"license_id":"target-license","device_id":"target-device"}',
        encoding="utf-8",
    )
    target_lease.write_text("target-secret-state", encoding="utf-8")

    restore_backup(
        backup_path,
        target_database,
        target_storage,
        target_root / "backups",
    )

    assert "target-license" in target_binding.read_text(encoding="utf-8")
    assert "target-device" in target_binding.read_text(encoding="utf-8")
    assert target_lease.read_text(encoding="utf-8") == "target-secret-state"


def test_export_is_blocked_while_scan_is_active(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "searchcar.sqlite3"
    storage_root = tmp_path / "storage"
    database_path.parent.mkdir(parents=True)
    _seed(database_path, storage_root)
    engine = _engine(database_path)
    with Session(engine) as database:
        database.add(
            ScanRun(owner_id=1, kind="PROJECTS", status="RUNNING", progress=25)
        )
        database.commit()
    engine.dispose()

    with pytest.raises(BackupBusyError, match="active_scan"):
        export_backup(
            database_path,
            storage_root,
            tmp_path / "blocked.searchcar-backup",
        )


def test_backup_rejects_tampered_payload(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "searchcar.sqlite3"
    storage_root = tmp_path / "storage"
    database_path.parent.mkdir(parents=True)
    _seed(database_path, storage_root)
    source = tmp_path / "valid.searchcar-backup"
    tampered = tmp_path / "tampered.searchcar-backup"
    export_backup(database_path, storage_root, source)

    with zipfile.ZipFile(source) as original, zipfile.ZipFile(tampered, "w") as changed:
        for info in original.infolist():
            payload = original.read(info)
            if info.filename == "storage/cars/123/main-image.jpg":
                payload = b"tampered"
            changed.writestr(info, payload)

    with pytest.raises(BackupValidationError, match="checksum_mismatch"):
        validate_backup(tampered)


@pytest.mark.parametrize(
    "unsafe_name",
    ["../outside", "/absolute", "C:/windows/file"],
)
def test_backup_rejects_cross_platform_unsafe_paths(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    archive_path = tmp_path / "unsafe.searchcar-backup"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(unsafe_name, b"unsafe")
        archive.writestr("database.sqlite3", b"not-important")
        archive.writestr("manifest.json", b"{}")
        archive.writestr("checksums.json", b"{}")
    with pytest.raises(BackupValidationError, match="unsafe_archive_path"):
        validate_backup(archive_path)


def test_backup_rejects_backslash_path_before_zip_normalization() -> None:
    # zipfile normalizes backslashes while constructing ZipInfo on Windows.
    # Test the validation boundary directly so the same assertion runs on all
    # supported operating systems.
    with pytest.raises(BackupValidationError, match="unsafe_archive_path"):
        _safe_archive_path("folder\\file")


def test_sqlite_source_conversion_is_read_only_and_portable(tmp_path: Path) -> None:
    source_database = tmp_path / "source" / "searchcar.sqlite3"
    source_storage = tmp_path / "source" / "storage"
    source_database.parent.mkdir(parents=True)
    _seed(source_database, source_storage)
    source_engine = _engine(source_database)
    with Session(source_engine) as database:
        database.add(
            ScanRun(owner_id=1, kind="PROJECTS", status="RUNNING", progress=40)
        )
        database.commit()
    source_engine.dispose()
    before = sha256_file(source_database)
    destination = tmp_path / "converted.searchcar-backup"

    report = convert_to_backup(
        f"sqlite+pysqlite:///{source_database.as_posix()}",
        source_storage,
        destination,
    )

    assert sha256_file(source_database) == before
    with sqlite3.connect(source_database) as connection:
        assert connection.execute("SELECT status FROM scan_runs").fetchone()[0] == "RUNNING"
    assert report.source_counts == report.target_counts
    assert report.skipped_counts == {"auth_sessions": 1}
    assert report.storage_files == 1
    assert validate_backup(destination).table_counts["cars"] == 1

    restored_database = tmp_path / "restored" / "data" / "searchcar.sqlite3"
    restored_storage = tmp_path / "restored" / "storage"
    restored_database.parent.mkdir(parents=True)
    restore_backup(
        destination,
        restored_database,
        restored_storage,
        tmp_path / "restored" / "backups",
    )
    with sqlite3.connect(restored_database) as connection:
        payload = json.loads(
            connection.execute("SELECT payload FROM car_images").fetchone()[0]
        )
        restored_scan = connection.execute(
            "SELECT status, payload FROM scan_runs"
        ).fetchone()
    assert payload["path"] == "cars/123/main-image.jpg"
    assert restored_scan[0] == "INTERRUPTED"
    assert json.loads(restored_scan[1])["interruption_reason"] == "BACKUP_RESTORED"
    assert (restored_storage / payload["path"]).read_bytes() == b"image-v1"


def test_storage_paths_are_portable_between_installations(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    image = storage_root / "cars" / "42" / "image.jpg"
    assert portable_storage_path(image, storage_root) == "cars/42/image.jpg"
    assert portable_storage_path(
        "/storage/cars/42/image.jpg", storage_root
    ) == "cars/42/image.jpg"
    assert resolve_storage_path("cars/42/image.jpg", storage_root) == image.resolve()
    assert resolve_storage_path("../outside", storage_root) is None


def test_maintenance_lock_is_visible_to_desktop_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "data" / "searchcar.sqlite3"
    database_path.parent.mkdir(parents=True)
    database_path.touch()
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))

    assert not maintenance_active()
    with maintenance_lock(database_path, "TEST"):
        assert maintenance_active()
    assert not maintenance_active()


def test_update_install_guard_blocks_new_desktop_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))

    first = begin_update_install()
    assert first.name == "update-install.lock"
    assert begin_update_install() == first
    assert maintenance_active()
    assert clear_stale_maintenance_lock() is False
    assert finish_update_install() is True
    assert not maintenance_active()
