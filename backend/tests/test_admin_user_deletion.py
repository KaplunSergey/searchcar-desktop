from datetime import timedelta

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.auth import hash_password, normalize_username, now_utc, secure_hash
from app.database import Base
from app.main import delete_user_account_data
from app.models import (
    AuditLog,
    AuthSession,
    Car,
    CarEvent,
    Project,
    ProjectCar,
    ProjectScanRun,
    ScanRun,
    ScheduledProject,
    SchedulerSetting,
    User,
    UserCarState,
)


def make_user(username: str, role: str = "USER") -> User:
    return User(
        username=username,
        username_key=normalize_username(username),
        password_hash=hash_password("test-password"),
        role=role,
        status="ACTIVE",
        project_limit=None if role == "ADMIN" else 1,
    )


def test_delete_user_removes_private_data_and_keeps_shared_car() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        admin = make_user("Admin", "ADMIN")
        target = make_user("Target")
        db.add_all([admin, target])
        db.flush()
        target_id = target.id

        car = Car(
            canonical_encar_id="shared-car",
            url="https://fem.encar.com/cars/detail/shared-car",
            status="AVAILABLE",
        )
        project = Project(
            owner_id=target.id,
            name="Target project",
            name_key="target project",
            search_url="https://www.encar.com/target",
        )
        run = ScanRun(owner_id=target.id, kind="PROJECTS", status="SUCCEEDED")
        scheduler = SchedulerSetting(
            user_id=target.id,
            enabled=True,
            interval_minutes=180,
        )
        db.add_all([car, project, run, scheduler])
        db.flush()
        car_id = car.id
        project_id = project.id
        run_id = run.id
        scheduler_id = scheduler.id
        db.add_all(
            [
                ProjectCar(project_id=project.id, car_id=car.id),
                ProjectScanRun(
                    scan_run_id=run.id,
                    project_id=project.id,
                    status="SUCCEEDED",
                ),
                ScheduledProject(
                    scheduler_id=scheduler.id,
                    project_id=project.id,
                ),
                UserCarState(user_id=target.id, car_id=car.id),
                AuthSession(
                    user_id=target.id,
                    token_hash=secure_hash("token"),
                    csrf_hash=secure_hash("csrf"),
                    expires_at=now_utc() + timedelta(days=1),
                ),
                CarEvent(
                    car_id=car.id,
                    user_id=target.id,
                    project_id=project.id,
                    kind="USER_VIEWED",
                ),
            ]
        )
        db.commit()

        delete_user_account_data(db, target, admin)
        db.commit()

        assert db.get(User, target_id) is None
        assert db.get(Project, project_id) is None
        assert db.get(ScanRun, run_id) is None
        assert db.get(SchedulerSetting, scheduler_id) is None
        assert db.get(Car, car_id) is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(UserCarState)
                .where(UserCarState.user_id == target_id)
            )
            == 0
        )
        event_row = db.scalar(
            select(CarEvent).where(CarEvent.car_id == car_id)
        )
        assert event_row is not None
        assert event_row.user_id is None
        assert event_row.project_id is None
        log = db.scalar(
            select(AuditLog).where(AuditLog.action == "USER_DELETED")
        )
        assert log is not None
        assert log.actor_user_id == admin.id
        assert log.target_user_id is None
        assert log.entity_id == str(target_id)
        assert log.payload["username"] == "Target"
