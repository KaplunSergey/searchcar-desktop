from sqlalchemy import select

from .database import SessionLocal
from .models import (
    Car,
    CarEvent,
    PriceHistory,
    Project,
    ProjectCar,
    SchedulerSetting,
    ScheduledProject,
    User,
    UserCarState,
)


def seed() -> None:
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username_key == "serhii"))
        if not user:
            user = User(
                username="Serhii",
                username_key="serhii",
                password_hash="!bootstrap-required",
                role="ADMIN",
                status="ACTIVE",
                project_limit=None,
            )
            db.add(user)
            db.flush()
        if db.scalar(
            select(Project.id).where(Project.owner_id == user.id).limit(1)
        ):
            return
        projects = [
            Project(
                owner_id=user.id,
                name="Audi A4 · Seoul shortlist",
                name_key="audi a4 · seoul shortlist",
                search_url="https://www.encar.com/list/car?page=1&search=A4-2021",
                scan_mode="FAST",
            ),
            Project(
                owner_id=user.id,
                name="Hyundai Tucson · 2020–2023",
                name_key="hyundai tucson · 2020–2023",
                search_url="https://www.encar.com/list/car?page=1&search=Tucson",
                scan_mode="ACCURATE",
            ),
            Project(
                owner_id=user.id,
                name="Kia Sportage · AWD Premium",
                name_key="kia sportage · awd premium",
                search_url="https://www.encar.com/list/car?page=1&search=Sportage",
                scan_mode="FAST",
            ),
        ]
        db.add_all(projects)
        db.flush()
        car = Car(
            canonical_encar_id="38492017",
            url="https://fem.encar.com/cars/detail/38492017",
            title="Audi A4 40 TFSI Premium · 2021.08",
            current_price=28_900_000,
            status="PRICE_DROP",
            details={
                "mileage_km": 52100,
                "fuel": "GASOLINE",
                "drivetrain": "FWD",
                "accident": {
                    "status": "CONFIRMED",
                    "original_evidence": ["보험이력 있음", "교환 1건"],
                },
            },
        )
        db.add(car)
        db.flush()
        db.add(
            ProjectCar(
                project_id=projects[0].id,
                car_id=car.id,
                favorite=True,
                viewed=False,
            )
        )
        db.add(UserCarState(user_id=user.id, car_id=car.id, rating=4))
        db.add_all(
            [
                PriceHistory(
                    car_id=car.id,
                    kind="PRICE",
                    payload={"price_krw": 29_500_000},
                ),
                PriceHistory(
                    car_id=car.id,
                    kind="PRICE",
                    payload={"price_krw": 28_900_000},
                ),
                CarEvent(
                    car_id=car.id,
                    kind="PRICE_DROP",
                    payload={"old": 29_500_000, "new": 28_900_000},
                ),
            ]
        )
        setting = SchedulerSetting(
            user_id=user.id,
            enabled=True,
            interval_minutes=180,
        )
        db.add(setting)
        db.flush()
        db.add_all(
            [
                ScheduledProject(
                    scheduler_id=setting.id,
                    project_id=project.id,
                )
                for project in projects[:2]
            ]
        )


if __name__ == "__main__":
    seed()
