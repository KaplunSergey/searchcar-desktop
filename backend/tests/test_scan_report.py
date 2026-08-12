from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import scan_out
from app.models import Car, Project, ProjectCar, ScanRun, User


def test_existing_relisted_item_is_hidden_from_scan_report() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(
            username="Owner",
            username_key="owner",
            password_hash="!test",
            role="USER",
            status="ACTIVE",
            project_limit=1,
        )
        db.add(user)
        db.flush()
        project = Project(
            owner_id=user.id,
            name="Project",
            name_key="project",
            search_url="https://www.encar.com/project",
        )
        car = Car(
            canonical_encar_id="41093659",
            url="https://fem.encar.com/cars/detail/41093659",
            title="Audi",
            status="UPDATED",
        )
        db.add_all([project, car])
        db.flush()
        db.add(
            ProjectCar(
                project_id=project.id,
                car_id=car.id,
                tracking_enabled=True,
                search_status="FOUND",
            )
        )
        run = ScanRun(
            owner_id=user.id,
            kind="PROJECTS",
            status="SUCCEEDED",
            payload={
                "project_ids": [project.id],
                "report": [
                    {
                        "project_id": project.id,
                        "car_id": car.id,
                        "encar_id": car.canonical_encar_id,
                        "change": "RELISTED",
                    }
                ],
                "summary": {
                    "changed": 1,
                    "new": 0,
                    "price_changes": 0,
                    "failed": 0,
                },
            },
        )
        db.add(run)
        db.commit()

        result = scan_out(run, db)

        assert result["payload"]["report"] == []
        assert result["payload"]["summary"]["changed"] == 0


def test_scan_report_groups_legacy_rows_for_one_registration_number() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(
            username="Owner",
            username_key="owner",
            password_hash="!test",
            role="USER",
            status="ACTIVE",
            project_limit=1,
        )
        db.add(user)
        db.flush()
        project = Project(
            owner_id=user.id,
            name="Project",
            name_key="project",
            search_url="https://www.encar.com/project",
        )
        first = Car(
            canonical_encar_id="42498401",
            url="https://fem.encar.com/cars/detail/42498401",
            title="Audi",
            details={"registration_number": "155노2743"},
            status="NEW",
        )
        second = Car(
            canonical_encar_id="42498400",
            url="https://fem.encar.com/cars/detail/42498400",
            title="Audi",
            details={"registration_number": "155 노2743"},
            status="NEW",
        )
        db.add_all([project, first, second])
        db.flush()
        db.add_all(
            [
                ProjectCar(project_id=project.id, car_id=first.id, tracking_enabled=True),
                ProjectCar(project_id=project.id, car_id=second.id, tracking_enabled=True),
            ]
        )
        run = ScanRun(
            owner_id=user.id,
            kind="PROJECTS",
            status="SUCCEEDED",
            payload={
                "project_ids": [project.id],
                "report": [
                    {"project_id": project.id, "car_id": first.id, "encar_id": "42498401", "change": "NEW"},
                    {"project_id": project.id, "car_id": second.id, "encar_id": "42498400", "change": "NEW"},
                ],
                "summary": {"changed": 2, "new": 2, "price_changes": 0, "failed": 0},
            },
        )
        db.add(run)
        db.commit()

        result = scan_out(run, db)

        assert len(result["payload"]["report"]) == 1
        assert result["payload"]["report"][0]["encar_ids"] == ["42498401", "42498400"]
        assert result["payload"]["summary"]["changed"] == 1
