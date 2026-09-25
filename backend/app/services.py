from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .models import (
    Car,
    CarEvent,
    CarImage,
    CarSnapshot,
    PriceHistory,
    Project,
    ProjectCar,
)
from .parser import (
    accident_signature,
    apply_missing,
    fingerprint_listing,
    material_changes,
    price_change,
)
from .database import settings
from .storage_paths import portable_storage_path


def now() -> datetime:
    return datetime.now(timezone.utc)


def _image_record(db, car_id: int, kind: str, path: str | None, checksum: str | None) -> None:
    path = portable_storage_path(path, Path(settings.storage_root))
    if not path:
        return
    existing = db.scalar(
        select(CarImage).where(CarImage.car_id == car_id, CarImage.kind == kind)
    )
    payload = {"path": path, "checksum": checksum, "updated_at": now().isoformat()}
    if existing:
        existing.payload = payload
    else:
        db.add(CarImage(car_id=car_id, kind=kind, payload=payload))


def merge_reliable_detail(previous: dict, current: dict) -> dict:
    if current.get("sold") and previous:
        # A sold/deleted Encar page is only an availability signal. It does not
        # contain the listing's former specifications, so parsing its generic
        # shell must never erase or mutate previously verified vehicle data.
        sold_page_fields = (
            "canonical_car_id",
            "source_car_id",
            "displayed_car_id",
            "source",
            "source_url",
            "url",
            "checked_at",
            "scan_run_id",
            "parse_quality",
            "sold",
            "unavailable",
            "screenshot_path",
            "screenshot_checksum",
        )
        merged = dict(previous)
        for field in sold_page_fields:
            if field in current:
                merged[field] = current[field]
        merged["sold"] = True
        merged["unavailable"] = False
        merged["parse_quality"] = "SOLD_PAGE"
        merged["fingerprint"] = fingerprint_listing(merged)
        return merged

    merged = {**previous, **current}
    stable_fields = (
        "trim",
        "year_month",
        "production_date",
        "registration_date",
        "registration_number",
        "vin",
        "mileage_km",
        "fuel",
        "drivetrain",
        "transmission",
        "engine_displacement_cc",
        "body_type",
        "exterior_color",
        "interior_color",
        "new_car_price_percent",
    )
    missing_values = (None, "", "UNVERIFIED")
    for field in stable_fields:
        if current.get(field) in missing_values and previous.get(field) not in missing_values:
            merged[field] = previous[field]

    merged["options"] = {
        **(previous.get("options") or {}),
        **(current.get("options") or {}),
    }
    old_condition = previous.get("condition") or {}
    new_condition = current.get("condition") or {}
    condition = {**old_condition}
    for key, new_value in new_condition.items():
        old_value = old_condition.get(key)
        if (
            isinstance(new_value, dict)
            and new_value.get("status") == "UNVERIFIED"
            and isinstance(old_value, dict)
            and old_value.get("status") in {"CONFIRMED", "NOT_FOUND"}
        ):
            continue
        condition[key] = new_value
    merged["condition"] = condition
    if (
        current.get("condition_summary") == "UNVERIFIED"
        and previous.get("condition_summary") not in missing_values
    ):
        merged["condition_summary"] = previous["condition_summary"]
    merged["fingerprint"] = fingerprint_listing(merged)
    return merged


class TrackingDisabledError(RuntimeError):
    pass


def upsert_price_status(db, project: Project, status: dict, *, mark_search_found: bool = True) -> Car:
    """Persist a verified price/availability probe without replacing a full profile."""

    return upsert_detail(
        db,
        project,
        {
            "canonical_car_id": status["canonical_car_id"],
            "source_car_id": status.get("source_car_id"),
            "displayed_car_id": status.get("displayed_car_id"),
            "url": status.get("url"),
            "price_krw": None if status.get("sold") else status.get("price_krw"),
            "price_source": None if status.get("sold") else "DETAIL_PRIMARY",
            "checked_at": status.get("checked_at"),
            "parse_quality": "SOLD_PAGE" if status.get("sold") else "PRICE_STATUS",
            "sold": bool(status.get("sold")),
            "unavailable": False,
        },
        mark_search_found=mark_search_found,
    )


def upsert_detail(
    db,
    project: Project,
    detail: dict,
    *,
    mark_search_found: bool = True,
) -> Car:
    canonical = str(detail["canonical_car_id"])
    source_id = str(detail.get("source_car_id") or canonical)
    if not detail.get("sold") and source_id != canonical:
        raise ValueError(
            f"Refusing cross-listing update: requested {source_id}, displayed {canonical}"
        )
    car = db.scalar(select(Car).where(Car.canonical_encar_id == canonical))
    link = (
        db.get(ProjectCar, {"project_id": project.id, "car_id": car.id})
        if car
        else None
    )
    if car and (car.excluded or (link and not link.tracking_enabled)):
        raise TrackingDisabledError(
            f"Tracking disabled for project={project.id}, car={car.id}"
        )

    old_price = car.current_price if car else None
    old_details = car.details or {} if car else {}
    old_fingerprint = fingerprint_listing(old_details) if old_details else None
    detail = merge_reliable_detail(old_details, detail)
    material_diff = material_changes(old_details, detail) if old_details else []
    if not car:
        car = Car(
            canonical_encar_id=canonical,
            url=detail.get("url") or f"https://fem.encar.com/cars/detail/{canonical}",
            title=detail.get("title"),
            status="NEW",
        )
        db.add(car)
        db.flush()
        db.add(CarEvent(car_id=car.id, kind="NEW", payload=detail))

    authoritative_price = (
        detail.get("price_krw")
        if detail.get("price_source") == "DETAIL_PRIMARY"
        else None
    )
    provenance = {
        "source_car_id": source_id,
        "price_source": detail.get("price_source"),
        "scan_run_id": detail.get("scan_run_id"),
        "checked_at": detail.get("checked_at"),
    }
    change = price_change(old_price, authoritative_price)
    sold_transition = bool(detail.get("sold") and car.status != "SOLD")
    if authoritative_price and authoritative_price != old_price:
        db.add(
            PriceHistory(
                car_id=car.id,
                kind="PRICE",
                payload={
                    "price_krw": authoritative_price,
                    "price_source": "DETAIL_PRIMARY",
                    "source_car_id": source_id,
                    "scan_run_id": detail.get("scan_run_id"),
                    "checked_at": detail.get("checked_at"),
                },
            )
        )
    if detail.get("sold"):
        if sold_transition:
            db.add(
                CarEvent(
                    car_id=car.id,
                    kind="SOLD",
                    payload={"reason": "encar_sold_or_deleted_page"},
                )
            )
        car.status = "SOLD"
    elif change:
        car.status = change["type"]
        db.add(
            CarEvent(
                car_id=car.id,
                kind=change["type"],
                payload={**change, **provenance},
            )
        )
    elif car.status == "SOLD":
        car.status = "UPDATED"
        db.add(
            CarEvent(
                car_id=car.id,
                kind="MATERIAL_UPDATE",
                payload={"availability": "active", **provenance},
            )
        )
    elif old_fingerprint and old_fingerprint != detail.get("fingerprint") and material_diff:
        car.status = "UPDATED"
        db.add(
            CarEvent(
                car_id=car.id,
                kind="MATERIAL_UPDATE",
                payload={"changes": material_diff, **provenance},
            )
        )

    if (
        old_details.get("mileage_km")
        and detail.get("mileage_km")
        and detail.get("mileage_km") != old_details.get("mileage_km")
    ):
        db.add(
            CarEvent(
                car_id=car.id,
                kind="MILEAGE_CHANGED",
                payload={
                    "old": old_details.get("mileage_km"),
                    "new": detail.get("mileage_km"),
                    **provenance,
                },
            )
        )
    if (
        old_details.get("accident")
        and accident_signature(detail.get("accident"))
        != accident_signature(old_details.get("accident"))
    ):
        db.add(
            CarEvent(
                car_id=car.id,
                kind="ACCIDENT_INFO_CHANGED",
                payload={"accident": detail["accident"], **provenance},
            )
        )

    car.url = detail.get("url") or car.url
    car.title = detail.get("title") or car.title
    car.current_price = authoritative_price or car.current_price
    car.fingerprint = detail.get("fingerprint")
    car.details = detail
    car.updated_at = now()

    link = link or db.get(
        ProjectCar, {"project_id": project.id, "car_id": car.id}
    )
    if not link:
        link = ProjectCar(
            project_id=project.id,
            car_id=car.id,
            first_seen_at=now(),
            search_status="FOUND",
            tracking_enabled=True,
        )
        db.add(link)
    if not link.tracking_enabled:
        raise TrackingDisabledError(
            f"Tracking disabled for project={project.id}, car={car.id}"
        )
    old_search_status = link.search_status
    if mark_search_found:
        link.search_status = "FOUND"
        link.last_seen_at = link.last_found_at = now()
        link.consecutive_missing_scans = 0
    else:
        link.last_seen_at = now()
    if mark_search_found and old_search_status == "NOT_FOUND_IN_SEARCH":
        db.add(
            CarEvent(
                car_id=car.id,
                kind="RELISTED",
                project_id=project.id,
                payload={
                    "project_id": project.id,
                    "reason": "found_again_in_same_project",
                },
            )
        )

    if (
        not old_fingerprint
        or change
        or material_diff
        or sold_transition
    ):
        db.add(CarSnapshot(car_id=car.id, kind="DATA_CHANGE", payload=detail))
    _image_record(db, car.id, "SCREENSHOT", detail.get("screenshot_path"), detail.get("screenshot_checksum"))
    _image_record(db, car.id, "MAIN", detail.get("main_image_path"), detail.get("main_image_checksum"))
    return car


def mark_found_without_detail(db, project: Project, car: Car) -> str | None:
    link = db.get(ProjectCar, {"project_id": project.id, "car_id": car.id})
    if not link:
        link = ProjectCar(
            project_id=project.id,
            car_id=car.id,
            first_seen_at=now(),
            search_status="FOUND",
            tracking_enabled=True,
        )
        db.add(link)
    if not link.tracking_enabled or car.excluded:
        return None
    change = None
    if link.search_status == "NOT_FOUND_IN_SEARCH":
        change = "RELISTED"
        db.add(
            CarEvent(
                car_id=car.id,
                kind="RELISTED",
                project_id=project.id,
                payload={
                    "project_id": project.id,
                    "reason": "found_again_in_same_project",
                },
            )
        )
    link.search_status = "FOUND"
    link.last_seen_at = link.last_found_at = now()
    link.consecutive_missing_scans = 0
    return change


def mark_absent(
    db,
    project: Project,
    found_ids: set[str],
    success: bool = True,
    confirmed_sold_ids: set[str] | None = None,
) -> list[tuple[Car, str]]:
    changed: list[tuple[Car, str]] = []
    confirmed_sold_ids = confirmed_sold_ids or set()
    links = db.execute(
        select(ProjectCar, Car)
        .join(Car, Car.id == ProjectCar.car_id)
        .where(
            ProjectCar.project_id == project.id,
            ProjectCar.tracking_enabled.is_(True),
            Car.excluded.is_(False),
        )
    ).all()
    for link, car in links:
        if car.canonical_encar_id in confirmed_sold_ids:
            link.search_status = "NOT_FOUND_IN_SEARCH"
            link.consecutive_missing_scans += 1
            if car.status != "SOLD":
                car.status = "SOLD"
                db.add(
                    CarEvent(
                        car_id=car.id,
                        kind="SOLD",
                        project_id=project.id,
                        payload={
                            "project_id": project.id,
                            "reason": "encar_sold_or_deleted_page",
                        },
                    )
                )
                changed.append((car, "SOLD"))
            continue
        count, status = apply_missing(
            success,
            car.canonical_encar_id in found_ids,
            link.consecutive_missing_scans,
            link.search_status,
        )
        stored_status = "FOUND" if status == "RELISTED" else status
        if status == "RELISTED":
            db.add(
                CarEvent(
                    car_id=car.id,
                    kind="RELISTED",
                    project_id=project.id,
                    payload={
                        "project_id": project.id,
                        "reason": "found_again_in_same_project",
                    },
                )
            )
            changed.append((car, "RELISTED"))
        elif stored_status != link.search_status:
            db.add(
                CarEvent(
                    car_id=car.id,
                    kind=stored_status,
                    project_id=project.id,
                    payload={"project_id": project.id, "missing_scans": count},
                )
            )
            changed.append((car, stored_status))
        link.consecutive_missing_scans = count
        link.search_status = stored_status
    return changed
