import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Car, CarAlias, PriceHistory, Project, ProjectCar, User
from app.scanner import resolve_listing_identity
from app.services import TrackingDisabledError, mark_absent, upsert_detail


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def tracked_car(db: Session):
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
        search_url="https://example.test/search",
        scan_mode="FAST",
        auto_update=False,
    )
    car = Car(
        canonical_encar_id="41093659",
        url="https://fem.encar.com/cars/detail/41093659",
        title="Audi",
        current_price=24_800_000,
        status="UPDATED",
        details={
            "canonical_car_id": "41093659",
            "price_krw": 24_800_000,
        },
    )
    db.add_all([project, car])
    db.flush()
    relation = ProjectCar(
        project_id=project.id,
        car_id=car.id,
        tracking_enabled=True,
        search_status="FOUND",
    )
    db.add(relation)
    db.commit()
    return project, car, relation


def test_clean_database_saves_listing_with_distinct_encar_ids(db):
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
        name="Honda",
        name_key="honda",
        search_url="https://www.encar.com/fc/fc_carsearchlist.do",
        scan_mode="ACCURATE",
        auto_update=False,
    )
    db.add(project)
    db.flush()

    canonical_id, registration_id = resolve_listing_identity(
        "42319346",
        "등록번호 42318013",
        resolved_url="https://fem.encar.com/cars/detail/42319346",
    )
    car = upsert_detail(
        db,
        project,
        {
            "canonical_car_id": canonical_id,
            "source_car_id": canonical_id,
            "displayed_car_id": registration_id,
            "url": f"https://fem.encar.com/cars/detail/{canonical_id}",
            "title": "Honda Accord",
            "price_krw": 27_500_000,
            "price_source": "DETAIL_PRIMARY",
            "sold": False,
            "checked_at": "2026-08-03T12:00:00+00:00",
        },
    )
    db.commit()

    assert car.canonical_encar_id == "42319346"
    assert car.details["displayed_car_id"] == "42318013"
    assert db.get(
        ProjectCar,
        {"project_id": project.id, "car_id": car.id},
    ).search_status == "FOUND"


def test_three_search_misses_never_mark_active_listing_sold(db):
    project, car, relation = tracked_car(db)

    first = mark_absent(db, project, set())
    second = mark_absent(db, project, set())
    third = mark_absent(db, project, set())

    assert first == [(car, "NOT_FOUND_IN_SEARCH")]
    assert second == []
    assert third == []
    assert relation.consecutive_missing_scans == 3
    assert relation.search_status == "NOT_FOUND_IN_SEARCH"
    assert car.status == "UPDATED"


def test_relisted_is_project_local_and_only_emitted_once(db):
    project, car, relation = tracked_car(db)
    mark_absent(db, project, set())

    changes = mark_absent(db, project, {"41093659"})
    repeated = mark_absent(db, project, {"41093659"})

    assert changes == [(car, "RELISTED")]
    assert repeated == []
    assert relation.search_status == "FOUND"
    assert relation.consecutive_missing_scans == 0
    assert car.status == "UPDATED"


def test_only_confirmed_sold_page_sets_global_sold(db):
    project, car, relation = tracked_car(db)

    changes = mark_absent(
        db,
        project,
        set(),
        confirmed_sold_ids={"41093659"},
    )

    assert changes == [(car, "SOLD")]
    assert car.status == "SOLD"
    assert relation.search_status == "NOT_FOUND_IN_SEARCH"


def test_sold_page_preserves_verified_listing_data(db):
    project, car, _ = tracked_car(db)
    car.details = {
        **car.details,
        "title": "Audi A4",
        "under_contract": True,
        "new_car_price_percent": 49,
        "options": {"memory_seat": True},
        "condition": {
            "inspection_report": {
                "status": "CONFIRMED",
                "value": True,
            }
        },
        "accident": {
            "status": "NOT_FOUND",
            "summary": "NO_PROBLEMS_STATED",
        },
    }
    db.commit()

    upsert_detail(
        db,
        project,
        {
            "canonical_car_id": "41093659",
            "source_car_id": "41093659",
            "title": "Encar",
            "price_krw": None,
            "price_source": None,
            "under_contract": False,
            "new_car_price_percent": None,
            "options": {},
            "condition": {},
            "accident": {"status": "UNVERIFIED"},
            "parse_quality": "SOLD_PAGE",
            "sold": True,
            "unavailable": False,
            "checked_at": "2026-07-28T10:00:00+00:00",
        },
    )

    assert car.status == "SOLD"
    assert car.current_price == 24_800_000
    assert car.details["title"] == "Audi A4"
    assert car.details["under_contract"] is True
    assert car.details["new_car_price_percent"] == 49
    assert car.details["options"] == {"memory_seat": True}
    assert car.details["condition"]["inspection_report"]["status"] == "CONFIRMED"
    assert car.details["accident"]["status"] == "NOT_FOUND"
    assert car.details["sold"] is True


def test_search_price_and_cross_listing_update_are_rejected(db):
    project, car, _ = tracked_car(db)
    unsafe = {
        "canonical_car_id": "41093659",
        "source_car_id": "41093659",
        "price_krw": 28_800_000,
        "price_source": "SEARCH_LIST",
        "sold": False,
    }
    upsert_detail(db, project, unsafe)

    assert car.current_price == 24_800_000
    assert list(db.scalars(select(PriceHistory))) == []

    with pytest.raises(ValueError, match="cross-listing"):
        upsert_detail(
            db,
            project,
            {
                **unsafe,
                "canonical_car_id": "42172299",
                "source_car_id": "42178983",
                "price_source": "DETAIL_PRIMARY",
            },
        )
    assert list(db.scalars(select(CarAlias))) == []


def test_removed_relation_cannot_be_revived_by_late_page_result(db):
    project, car, relation = tracked_car(db)
    relation.tracking_enabled = False
    relation.search_status = "REMOVED_FROM_PROJECT"
    db.commit()

    with pytest.raises(TrackingDisabledError):
        upsert_detail(
            db,
            project,
            {
                "canonical_car_id": "41093659",
                "source_car_id": "41093659",
                "price_krw": 24_800_000,
                "price_source": "DETAIL_PRIMARY",
                "sold": False,
            },
        )

    assert relation.tracking_enabled is False
    assert relation.search_status == "REMOVED_FROM_PROJECT"
