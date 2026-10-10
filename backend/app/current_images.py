"""One current image per listing/kind, committed by the existing coordinator.

Workers return validated image bytes without writing files. Files are checksum-named candidates:
the DB pointer switches at commit, then the former file is removed. Rollback
removes new candidates, never the old file. Startup migration also cleans
unreferenced candidates left by a process crash; they are not image history.
"""
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
import re
import struct
import tempfile
import zlib

from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .models import Car, CarImage, ScanRun
from .storage_paths import portable_storage_path, resolve_storage_path

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MANAGED_NAME = re.compile(r"(?:main|screenshot)-[a-f0-9]{64}\.(?:png|jpg|webp|gif)$")


def image_format(content: bytes) -> str | None:
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_IMAGE_BYTES:
        return None
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        offset, seen_header, seen_data = 8, False, False
        while offset + 12 <= len(content):
            length = struct.unpack(">I", content[offset:offset + 4])[0]
            end = offset + 12 + length
            if end > len(content):
                return None
            kind, data = content[offset + 4:offset + 8], content[offset + 8:end - 4]
            if zlib.crc32(kind + data) != struct.unpack(">I", content[end - 4:end])[0]:
                return None
            if not seen_header:
                if kind != b"IHDR" or length != 13:
                    return None
                width, height = struct.unpack(">II", data[:8])
                if not width or not height or width * height > 20_000_000:
                    return None
                seen_header = True
            seen_data |= kind == b"IDAT"
            if kind == b"IEND":
                return "png" if seen_data and length == 0 and end == len(content) else None
            offset = end
        return None
    # Validated source originals are retained without re-encoding.
    if content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
        return "jpg"
    if content[:6] in (b"GIF87a", b"GIF89a") and len(content) > 13 and content.endswith(b";"):
        return "gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP" and len(content) >= 20:
        return "webp" if int.from_bytes(content[4:8], "little") + 8 == len(content) else None
    return None


def _write_candidate(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".image-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(path)  # Same filesystem, closed handle (also on Windows).
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_path(root: Path, relative: str) -> Path | None:
    # Neither reads nor cleanup follow symlinks, including an in-root symlink.
    candidate = root / relative
    if any(parent.is_symlink() for parent in (candidate, *candidate.parents)):
        return None
    return resolve_storage_path(relative, root)


def image_storage_path(value, storage_root: Path) -> Path | None:
    root = Path(storage_root).resolve()
    relative = portable_storage_path(value, root)
    return _safe_path(root, relative) if relative else None


def _referenced(db, root: Path, relative: str) -> bool:
    with db.get_bind().connect() as connection:
        return any(portable_storage_path((payload or {}).get("path"), root) == relative
                   for payload in connection.scalars(select(CarImage.payload)))


def _unlink(root: Path, relative: str) -> None:
    path = _safe_path(root, relative)
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Current image cleanup deferred")


@event.listens_for(Session, "after_commit")
def _commit_files(db):
    if db.in_nested_transaction():
        return
    db.info.pop("image_candidates", None)
    for root, relative in db.info.pop("image_cleanup", set()):
        try:
            if not _referenced(db, root, relative):
                _unlink(root, relative)
        except (OSError, SQLAlchemyError):
            logger.warning("Current image cleanup deferred")
    for root, car_id in db.info.pop("deleted_image_listings", set()):
        folder = root / "cars" / "by-id" / str(car_id)
        if _safe_path(root, folder.relative_to(root).as_posix()) is None:
            continue
        try:
            if folder.is_dir():
                for path in folder.iterdir():
                    if MANAGED_NAME.fullmatch(path.name) and not _referenced(db, root, path.relative_to(root).as_posix()):
                        _unlink(root, path.relative_to(root).as_posix())
                folder.rmdir()
        except (OSError, SQLAlchemyError):
            pass  # Never recursively delete unrelated files; retry at startup.


@event.listens_for(Session, "after_rollback")
def _rollback_files(db):
    if db.in_nested_transaction():
        return
    for root, relative in db.info.pop("image_candidates", set()):
        _unlink(root, relative)
    db.info.pop("image_cleanup", None)
    db.info.pop("deleted_image_listings", None)


@event.listens_for(Session, "before_flush")
def _deleted_listings(db, _context, _instances):
    from .database import settings
    root = Path(db.info.get("image_storage_root", settings.storage_root)).resolve()
    for item in db.deleted:
        if isinstance(item, Car):
            db.info.setdefault("deleted_image_listings", set()).add((root, item.id))
            for payload in db.scalars(select(CarImage.payload).where(CarImage.car_id == item.id)):
                relative = portable_storage_path((payload or {}).get("path"), root)
                if relative and _owned_path(item, relative):
                    db.info.setdefault("image_cleanup", set()).add((root, relative))


def persist_images(db, car: Car, images: dict, storage_root: Path, *, capture="BROWSER_SCREENSHOT") -> dict:
    """Validate/copy optional bytes; a photo failure never fails a listing read."""
    root = Path(storage_root).resolve()
    db.info["image_storage_root"] = root
    if db.in_nested_transaction():
        raise ValueError("Image persistence requires an outer transaction")
    if type(car.id) is not int or car.id <= 0:
        raise ValueError("Image persistence requires a stored listing")
    result = {}
    for kind, prefix in (("MAIN", "main"), ("SCREENSHOT", "screenshot")):
        content = images.get(kind)
        extension = image_format(content)
        if extension is None:
            continue
        checksum = hashlib.sha256(content).hexdigest()
        relative = f"cars/by-id/{car.id}/{prefix}-{checksum}.{extension}"
        target = _safe_path(root, relative)
        if target is None:
            continue
        rows = list(db.scalars(select(CarImage).where(CarImage.car_id == car.id, CarImage.kind == kind,
                                                     CarImage.integrity_status == "VALID")
                               .order_by(CarImage.created_at.desc(), CarImage.id.desc())))
        current = rows[0] if rows else None
        existed = target.is_file()
        try:
            if not existed or hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
                _write_candidate(target, content)
        except OSError:
            logger.warning("Current image write failed; keeping previous image")
            continue
        if not existed:
            db.info.setdefault("image_candidates", set()).add((root, relative))
        payload = {"path": relative, "checksum": checksum, "storage_version": 2, "capture": capture}
        if current and all((current.payload or {}).get(key) == value for key, value in payload.items()):
            payload = dict(current.payload)
        else:
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        if current is None:
            current = CarImage(car_id=car.id, kind=kind, payload=payload)
            db.add(current)
        elif current.payload != payload:
            old_path = portable_storage_path((current.payload or {}).get("path"), root)
            if old_path and old_path != relative and _owned_path(car, old_path):
                db.info.setdefault("image_cleanup", set()).add((root, old_path))
            current.payload = payload
        for duplicate in rows:
            if duplicate is not current:
                old_path = portable_storage_path((duplicate.payload or {}).get("path"), root)
                if old_path and old_path != relative and _owned_path(car, old_path):
                    db.info.setdefault("image_cleanup", set()).add((root, old_path))
                db.delete(duplicate)
        field = "main_image" if kind == "MAIN" else "screenshot"
        result.update({f"{field}_path": relative, f"{field}_checksum": checksum})
    if result:
        car.details = {**(car.details or {}), **result}
    return result


def _owned_path(car, relative: str) -> bool:
    parts = Path(relative).parts
    if len(parts) == 4 and parts[:3] == ("cars", "by-id", str(car.id)):
        return bool(MANAGED_NAME.fullmatch(parts[3]))
    return (car.source_key == "encar" and len(parts) == 3
            and parts[:2] == ("cars", car.source_listing_id)
            and parts[2] in {"main-image.jpg", "main-image.png", "screenshot.png"})


def _migration_rows(db):
    rows = list(db.execute(select(Car, CarImage).join(CarImage, CarImage.car_id == Car.id)
                           .where(CarImage.kind.in_(["MAIN", "SCREENSHOT"]), CarImage.integrity_status == "VALID")
                           .order_by(CarImage.created_at.desc(), CarImage.id.desc())))
    counts = {}
    for car, image in rows:
        key = (car.id, image.kind)
        counts[key] = counts.get(key, 0) + 1
    seen = set()
    for car, image in rows:
        key = (car.id, image.kind)
        if key not in seen:
            seen.add(key)
            if (image.payload or {}).get("storage_version") != 2 or counts[key] > 1:
                yield car, image


def _migration_content(car, image, root):
    payload = image.payload or {}
    relative = portable_storage_path(payload.get("path"), root)
    if not relative or not _owned_path(car, relative):
        return None
    path = _safe_path(root, relative)
    try:
        if path is None or not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
            return None
        content = path.read_bytes()
    except OSError:
        return None
    checksum = hashlib.sha256(content).hexdigest()
    if image_format(content) is None or payload.get("checksum") not in (None, "", checksum):
        logger.warning("Legacy image not verified; keeping original metadata and file")
        return None
    return content


def migrate_current_images(db, storage_root: Path) -> int:
    """Stage legacy copies in the caller's transaction; no deletion until commit."""
    root, changed = Path(storage_root).resolve(), 0
    for car, image in _migration_rows(db):
        content = _migration_content(car, image, root)
        if content is None:
            continue
        payload = image.payload or {}
        if persist_images(db, car, {image.kind: content}, root,
                          capture=payload.get("capture") or "LEGACY_COPY"):
            changed += 1
    return changed


def cleanup_image_candidates(db, storage_root: Path) -> int:
    """Startup/maintenance only, never while a worker has uncommitted candidates."""
    if db.scalar(select(ScanRun.id).where(ScanRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"])).limit(1)):
        return 0
    root, removed = Path(storage_root).resolve(), 0
    referenced = {portable_storage_path((payload or {}).get("path"), root)
                  for payload in db.scalars(select(CarImage.payload))}
    parent = root / "cars" / "by-id"
    if not parent.is_dir() or _safe_path(root, "cars/by-id") is None:
        return 0
    for folder in parent.iterdir():
        if not folder.name.isdigit() or folder.is_symlink() or not folder.is_dir():
            continue
        for path in folder.iterdir():
            relative = path.relative_to(root).as_posix()
            if (MANAGED_NAME.fullmatch(path.name) or path.name.startswith(".image-")) and relative not in referenced:
                if path.is_file() and not path.is_symlink():
                    _unlink(root, relative)
                    removed += not path.exists()
        try:
            folder.rmdir()
        except OSError:
            pass
    return removed


def maintain_image_storage(engine, storage_root: Path, backup_dir: Path) -> dict:
    """Run before desktop workers start; backup precedes any legacy pointer change."""
    from uuid import uuid4
    from .desktop_backup import BackupBusyError, export_backup
    from .maintenance import MaintenanceBusyError, maintenance_lock

    database_path = Path(engine.url.database)
    result = {"migrated": 0, "removed_candidates": 0, "backup": None}
    with Session(engine) as db:
        if db.scalar(select(ScanRun.id).where(ScanRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"])).limit(1)):
            return {**result, "deferred": "active_scan"}
        needs_migration = any(_migration_content(car, image, Path(storage_root).resolve()) is not None
                              for car, image in _migration_rows(db))
    try:
        if needs_migration:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = Path(backup_dir) / f"pre-current-images-{stamp}-{uuid4().hex[:8]}.searchcar-backup"
            export_backup(database_path, storage_root, backup)
            result["backup"] = str(backup)
        with maintenance_lock(database_path, "CURRENT_IMAGES"), Session(engine) as db:
            if db.scalar(select(ScanRun.id).where(ScanRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"])).limit(1)):
                return {**result, "deferred": "active_scan"}
            result["migrated"] = migrate_current_images(db, storage_root)
            db.commit()
            result["removed_candidates"] = cleanup_image_candidates(db, storage_root)
    except (BackupBusyError, MaintenanceBusyError):
        return {**result, "deferred": "maintenance_busy"}
    return result
