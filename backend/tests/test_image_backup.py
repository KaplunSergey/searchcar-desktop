import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.current_images import maintain_image_storage, persist_images
from app.desktop_backup import export_backup, restore_backup, validate_backup
from app.models import Car, CarImage, ScanRun, User
from test_current_images import png
from test_desktop_backup import _engine


def seed_legacy(tmp_path):
    database = tmp_path / "live" / "data" / "searchcar.sqlite3"
    database.parent.mkdir(parents=True)
    root = tmp_path / "live" / "storage"
    engine = _engine(database)
    photo = root / "cars" / "42688672" / "main-image.png"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(png())
    with Session(engine) as db:
        car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672", comment="note", rating=4,
                  details={"main_image_path": str(photo)})
        db.add(car)
        db.flush()
        db.add(CarImage(car_id=car.id, kind="MAIN", payload={"path": str(photo), "checksum": hashlib.sha256(png()).hexdigest()}))
        db.commit()
    return engine, database, root, photo


def test_startup_migration_creates_verified_backup_before_switch_and_repeats_without_backup(tmp_path):
    engine, database, root, old = seed_legacy(tmp_path)
    backups = tmp_path / "backups"
    result = maintain_image_storage(engine, root, backups)
    assert result["migrated"] == 1
    archive = Path(result["backup"])
    assert validate_backup(archive).table_counts["car_images"] == 1
    assert not old.exists()
    with Session(engine) as db:
        image = db.scalar(select(CarImage))
        assert (root / image.payload["path"]).read_bytes() == png()
    repeated = maintain_image_storage(engine, root, backups)
    assert repeated["migrated"] == 0 and repeated["backup"] is None
    assert len(list(backups.iterdir())) == 1
    # The pre-migration backup really restores the original portable pointer and bytes.
    destination = tmp_path / "restored" / "data" / "searchcar.sqlite3"
    destination.parent.mkdir(parents=True)
    target = _engine(destination)
    target.dispose()
    restored_root = tmp_path / "restored" / "storage"
    restore_backup(archive, destination, restored_root, tmp_path / "restore-backups")
    target = _engine(destination)
    with Session(target) as db:
        car, image = db.scalar(select(Car)), db.scalar(select(CarImage))
        assert (car.id, car.comment, car.rating) == (1, "note", 4)
        assert image.payload["path"] == "cars/42688672/main-image.png"
        assert (restored_root / image.payload["path"]).read_bytes() == png()


def test_backup_failure_never_switches_metadata_or_removes_original(tmp_path, monkeypatch):
    engine, database, root, old = seed_legacy(tmp_path)
    def fail(*args, **kwargs):
        raise OSError("test_disk_full")
    monkeypatch.setattr("app.desktop_backup.export_backup", fail)
    with pytest.raises(OSError):
        maintain_image_storage(engine, root, tmp_path / "backups")
    assert old.read_bytes() == png()
    with Session(engine) as db:
        assert db.scalar(select(CarImage)).payload["path"] == str(old)
    assert not (root / "cars" / "by-id").exists()


def test_unverified_legacy_file_does_not_create_backup_on_every_startup(tmp_path):
    engine, database, root, old = seed_legacy(tmp_path)
    old.write_bytes(b"bad image")
    for _ in range(2):
        result = maintain_image_storage(engine, root, tmp_path / "backups")
        assert result["migrated"] == 0 and result["backup"] is None
    assert old.read_bytes() == b"bad image"
    assert not (tmp_path / "backups").exists()


def test_active_queue_defers_migration_and_orphan_cleanup(tmp_path):
    engine, database, root, old = seed_legacy(tmp_path)
    with Session(engine) as db:
        owner = User(username="owner", username_key="owner", password_hash="test", status="ACTIVE", role="USER")
        db.add(owner)
        db.flush()
        db.add(ScanRun(owner_id=owner.id, kind="PROJECTS", status="QUEUED", payload={}))
        db.commit()
    orphan = root / "cars" / "by-id" / "1" / ("main-" + "0" * 64 + ".png")
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(png())
    assert maintain_image_storage(engine, root, tmp_path / "backups")["deferred"] == "active_scan"
    assert old.is_file() and orphan.is_file()
    assert not (tmp_path / "backups").exists()


def test_mixed_source_internal_images_backup_restores_windows_relative_paths(tmp_path):
    engine, database, root, old = seed_legacy(tmp_path)
    maintain_image_storage(engine, root, tmp_path / "migration-backups")
    with Session(engine) as db:
        copart = Car(source_key="copart", source_listing_id="42688672", url="https://copart.example/lot/42688672")
        db.add(copart)
        db.flush()
        persist_images(db, copart, {"MAIN": png(2)}, root)
        db.commit()
        for image in db.scalars(select(CarImage)):
            image.payload = {**image.payload, "path": image.payload["path"].replace("/", "\\")}
        db.commit()
    archive = tmp_path / "mixed.searchcar-backup"
    export_backup(database, root, archive)
    destination = tmp_path / "restored" / "data" / "searchcar.sqlite3"
    destination.parent.mkdir(parents=True)
    target = _engine(destination)
    target.dispose()
    restored_root = tmp_path / "restored" / "storage"
    restore_backup(archive, destination, restored_root, tmp_path / "restore-backups")
    target = _engine(destination)
    with Session(target) as db:
        rows = list(db.scalars(select(CarImage).order_by(CarImage.car_id)))
        assert len({row.payload["path"] for row in rows}) == 2
        assert [hashlib.sha256((restored_root / row.payload["path"]).read_bytes()).hexdigest()
                for row in rows] == [row.payload["checksum"] for row in rows]
        assert [(db.get(Car, row.car_id).source_key, db.get(Car, row.car_id).source_listing_id) for row in rows] == [
            ("encar", "42688672"), ("copart", "42688672")]
