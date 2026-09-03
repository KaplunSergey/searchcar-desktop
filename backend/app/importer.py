import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .database import SessionLocal, settings
from .models import (
    Car,
    CarAlias,
    CarImage,
    CarSnapshot,
    PriceHistory,
    Project,
    ProjectCar,
    User,
)
from .parser import extract_car_id, fingerprint_listing

HISTORY_FILES = (
    "search_debug.json",
    "all_found.json",
    "viewed_list.json",
    "new_or_updated.json",
)
REMOVED_KEYS = {
    "landed_cost_estimate",
    "rates_used",
    "car_price_pln",
    "customs_10_pln",
    "german_vat_19_pln",
    "polish_excise_rate",
    "polish_excise_pln",
    "service_1000_eur_pln",
    "estimated_total_low_pln",
    "estimated_total_high_pln",
    "exchange_rate",
}


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items() if key not in REMOVED_KEYS}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def discover(root: Path) -> list[Path]:
    files: list[Path] = []
    runs = root / "data" / "runs"
    for name in HISTORY_FILES:
        files.extend(runs.glob(f"**/{name}"))
    for name in (
        "last_search_debug.json",
        "last_scan_all_found.json",
        "last_viewed_list.json",
        "new_listings.json",
    ):
        path = root / "data" / name
        if path.exists():
            files.append(path)
    return sorted(set(files))


def inspect_source(root: Path) -> dict:
    searches = load(root / "searches.json", [])
    files = discover(root)
    records = 0
    errors = 0
    screenshots = 0
    ids: set[str] = set()
    for file in files:
        payload = load(file, [])
        if not isinstance(payload, list):
            continue
        records += len(payload)
        for row in payload:
            if not isinstance(row, dict):
                continue
            car_id = str(
                row.get("canonical_car_id")
                or row.get("car_id")
                or extract_car_id(row.get("url", ""))
                or ""
            )
            if car_id:
                ids.add(car_id)
            if row.get("error") or row.get("read_error"):
                errors += 1
            if row.get("screenshot"):
                screenshots += 1
    seen = load(root / "data" / "seen.json", {})
    ids.update(str(key) for key in seen if str(key).isdigit())
    return {
        "path": str(root),
        "available": root.exists(),
        "searches": len(searches) if isinstance(searches, list) else 0,
        "history_files": len(files),
        "records": records,
        "unique_cars": len(ids),
        "errors": errors,
        "screenshots": screenshots,
    }


def _unique_name(db, owner_id: int, name: str, url: str) -> str:
    base = (name or "Legacy Encar search").strip()[:140]
    candidate = base
    suffix = 2
    while True:
        existing = db.scalar(
            select(Project).where(
                Project.owner_id == owner_id,
                Project.name_key == candidate.casefold(),
            )
        )
        if not existing or existing.search_url == url:
            return candidate
        marker = f" ({suffix})"
        candidate = f"{base[:140-len(marker)]}{marker}"
        suffix += 1


def _project_for(
    db,
    project_by_url: dict[str, Project],
    project_by_name: dict[str, Project],
    owner_id: int,
    name: str,
    url: str,
) -> Project | None:
    if not url:
        return None
    if url in project_by_url:
        return project_by_url[url]
    project = db.scalar(
        select(Project).where(
            Project.owner_id == owner_id,
            Project.search_url == url,
        )
    )
    name_key = (name or "").strip().casefold()
    if not project and name_key in project_by_name:
        return project_by_name[name_key]
    if not project:
        unique_name = _unique_name(db, owner_id, name, url)
        project = Project(
            owner_id=owner_id,
            name=unique_name,
            name_key=unique_name.casefold(),
            search_url=url,
            scan_mode="FAST",
            search_page_mode="ALL_PAGES",
            auto_update=True,
        )
        db.add(project)
        db.flush()
    project_by_url[url] = project
    project_by_name.setdefault(name_key, project)
    return project


def _resolve_screenshot(root: Path, raw_path: Any) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(str(raw_path))
    if candidate.exists():
        return candidate
    parts = candidate.parts
    if "data" in parts:
        mapped = root.joinpath(*parts[parts.index("data") :])
        if mapped.exists():
            return mapped
    matches = list((root / "data" / "runs").glob(f"**/screenshots/{candidate.name}"))
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def _copy_current_screenshot(root: Path, row: dict, car: Car) -> bool:
    source = _resolve_screenshot(root, row.get("screenshot") or row.get("screenshot_path"))
    if not source:
        return False
    content = source.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    destination = Path(settings.storage_root) / "cars" / car.canonical_encar_id / "screenshot.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != checksum:
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(destination)
    return True


def run(
    root: Path,
    commit: bool = False,
    *,
    owner_id: int | None = None,
) -> dict:
    source = inspect_source(root)
    summary = {
        **source,
        "commit": commit,
        "projects_created": 0,
        "cars_created": 0,
        "relations_created": 0,
        "aliases_created": 0,
        "prices_created": 0,
        "duplicate_prices_removed": 0,
        "snapshots_created": 0,
        "images_copied": 0,
        "skipped_existing": 0,
    }
    if not root.exists() or not commit:
        return summary

    records: list[tuple[datetime, str, int, dict]] = []
    seen = load(root / "data" / "seen.json", {})
    for index, (key, value) in enumerate(seen.items() if isinstance(seen, dict) else []):
        if not isinstance(value, dict):
            continue
        row = clean(
            {
                "canonical_car_id": value.get("canonical_car_id")
                or extract_car_id(value.get("url", ""))
                or key,
                **value,
            }
        )
        timestamp = parse_time(row.get("last_seen_at") or row.get("first_seen_at")) or datetime.min.replace(
            tzinfo=timezone.utc
        )
        records.append((timestamp, "data/seen.json", index, row))

    for file in discover(root):
        payload = load(file, [])
        if not isinstance(payload, list):
            continue
        relative = str(file.relative_to(root))
        for index, raw in enumerate(payload):
            if not isinstance(raw, dict) or file.name == "search_debug.json" or file.name == "last_search_debug.json":
                continue
            row = clean(raw)
            timestamp = parse_time(
                row.get("checked_at") or row.get("found_at") or row.get("last_seen_at")
            ) or datetime.min.replace(tzinfo=timezone.utc)
            records.append((timestamp, relative, index, row))
    records.sort(key=lambda item: item[0])

    with SessionLocal.begin() as db:
        if owner_id is None:
            owner_id = db.scalar(
                select(User.id)
                .where(User.username_key == "serhii")
                .order_by(User.id)
            )
        if owner_id is None or not db.get(User, owner_id):
            raise ValueError("import_owner_not_found")
        project_by_url: dict[str, Project] = {}
        project_by_name: dict[str, Project] = {}
        before_projects = len(
            list(
                db.scalars(
                    select(Project).where(Project.owner_id == owner_id)
                )
            )
        )
        searches = load(root / "searches.json", [])
        for search in searches if isinstance(searches, list) else []:
            if isinstance(search, dict):
                _project_for(
                    db,
                    project_by_url,
                    project_by_name,
                    owner_id,
                    search.get("name", ""),
                    search.get("url", ""),
                )
        db.flush()

        snapshots = {
            (record.payload or {}).get("_legacy_key")
            for record in db.scalars(select(CarSnapshot).where(CarSnapshot.kind == "LEGACY_IMPORT"))
        }
        image_checksums = {
            (image.payload or {}).get("checksum")
            for image in db.scalars(select(CarImage).where(CarImage.kind == "SCREENSHOT"))
        }
        price_signatures: dict[int, set[tuple[int, str]]] = defaultdict(set)
        price_values: dict[int, set[int]] = defaultdict(set)
        for history in db.scalars(select(PriceHistory)):
            payload = history.payload or {}
            price = payload.get("price_krw")
            checked = payload.get("checked_at") or history.created_at.isoformat()
            if price:
                price_signatures[history.car_id].add((int(price), str(checked)))
                price_values[history.car_id].add(int(price))

        newest_screenshot: dict[int, tuple[datetime, dict]] = {}
        last_imported_price: dict[int, int] = {}
        for timestamp, source_file, index, row in records:
            canonical = str(
                row.get("canonical_car_id")
                or row.get("car_id")
                or extract_car_id(row.get("url", ""))
                or ""
            )
            if not canonical:
                continue
            alias_ids = {
                str(value)
                for value in (
                    row.get("source_car_id"),
                    row.get("car_id"),
                    *(row.get("duplicate_source_car_ids") or []),
                    *(row.get("alias_car_ids") or []),
                )
                if value
            }
            car = db.scalar(select(Car).where(Car.canonical_encar_id == canonical))
            if not car:
                for alias in alias_ids:
                    car = db.scalar(
                        select(Car)
                        .join(CarAlias, CarAlias.car_id == Car.id)
                        .where(CarAlias.alias_id == alias)
                    )
                    if car:
                        break
            if not car:
                car = Car(
                    canonical_encar_id=canonical,
                    url=row.get("url") or f"https://fem.encar.com/cars/detail/{canonical}",
                    title=row.get("title"),
                    current_price=row.get("price_krw") or row.get("last_price_krw"),
                    status="NEW",
                    details=row,
                    fingerprint=row.get("fingerprint") or fingerprint_listing(row),
                )
                db.add(car)
                db.flush()
                summary["cars_created"] += 1
            else:
                car.url = row.get("url") or car.url
                car.title = row.get("title") or car.title
                car.current_price = row.get("price_krw") or row.get("last_price_krw") or car.current_price
                existing_time = parse_time((car.details or {}).get("checked_at"))
                if not car.details or not existing_time or timestamp >= existing_time:
                    car.details = row
                car.fingerprint = row.get("fingerprint") or car.fingerprint

            for alias in alias_ids:
                if alias == car.canonical_encar_id:
                    continue
                if db.scalar(
                    select(Car.id).where(Car.canonical_encar_id == alias)
                ):
                    # One external Encar listing id must never identify two
                    # database cars. Keep the canonical record authoritative.
                    continue
                existing_alias = db.scalar(select(CarAlias).where(CarAlias.alias_id == alias))
                if not existing_alias:
                    db.add(CarAlias(car_id=car.id, alias_id=alias))
                    summary["aliases_created"] += 1

            project = _project_for(
                db,
                project_by_url,
                project_by_name,
                owner_id,
                row.get("source") or "Legacy Encar search",
                row.get("source_search_url") or "",
            )
            if project:
                link = db.get(ProjectCar, {"project_id": project.id, "car_id": car.id})
                if not link:
                    link = ProjectCar(project_id=project.id, car_id=car.id)
                    db.add(link)
                    summary["relations_created"] += 1
                first = parse_time(row.get("first_seen_at") or row.get("found_at"))
                last = parse_time(row.get("last_seen_at") or row.get("checked_at") or row.get("found_at"))
                if first and (not link.first_seen_at or first < link.first_seen_at):
                    link.first_seen_at = first
                if last and (not link.last_seen_at or last > link.last_seen_at):
                    link.last_seen_at = link.last_found_at = last

            legacy_key = f"{source_file}:{index}:{canonical}"
            if legacy_key not in snapshots:
                snapshot = dict(row)
                snapshot["_legacy_key"] = legacy_key
                snapshot["_source_file"] = source_file
                db.add(CarSnapshot(car_id=car.id, kind="LEGACY_IMPORT", payload=snapshot))
                snapshots.add(legacy_key)
                summary["snapshots_created"] += 1
            else:
                summary["skipped_existing"] += 1

            points = row.get("price_history") or []
            if row.get("price_krw") or row.get("last_price_krw"):
                points = [
                    *points,
                    {
                        "price_krw": row.get("price_krw") or row.get("last_price_krw"),
                        "checked_at": row.get("checked_at") or row.get("last_seen_at"),
                    },
                ]
            for point in points:
                if not isinstance(point, dict) or not point.get("price_krw"):
                    continue
                price = int(point["price_krw"])
                checked = str(point.get("checked_at") or timestamp.isoformat())
                signature = (price, checked)
                if signature in price_signatures[car.id] or price in price_values[car.id]:
                    last_imported_price[car.id] = price
                    continue
                if last_imported_price.get(car.id) == price:
                    continue
                db.add(
                    PriceHistory(
                        car_id=car.id,
                        kind="LEGACY",
                        payload={"price_krw": price, "checked_at": checked},
                        created_at=parse_time(checked) or timestamp,
                    )
                )
                price_signatures[car.id].add(signature)
                price_values[car.id].add(price)
                last_imported_price[car.id] = price
                summary["prices_created"] += 1

            if row.get("screenshot") or row.get("screenshot_path"):
                current = newest_screenshot.get(car.id)
                if not current or timestamp >= current[0]:
                    newest_screenshot[car.id] = (timestamp, row)

        for car_id, (_, row) in newest_screenshot.items():
            car = db.get(Car, car_id)
            if not car or not _copy_current_screenshot(root, row, car):
                continue
            path = Path(settings.storage_root) / "cars" / car.canonical_encar_id / "screenshot.png"
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            image = db.scalar(
                select(CarImage).where(CarImage.car_id == car.id, CarImage.kind == "SCREENSHOT")
            )
            if image:
                image.payload = {"path": str(path), "checksum": checksum}
            else:
                db.add(
                    CarImage(
                        car_id=car.id,
                        kind="SCREENSHOT",
                        payload={"path": str(path), "checksum": checksum},
                    )
                )
            if checksum not in image_checksums:
                summary["images_copied"] += 1
                image_checksums.add(checksum)
        previous_price: dict[int, int] = {}
        for point in db.scalars(
            select(PriceHistory).order_by(
                PriceHistory.car_id, PriceHistory.created_at, PriceHistory.id
            )
        ):
            price = int((point.payload or {}).get("price_krw") or 0)
            if price and previous_price.get(point.car_id) == price:
                db.delete(point)
                summary["duplicate_prices_removed"] += 1
            elif price:
                previous_price[point.car_id] = price
        summary["projects_created"] = max(
            0,
            len(
                list(
                    db.scalars(
                        select(Project).where(Project.owner_id == owner_id)
                    )
                )
            )
            - before_projects,
        )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--commit", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.root, arguments.commit), ensure_ascii=False, indent=2))
