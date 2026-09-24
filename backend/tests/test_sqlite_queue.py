from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.database import create_database_engine
from app.desktop_onboarding import (
    create_desktop_workspace,
    desktop_workspace_user,
    has_local_users,
)
from app.desktop_scheduler import (
    prepare_overdue_scheduler_catch_up,
    scheduler_is_overdue_after_resume,
    toggle_all_schedulers,
    tray_scheduler_status,
)
from app.job_queue import (
    claim_next_job,
    detach_project_from_active_jobs,
    recover_interrupted_jobs,
    request_sleep_interruption,
    request_shutdown_cancellation,
)
from app.main import enqueue
from app.models import (
    Project,
    ProjectScanRun,
    ScanRun,
    ScheduledProject,
    SchedulerSetting,
    User,
)
from app.sqlite_migrations import migrate_sqlite, sqlite_schema_version
from app.worker import (
    _requires_initial_full_scan,
    _reanchor_scheduler_after_manual_projects,
    _record_completed_scheduler_run,
    enqueue_scheduled,
)


def sqlite_engine(tmp_path: Path):
    return create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'queue.sqlite3').as_posix()}"
    )


def add_user(db: Session, username: str = "owner") -> User:
    user = User(
        username=username,
        username_key=username,
        password_hash="test-only",
        role="USER",
        status="ACTIVE",
        project_limit=1,
    )
    db.add(user)
    db.flush()
    return user


def test_sqlite_migrations_are_versioned_and_idempotent(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)

    assert migrate_sqlite(engine) == 6
    assert migrate_sqlite(engine) == 6
    assert sqlite_schema_version(engine) == 6

    with engine.connect() as connection:
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("scan_runs")
        }
        assert {
            "worker_id",
            "attempt_count",
            "started_at",
            "heartbeat_at",
            "finished_at",
        } <= columns
        scheduler_columns = {
            column["name"]
            for column in inspect(connection).get_columns("scheduler_settings")
        }
        assert {
            "paused",
            "catch_up_enabled",
            "last_completed_run_at",
            "performance_mode",
        } <= scheduler_columns
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert connection.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"


def test_pre_migration_desktop_database_is_adopted(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, status VARCHAR(24))"
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE scan_runs (
                id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                kind VARCHAR(20) NOT NULL,
                status VARCHAR(20) NOT NULL,
                progress INTEGER DEFAULT 0,
                payload JSON,
                error TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(owner_id) REFERENCES users(id)
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE project_scan_runs (
                id INTEGER PRIMARY KEY,
                scan_run_id INTEGER,
                project_id INTEGER,
                status VARCHAR(20),
                error_code VARCHAR(30)
            )
            """
        )
        connection.execute(text("INSERT INTO users (id, status) VALUES (1, 'ACTIVE')"))
        connection.execute(
            text(
                """
                INSERT INTO scan_runs (id, owner_id, kind, status)
                VALUES (1, 1, 'PROJECTS', 'RUNNING')
                """
            )
        )

    assert migrate_sqlite(engine) == 6
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT status, attempt_count, finished_at
                FROM scan_runs WHERE id = 1
                """
            )
        ).one()
        assert row.status == "INTERRUPTED"
        assert row.attempt_count == 0
        assert row.finished_at is not None


def test_first_run_creates_one_passwordless_desktop_workspace(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    with Session(engine) as db:
        assert not has_local_users(db)
        created = create_desktop_workspace(db, preferred_locale="uk")
        db.commit()
        assert created.username == "SearchCar"
        assert created.role == "USER"
        assert created.password_hash == "!desktop-workspace"
        assert not created.must_change_password
        assert created.preferred_locale == "uk"
        assert desktop_workspace_user(db).id == created.id
        assert has_local_users(db)
        try:
            create_desktop_workspace(db)
        except ValueError as exc:
            assert str(exc) == "desktop_workspace_already_initialized"
        else:  # pragma: no cover - assertion failure reports the unexpected state.
            raise AssertionError("desktop workspace was recreated")
        assert len(list(db.scalars(select(User)))) == 1


def test_two_workers_cannot_claim_jobs_at_the_same_time(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    with Session(engine) as db:
        user = add_user(db)
        db.add_all(
            [
                ScanRun(owner_id=user.id, kind="PROJECTS", status="QUEUED"),
                ScanRun(owner_id=user.id, kind="PROJECTS", status="QUEUED"),
            ]
        )
        db.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(
            executor.map(
                lambda worker: claim_next_job(engine, worker),
                ("worker-a", "worker-b"),
            )
        )
    assert sum(job_id is not None for job_id in claimed) == 1
    with Session(engine) as db:
        jobs = list(db.scalars(select(ScanRun).order_by(ScanRun.id)))
        assert [job.status for job in jobs].count("RUNNING") == 1
        assert [job.status for job in jobs].count("QUEUED") == 1
        running = next(job for job in jobs if job.status == "RUNNING")
        assert running.attempt_count == 1
        assert running.started_at is not None
        assert running.started_at.tzinfo == timezone.utc


def test_deleting_only_project_cancels_queued_job(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    cancelled_at = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
    with Session(engine, expire_on_commit=False) as db:
        user = add_user(db)
        project = Project(
            owner_id=user.id,
            name="Honda",
            name_key="honda",
            search_url="https://www.encar.com/honda",
        )
        db.add(project)
        db.flush()
        job = ScanRun(
            owner_id=user.id,
            kind="PROJECTS",
            status="QUEUED",
            payload={"project_ids": [project.id]},
        )
        db.add(job)
        db.flush()

        affected = detach_project_from_active_jobs(
            db,
            user.id,
            project.id,
            now=cancelled_at,
        )
        db.commit()

        assert affected == [job.id]
        assert job.status == "CANCELLED"
        assert job.payload["project_ids"] == []
        assert job.payload["cancellation_reason"] == "PROJECT_DELETED"
        assert job.finished_at == cancelled_at


def test_startup_recovery_preserves_partial_payload(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    with Session(engine) as db:
        user = add_user(db)
        project = Project(
            owner_id=user.id,
            name="Test project",
            name_key="test project",
            search_url="https://example.invalid/search",
        )
        db.add(project)
        db.flush()
        run = ScanRun(
            owner_id=user.id,
            kind="PROJECTS",
            status="RUNNING",
            progress=40,
            payload={"project_ids": [project.id], "report": [{"car_id": 7}]},
            worker_id="old-worker",
            attempt_count=1,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
        db.add(run)
        db.flush()
        db.add(
            ProjectScanRun(
                scan_run_id=run.id,
                project_id=project.id,
                status="RUNNING",
            )
        )
        db.commit()
        run_id = run.id

    recovered_at = datetime(2026, 8, 2, 10, 30, tzinfo=timezone.utc)
    assert recover_interrupted_jobs(engine, now=recovered_at) == [run_id]
    assert recover_interrupted_jobs(engine, now=recovered_at) == []

    with Session(engine) as db:
        run = db.get(ScanRun, run_id)
        project_run = db.scalar(
            select(ProjectScanRun).where(ProjectScanRun.scan_run_id == run_id)
        )
        assert run.status == "INTERRUPTED"
        assert run.progress == 40
        assert run.payload["report"] == [{"car_id": 7}]
        assert run.payload["interruption_reason"] == "APPLICATION_RESTARTED"
        assert run.finished_at == recovered_at
        assert project_run.status == "INTERRUPTED"
        assert project_run.error_code == "APP_INTERRUPTED"


def test_shutdown_cancels_queue_and_requests_running_job(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    with Session(engine) as db:
        user = add_user(db)
        project = Project(
            owner_id=user.id,
            name="Shutdown project",
            name_key="shutdown project",
            search_url="https://example.invalid/search",
        )
        db.add(project)
        db.flush()
        running = ScanRun(owner_id=user.id, kind="PROJECTS", status="RUNNING")
        queued = ScanRun(owner_id=user.id, kind="PROJECTS", status="QUEUED")
        db.add_all([running, queued])
        db.flush()
        db.add(
            ProjectScanRun(
                scan_run_id=queued.id,
                project_id=project.id,
                status="QUEUED",
            )
        )
        db.commit()
        running_id, queued_id = running.id, queued.id

    stopped_at = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
    result = request_shutdown_cancellation(engine, now=stopped_at)

    assert result == {
        "cancelled": [queued_id],
        "cancel_requested": [running_id],
    }
    with Session(engine) as db:
        running = db.get(ScanRun, running_id)
        queued = db.get(ScanRun, queued_id)
        assert running.status == "CANCEL_REQUESTED"
        assert running.payload["cancellation_reason"] == "APPLICATION_SHUTDOWN"
        assert queued.status == "CANCELLED"
        assert queued.finished_at == stopped_at
        project_run = db.scalar(
            select(ProjectScanRun).where(ProjectScanRun.scan_run_id == queued_id)
        )
        assert project_run.status == "CANCELLED"


def test_new_project_uses_all_pages_until_one_scan_succeeds(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = add_user(db)
        project = Project(
            owner_id=user.id,
            name="Initial scan",
            name_key="initial scan",
            search_url="https://www.encar.com/initial",
        )
        db.add(project)
        db.flush()

        assert project.search_page_mode == "ALL_PAGES"
        assert _requires_initial_full_scan(db, project.id)

        for status in ("FAILED", "CANCELLED"):
            interrupted = ScanRun(owner_id=user.id, kind="PROJECTS", status=status)
            db.add(interrupted)
            db.flush()
            db.add(
                ProjectScanRun(
                    scan_run_id=interrupted.id,
                    project_id=project.id,
                    status=status,
                )
            )
            db.flush()
            assert _requires_initial_full_scan(db, project.id)

        succeeded = ScanRun(owner_id=user.id, kind="PROJECTS", status="SUCCEEDED")
        db.add(succeeded)
        db.flush()
        db.add(
            ProjectScanRun(
                scan_run_id=succeeded.id,
                project_id=project.id,
                status="SUCCEEDED",
            )
        )
        db.flush()
        assert not _requires_initial_full_scan(db, project.id)


def test_sqlite_datetime_round_trip_is_aware_utc(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    supplied = datetime(2026, 8, 2, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    with Session(engine) as db:
        user = add_user(db)
        run = ScanRun(
            owner_id=user.id,
            kind="PROJECTS",
            status="QUEUED",
            started_at=supplied,
        )
        db.add(run)
        db.commit()
        run_id = run.id

    with Session(engine) as db:
        stored = db.get(ScanRun, run_id)
        assert stored.started_at == datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
        assert stored.started_at.tzinfo == timezone.utc


def test_manual_project_run_absorbs_queued_automatic_run(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = add_user(db)
        project = Project(
            owner_id=user.id,
            name="Test project",
            name_key="test project",
            search_url="https://example.invalid/search",
        )
        db.add(project)
        db.flush()
        automatic = ScanRun(
            owner_id=user.id,
            kind="PROJECTS",
            status="QUEUED",
            payload={
                "project_ids": [project.id],
                "scheduled": True,
                "trigger": "AUTOMATIC",
            },
        )
        db.add(automatic)
        db.commit()

        result = enqueue(
            "PROJECTS",
            {"project_ids": [project.id], "trigger": "MANUAL"},
            user,
            db,
        )

        db.refresh(automatic)
        assert automatic.status == "CANCELLED"
        assert automatic.payload["cancellation_reason"] == "MERGED_INTO_MANUAL_RUN"
        manual = db.get(ScanRun, result["id"])
        assert manual.status == "QUEUED"
        assert manual.payload["trigger"] == "MANUAL"


def test_finished_manual_project_run_reanchors_scheduler(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    with Session(engine) as db:
        user = add_user(db)
        project = Project(
            owner_id=user.id,
            name="Test project",
            name_key="test project",
            search_url="https://example.invalid/search",
        )
        db.add(project)
        db.flush()
        setting = SchedulerSetting(
            user_id=user.id,
            enabled=True,
            interval_minutes=180,
        )
        db.add(setting)
        db.flush()
        db.add(
            ScheduledProject(
                scheduler_id=setting.id,
                project_id=project.id,
            )
        )
        run = ScanRun(
            owner_id=user.id,
            kind="PROJECTS",
            status="SUCCEEDED",
            payload={"project_ids": [project.id], "trigger": "MANUAL"},
            started_at=datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc),
        )
        db.add(run)
        db.flush()

        finished_at = datetime(2026, 8, 2, 10, 27, tzinfo=timezone.utc)
        _reanchor_scheduler_after_manual_projects(db, run, finished_at)
        db.commit()

        assert setting.next_run_at == datetime(
            2026,
            8,
            2,
            13,
            27,
            tzinfo=timezone.utc,
        )
        assert run.payload["scheduler_reanchored_at"] == finished_at.isoformat()


def test_scheduler_always_enqueues_one_overdue_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    with factory.begin() as db:
        user = add_user(db)
        project = Project(
            owner_id=user.id,
            name="Wake test",
            name_key="wake test",
            search_url="https://example.invalid/search",
            auto_update=True,
        )
        db.add(project)
        db.flush()
        setting = SchedulerSetting(
            user_id=user.id,
            enabled=True,
            paused=False,
            catch_up_enabled=False,
            interval_minutes=60,
            next_run_at=now - timedelta(minutes=20),
        )
        db.add(setting)
        db.flush()
        db.add(ScheduledProject(scheduler_id=setting.id, project_id=project.id))

    monkeypatch.setattr("app.worker.SessionLocal", factory)
    enqueue_scheduled(now=now)

    with factory() as db:
        runs = list(db.scalars(select(ScanRun)))
        assert len(runs) == 1
        assert runs[0].payload["trigger"] == "AUTOMATIC"
        setting = db.scalar(select(SchedulerSetting))
        assert setting.next_run_at == now + timedelta(hours=1)


def test_scheduler_enqueues_one_catch_up_and_tray_can_pause(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    with factory.begin() as db:
        user = add_user(db)
        project = Project(
            owner_id=user.id,
            name="Catch-up test",
            name_key="catch-up test",
            search_url="https://example.invalid/search",
            auto_update=True,
        )
        db.add(project)
        db.flush()
        setting = SchedulerSetting(
            user_id=user.id,
            enabled=True,
            paused=False,
            catch_up_enabled=True,
            interval_minutes=180,
            next_run_at=now - timedelta(hours=8),
        )
        db.add(setting)
        db.flush()
        db.add(ScheduledProject(scheduler_id=setting.id, project_id=project.id))

    monkeypatch.setattr("app.worker.SessionLocal", factory)
    enqueue_scheduled(now=now)
    enqueue_scheduled(now=now)

    with factory() as db:
        runs = list(db.scalars(select(ScanRun)))
        assert len(runs) == 1
        assert runs[0].payload["trigger"] == "AUTOMATIC"
        status = tray_scheduler_status(db)
        assert status["enabled"] is True
        assert status["paused"] is False
        toggled = toggle_all_schedulers(db, now=now)
        assert toggled["paused"] is True


def test_sleep_interruption_preserves_active_report_and_makes_catch_up_due(
    tmp_path: Path,
) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    with Session(engine) as db:
        user = add_user(db)
        project = Project(
            owner_id=user.id,
            name="Sleep test",
            name_key="sleep test",
            search_url="https://example.invalid/search",
        )
        db.add(project)
        db.flush()
        setting = SchedulerSetting(
            user_id=user.id,
            enabled=True,
            paused=False,
            interval_minutes=60,
            next_run_at=now + timedelta(minutes=40),
            last_completed_run_at=now - timedelta(minutes=20),
        )
        running = ScanRun(
            owner_id=user.id,
            kind="PROJECTS",
            status="RUNNING",
            payload={"report": [{"car_id": 7}], "trigger": "AUTOMATIC"},
        )
        db.add_all([setting, running])
        db.flush()
        db.add_all(
            [
                ScheduledProject(scheduler_id=setting.id, project_id=project.id),
                ProjectScanRun(
                    scan_run_id=running.id,
                    project_id=project.id,
                    status="RUNNING",
                ),
            ]
        )
        db.commit()
        running_id = running.id
        owner_id = user.id

    interruption = request_sleep_interruption(engine, now=now)
    assert interruption == {
        "cancel_requested": [running_id],
        "catch_up_owner_ids": [owner_id],
    }

    with Session(engine) as db:
        assert prepare_overdue_scheduler_catch_up(
            db,
            now=now,
            force_user_ids=set(interruption["catch_up_owner_ids"]),
        )
        run = db.get(ScanRun, running_id)
        setting = db.scalar(select(SchedulerSetting))
        assert run.status == "CANCEL_REQUESTED"
        assert run.payload["report"] == [{"car_id": 7}]
        assert run.payload["cancellation_reason"] == "SYSTEM_SLEEP"
        assert setting.next_run_at == now


def test_resume_uses_last_completed_run_even_when_old_timer_is_in_future(
    tmp_path: Path,
) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    with Session(engine) as db:
        user = add_user(db)
        project = Project(
            owner_id=user.id,
            name="Resume checkpoint test",
            name_key="resume checkpoint test",
            search_url="https://example.invalid/search",
        )
        db.add(project)
        db.flush()
        setting = SchedulerSetting(
            user_id=user.id,
            enabled=True,
            paused=False,
            interval_minutes=60,
            last_completed_run_at=now - timedelta(hours=2),
            next_run_at=now + timedelta(minutes=30),
        )
        db.add(setting)
        db.flush()
        db.add(ScheduledProject(scheduler_id=setting.id, project_id=project.id))

        assert prepare_overdue_scheduler_catch_up(db, now=now) == [setting.id]
        assert setting.next_run_at == now


def test_resume_interval_uses_utc_when_dst_repeats_local_clock_hour() -> None:
    completed_at = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    resumed_at = datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)
    warsaw = ZoneInfo("Europe/Warsaw")
    setting = SchedulerSetting(
        user_id=1,
        enabled=True,
        interval_minutes=60,
        last_completed_run_at=completed_at,
    )

    # Both instants display as 02:30 in Warsaw when summer time ends, but an
    # hour really elapsed. Scheduler state is UTC, so catch-up is not skipped.
    assert completed_at.astimezone(warsaw).strftime("%H:%M") == "02:30"
    assert resumed_at.astimezone(warsaw).strftime("%H:%M") == "02:30"
    assert scheduler_is_overdue_after_resume(setting, now=resumed_at)


def test_completed_scheduler_run_records_full_update_timestamp(tmp_path: Path) -> None:
    engine = sqlite_engine(tmp_path)
    migrate_sqlite(engine)
    finished_at = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    with Session(engine) as db:
        user = add_user(db)
        project = Project(
            owner_id=user.id,
            name="Completion test",
            name_key="completion test",
            search_url="https://example.invalid/search",
        )
        db.add(project)
        db.flush()
        setting = SchedulerSetting(
            user_id=user.id,
            enabled=True,
            interval_minutes=180,
        )
        run = ScanRun(
            owner_id=user.id,
            kind="PROJECTS",
            status="SUCCEEDED",
            payload={"trigger": "AUTOMATIC", "scheduled": True},
        )
        db.add_all([setting, run])
        db.flush()
        db.add(ScheduledProject(scheduler_id=setting.id, project_id=project.id))

        _record_completed_scheduler_run(db, run, finished_at)
        db.commit()

        assert setting.last_completed_run_at == finished_at
        assert setting.next_run_at == finished_at + timedelta(hours=3)
        assert run.payload["scheduler_last_completed_run_at"] == finished_at.isoformat()
