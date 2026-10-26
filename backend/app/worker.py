import os
import time
from copy import deepcopy
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select, update

from .database import SessionLocal, engine, settings
from .desktop_runtime import desktop_playwright
from .browser_runtime_pool import BrowserRuntimePool
from .detail_workers import DetailTask, DetailWorkerPool
from .external_links import validated_external_url
from .job_queue import SCAN_COMMAND_LOCK, claim_next_job, project_execution_order
from .live_updates import publish_scan_update
from .models import (
    Car,
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
from .sources.contracts import ScanError
from .sources.registry import get_source, get_source_descriptor
from .version import APP_VERSION
from .services import (
    TrackingDisabledError,
    mark_absent,
    mark_found_without_detail,
    upsert_detail,
    upsert_price_status,
)
from .reporting import dedupe_report
from .money import comparable_money, encar_money_values, payload_money_values


class ScanCancelled(Exception):
    pass


class ProjectScanCancelled(Exception):
    pass


class SourceBlockedError(ScanError):
    code = "CAPTCHA"

    def __init__(self, message, *, code="CAPTCHA"):
        self.code = code
        super().__init__(message)


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


def _raise_if_project_cancelled(db, job: ScanRun, project_id: int) -> None:
    _raise_if_cancelled(db, job)
    if project_id in set((job.payload or {}).get("cancelled_project_ids") or []):
        raise ProjectScanCancelled()
    if hasattr(job, "owner_id") and db.scalar(
        select(ProjectScanRun.id).where(
            ProjectScanRun.scan_run_id == job.id,
            ProjectScanRun.project_id == project_id,
            ProjectScanRun.status.in_(["CANCEL_REQUESTED", "CANCELLED"]),
        ).limit(1)
    ) is not None:
        raise ProjectScanCancelled()


def _owned_project(db, project_id: int, owner_id: int) -> Project | None:
    return db.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.owner_id == owner_id,
        )
    )


def _project_for_execution(project: Project, payload: dict):
    plan = next(
        (
            item
            for item in payload.get("project_plans") or []
            if item.get("project_id") == project.id
        ),
        None,
    )
    if plan is None:
        return project
    return SimpleNamespace(
        id=project.id,
        owner_id=project.owner_id,
        source_key=plan["source_key"],
        name=plan["name"],
        search_url=plan["search_url"],
        search_config=deepcopy(plan.get("search_config")),
        scan_mode=plan["scan_mode"],
        search_page_mode=plan["search_page_mode"],
        price_filter_mode=plan["price_filter_mode"],
        price_min_krw=plan["price_min_krw"],
        price_max_krw=plan["price_max_krw"],
        price_filter_revision=plan["price_filter_revision"],
        price_filter_baseline_revision=plan["price_filter_baseline_revision"],
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
        if project_run.status in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
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


def _publish_live_results(
    job: ScanRun,
    payload: dict,
    report: list[dict],
    failures: list[str],
    failure_details: list[dict],
    project_statuses: dict[str, str],
    *,
    current_project_id: int | None,
) -> None:
    """Persist committed report rows while a project is still running."""

    partial_report = _dedupe_report(report)
    job.payload = {
        **(job.payload or payload),
        "current_project_id": current_project_id,
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


def _publish_project_results(
    job: ScanRun,
    payload: dict,
    report: list[dict],
    failures: list[str],
    failure_details: list[dict],
    project_statuses: dict[str, str],
) -> None:
    """Persist the completed-project portion so the UI can render it mid-scan."""

    _publish_live_results(
        job,
        payload,
        report,
        failures,
        failure_details,
        project_statuses,
        current_project_id=None,
    )


def _material_changes(
    old: dict, new: dict, source_key: str = "encar"
) -> list[dict]:
    return get_source(source_key).material_changes(old, new)


def _failure_detail(
    *,
    exc: Exception,
    scope: str,
    project: Project | None = None,
    car: Car | None = None,
    url: str | None = None,
) -> dict:
    technical = str(exc)
    if isinstance(exc, ScanError) or isinstance(getattr(exc, "code", None), str):
        code = exc.code
    elif any(marker in technical for marker in ("ERR_TIMED_OUT", "Timeout", "timeout")):
        code = "TIMEOUT"
    else:
        code = type(exc).__name__[:30].upper()
    external_url = url or (car.url if car else None)
    try:
        external_url = validated_external_url(external_url) if external_url else None
    except ValueError:
        external_url = None
    return {
        "scope": scope,
        "source_key": getattr(project, "source_key", None) or getattr(car, "source_key", None),
        "project_id": project.id if project else None,
        "project_name": project.name if project else None,
        "car_id": car.id if car else None,
        "encar_id": car.canonical_encar_id if car else None,
        "url": external_url,
        "code": code,
        "technical": technical[:4000],
        **({"network_failures": exc.network_failures} if getattr(exc, "network_failures", None) else {}),
    }


def _dedupe_report(report: list[dict]) -> list[dict]:
    return dedupe_report(report)


def _known_car(db, source_id: str, source_key: str = "encar") -> Car | None:
    # A result id is unique only inside its source. Legacy aliases are kept for
    # user lookup and must not merge listings from two sites during a scan.
    return db.scalar(
        select(Car).where(
            Car.source_key == source_key,
            Car.source_listing_id == source_id,
        )
    )


def _should_refresh_missing_car(
    relation: ProjectCar, car: Car, found_ids: set[str]
) -> bool:
    return (
        getattr(relation, "tracking_enabled", True)
        and not car.excluded
        and getattr(car, "status", None) != "SOLD"
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


def _previous_new_car_ids(db, project_id: int) -> set[int]:
    return set(
        db.scalars(
            select(ProjectCar.car_id).where(
                ProjectCar.project_id == project_id,
                ProjectCar.is_new.is_(True),
            )
        )
    )


def _expire_previous_new_relations(
    db, project_id: int, car_ids: set[int]
) -> None:
    if not car_ids:
        return
    db.execute(
        update(ProjectCar)
        .where(
            ProjectCar.project_id == project_id,
            ProjectCar.car_id.in_(car_ids),
        )
        .values(is_new=False)
    )


def _should_apply_search_absence(page_mode: str) -> bool:
    # A first-page scan is deliberately a partial view of the search result,
    # so absence from it cannot change project-wide search membership.
    return page_mode == "ALL_PAGES"


def _requires_initial_full_scan(db, project_id: int) -> bool:
    return db.scalar(
        select(ProjectScanRun.id)
        .where(
            ProjectScanRun.project_id == project_id,
            ProjectScanRun.status == "SUCCEEDED",
        )
        .limit(1)
    ) is None


def _price_filter_needs_baseline(project: Project) -> bool:
    return int(getattr(project, "price_filter_revision", 1) or 1) != int(
        getattr(project, "price_filter_baseline_revision", 0) or 0
    )


def _effective_search_url(project: Project, source=None) -> str:
    mode = getattr(project, "price_filter_mode", "LINK")
    if mode == "CUSTOM":
        source = source or get_source(getattr(project, "source_key", "encar"))
        return source.apply_price_filter(
            project.search_url,
            getattr(project, "price_min_krw", None),
            getattr(project, "price_max_krw", None),
        )
    # Old desktop databases may still contain the removed NONE mode.
    # Treat it as LINK so existing projects continue to scan safely.
    return project.search_url


def _project_scan_modes(
    project: Project,
    initial_full_scan: bool,
    price_filter_needs_baseline: bool = False,
) -> tuple[str, str]:
    if initial_full_scan or price_filter_needs_baseline:
        return "ACCURATE", "ALL_PAGES"
    return project.scan_mode, getattr(project, "search_page_mode", "ALL_PAGES")


def _detail_worker_count(db, owner_id: int) -> int:
    """Keep existing installations conservative until accelerated mode is chosen."""

    setting = db.scalar(
        select(SchedulerSetting).where(SchedulerSetting.user_id == owner_id)
    )
    return 2 if getattr(setting, "performance_mode", "ECO") == "FAST" else 1


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


@dataclass(frozen=True)
class ProjectDetailContext:
    source_id: str
    known_car_id: int | None
    previous: PreviousState | None
    mark_search_found: bool = True


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
            else get_source(car.source_key).fingerprint_listing(previous_details)
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
    relation.last_observed_fingerprint = get_source(car.source_key).fingerprint_listing(
        car.details or {}
    )
    relation.last_observed_status = car.status
    relation.last_observed_at = datetime.now(timezone.utc)


def _read_verified_detail(
    page,
    row: dict,
    storage: Path,
    known_price: int | Car | None,
    *,
    read_kind: str = "FULL",
) -> dict:
    """Compatibility entry point; source readers receive scalar values, not ORM."""
    return get_source(row.get("source_key") or "encar").read_verified_detail(
        page, row, storage, getattr(known_price, "current_price", known_price),
        read_kind=read_kind,
    )


def _change_report(
    car: Car,
    project: Project,
    previous: PreviousState | None,
    *,
    search_status: str | None = None,
    after_snapshot_id: int | None = None,
) -> dict | None:
    details = car.details or {}
    current_money = (payload_money_values(details, getattr(car, "source_key", "encar"))
                     if "money_values" in details else encar_money_values(details, car.current_price))
    previous_money = []
    if previous:
        previous_money = (payload_money_values(previous.details or {}, getattr(car, "source_key", "encar"))
                          if "money_values" in (previous.details or {})
                          else encar_money_values(previous.details or {}, previous.price))
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
        previous_offer_type = (previous.details or {}).get("offer_type") or "SALE"
        current_offer_type = (car.details or {}).get("offer_type") or "SALE"
        if car.status == "SOLD":
            if previous.status == "SOLD":
                return None
            change = "SOLD"
            changes = [
                {"field": "status", "old": previous.status, "new": "SOLD"}
            ]
        elif previous_offer_type != current_offer_type:
            change = "MATERIAL_UPDATE"
            changes = _material_changes(
                previous.details,
                car.details or {},
                getattr(car, "source_key", "encar"),
            )
        elif old_price != car.current_price and not (
            len(previous_money) == len(current_money) == 1
            and comparable_money(previous_money[0], current_money[0])
        ):
            change = "MATERIAL_UPDATE"
            changes = [{"field": "money_values", "old": previous_money, "new": current_money}]
        elif old_price != car.current_price:
            change = "PRICE_DROP" if (old_price or 0) > (car.current_price or 0) else "PRICE_INCREASE"
            changes = [{"field": "price", "old": old_price, "new": car.current_price}]
        elif previous.fingerprint != car.fingerprint:
            changes = _material_changes(
                previous.details,
                car.details or {},
                getattr(car, "source_key", "encar"),
            )
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
        "source_key": getattr(project, "source_key", "encar"),
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
        "money_values": current_money,
        "previous_money_values": previous_money,
        "offer_type": (car.details or {}).get("offer_type") or "SALE",
        "rental_term_months": (car.details or {}).get("rental_term_months"),
        "lease_term_months": (car.details or {}).get("lease_term_months"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def process_job(job_id: int, *, runtime_pool=None) -> None:
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
            publish_scan_update(job.owner_id, job.id)
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
        publish_scan_update(job.owner_id, job.id)
        payload = job.payload or {}
        project_ids = list(dict.fromkeys(payload.get("project_ids") or []))
        car_ids = list(dict.fromkeys(payload.get("car_ids") or []))
        if payload.get("project_id") and not car_ids:
            project_ids = [payload["project_id"]]
        project_ids = project_execution_order(db, job.owner_id, payload, project_ids)
        with SCAN_COMMAND_LOCK:
            for project_id in project_ids:
                if _owned_project(db, project_id, job.owner_id) and not db.scalar(
                    select(ProjectScanRun.id).where(
                        ProjectScanRun.scan_run_id == job.id,
                        ProjectScanRun.project_id == project_id,
                    ).limit(1)
                ):
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
        blocked_sources: dict[str, str] = {}

        try:
            from .desktop_license import require_search_entitlement
            from .source_test_profile import validate_source_test_scan
            validate_source_test_scan(settings, job.kind, job.payload or {}, database_url=db.get_bind().url)

            source_keys = list(
                dict.fromkeys((job.payload or payload).get("source_keys") or ["encar"])
            )
            if not project_ids and not car_ids:
                for key in source_keys:
                    require_search_entitlement(get_source_descriptor(key).key)
            job.payload = {
                **(job.payload or payload),
                "execution_versions": {
                    "app": APP_VERSION,
                    "sources": {},
                },
            }
            db.commit()
            driver_context = nullcontext(runtime_pool.driver()) if runtime_pool else desktop_playwright()
            with driver_context as playwright, ExitStack() as runtime_cleanup:
                executable_path = os.environ.get("SEARCHCAR_CHROMIUM_EXECUTABLE")
                source = browser = context = page = None
                active_source_key = None

                def close_source_runtime() -> None:
                    nonlocal browser, context, page, source, active_source_key
                    closing_context, closing_browser, closing_source = context, browser, source
                    source = browser = context = page = None
                    active_source_key = None
                    if runtime_pool and getattr(closing_source, 'reuse_browser_session', False):
                        runtime_pool.release(closing_context)
                        return
                    try:
                        if closing_context is not None:
                            closing_context.close()
                    finally:
                        if closing_browser is not None:
                            closing_browser.close()

                runtime_cleanup.callback(close_source_runtime)

                def activate_source(source_key: str):
                    nonlocal browser, context, page, source, active_source_key
                    descriptor = get_source_descriptor(source_key)
                    require_search_entitlement(descriptor.key)
                    if source_key == active_source_key and source is not None:
                        return source, page
                    close_source_runtime()
                    next_source = get_source(source_key)
                    versions = dict((job.payload or {}).get("execution_versions") or {})
                    versions["sources"] = {
                        **versions.get("sources", {}),
                        descriptor.key: {
                            "adapter": descriptor.adapter_version,
                            "contract": descriptor.contract_version,
                        },
                    }
                    job.payload = {**(job.payload or payload), "execution_versions": versions}
                    db.commit()
                    while True:
                        _raise_if_cancelled(db, job)
                        db.commit()  # Release the heartbeat write before a browser/profile wait.
                        try:
                            options = dict(headless=settings.playwright_headless,
                                           executable_path=executable_path or None)
                            if runtime_pool and getattr(next_source, 'reuse_browser_session', False):
                                next_browser, next_context, next_page = runtime_pool.acquire(
                                    job.owner_id, next_source, **options)
                            else:
                                if runtime_pool:
                                    runtime_pool.close_idle()  # Preserve the existing detail-worker browser budget.
                                next_browser, next_context, next_page = next_source.open_browser(playwright, **options)
                            break
                        except ScanError as exc:
                            if getattr(exc, 'code', None) != 'SOURCE_BROWSER_BUSY':
                                raise
                            # A selection window owns this same source profile.
                            # Keep the queued scan cancellable, never launch a second Chrome.
                            time.sleep(0.25)
                    source = next_source
                    browser, context, page = (
                        next_browser,
                        next_context,
                        next_page,
                    )
                    active_source_key = source_key
                    return source, page

                _raise_if_cancelled(db, job)

                for project_id in project_ids:
                    _raise_if_cancelled(db, job)
                    stored_project = _owned_project(db, project_id, job.owner_id)
                    if not stored_project:
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
                    project = _project_for_execution(stored_project, payload)
                    project_run = db.scalar(
                        select(ProjectScanRun).where(
                            ProjectScanRun.scan_run_id == job.id,
                            ProjectScanRun.project_id == project.id,
                        )
                    )
                    try:
                        _raise_if_project_cancelled(db, job, project.id)
                    except ProjectScanCancelled:
                        project_run.status = "CANCELLED"
                        project_run.error_code = None
                        project_statuses = dict(
                            (job.payload or {}).get("project_statuses") or {}
                        )
                        project_statuses[str(project.id)] = "CANCELLED"
                        _publish_project_results(
                            job,
                            payload,
                            report,
                            failures,
                            failure_details,
                            project_statuses,
                        )
                        completed += 1
                        job.progress = min(
                            99, round(completed / total_units * 100)
                        )
                        db.commit()
                        publish_scan_update(job.owner_id, job.id)
                        continue
                    project_run.status = "RUNNING"
                    previous_new_car_ids = _previous_new_car_ids(db, project.id)
                    initial_full_scan = _requires_initial_full_scan(db, project.id)
                    price_filter_needs_baseline = _price_filter_needs_baseline(
                        project
                    )
                    price_filter_revision = int(
                        getattr(project, "price_filter_revision", 1) or 1
                    )
                    project_statuses = dict(
                        (job.payload or {}).get("project_statuses") or {}
                    )
                    job.payload = {
                        **(job.payload or payload),
                        "current_project_id": project.id,
                        "project_statuses": project_statuses,
                    }
                    db.commit()
                    publish_scan_update(job.owner_id, job.id)
                    try:
                        if project.source_key in blocked_sources:
                            raise SourceBlockedError(
                                "Source blocked earlier in this run",
                                code=blocked_sources[project.source_key],
                            )
                        source, page = activate_source(project.source_key)
                        scan_mode, page_mode = _project_scan_modes(
                            project,
                            initial_full_scan,
                            price_filter_needs_baseline,
                        )
                        list_only = 'LIST_ONLY' in source.descriptor.capabilities
                        if list_only:
                            page_mode = project.search_page_mode  # Do not apply Encar's initial full-scan policy.
                            initial_full_scan = False
                            price_filter_needs_baseline = False
                        effective_search_url = _effective_search_url(project, source)

                        def persist_page_progress(info: dict) -> None:
                            pagination = dict(
                                (job.payload or {}).get("pagination") or {}
                            )
                            pagination[str(project.id)] = {
                                **info,
                                "mode": page_mode,
                                "initial_full_scan": initial_full_scan,
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
                            publish_scan_update(job.owner_id, job.id)

                        def collection_checkpoint() -> None:
                            if runtime_pool:
                                runtime_pool.reap()  # Yield another site's idle profile to filter selection.
                            if not list_only:
                                _raise_if_project_cancelled(db, job, project.id)
                                return
                            # A manual browser wait may last minutes. Never
                            # retain its flushed heartbeat write transaction
                            # while sleeping; serialize with the cancel API.
                            with SCAN_COMMAND_LOCK:
                                _raise_if_project_cancelled(db, job, project.id)
                                db.commit()

                        def update_page_progress(info: dict) -> None:
                            if not list_only:
                                persist_page_progress(info)
                                return
                            with SCAN_COMMAND_LOCK:
                                _raise_if_project_cancelled(db, job, project.id)
                                persist_page_progress(info)

                        def persist_auction_collection(collection, *, complete):
                            from .auction_storage import store_auction_row

                            for row in collection.rows.values():
                                if row.get('out_of_scope'):
                                    continue
                                item = store_auction_row(
                                    db, project, row, scan_id=job.id, complete=complete
                                )
                                if item:
                                    report.append(item)
                            db.commit()

                        try:
                            collection = source.collect_search_pages(
                                page,
                                effective_search_url,
                                project.name,
                                all_pages=page_mode == "ALL_PAGES",
                                checkpoint=collection_checkpoint,
                                progress=update_page_progress,
                                **({'search_config': project.search_config} if list_only and getattr(project, 'search_config', None) is not None else {}),
                            )
                        except Exception as exc:
                            partial = getattr(exc, 'partial_collection', None)
                            if list_only and partial and partial.rows:
                                persist_auction_collection(partial, complete=False)
                            raise
                        if list_only:
                            from .auction_storage import finish_auction_search
                            # No network inside this final transaction. Cancellation
                            # either wins before it or observes a completed project.
                            with SCAN_COMMAND_LOCK:
                                _raise_if_project_cancelled(db, job, project.id)
                                persist_auction_collection(collection, complete=True)
                                unchanged_conditions = db.scalar(
                                    select(Project.price_filter_revision).where(Project.id == project.id)
                                ) == price_filter_revision and db.scalar(
                                    select(Project.search_config).where(Project.id == project.id)
                                ) == getattr(project, 'search_config', None)
                                finish_auction_search(
                                    db, project, set(collection.rows),
                                    all_pages=page_mode == 'ALL_PAGES' and unchanged_conditions,
                                )
                                _expire_previous_new_relations(db, project.id, previous_new_car_ids)
                                stored_project.updated_at = datetime.now(timezone.utc)
                                project_run.status = 'SUCCEEDED'
                                project_statuses[str(project.id)] = 'SUCCEEDED'
                                successes += 1
                                completed += 1
                                job.progress = min(99, round(completed / total_units * 100))
                                _publish_live_results(
                                    job, payload, report, failures, failure_details,
                                    project_statuses, current_project_id=None,
                                )
                                db.commit()
                            publish_scan_update(job.owner_id,job.id)
                            continue
                        rows = collection.rows
                        _raise_if_project_cancelled(db, job, project.id)
                        search_found_ids: set[str] = set()
                        processed_source_ids: set[str] = set()
                        confirmed_sold: set[str] = set()
                        row_values = list(rows.values())
                        detail_tasks: list[DetailTask] = []
                        processed_rows = 0

                        def publish_row_progress() -> None:
                            job.progress = round(
                                (
                                    completed
                                    + 0.15
                                    + 0.30
                                    * processed_rows
                                    / max(1, len(row_values))
                                )
                                / total_units
                                * 100
                            )
                            _publish_live_results(
                                job,
                                payload,
                                report,
                                failures,
                                failure_details,
                                project_statuses,
                                current_project_id=project.id,
                            )
                            db.commit()
                            publish_scan_update(job.owner_id, job.id)

                        for row_index, row in enumerate(row_values):
                            _raise_if_project_cancelled(db, job, project.id)
                            source_id = str(row["source_car_id"])
                            if source_id in processed_source_ids:
                                continue
                            processed_source_ids.add(source_id)
                            known = _known_car(
                                db, source_id, source.descriptor.key
                            )
                            if known and (known.excluded or known.status == "SOLD"):
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
                            if known:
                                # Presence in a verified result page is enough to
                                # exclude this project car from the missing pass.
                                search_found_ids.add(known.canonical_encar_id)
                            previous = _previous_state(db, known, known_relation)
                            list_data = source.parse_list_row(row)
                            read_kind = source.detail_read_kind(
                                scan_mode,
                                is_new=known is None,
                                list_price_changed=bool(
                                    known
                                    and list_data.get("price_krw") is not None
                                    and list_data["price_krw"] != known.current_price
                                ),
                                list_price_missing=bool(
                                    known
                                    and (
                                        list_data.get("price_krw") is None
                                        or known.current_price is None
                                    )
                                ),
                            )
                            if read_kind:
                                detail_tasks.append(
                                    DetailTask(
                                        key=f"{project.id}:{source_id}:{row_index}",
                                        source_key=source.descriptor.key,
                                        row={
                                            **row,
                                            "_searchcar_read_kind": read_kind,
                                        },
                                        known_price=(
                                            known.current_price if known else None
                                        ),
                                        context=ProjectDetailContext(
                                            source_id=source_id,
                                            known_car_id=known.id if known else None,
                                            previous=previous,
                                        ),
                                        read_kind=read_kind,
                                    )
                                )
                                continue

                            car = known
                            mark_found_without_detail(db, project, car)
                            current_relation = db.get(
                                ProjectCar,
                                {"project_id": project.id, "car_id": car.id},
                            )
                            after_snapshot_id = _latest_snapshot_id(db, car.id)
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
                            processed_rows += 1
                            publish_row_progress()
                            _raise_if_project_cancelled(db, job, project.id)

                        missing_cars = (
                            []
                            if price_filter_needs_baseline
                            else _missing_cars(db, project, search_found_ids)
                        )
                        for missing_index, missing_car in enumerate(missing_cars):
                            missing_relation = db.get(
                                ProjectCar,
                                {
                                    "project_id": project.id,
                                    "car_id": missing_car.id,
                                },
                            )
                            detail_tasks.append(
                                DetailTask(
                                    key=f"{project.id}:missing:{missing_car.id}:{missing_index}",
                                    source_key=source.descriptor.key,
                                    row={
                                        "url": missing_car.url
                                        or source.listing_url(
                                            missing_car.canonical_encar_id
                                        ),
                                        "source_car_id": missing_car.canonical_encar_id,
                                        "source": project.name,
                                        "list_text": "",
                                        "_searchcar_read_kind": "PRICE_STATUS",
                                    },
                                    known_price=missing_car.current_price,
                                    context=ProjectDetailContext(
                                        source_id=missing_car.canonical_encar_id,
                                        known_car_id=missing_car.id,
                                        previous=_previous_state(
                                            db, missing_car, missing_relation
                                        ),
                                        mark_search_found=False,
                                    ),
                                    read_kind="PRICE_STATUS",
                                )
                            )

                        if detail_tasks:
                            # The list browser is intentionally closed before
                            # starting detail workers: the source budget is two
                            # active Chromium browsers, not two plus an idle one.
                            db.commit()
                            detail_source = source
                            close_source_runtime()
                            detail_workers_count = _detail_worker_count(
                                db, job.owner_id
                            )
                            detail_total = processed_rows + len(detail_tasks)

                            def publish_detail_progress() -> None:
                                detail_progress = dict(
                                    (job.payload or {}).get(
                                        "detail_progress"
                                    )
                                    or {}
                                )
                                detail_progress[str(project.id)] = {
                                    "completed": processed_rows,
                                    "total": detail_total,
                                    "checking": min(
                                        detail_workers_count,
                                        detail_total - processed_rows,
                                    ),
                                }
                                job.payload = {
                                    **(job.payload or payload),
                                    "detail_progress": detail_progress,
                                }
                                job.progress = round(
                                    (
                                        completed
                                        + 0.15
                                        + 0.80
                                        * processed_rows
                                        / max(1, detail_total)
                                    )
                                    / total_units
                                    * 100
                                )
                                _publish_live_results(
                                    job,
                                    payload,
                                    report,
                                    failures,
                                    failure_details,
                                    project_statuses,
                                    current_project_id=project.id,
                                )
                                db.commit()
                                publish_scan_update(job.owner_id, job.id)

                            publish_detail_progress()
                            with DetailWorkerPool(
                                lambda: detail_source.detail_reader(
                                    Path(settings.storage_root),
                                    headless=settings.playwright_headless,
                                    executable_path=executable_path or None,
                                ),
                                workers=detail_workers_count,
                            ) as detail_workers:
                                detail_workers.submit_all(detail_tasks)
                                for result in detail_workers.results(
                                    len(detail_tasks)
                                ):
                                    _raise_if_project_cancelled(
                                        db, job, project.id
                                    )
                                    task_context = result.task.context
                                    assert isinstance(
                                        task_context, ProjectDetailContext
                                    )
                                    known = (
                                        db.get(Car, task_context.known_car_id)
                                        if task_context.known_car_id is not None
                                        else None
                                    )
                                    known_relation = (
                                        db.get(
                                            ProjectCar,
                                            {
                                                "project_id": project.id,
                                                "car_id": known.id,
                                            },
                                        )
                                        if known
                                        else None
                                    )
                                    if result.error:
                                        if (
                                            task_context.mark_search_found
                                            and not isinstance(result.error, ScanError)
                                        ):
                                            raise result.error
                                        if (
                                            task_context.mark_search_found
                                            and known
                                            and known_relation
                                        ):
                                            mark_found_without_detail(
                                                db, project, known
                                            )
                                            search_found_ids.add(
                                                known.canonical_encar_id
                                            )
                                        failure = _failure_detail(
                                            exc=result.error,
                                            scope=(
                                                "SEARCH_CAR"
                                                if task_context.mark_search_found
                                                else "MISSING_CAR"
                                            ),
                                            project=project,
                                            car=known,
                                            url=result.task.row.get("url"),
                                        )
                                        failure["encar_id"] = task_context.source_id
                                        if task_context.mark_search_found:
                                            failures.append(
                                                f"{project.name}:{task_context.source_id}:"
                                                f"{failure['code']}: {result.error}"
                                            )
                                        else:
                                            failures.append(
                                                f"missing:{task_context.source_id}:"
                                                f"{failure['code']}: {result.error}"
                                            )
                                        failure_details.append(failure)
                                        processed_rows += 1
                                        publish_detail_progress()
                                        if (
                                            isinstance(result.error, ScanError)
                                            and result.error.code == "CAPTCHA"
                                        ):
                                            detail_workers.cancel()
                                            raise result.error
                                        continue

                                    detail = result.detail
                                    assert detail is not None
                                    detail["scan_run_id"] = job.id
                                    try:
                                        car = (
                                            upsert_price_status(
                                                db,
                                                project,
                                                detail,
                                                mark_search_found=(
                                                    task_context.mark_search_found
                                                ),
                                            )
                                            if result.task.read_kind == "PRICE_STATUS"
                                            else upsert_detail(
                                            db,
                                            project,
                                            detail,
                                            mark_search_found=(
                                                task_context.mark_search_found
                                            ),
                                            )
                                        )
                                    except TrackingDisabledError:
                                        db.rollback()
                                        processed_rows += 1
                                        publish_detail_progress()
                                        continue
                                    if detail.get("sold"):
                                        confirmed_sold.update(
                                            {
                                                task_context.source_id,
                                                car.canonical_encar_id,
                                            }
                                        )
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
                                        task_context.previous,
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
                                    if task_context.mark_search_found:
                                        search_found_ids.add(
                                            car.canonical_encar_id
                                        )
                                    processed_rows += 1
                                    publish_detail_progress()
                                    _raise_if_project_cancelled(
                                        db, job, project.id
                                    )
                        _raise_if_project_cancelled(db, job, project.id)
                        if (
                            not price_filter_needs_baseline
                            and _should_apply_search_absence(page_mode)
                        ):
                            absence_changed = False
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
                                        "source_key": project.source_key,
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
                                absence_changed = True
                            if absence_changed:
                                _publish_live_results(
                                    job,
                                    payload,
                                    report,
                                    failures,
                                    failure_details,
                                    project_statuses,
                                    current_project_id=project.id,
                                )
                                db.commit()
                                publish_scan_update(job.owner_id, job.id)
                        if page_mode == "ALL_PAGES" and db.scalar(
                            select(Project.price_filter_revision).where(
                                Project.id == project.id
                            )
                        ) == price_filter_revision:
                            stored_project.price_filter_baseline_revision = (
                                price_filter_revision
                            )
                        stored_project.updated_at = datetime.now(timezone.utc)
                        _expire_previous_new_relations(
                            db, project.id, previous_new_car_ids
                        )
                        project_run.status = "SUCCEEDED"
                        successes += 1
                        db.commit()
                    except ProjectScanCancelled:
                        project_run.status = "CANCELLED"
                        project_run.error_code = None
                        db.commit()
                    except ScanCancelled:
                        raise
                    except ScanError as exc:
                        project_run.status = "FAILED"
                        project_run.error_code = exc.code
                        if exc.code in {"CAPTCHA", "SOURCE_ACCESS_DENIED", "SOURCE_RATE_LIMITED"}:
                            blocked_sources[project.source_key] = exc.code
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
                    finally:
                        if project_run.status != "SUCCEEDED":
                            close_source_runtime()
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
                    job.progress = min(99, round(completed / total_units * 100))
                    db.commit()
                    publish_scan_update(job.owner_id, job.id)
                    _raise_if_cancelled(db, job)

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
                                source, page = activate_source(project.source_key)
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
                                    "source_key": project.source_key,
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
                    job.progress = min(99, round(completed / total_units * 100))
                    _publish_live_results(
                        job,
                        payload,
                        report,
                        failures,
                        failure_details,
                        dict((job.payload or {}).get("project_statuses") or {}),
                        current_project_id=None,
                    )
                    db.commit()
                    publish_scan_update(job.owner_id, job.id)
                    _raise_if_cancelled(db, job)
                close_source_runtime()

            _raise_if_cancelled(db, job)
            report = _dedupe_report(report)
            job.progress = 100
            job.error = "\n".join(failures) or None
            cancelled_count = sum(
                status == "CANCELLED"
                for status in dict(
                    (job.payload or {}).get("project_statuses") or {}
                ).values()
            )
            if failures:
                job.status = "PARTIAL" if successes else "FAILED"
            elif cancelled_count:
                job.status = "PARTIAL" if successes else "CANCELLED"
            else:
                job.status = "SUCCEEDED"
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
                    "cancelled": cancelled_count,
                },
            }
            _record_completed_scheduler_run(db, job, job.finished_at)
            _reanchor_scheduler_after_manual_projects(
                db,
                job,
                job.finished_at,
            )
            db.commit()
            publish_scan_update(job.owner_id, job.id)
        except ScanCancelled:
            _finish_cancelled_job(
                db,
                job,
                payload,
                report,
                failures,
                failure_details,
            )
            publish_scan_update(job.owner_id, job.id)
        except Exception as exc:
            report = _dedupe_report(report)
            project_statuses = dict((job.payload or {}).get("project_statuses") or {})
            for portion in db.scalars(select(ProjectScanRun).where(
                ProjectScanRun.scan_run_id == job.id,
            )):
                if portion.status == "CANCEL_REQUESTED":
                    portion.status = "CANCELLED"
                    portion.error_code = None
                elif portion.status in {"QUEUED", "RUNNING"}:
                    portion.status = "FAILED"
                    portion.error_code = "RUN_FAILED"
                project_statuses[str(portion.project_id)] = portion.status
            job.status = "FAILED"
            job.finished_at = datetime.now(timezone.utc)
            job.heartbeat_at = job.finished_at
            job.error = f"{type(exc).__name__}: {exc}"
            job.payload = {
                **(job.payload or payload),
                "current_project_id": None,
                "project_statuses": project_statuses,
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
            publish_scan_update(job.owner_id, job.id)


def enqueue_scheduled(*, now: datetime | None = None) -> None:
    from .desktop_license import SearchEntitlementError, require_search_entitlement
    from .maintenance import maintenance_active
    from fastapi import HTTPException

    if settings.source_test_mode:
        return
    if maintenance_active():
        return
    try:
        require_search_entitlement()
        can_run_scheduled_search = True
    except SearchEntitlementError:
        can_run_scheduled_search = False
    with SessionLocal() as db:
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
            if not can_run_scheduled_search:
                setting.next_run_at = current + timedelta(
                    minutes=setting.interval_minutes
                )
                continue
            projects = list(
                db.scalars(
                    select(Project)
                    .join(
                        ScheduledProject,
                        ScheduledProject.project_id == Project.id,
                    )
                    .where(
                        ScheduledProject.scheduler_id == setting.id,
                        Project.owner_id == setting.user_id,
                        Project.auto_update.is_(True),
                    )
                    .order_by(Project.id)
                )
            )
            if not projects:
                setting.enabled = False
                setting.next_run_at = None
                continue
            allowed_projects = []
            for project in projects:
                try:
                    require_search_entitlement(project.source_key)
                except SearchEntitlementError:
                    continue
                allowed_projects.append(project)
            projects = allowed_projects
            if not projects:
                setting.next_run_at = current + timedelta(minutes=setting.interval_minutes)
                continue
            from .main import enqueue

            launch_payload = {
                "project_ids": [project.id for project in projects],
                "source_keys": list(
                    dict.fromkeys(project.source_key for project in projects)
                ),
                "project_plans": [
                    {
                        "project_id": project.id,
                        "source_key": project.source_key,
                        "name": project.name,
                        "search_url": project.search_url,
                        "search_config": deepcopy(project.search_config),
                        "scan_mode": project.scan_mode,
                        "search_page_mode": project.search_page_mode,
                        "price_filter_mode": project.price_filter_mode,
                        "price_min_krw": project.price_min_krw,
                        "price_max_krw": project.price_max_krw,
                        "price_filter_revision": project.price_filter_revision,
                        "price_filter_baseline_revision": (
                            project.price_filter_baseline_revision
                        ),
                    }
                    for project in projects
                ],
                "launch_scope": {
                    "mode": "SCHEDULED_PROJECTS",
                    "project_ids": [project.id for project in projects],
                    "source_keys": [],
                },
                "scheduled": True,
                "trigger": "AUTOMATIC",
            }
            try:
                enqueue("PROJECTS", launch_payload, db.get(User, setting.user_id), db)
            except HTTPException as exc:
                # A normal license refresh can revoke access after preflight.
                # Reanchor the schedule, without stopping the shared worker.
                if exc.status_code != 402:
                    raise
            setting.next_run_at = current + timedelta(
                minutes=setting.interval_minutes
            )
        db.commit()


def run(stop_event: Event | None = None) -> None:
    worker_id = f"desktop-{uuid4().hex}"
    with BrowserRuntimePool(desktop_playwright) as runtime_pool:
        while not (stop_event and stop_event.is_set()):
            runtime_pool.reap()
            enqueue_scheduled()
            job_id = claim_next_job(engine, worker_id)
            if job_id:
                process_job(job_id, runtime_pool=runtime_pool)
            elif stop_event:
                stop_event.wait(settings.worker_poll_seconds)
            else:
                time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run()
