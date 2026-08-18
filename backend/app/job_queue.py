from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine, bindparam, select, text, update
from sqlalchemy.orm import Session

from .db_types import UTCDateTime
from .models import ProjectScanRun, ScanRun
from .maintenance import maintenance_active


ACTIVE_SCAN_STATUSES = ("RUNNING", "CANCEL_REQUESTED")
TERMINAL_SCAN_STATUSES = (
    "SUCCEEDED",
    "PARTIAL",
    "FAILED",
    "CANCELLED",
    "INTERRUPTED",
    "INTERRUPTED_SLEEP",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def claim_next_job(
    engine: Engine,
    worker_id: str,
    *,
    now: datetime | None = None,
) -> int | None:
    """Atomically claim the oldest queued job for the only active worker."""

    if maintenance_active():
        return None

    timestamp = now or utc_now()
    statement = text(
        """
        UPDATE scan_runs
        SET status = 'RUNNING',
            worker_id = :worker_id,
            attempt_count = COALESCE(attempt_count, 0) + 1,
            started_at = COALESCE(started_at, :timestamp),
            heartbeat_at = :timestamp,
            finished_at = NULL,
            updated_at = :timestamp
        WHERE id = (
            SELECT queued.id
            FROM scan_runs AS queued
            JOIN users AS owner ON owner.id = queued.owner_id
            WHERE queued.status = 'QUEUED'
              AND owner.status = 'ACTIVE'
              AND NOT EXISTS (
                  SELECT 1
                  FROM scan_runs AS active
                  WHERE active.status IN ('RUNNING', 'CANCEL_REQUESTED')
              )
            ORDER BY queued.created_at, queued.id
            LIMIT 1
        )
          AND status = 'QUEUED'
        RETURNING id
        """
    ).bindparams(bindparam("timestamp", type_=UTCDateTime()))
    with engine.begin() as connection:
        return connection.execute(
            statement,
            {"worker_id": worker_id, "timestamp": timestamp},
        ).scalar_one_or_none()


def heartbeat_job(
    engine: Engine,
    job_id: int,
    worker_id: str,
    *,
    now: datetime | None = None,
) -> bool:
    timestamp = now or utc_now()
    with engine.begin() as connection:
        result = connection.execute(
            update(ScanRun)
            .where(
                ScanRun.id == job_id,
                ScanRun.worker_id == worker_id,
                ScanRun.status.in_(ACTIVE_SCAN_STATUSES),
            )
            .values(heartbeat_at=timestamp, updated_at=timestamp)
        )
        return result.rowcount == 1


def detach_project_from_active_jobs(
    db: Session,
    owner_id: int,
    project_id: int,
    *,
    now: datetime | None = None,
) -> list[int]:
    """Remove a deleted project from queued work and cancel empty jobs."""

    timestamp = now or utc_now()
    affected: list[int] = []
    jobs = list(
        db.scalars(
            select(ScanRun).where(
                ScanRun.owner_id == owner_id,
                ScanRun.kind == "PROJECTS",
                ScanRun.status.in_(("QUEUED", "RUNNING", "CANCEL_REQUESTED")),
            )
        )
    )
    for job in jobs:
        payload = dict(job.payload or {})
        project_ids = list(payload.get("project_ids") or [])
        if project_id not in project_ids:
            continue

        remaining = [item for item in project_ids if item != project_id]
        payload["project_ids"] = remaining
        affected.append(job.id)
        if remaining and payload.get("current_project_id") != project_id:
            job.payload = payload
            continue

        payload.update(
            {
                "cancellation_reason": "PROJECT_DELETED",
                "cancellation_requested_at": timestamp.isoformat(),
            }
        )
        if job.status == "QUEUED":
            job.status = "CANCELLED"
            job.finished_at = timestamp
            job.heartbeat_at = timestamp
            job.worker_id = None
            payload.update(
                {
                    "cancelled_at": timestamp.isoformat(),
                    "current_project_id": None,
                    "report": list(payload.get("report") or []),
                    "failures": list(payload.get("failures") or []),
                    "summary": payload.get("summary")
                    or {
                        "changed": 0,
                        "new": 0,
                        "price_changes": 0,
                        "failed": 0,
                    },
                }
            )
        else:
            job.status = "CANCEL_REQUESTED"
        job.payload = payload
    return affected


def recover_interrupted_jobs(
    engine: Engine,
    *,
    reason: str = "APPLICATION_RESTARTED",
    now: datetime | None = None,
) -> list[int]:
    """Turn abandoned active work into a visible terminal history entry."""

    timestamp = now or utc_now()
    recovered_ids: list[int] = []
    with Session(engine, expire_on_commit=False) as db:
        jobs = list(
            db.scalars(
                select(ScanRun)
                .where(ScanRun.status.in_(ACTIVE_SCAN_STATUSES))
                .order_by(ScanRun.id)
            )
        )
        for job in jobs:
            payload = dict(job.payload or {})
            payload.update(
                {
                    "current_project_id": None,
                    "interrupted_at": timestamp.isoformat(),
                    "interruption_reason": reason,
                }
            )
            job.status = "INTERRUPTED"
            job.payload = payload
            job.error = job.error or "Application stopped before the scan finished"
            job.finished_at = timestamp
            job.heartbeat_at = timestamp
            job.worker_id = None
            recovered_ids.append(job.id)

        if recovered_ids:
            for project_run in db.scalars(
                select(ProjectScanRun).where(
                    ProjectScanRun.scan_run_id.in_(recovered_ids),
                    ProjectScanRun.status.in_(("QUEUED", "RUNNING")),
                )
            ):
                project_run.status = "INTERRUPTED"
                project_run.error_code = project_run.error_code or "APP_INTERRUPTED"
        db.commit()
    return recovered_ids


def request_shutdown_cancellation(
    engine: Engine,
    *,
    now: datetime | None = None,
) -> dict[str, list[int]]:
    """Stop queued work and ask the active worker to finish cooperatively."""

    timestamp = now or utc_now()
    cancelled: list[int] = []
    requested: list[int] = []
    with Session(engine, expire_on_commit=False) as db:
        jobs = list(
            db.scalars(
                select(ScanRun)
                .where(ScanRun.status.in_(("QUEUED", "RUNNING")))
                .order_by(ScanRun.id)
            )
        )
        for job in jobs:
            payload = dict(job.payload or {})
            payload.update(
                {
                    "shutdown_requested_at": timestamp.isoformat(),
                    "cancellation_reason": "APPLICATION_SHUTDOWN",
                }
            )
            job.payload = payload
            if job.status == "QUEUED":
                job.status = "CANCELLED"
                job.finished_at = timestamp
                job.heartbeat_at = timestamp
                job.worker_id = None
                cancelled.append(job.id)
            else:
                job.status = "CANCEL_REQUESTED"
                job.heartbeat_at = timestamp
                requested.append(job.id)

        if cancelled:
            for project_run in db.scalars(
                select(ProjectScanRun).where(
                    ProjectScanRun.scan_run_id.in_(cancelled),
                    ProjectScanRun.status.in_(("QUEUED", "RUNNING")),
                )
            ):
                project_run.status = "CANCELLED"
                project_run.error_code = None
        db.commit()
    return {"cancelled": cancelled, "cancel_requested": requested}


def request_sleep_interruption(
    engine: Engine,
    *,
    now: datetime | None = None,
) -> dict[str, list[int]]:
    """Ask the worker to stop scans that became invalid during system sleep.

    The call is made only after the native desktop shell receives its resume
    event. A running Playwright action is allowed to reach its normal bounded
    checkpoint, where the worker preserves all committed report rows and turns
    the job into a visible ``INTERRUPTED_SLEEP`` history entry.
    """

    timestamp = now or utc_now()
    requested: list[int] = []
    catch_up_owner_ids: list[int] = []
    with Session(engine, expire_on_commit=False) as db:
        jobs = list(
            db.scalars(
                select(ScanRun)
                .where(ScanRun.status == "RUNNING")
                .order_by(ScanRun.id)
            )
        )
        for job in jobs:
            payload = dict(job.payload or {})
            payload.update(
                {
                    "cancellation_reason": "SYSTEM_SLEEP",
                    "cancellation_requested_at": timestamp.isoformat(),
                }
            )
            job.status = "CANCEL_REQUESTED"
            job.heartbeat_at = timestamp
            job.payload = payload
            requested.append(job.id)
            if (
                (
                    payload.get("trigger") == "AUTOMATIC"
                    or payload.get("scheduled") is True
                )
                and job.owner_id not in catch_up_owner_ids
            ):
                catch_up_owner_ids.append(job.owner_id)
        db.commit()
    return {
        "cancel_requested": requested,
        "catch_up_owner_ids": catch_up_owner_ids,
    }
