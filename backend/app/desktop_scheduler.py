from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ScheduledProject, SchedulerSetting, User


def _configured_settings(db: Session) -> list[SchedulerSetting]:
    settings = list(
        db.scalars(
            select(SchedulerSetting)
            .join(User, User.id == SchedulerSetting.user_id)
            .where(
                SchedulerSetting.enabled.is_(True),
                User.status == "ACTIVE",
            )
        )
    )
    return [
        setting
        for setting in settings
        if db.scalar(
            select(func.count())
            .select_from(ScheduledProject)
            .where(ScheduledProject.scheduler_id == setting.id)
        )
    ]


def scheduler_is_overdue(setting: SchedulerSetting, *, now: datetime) -> bool:
    """Determine normal queue eligibility without overriding an explicit timer."""

    if setting.next_run_at is not None:
        return setting.next_run_at <= now
    completed_at = setting.last_completed_run_at
    return (
        completed_at is not None
        and completed_at + timedelta(minutes=setting.interval_minutes) <= now
    )


def scheduler_is_overdue_after_resume(
    setting: SchedulerSetting,
    *,
    now: datetime,
) -> bool:
    """Evaluate missed work from the last full update, not from a stale timer."""

    completed_at = setting.last_completed_run_at
    if completed_at is not None:
        return completed_at + timedelta(minutes=setting.interval_minutes) <= now
    return scheduler_is_overdue(setting, now=now)


def prepare_overdue_scheduler_catch_up(
    db: Session,
    *,
    now: datetime | None = None,
    force_user_ids: set[int] | None = None,
) -> list[int]:
    """Make one overdue automatic run eligible immediately after resume.

    The worker still owns queue insertion, so an active scan cannot be raced or
    duplicated by the desktop resume signal.
    """

    current = now or datetime.now(timezone.utc)
    due_scheduler_ids: list[int] = []
    forced = force_user_ids or set()
    for setting in _configured_settings(db):
        if setting.paused or (
            setting.user_id not in forced
            and not scheduler_is_overdue_after_resume(setting, now=current)
        ):
            continue
        setting.next_run_at = current
        due_scheduler_ids.append(setting.id)
    db.commit()
    return due_scheduler_ids


def tray_scheduler_status(db: Session) -> dict[str, object]:
    configured = _configured_settings(db)
    next_runs = [setting.next_run_at for setting in configured if setting.next_run_at]
    return {
        "enabled": bool(configured),
        "paused": bool(configured) and all(setting.paused for setting in configured),
        "next_run_at": min(next_runs).astimezone(timezone.utc) if next_runs else None,
    }


def toggle_all_schedulers(db: Session, *, now: datetime | None = None) -> dict[str, object]:
    settings = _configured_settings(db)
    if not settings:
        return {"enabled": False, "paused": False, "next_run_at": None}
    pause = any(not setting.paused for setting in settings)
    for setting in settings:
        setting.paused = pause
        if not pause and setting.next_run_at is None:
            setting.next_run_at = now or datetime.now(timezone.utc)
    db.commit()
    return tray_scheduler_status(db)
