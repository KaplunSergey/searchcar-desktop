import struct
import zlib

import pytest
from sqlalchemy import select

from app.models import Car, CarImage, Project, ProjectCar, User
from test_money_values import db, detail


def png(color=0):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, color, 0, 0]))) + chunk(b"IEND", b""))


def test_images_bind_to_internal_id_and_never_persist_bytes_in_json(db, tmp_path, monkeypatch):
    from app.services import upsert_detail
    monkeypatch.setattr("app.services.settings.storage_root", str(tmp_path))
    source_url = "https://ci.encar.com/original.jpg"
    car = upsert_detail(db, db.get(Project, 1), {
        **detail(1000),
        "main_image_source_url": source_url,
        "_images": {"MAIN": png()},
    })
    db.commit()
    image = db.scalar(select(CarImage).where(CarImage.car_id == car.id, CarImage.kind == "MAIN"))
    assert image is not None
    assert image.payload["path"].startswith(f"cars/by-id/{car.id}/main-")
    assert (tmp_path / image.payload["path"]).read_bytes() == png()
    assert "_images" not in car.details
    assert car.details["main_image_path"] == image.payload["path"]
    assert image.payload["storage_version"] == 2
    assert image.payload["capture"] == "SOURCE_DOWNLOAD"
    assert car.details["main_image_source_url"] == source_url


def test_same_external_id_on_two_sources_has_two_files(db, tmp_path):
    from app.current_images import persist_images
    a = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672")
    b = Car(source_key="copart", source_listing_id="42688672", url="https://copart.example/lot/42688672")
    db.add_all([a, b])
    db.flush()
    persist_images(db, a, {"MAIN": png(1)}, tmp_path)
    persist_images(db, b, {"MAIN": png(2)}, tmp_path)
    db.commit()
    rows = list(db.scalars(select(CarImage)))
    assert len(rows) == 2
    assert len({image.payload["path"] for image in rows}) == 2
    assert sorted((tmp_path / image.payload["path"]).read_bytes() for image in rows) == sorted([png(1), png(2)])


def test_replace_and_repeat_keep_one_main_copy_and_rollback_restores_old(db, tmp_path):
    from app.current_images import persist_images
    car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672")
    db.add(car)
    db.flush()
    persist_images(db, car, {"MAIN": png(1)}, tmp_path)
    db.commit()
    row = db.scalar(select(CarImage))
    assert row.payload.get("updated_at")
    old_path, image_id = row.payload["path"], row.id
    persist_images(db, car, {"MAIN": png(2)}, tmp_path)
    db.flush()
    db.rollback()
    assert db.get(CarImage, image_id).payload["path"] == old_path
    assert (tmp_path / old_path).read_bytes() == png(1)
    assert len(list((tmp_path / "cars").rglob("main-*"))) == 1
    persist_images(db, car, {"MAIN": png(2)}, tmp_path)
    db.commit()
    assert db.scalar(select(CarImage)).id == image_id
    assert not (tmp_path / old_path).exists()
    path = db.scalar(select(CarImage)).payload["path"]
    payload = dict(db.scalar(select(CarImage)).payload)
    modified = (tmp_path / path).stat().st_mtime_ns
    persist_images(db, car, {"MAIN": png(2)}, tmp_path)
    db.commit()
    assert db.scalar(select(CarImage)).payload == payload
    assert (tmp_path / path).stat().st_mtime_ns == modified
    assert len(list((tmp_path / "cars").rglob("main-*"))) == 1


@pytest.mark.parametrize("content", [None, b"", b"html error", b"\x89PNG\r\n\x1a\n", b"x" * (5 * 1024 * 1024 + 1)],
                         ids=["missing", "empty", "html", "truncated", "oversized"])
def test_missing_or_invalid_image_preserves_previous_file_and_metadata(db, tmp_path, content):
    from app.current_images import persist_images
    car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672")
    db.add(car)
    db.flush()
    persist_images(db, car, {"MAIN": png(1)}, tmp_path)
    db.commit()
    old = dict(db.scalar(select(CarImage)).payload)
    persist_images(db, car, {"MAIN": content}, tmp_path)
    db.commit()
    assert db.scalar(select(CarImage)).payload == old
    assert (tmp_path / old["path"]).read_bytes() == png(1)


def test_failed_file_write_keeps_good_image_and_does_not_fail_search(db, tmp_path, monkeypatch):
    from app.current_images import persist_images
    car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672")
    db.add(car)
    db.flush()
    persist_images(db, car, {"MAIN": png(1)}, tmp_path)
    db.commit()
    old = dict(db.scalar(select(CarImage)).payload)
    def fail(*args, **kwargs):
        raise OSError("test_disk_full")
    monkeypatch.setattr("app.current_images._write_candidate", fail)
    persist_images(db, car, {"MAIN": png(2)}, tmp_path)
    db.commit()
    assert db.scalar(select(CarImage)).payload == old
    assert (tmp_path / old["path"]).read_bytes() == png(1)


def test_removing_project_keeps_shared_image_final_listing_delete_removes_it(db, tmp_path, monkeypatch):
    from app.current_images import persist_images
    from app.main import delete_project
    # Match the app's SQLite engine: cascades are not active in bare create_engine.
    db.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    monkeypatch.setattr("app.database.settings.storage_root", str(tmp_path))
    car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672")
    db.add(car)
    db.flush()
    other = Project(owner_id=1, name="Other", name_key="other", search_url="https://encar.example/other")
    db.add(other)
    db.flush()
    db.add_all([ProjectCar(project_id=1, car_id=car.id), ProjectCar(project_id=other.id, car_id=car.id)])
    persist_images(db, car, {"MAIN": png()}, tmp_path)
    db.commit()
    path = db.scalar(select(CarImage)).payload["path"]
    delete_project(project_id=1, current=db.get(User, 1), db=db)
    assert (tmp_path / path).exists()
    assert db.get(Car, car.id) is not None
    db.delete(db.get(ProjectCar, (other.id, car.id)))
    db.flush()  # No ORM relationship orders these explicit deletes.
    db.delete(car)
    db.commit()
    assert not (tmp_path / path).exists()


def test_storage_paths_reject_legacy_traversal_and_symlink_escape(tmp_path):
    from app.storage_paths import portable_storage_path, resolve_storage_path
    root = tmp_path / "storage"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    assert portable_storage_path("/storage/../outside/photo.png", root) is None
    assert resolve_storage_path("escape/photo.png", root) is None
    assert portable_storage_path("cars\\by-id\\1\\main.png", root) == "cars/by-id/1/main.png"


def test_nested_rollback_does_not_remove_outer_candidate(db, tmp_path):
    from app.current_images import persist_images
    car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672")
    db.add(car)
    db.flush()
    result = persist_images(db, car, {"MAIN": png()}, tmp_path)
    with db.begin_nested() as nested:
        nested.rollback()
    db.commit()
    assert (tmp_path / result["main_image_path"]).is_file()


def test_cleanup_failure_does_not_fail_committed_listing(db, tmp_path, monkeypatch):
    from app.current_images import persist_images
    car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672")
    db.add(car)
    db.flush()
    persist_images(db, car, {"MAIN": png()}, tmp_path)
    db.commit()
    def fail(*args):
        raise OSError("test_busy_file")
    monkeypatch.setattr("app.current_images._referenced", fail)
    result = persist_images(db, car, {"MAIN": png(1)}, tmp_path)
    db.commit()
    assert db.scalar(select(CarImage)).payload["path"] == result["main_image_path"]


def test_file_route_authorizes_exact_current_internal_path_and_owner(db, tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app.current_images import persist_images
    from app.main import storage_file, image_payloads
    monkeypatch.setattr("app.main.storage_path", tmp_path)
    car = Car(source_key="copart", source_listing_id="42688672", url="https://copart.example/lot/42688672")
    db.add(car)
    db.flush()
    db.add(ProjectCar(project_id=1, car_id=car.id))
    result = persist_images(db, car, {"MAIN": png()}, tmp_path)
    db.commit()
    path = result["main_image_path"]
    assert str(storage_file(path, current=db.get(User, 1), db=db).path) == str(tmp_path / path)
    assert image_payloads(car.id, db)["MAIN"]["url"] == f"/storage/{path}"
    intruder = User(username="Other", username_key="other", password_hash="!test", role="USER", status="ACTIVE")
    db.add(intruder)
    db.commit()
    with pytest.raises(HTTPException):
        storage_file(path, current=intruder, db=db)
    stale = path.replace(path.split("/")[-1], "old-main.png")
    (tmp_path / stale).write_bytes(png())
    with pytest.raises(HTTPException):
        storage_file(stale, current=db.get(User, 1), db=db)
    symlink = tmp_path / "cars" / "alias"
    symlink.symlink_to(tmp_path / "cars" / "by-id" / str(car.id), target_is_directory=True)
    with pytest.raises(HTTPException):
        storage_file(f"cars/alias/{path.split('/')[-1]}", current=db.get(User, 1), db=db)


def test_migration_copies_legacy_current_image_preserves_id_and_is_idempotent(db, tmp_path):
    from app.current_images import migrate_current_images
    import hashlib
    old = tmp_path / "cars" / "42688672" / "main-image.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(png())
    car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672", comment="keep", rating=4,
              details={"main_image_path": str(old), "mileage_km": 123})
    db.add(car)
    db.flush()
    image = CarImage(car_id=car.id, kind="MAIN", payload={"path": str(old), "checksum": hashlib.sha256(png()).hexdigest()})
    db.add(image)
    db.commit()
    car_id, image_id = car.id, image.id
    assert migrate_current_images(db, tmp_path) == 1
    db.rollback()
    assert old.is_file()
    assert not list((tmp_path / "cars" / "by-id").rglob("main-*"))
    assert migrate_current_images(db, tmp_path) == 1
    db.commit()
    assert (car.id, image.id, car.comment, car.rating, car.details["mileage_km"]) == (car_id, image_id, "keep", 4, 123)
    assert (tmp_path / image.payload["path"]).read_bytes() == png()
    assert image.payload["capture"] == "LEGACY_COPY"
    assert not old.exists()
    assert migrate_current_images(db, tmp_path) == 0


def test_migration_rejects_checksum_mismatch_and_outside_files(db, tmp_path):
    from app.current_images import migrate_current_images
    old = tmp_path / "cars" / "42688672" / "main-image.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(png())
    car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672")
    db.add(car)
    db.flush()
    db.add(CarImage(car_id=car.id, kind="MAIN", payload={"path": str(old), "checksum": "wrong"}))
    db.commit()
    assert migrate_current_images(db, tmp_path) == 0
    assert old.is_file()


def test_orphan_cleanup_keeps_current_and_unrelated_files(db, tmp_path):
    from app.current_images import cleanup_image_candidates, persist_images
    car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672")
    db.add(car)
    db.flush()
    result = persist_images(db, car, {"MAIN": png()}, tmp_path)
    db.commit()
    folder = (tmp_path / result["main_image_path"]).parent
    orphan = folder / ("main-" + "0" * 64 + ".png")
    orphan.write_bytes(png(1))
    unrelated = folder / "user-photo.png"
    unrelated.write_bytes(png(2))
    assert cleanup_image_candidates(db, tmp_path) == 1
    assert not orphan.exists()
    assert unrelated.is_file()
    assert (tmp_path / result["main_image_path"]).is_file()


def test_final_legacy_listing_delete_removes_only_owned_file_after_commit(db, tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr("app.database.settings.storage_root", str(tmp_path))
    db.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    old = tmp_path / "cars" / "42688672" / "main-image.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(png())
    unrelated = old.parent / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672")
    db.add(car)
    db.flush()
    db.add(CarImage(car_id=car.id, kind="MAIN", payload={"path": str(old), "checksum": hashlib.sha256(png()).hexdigest()}))
    db.commit()
    db.delete(car)
    db.flush()
    assert old.is_file()
    db.rollback()
    assert old.is_file()
    db.delete(car)
    db.commit()
    assert not old.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_legacy_import_screenshot_reuses_internal_current_image_storage(db, tmp_path, monkeypatch):
    from app.importer import _copy_current_screenshot
    monkeypatch.setattr("app.importer.settings.storage_root", str(tmp_path / "storage"))
    source = tmp_path / "legacy" / "data" / "runs" / "run1" / "screenshots" / "car.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(png())
    car = Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672")
    db.add(car)
    db.flush()
    result = _copy_current_screenshot(db, tmp_path / "legacy", {"screenshot": str(source)}, car)
    db.commit()
    assert result["screenshot_path"].startswith(f"cars/by-id/{car.id}/screenshot-")
    assert (tmp_path / "storage" / result["screenshot_path"]).read_bytes() == png()
    assert source.is_file()
    image = db.scalar(select(CarImage))
    assert image.kind == "SCREENSHOT" and image.payload["capture"] == "LEGACY_COPY"
    assert _copy_current_screenshot(db, tmp_path / "legacy", {"screenshot": str(source)}, car) == result
    db.commit()
    assert len(list(db.scalars(select(CarImage)))) == 1


def test_new_verified_photo_does_not_rewrite_quarantined_image_record(db, tmp_path, monkeypatch):
    from app.services import upsert_detail
    monkeypatch.setattr("app.services.settings.storage_root", str(tmp_path))
    car = upsert_detail(db, db.get(Project, 1), detail(1000))
    quarantined = CarImage(car_id=car.id, kind="MAIN", integrity_status="QUARANTINED", integrity_reason="wrong_identity",
                           payload={"path": "cars/wrong/main-image.jpg", "checksum": "wrong"})
    db.add(quarantined)
    db.commit()
    payload = dict(quarantined.payload)
    upsert_detail(db, db.get(Project, 1), {**detail(1000), "_images": {"MAIN": png()}})
    db.commit()
    assert quarantined.payload == payload
    valid = list(db.scalars(select(CarImage).where(CarImage.car_id == car.id, CarImage.integrity_status == "VALID")))
    assert len(valid) == 1
    assert valid[0].payload["storage_version"] == 2
