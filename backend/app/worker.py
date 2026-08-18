import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

from sqlalchemy import select

from .database import SessionLocal, engine, settings
from .job_queue import claim_next_job
from .models import (
    Car,
    CarImage,
    CarSnapshot,
    Project,
    ProjectCar,
    ProjectScanRun,
    CarEvent,
    ScanRun,
    ScheduledProject,
    SchedulerSetting,
    User,
)
from .parser import fingerprint_listing, material_changes, should_read_detail
from .scanner import (
    PriceConfirmationError,
    ScanError,
    collect_search_pages,
    parse_list_row,
    read_detail,
)
from .services import (
    TrackingDisabledError,
    mark_absent,
    mark_found_without_detail,
    upsert_detail,
)
from .reporting import dedupe_report


class ScanCancelled(Exception):
    pass


def _reanchor_scheduler_after_manual_projects(
    db,
    job: ScanRun,
    finished_at: datetime,
) -> None:
    payload = dict(job.payload or {})
    if (
        getattr(job, "kind", None) != "PROJECTS"
        or payload.get("trigger") != "MANUAL"
        or getattr(job, "started_at", None) is None
    ):
        return
    setting = db.scalar(
        select(SchedulerSetting).where(
            SchedulerSetting.user_id == job.owner_id,
            SchedulerSetting.enabled.is_(True),
        )
    )
    if not setting or not db.scalar(
        select(ScheduledProject.project_id)
        .where(ScheduledProject.scheduler_id == setting.id)
        .limit(1)
    ):
        return
    setting.next_run_at = finished_at + timedelta(
        minutes=setting.interval_minutes
    )
    job.payload = {
        **payload,
        "scheduler_reanchored_at": finished_at.isoformat(),
        "scheduler_next_run_at": setting.next_run_at.isoformat(),
    }


def _record_completed_scheduler_run(
    db,
    job: ScanRun,
    finished_at: datetime,
) -> None:
    """Persist the successful-run checkpoint used after resume or restart."""

    payload = dict(job.payload or {})
    if job.status != "SUCCEEDED" or job.kind != "PROJECTS" or not (
        payload.get("trigger") == "MANUAL"
        or payload.get("trigger") == "AUTOMATIC"
        or payload.get("scheduled") is True
    ):
        return
    setting = db.scalar(
        select(SchedulerSetting).where(
            SchedulerSetting.user_id == job.owner_id,
            SchedulerSetting.enabled.is_(True),
        )
    )
    if not setting or not db.scalar(
        select(ScheduledProject.project_id)
        .where(ScheduledProject.scheduler_id == setting.id)
        .limit(1)
    ):
        return
    setting.last_completed_run_at = finished_at
    setting.next_run_at = finished_at + timedelta(minutes=setting.interval_minutes)
    job.payload = {
        **payload,
        "scheduler_last_completed_run_at": finished_at.isoformat(),
        "scheduler_next_run_at": setting.next_run_at.isoformat(),
    }


def _raise_if_cancelled(db, job: ScanRun) -> None:
    db.refresh(job, attribute_names=["status", "payload"])
    if job.status in {"CANCEL_REQUESTED", "CANCELLED"}:
        raise ScanCancelled()
    job.heartbeat_at = datetime.now(timezone.utc)
    if not hasattr(job, "owner_id"):
        return
    owner = db.get(User, job.owner_id)
    if not owner or owner.status != "ACTIVE":
        job.status = "CANCEL_REQUESTED"
        db.commit()
        raise ScanCancelled()


def _owned_project(db, project_id: int, owner_id: int) -> Project | None:
    return db.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.owner_id == owner_id,
        )
    )


def _finish_cancelled_job(
    db,
    job: ScanRun,
    payload: dict,
    report: list[dict],
    failures: list[str],
    failure_details: list[dict],
) -> None:
    project_runs = list(
        db.scalars(
            select(ProjectScanRun).where(ProjectScanRun.scan_run_id == job.id)
        )
    )
    project_statuses = dict((job.payload or {}).get("project_statuses") or {})
    interruption_reason = (job.payload or {}).get("cancellation_reason")
    interrupted_by_sleep = interruption_reason == "SYSTEM_SLEEP"
    terminal_status = "INTERRUPTED_SLEEP" if interrupted_by_sleep else "CANCELLED"
    for project_run in project_runs:
        if project_run.status in {"QUEUED", "RUNNING"}:
            project_run.status = terminal_status
            project_run.error_code = "SYSTEM_SLEEP" if interrupted_by_sleep else None
        project_statuses[str(project_run.project_id)] = project_run.status

    report = _dedupe_report(report)
    finished_at = datetime.now(timezone.utc)
    timestamp = finished_at.isoformat()
    job.status = terminal_status
    job.finished_at = finished_at
    job.heartbeat_at = finished_at
    job.error = "\n".join(failures) or (
        "Computer entered sleep before the scan finished"
        if interrupted_by_sleep
        else None
    )
    job.payload = {
        **payload,
        **(job.payload or {}),
        "current_project_id": None,
        "project_statuses": project_statuses,
        **(
            {
                "interrupted_at": timestamp,
                "interruption_reason": "SYSTEM_SLEEP",
                "partial_report": True,
            }
            if interrupted_by_sleep
            else {"cancelled_at": timestamp}
        ),
        "report": report,
        "failures": failure_details,
        "summary": {
            "changed": len(report),
            "new": sum(item["change"] == "NEW" for item in report),
            "price_changes": sum(
                item["change"] in {"PRICE_DROP", "PRICE_INCREASE"}
                for item in report
            ),
            "failed": len(failures),
        },
    }
    _reanchor_scheduler_after_manual_projects(db, job, finished_at)
    db.commit()


def _publish_project_results(
    job: ScanRun,
    payload: dict,
    report: list[dict],
    failures: list[str],
    failure_details: list[dict],
    project_statuses: dict[str, str],
) -> None:
    """Persist the completed-project portion so the UI can render it mid-scan."""

    partial_report = _dedupe_report(report)
    job.payload = {
        **(job.payload or payload),
        "current_project_id": None,
        "project_statuses": project_statuses,
        "report": partial_report,
        "failures": list(failure_details),
        "summary": {
            "changed": len(partial_report),
            "new": sum(item.get("change") == "NEW" for item in partial_report),
            "price_changes": sum(
                item.get("change") in {"PRICE_DROP", "PRICE_INCREASE"}
                for item in partial_report
            ),
            "failed": len(failures),
        },
    }


def _material_changes(old: dict, new: dict) -> list[dict]:
    return material_changes(old, new)


def _failure_detail(
    *,
    exc: Exception,
    scope: str,
    project: Project | None = None,
    car: Car | None = None,
) -> dict:
    technical = str(exc)
    if isinstance(exc, ScanError) or isinstance(getattr(exc, "code", None), str):
        code = exc.code
    elif any(marker in technical for marker in ("ERR_TIMED_OUT", "Timeout", "timeout")):
        code = "TIMEOUT"
    else:
        code = type(exc).__name__[:30].upper()
    return {
        "scope": scope,
        "project_id": project.id if project else None,
        "project_name": project.name if project else None,
        "car_id": car.id if car else None,
        "encar_id": car.canonical_encar_id if car else None,
        "code": code,
        "technical": technical[:4000],
    }


def _dedupe_report(report: list[dict]) -> list[dict]:
    return dedupe_report(report)


def _known_car(db, source_id: str) -> Car | None:
    # A search result id identifies one Encar listing. Legacy aliases are kept
    # for user lookup only; using them during scans caused two database rows to
    # be compared as if they were the same listing.
    return db.scalar(select(Car).where(Car.canonical_encar_id == source_id))


def _should_refresh_missing_car(
    relation: ProjectCar, car: Car, found_ids: set[str]
) -> bool:
    return (
        getattr(relation, "tracking_enabled", True)
        and not car.excluded
        and car.canonical_encar_id not in found_ids
    )


def _missing_cars(
    db, project: Project, found_ids: set[str]
) -> list[Car]:
    links = db.execute(
        select(ProjectCar, Car)
        .join(Car, Car.id == ProjectCar.car_id)
        .where(
            ProjectCar.project_id == project.id,
            ProjectCar.tracking_enabled.is_(True),
        )
    ).all()
    return [
        car
        for relation, car in links
        if _should_refresh_missing_car(relation, car, found_ids)
    ]


def _should_apply_search_absence(page_mode: str) -> bool:
    # A first-page scan is deliberately a partial view of the search result,
    # so absence from it cannot change project-wide search membership.
    return page_mode == "ALL_PAGES"


def _list_changed(car: Car, list_data: dict) -> bool:
    details = car.details or {}
    return any(
        value is not None and value != (car.current_price if key == "price_krw" else details.get(key))
        for key, value in list_data.items()
        if key != "title"
    )


@dataclass(frozen=True)
class PreviousState:
    car_id: int
    canonical_id: str
    price: int | None
    fingerprint: str | None
    status: str
    details: dict
    search_status: str | None
    snapshot_id: int | None = None


def _previous_state(
    db, car: Car | None, relation: ProjectCar | None = None
) -> PreviousState | None:
    if not car or relation is None:
        return None
    snapshot_id = relation.last_observed_snapshot_id
    snapshot = db.get(CarSnapshot, snapshot_id) if snapshot_id else None
    previous_details = (
        dict(snapshot.payload or {})
        if snapshot and snapshot.integrity_status == "VALID"
        else dict(car.details or {})
    )
    previous_status = (
        relation.last_observed_status
        if relation.last_observed_at is not None
        and relation.last_observed_status
        else (
            "SOLD"
            if previous_details.get("sold")
            else "UPDATED" if car.status == "SOLD" else car.status
        )
    )
    return PreviousState(
        car_id=car.id,
        canonical_id=car.canonical_encar_id,
        price=(
            relation.last_observed_price
            if relation.last_observed_at is not None
            else car.current_price
        ),
        fingerprint=(
            relation.last_observed_fingerprint
            if relation.last_observed_at is not None
            else fingerprint_listing(previous_details)
        ),
        status=previous_status,
        details=previous_details,
        search_status=getattr(relation, "search_status", None),
        snapshot_id=snapshot_id,
    )


def _latest_snapshot_id(db, car_id: int) -> int | None:
    db.flush()
    return db.scalar(
        select(CarSnapshot.id)
        .where(
            CarSnapshot.car_id == car_id,
            CarSnapshot.integrity_status == "VALID",
        )
        .order_by(CarSnapshot.created_at.desc(), CarSnapshot.id.desc())
        .limit(1)
    )


def _remember_observation(
    db,
    relation: ProjectCar,
    car: Car,
    snapshot_id: int | None,
) -> None:
    relation.last_observed_snapshot_id = snapshot_id
    relation.last_observed_price = car.current_price
    relation.last_observed_fingerprint = fingerprint_listing(car.details or {})
    relation.last_observed_status = car.status
    relation.last_observed_at = datetime.now(timezone.utc)


def _read_verified_detail(page, row: dict, storage: Path, known: Car | None) -> dict:
    detail = read_detail(page, row, storage)
    new_price = detail.get("price_krw")
    if (
        known
        and known.current_price
        and new_price
        and new_price != known.current_price
        and not detail.get("sold")
    ):
        confirmation = read_detail(page, row, storage)
        if (
            confirmation.get("canonical_car_id") != detail.get("canonical_car_id")
            or confirmation.get("price_krw") != new_price
        ):
            raise PriceConfirmationError(
                "Encar returned inconsistent listing identity or price on confirmation"
            )
        detail = confirmation
    return detail


def _change_report(
    car: Car,
    project: Project,
    previous: PreviousState | None,
    *,
    search_status: str | None = None,
    after_snapshot_id: int | None = None,
) -> dict | None:
    if previous is None:
        change = "NEW"
        old_price = None
        changes = []
    else:
        if (
            previous.car_id != car.id
            or previous.canonical_id != car.canonical_encar_id
        ):
            return None
        old_price = previous.price
        if car.status == "SOLD":
            if previous.status == "SOLD":
                return None
            change = "SOLD"
            changes = [
                {"field": "status", "old": previous.status, "new": "SOLD"}
            ]
        elif old_price != car.current_price:
            change = "PRICE_DROP" if (old_price or 0) > (car.current_price or 0) else "PRICE_INCREASE"
            changes = [{"field": "price", "old": old_price, "new": car.current_price}]
        elif previous.fingerprint != car.fingerprint:
            changes = _material_changes(previous.details, car.details or {})
            if not changes:
                return None
            change = "MATERIAL_UPDATE"
        elif previous.status != car.status:
            change = car.status
            changes = [{"field": "status", "old": previous.status, "new": car.status}]
        elif (
            previous.search_status == "NOT_FOUND_IN_SEARCH"
            and search_status == "FOUND"
        ):
            change = "RELISTED"
            changes = [
                {
                    "field": "search_status",
                    "old": "NOT_FOUND_IN_SEARCH",
                    "new": "FOUND",
                }
            ]
        else:
            return None
    return {
        "car_id": car.id,
        "project_id": project.id,
        "project_name": project.name,
        "encar_id": car.canonical_encar_id,
        "source_car_id": (car.details or {}).get("source_car_id"),
        "registration_number": (car.details or {}).get("registration_number"),
        "previous_car_id": previous.car_id if previous else None,
        "before_snapshot_id": previous.snapshot_id if previous else None,
        "after_snapshot_id": after_snapshot_id,
        "title": car.title,
        "change": change,
        "changes": changes,
        "old_price": old_price,
        "price": car.current_price,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def process_job(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(ScanRun, job_id)
        if not job or job.status not in {"QUEUED", "RUNNING"}:
            return
        owner = db.get(User, job.owner_id)
        if not owner or owner.status != "ACTIVE":
            finished_at = datetime.now(timezone.utc)
            job.status = "CANCELLED"
            job.finished_at = finished_at
            job.heartbeat_at = finished_at
            job.payload = {
                **(job.payload or {}),
                "cancelled_at": finished_at.isoformat(),
                "cancellation_reason": "ACCOUNT_INACTIVE",
            }
            db.commit()
            return
        timestamp = datetime.now(timezone.utc)
        if job.status == "QUEUED":
            # Compatibility path for callers outside the durable worker loop.
            # Desktop workers claim atomically before entering this function.
            job.status = "RUNNING"
            job.attempt_count = (job.attempt_count or 0) + 1
        job.started_at = job.started_at or timestamp
        job.heartbeat_at = timestamp
        db.commit()
        payload = job.payload or {}
        project_ids = list(dict.fromkeys(payload.get("project_ids") or []))
        car_ids = list(dict.fromkeys(payload.get("car_ids") or []))
        if payload.get("project_id") and not car_ids:
            project_ids = [payload["project_id"]]
        for project_id in project_ids:
            if _owned_project(db, project_id, job.owner_id):
                db.add(
                    ProjectScanRun(
                        scan_run_id=job.id,
                        project_id=project_id,
                        status="QUEUED",
                    )
                )
        db.commit()
        total_units = max(1, len(project_ids) + len(car_ids))
        completed = 0
        failures: list[str] = []
        failure_details: list[dict] = []
        successes = 0
        report: list[dict] = []
        stop_for_captcha = False

        try:
            from .desktop_license import require_search_entitlement

            require_search_entitlement()
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                executable_path = os.environ.get("SEARCHCAR_CHROMIUM_EXECUTABLE")
                browser = playwright.chromium.launch(
                    headless=settings.playwright_headless,
                    executable_path=executable_path or None,
                )
                context = browser.new_context(viewport={"width": 1440, "height": 1200}, locale="ko-KR")
                page = context.new_page()
                _raise_if_cancelled(db, job)

                for project_id in project_ids:
                    _raise_if_cancelled(db, job)
                    project = _owned_project(db, project_id, job.owner_id)
                    if not project:
                        failures.append(f"project:{project_id}:NOT_FOUND")
                        failure_details.append(
                            {
                                "scope": "PROJECT",
                                "project_id": project_id,
                                "project_name": None,
                                "car_id": None,
                                "encar_id": None,
                                "code": "NOT_FOUND",
                                "technical": "Project no longer exists",
                            }
                        )
                        completed += 1
                        continue
                    project_run = db.scalar(
                        select(ProjectScanRun).where(
                            ProjectScanRun.scan_run_id == job.id,
                            ProjectScanRun.project_id == project.id,
                        )
                    )
                    project_run.status = "RUNNING"
                    job.payload = {
                        **(job.payload or payload),
                        "current_project_id": project.id,
                    }
                    db.commit()
                    try:
                        page_mode = getattr(
                            project,
                            "search_page_mode",
                            "FIRST_PAGE",
                        )

                        def update_page_progress(info: dict) -> None:
                            pagination = dict(
                                (job.payload or {}).get("pagination") or {}
                            )
                            pagination[str(project.id)] = {
                                **info,
                                "mode": page_mode,
                            }
                            job.payload = {
                                **(job.payload or payload),
                                "pagination": pagination,
                            }
                            page_total = max(
                                int(info.get("total_pages") or 1),
                                int(info.get("current_page") or 1),
                            )
                            page_fraction = min(
                                1,
                                int(info.get("current_page") or 1)
                                / page_total,
                            )
                            job.progress = round(
                                (
                                    completed
                                    + 0.15 * page_fraction
                                )
                                / total_units
                                * 100
                            )
                            db.commit()

                        collection = collect_search_pages(
                            page,
                            project.search_url,
                            project.name,
                            all_pages=page_mode == "ALL_PAGES",
                            checkpoint=lambda: _raise_if_cancelled(db, job),
                            progress=update_page_progress,
                        )
                        rows = collection.rows
                        _raise_if_cancelled(db, job)
                        search_found_ids: set[str] = set()
                        processed_source_ids: set[str] = set()
                        confirmed_sold: set[str] = set()
                        row_values = list(rows.values())
                        for row_index, row in enumerate(row_values):
                            _raise_if_cancelled(db, job)
                            source_id = str(row["source_car_id"])
                            if source_id in processed_source_ids:
                                continue
                            processed_source_ids.add(source_id)
                            known = _known_car(db, source_id)
                            if known and known.excluded:
                                continue
                            known_relation = (
                                db.get(
                                    ProjectCar,
                                    {"project_id": project.id, "car_id": known.id},
                                )
                                if known
                                else None
                            )
                            if known_relation and not known_relation.tracking_enabled:
                                continue
                            previous = _previous_state(db, known, known_relation)
                            list_data = parse_list_row(row)
                            incomplete = (
                                not known
                                or not known.details
                                or not known.current_price
                                or not (known.details or {}).get("mileage_km")
                                or not db.scalar(
                                    select(CarImage.id).where(
                                        CarImage.car_id == known.id,
                                        CarImage.kind == "MAIN",
                                    )
                                )
                            )
                            if should_read_detail(
                                project.scan_mode,
                                is_new=known is None,
                                list_changed=bool(known and _list_changed(known, list_data)),
                                incomplete=incomplete,
                            ):
                                try:
                                    # Browser I/O must not hold a SQLite write
                                    # or read transaction open.
                                    db.commit()
                                    detail = _read_verified_detail(
                                        page,
                                        row,
                                        Path(settings.storage_root),
                                        known,
                                    )
                                    detail["scan_run_id"] = job.id
                                    car = upsert_detail(
                                        db,
                                        project,
                                        detail,
                                        mark_search_found=True,
                                    )
                                except TrackingDisabledError:
                                    db.rollback()
                                    continue
                                except ScanError as exc:
                                    if known and known_relation:
                                        mark_found_without_detail(db, project, known)
                                        search_found_ids.add(
                                            known.canonical_encar_id
                                        )
                                    failure = _failure_detail(
                                        exc=exc,
                                        scope="SEARCH_CAR",
                                        project=project,
                                        car=known,
                                    )
                                    failure["encar_id"] = source_id
                                    failures.append(
                                        f"{project.name}:{source_id}:"
                                        f"{exc.code}: {exc}"
                                    )
                                    failure_details.append(failure)
                                    job.progress = round(
                                        (
                                            completed
                                            + 0.15
                                            + 0.85
                                            * (row_index + 1)
                                            / max(1, len(row_values))
                                        )
                                        / total_units
                                        * 100
                                    )
                                    db.commit()
                                    continue
                                if detail.get("sold"):
                                    confirmed_sold.add(car.canonical_encar_id)
                                current_relation = db.get(
                                    ProjectCar,
                                    {"project_id": project.id, "car_id": car.id},
                                )
                                after_snapshot_id = _latest_snapshot_id(
                                    db, car.id
                                )
                                if item := _change_report(
                                    car,
                                    project,
                                    previous,
                                    search_status=current_relation.search_status,
                                    after_snapshot_id=after_snapshot_id,
                                ):
                                    report.append(item)
                                _remember_observation(
                                    db,
                                    current_relation,
                                    car,
                                    after_snapshot_id,
                                )
                            else:
                                car = known
                                mark_found_without_detail(db, project, car)
                                current_relation = db.get(
                                    ProjectCar,
                                    {"project_id": project.id, "car_id": car.id},
                                )
                                after_snapshot_id = _latest_snapshot_id(
                                    db, car.id
                                )
                                if item := _change_report(
                                    car,
                                    project,
                                    previous,
                                    search_status=current_relation.search_status,
                                    after_snapshot_id=after_snapshot_id,
                                ):
                                    report.append(item)
                                _remember_observation(
                                    db,
                                    current_relation,
                                    car,
                                    after_snapshot_id,
                                )
                            search_found_ids.add(car.canonical_encar_id)
                            job.progress = round(
                                (
                                    completed
                                    + 0.15
                                    + 0.85
                                    * (row_index + 1)
                                    / max(1, len(row_values))
                                )
                                / total_units
                                * 100
                            )
                            db.commit()
                            _raise_if_cancelled(db, job)
                        for missing_car in _missing_cars(
                            db, project, search_found_ids
                        ):
                            try:
                                _raise_if_cancelled(db, job)
                                missing_relation = db.get(
                                    ProjectCar,
                                    {
                                        "project_id": project.id,
                                        "car_id": missing_car.id,
                                    },
                                )
                                previous = _previous_state(
                                    db, missing_car, missing_relation
                                )
                                missing_row = {
                                    "url": missing_car.url
                                    or (
                                        "https://fem.encar.com/cars/detail/"
                                        f"{missing_car.canonical_encar_id}"
                                    ),
                                    "source_car_id": missing_car.canonical_encar_id,
                                    "source": project.name,
                                    "list_text": "",
                                }
                                db.commit()
                                detail = _read_verified_detail(
                                    page,
                                    missing_row,
                                    Path(settings.storage_root),
                                    missing_car,
                                )
                                detail["scan_run_id"] = job.id
                                refreshed_car = upsert_detail(
                                    db,
                                    project,
                                    detail,
                                    mark_search_found=False,
                                )
                                if detail.get("sold"):
                                    confirmed_sold.update(
                                        {
                                            missing_car.canonical_encar_id,
                                            refreshed_car.canonical_encar_id,
                                        }
                                    )
                                refreshed_relation = db.get(
                                    ProjectCar,
                                    {
                                        "project_id": project.id,
                                        "car_id": refreshed_car.id,
                                    },
                                )
                                after_snapshot_id = _latest_snapshot_id(
                                    db, refreshed_car.id
                                )
                                if item := _change_report(
                                    refreshed_car,
                                    project,
                                    previous,
                                    search_status=refreshed_relation.search_status,
                                    after_snapshot_id=after_snapshot_id,
                                ):
                                    report.append(item)
                                _remember_observation(
                                    db,
                                    refreshed_relation,
                                    refreshed_car,
                                    after_snapshot_id,
                                )
                                db.commit()
                                _raise_if_cancelled(db, job)
                            except ScanCancelled:
                                raise
                            except Exception as exc:
                                failures.append(
                                    "missing:"
                                    f"{missing_car.canonical_encar_id}:"
                                    f"{type(exc).__name__}: {exc}"
                                )
                                failure_details.append(
                                    _failure_detail(
                                        exc=exc,
                                        scope="MISSING_CAR",
                                        project=project,
                                        car=missing_car,
                                    )
                                )
                                db.rollback()
                        _raise_if_cancelled(db, job)
                        if _should_apply_search_absence(page_mode):
                            for missing_car, search_change in mark_absent(
                                db,
                                project,
                                search_found_ids,
                                True,
                                confirmed_sold_ids=confirmed_sold,
                            ):
                                report.append(
                                    {
                                        "car_id": missing_car.id,
                                        "project_id": project.id,
                                        "project_name": project.name,
                                        "encar_id": missing_car.canonical_encar_id,
                                        "title": missing_car.title,
                                        "change": search_change,
                                        "changes": (
                                            [
                                                {
                                                    "field": "search_status",
                                                    "old": "NOT_FOUND_IN_SEARCH",
                                                    "new": "FOUND",
                                                }
                                            ]
                                            if search_change == "RELISTED"
                                            else []
                                        ),
                                        "price": missing_car.current_price,
                                        "updated_at": datetime.now(timezone.utc).isoformat(),
                                    }
                                )
                        project.updated_at = datetime.now(timezone.utc)
                        project_run.status = "SUCCEEDED"
                        successes += 1
                        db.commit()
                    except ScanCancelled:
                        raise
                    except ScanError as exc:
                        project_run.status = "FAILED"
                        project_run.error_code = exc.code
                        stop_for_captcha = exc.code == "CAPTCHA"
                        failures.append(f"{project.name}:{exc.code}: {exc}")
                        failure_details.append(
                            _failure_detail(exc=exc, scope="PROJECT", project=project)
                        )
                        db.commit()
                    except Exception as exc:
                        failure = _failure_detail(
                            exc=exc, scope="PROJECT", project=project
                        )
                        project_run.status = "FAILED"
                        project_run.error_code = failure["code"]
                        failures.append(f"{project.name}:{type(exc).__name__}: {exc}")
                        failure_details.append(failure)
                        db.commit()
                    project_statuses = dict(
                        (job.payload or {}).get("project_statuses") or {}
                    )
                    project_statuses[str(project.id)] = project_run.status
                    _publish_project_results(
                        job,
                        payload,
                        report,
                        failures,
                        failure_details,
                        project_statuses,
                    )
                    completed += 1
                    job.progress = round(completed / total_units * 100)
                    db.commit()
                    _raise_if_cancelled(db, job)
                    if stop_for_captcha:
                        for pending in db.scalars(
                            select(ProjectScanRun).where(
                                ProjectScanRun.scan_run_id == job.id,
                                ProjectScanRun.status == "QUEUED",
                            )
                        ):
                            pending.status = "FAILED"
                            pending.error_code = "CAPTCHA"
                        db.commit()
                        break

                for car_id in car_ids:
                    _raise_if_cancelled(db, job)
                    car = db.get(Car, car_id)
                    if not car:
                        failures.append(f"car:{car_id}:NOT_FOUND")
                        failure_details.append(
                            {
                                "scope": "CAR",
                                "project_id": payload.get("project_id"),
                                "project_name": None,
                                "car_id": car_id,
                                "encar_id": None,
                                "code": "NOT_FOUND",
                                "technical": "Car no longer exists",
                            }
                        )
                    else:
                        project = (
                            _owned_project(
                                db,
                                payload.get("project_id"),
                                job.owner_id,
                            )
                            if payload.get("project_id")
                            else db.scalar(
                                select(Project)
                                .join(ProjectCar, ProjectCar.project_id == Project.id)
                                .where(
                                    Project.owner_id == job.owner_id,
                                    ProjectCar.car_id == car.id,
                                    ProjectCar.tracking_enabled.is_(True),
                                )
                            )
                        )
                        if project:
                            try:
                                current_relation = db.get(
                                    ProjectCar,
                                    {
                                        "project_id": project.id,
                                        "car_id": car.id,
                                    },
                                )
                                previous = _previous_state(
                                    db, car, current_relation
                                )
                                manual_row = {
                                    "url": car.url,
                                    "source_car_id": car.canonical_encar_id,
                                    "source": project.name,
                                    "list_text": "",
                                }
                                db.commit()
                                detail = _read_verified_detail(
                                    page,
                                    manual_row,
                                    Path(settings.storage_root),
                                    car,
                                )
                                detail["scan_run_id"] = job.id
                                refreshed_car = upsert_detail(
                                    db,
                                    project,
                                    detail,
                                    mark_search_found=False,
                                )
                                db.add(
                                    CarEvent(
                                        car_id=refreshed_car.id,
                                        kind="MANUAL_REFRESH",
                                        user_id=job.owner_id,
                                        project_id=project.id,
                                        payload={"scan_run_id": job.id},
                                    )
                                )
                                refreshed_relation = db.get(
                                    ProjectCar,
                                    {
                                        "project_id": project.id,
                                        "car_id": refreshed_car.id,
                                    },
                                )
                                after_snapshot_id = _latest_snapshot_id(
                                    db, refreshed_car.id
                                )
                                if item := _change_report(
                                    refreshed_car,
                                    project,
                                    previous,
                                    search_status=refreshed_relation.search_status,
                                    after_snapshot_id=after_snapshot_id,
                                ):
                                    report.append(item)
                                else:
                                    report.append(
                                        {
                                            "car_id": refreshed_car.id,
                                            "project_id": project.id,
                                            "project_name": project.name,
                                            "encar_id": refreshed_car.canonical_encar_id,
                                            "title": refreshed_car.title,
                                            "change": "MANUAL_REFRESH",
                                            "changes": [],
                                            "before_snapshot_id": (
                                                previous.snapshot_id
                                                if previous
                                                else None
                                            ),
                                            "after_snapshot_id": after_snapshot_id,
                                            "price": refreshed_car.current_price,
                                            "updated_at": datetime.now(timezone.utc).isoformat(),
                                        }
                                    )
                                _remember_observation(
                                    db,
                                    refreshed_relation,
                                    refreshed_car,
                                    after_snapshot_id,
                                )
                                successes += 1
                                db.commit()
                            except ScanCancelled:
                                raise
                            except Exception as exc:
                                failures.append(f"{car.canonical_encar_id}:{type(exc).__name__}: {exc}")
                                failure_details.append(
                                    _failure_detail(
                                        exc=exc,
                                        scope="CAR",
                                        project=project,
                                        car=car,
                                    )
                                )
                                db.rollback()
                    completed += 1
                    job.progress = round(completed / total_units * 100)
                    db.commit()
                    _raise_if_cancelled(db, job)
                context.close()
                browser.close()

            _raise_if_cancelled(db, job)
            report = _dedupe_report(report)
            job.progress = 100
            job.error = "\n".join(failures) or None
            job.status = "SUCCEEDED" if not failures else ("PARTIAL" if successes else "FAILED")
            job.finished_at = datetime.now(timezone.utc)
            job.heartbeat_at = job.finished_at
            job.payload = {
                **(job.payload or payload),
                "report": report,
                "failures": failure_details,
                "summary": {
                    "changed": len(report),
                    "new": sum(item["change"] == "NEW" for item in report),
                    "price_changes": sum(
                        item["change"] in {"PRICE_DROP", "PRICE_INCREASE"} for item in report
                    ),
                    "failed": len(failures),
                },
            }
            _record_completed_scheduler_run(db, job, job.finished_at)
            _reanchor_scheduler_after_manual_projects(
                db,
                job,
                job.finished_at,
            )
            db.commit()
        except ScanCancelled:
            _finish_cancelled_job(
                db,
                job,
                payload,
                report,
                failures,
                failure_details,
            )
        except Exception as exc:
            report = _dedupe_report(report)
            job.status = "FAILED"
            job.finished_at = datetime.now(timezone.utc)
            job.heartbeat_at = job.finished_at
            job.error = f"{type(exc).__name__}: {exc}"
            job.payload = {
                **(job.payload or payload),
                "report": report,
                "failures": [
                    *failure_details,
                    _failure_detail(exc=exc, scope="RUN"),
                ],
                "summary": {
                    "changed": len(report),
                    "new": sum(item["change"] == "NEW" for item in report),
                    "price_changes": sum(
                        item["change"] in {"PRICE_DROP", "PRICE_INCREASE"}
                        for item in report
                    ),
                    "failed": len(failure_details) + 1,
                },
            }
            _reanchor_scheduler_after_manual_projects(
                db,
                job,
                job.finished_at,
            )
            db.commit()


def enqueue_scheduled(*, now: datetime | None = None) -> None:
    from .desktop_license import evaluate_search_entitlement
    from .maintenance import maintenance_active

    if maintenance_active():
        return
    entitlement = evaluate_search_entitlement()
    with SessionLocal.begin() as db:
        current = now or datetime.now(timezone.utc)
        settings_to_check = list(
            db.scalars(
                select(SchedulerSetting)
                .join(User, User.id == SchedulerSetting.user_id)
                .where(
                    SchedulerSetting.enabled.is_(True),
                    User.status == "ACTIVE",
                )
            )
        )
        for setting in settings_to_check:
            if setting.paused:
                continue
            if not setting.next_run_at:
                setting.next_run_at = current + timedelta(
                    minutes=setting.interval_minutes
                )
                continue
            if setting.next_run_at > current:
                continue
            if not entitlement.can_search:
                setting.next_run_at = current + timedelta(
                    minutes=setting.interval_minutes
                )
                continue
            ids = list(
                db.scalars(
                    select(ScheduledProject.project_id)
                    .join(Project, Project.id == ScheduledProject.project_id)
                    .where(
                        ScheduledProject.scheduler_id == setting.id,
                        Project.owner_id == setting.user_id,
                        Project.auto_update.is_(True),
                    )
                )
            )
            if not ids:
                setting.enabled = False
                setting.next_run_at = None
                continue
            active = db.scalar(
                select(ScanRun).where(
                    ScanRun.owner_id == setting.user_id,
                    ScanRun.status.in_(
                        ["QUEUED", "RUNNING", "CANCEL_REQUESTED"]
                    ),
                )
            )
            if not active:
                db.add(
                    ScanRun(
                        owner_id=setting.user_id,
                        kind="PROJECTS",
                        status="QUEUED",
                        payload={
                            "project_ids": ids,
                            "scheduled": True,
                            "trigger": "AUTOMATIC",
                        },
                    )
                )
            setting.next_run_at = current + timedelta(
                minutes=setting.interval_minutes
            )


def run(stop_event: Event | None = None) -> None:
    worker_id = f"desktop-{uuid4().hex}"
    while not (stop_event and stop_event.is_set()):
        enqueue_scheduled()
        job_id = claim_next_job(engine, worker_id)
        if job_id:
            process_job(job_id)
        elif stop_event:
            stop_event.wait(settings.worker_poll_seconds)
        else:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run()
