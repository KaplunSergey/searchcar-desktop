from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ScheduledProject, SchedulerSetting, User


def tray_scheduler_status(db: Session) -> dict[str, object]:
    settings = list(
        db.scalars(
            select(SchedulerSetting)
            .join(User, User.id == SchedulerSetting.user_id)
            .where(SchedulerSetting.enabled.is_(True))
            .where(User.status == "ACTIVE")
            .order_by(SchedulerSetting.next_run_at, SchedulerSetting.id)
        )
    )
    configured = []
    for setting in settings:
        project_count = db.scalar(
            select(func.count())
            .select_from(ScheduledProject)
            .where(ScheduledProject.scheduler_id == setting.id)
        )
        if project_count:
            configured.append(setting)
    next_runs = [setting.next_run_at for setting in configured if setting.next_run_at]
    return {
        "enabled": bool(configured),
        "paused": bool(configured) and all(setting.paused for setting in configured),
        "next_run_at": min(next_runs).astimezone(timezone.utc) if next_runs else None,
    }


def toggle_all_schedulers(db: Session, *, now: datetime | None = None) -> dict[str, object]:
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
    if not settings:
        return {"enabled": False, "paused": False, "next_run_at": None}
    pause = any(not setting.paused for setting in settings)
    for setting in settings:
        setting.paused = pause
        if not pause and setting.next_run_at is None:
            setting.next_run_at = now or datetime.now(timezone.utc)
    db.commit()
    return tray_scheduler_status(db)
