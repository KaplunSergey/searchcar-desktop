"""Read-only stored auction data with owner boundaries and controlled UTC clock."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import require_user
from app.database import Base, create_database_engine, get_db
from app.main import app
from app.models import AuctionObservation, AuctionRound, AuctionScheduleVersion, Car, Project, ProjectCar, User, UserCarState
from app.auction_rounds import record_auction_observation
from app.money import store_money_values
from test_money_values import quote


NOW = datetime(2026, 10, 11, 0, 30, tzinfo=timezone.utc)


@pytest.fixture
def auction_api(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'auction-api.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([User(id=1, username="owner", username_key="owner", password_hash="!fixture"),
                    User(id=2, username="foreign", username_key="foreign", password_hash="!fixture")])
        db.flush()
        for key, source, owner in [(1, "copart", 1), (2, "iaa", 1), (3, "encar", 1), (4, "copart", 2)]:
            db.add(Project(id=key, owner_id=owner, source_key=source, name=f"project-{key}",
                           name_key=f"project-{key}", search_url=f"https://{source}.example.test/search/{key}"))
        db.flush()
        for key, source, project in [(1, "copart", 1), (2, "iaa", 2), (3, "copart", 4)]:
            car = Car(id=key, source_key=source, source_listing_id="same-id" if key != 3 else "foreign",
                      url=f"https://{source}.example.test/lot/{key}", title=f"Lot {key}",
                      details={"mileage": {"value": 1234, "unit": "mi", "accuracy": "NOT_ACTUAL"}})
            db.add(car)
            db.flush()
            db.add(ProjectCar(project_id=project, car_id=key, favorite=True))
            record_auction_observation(db, car, observation_key="fixture", observed_at=NOW - timedelta(hours=2),
                sale_id="sale-A", schedule={"local_datetime": "2026-10-10T20:00:00", "source_timezone": "America/Chicago", "event_kind": "START", "raw": "Oct 10 8 PM CDT"})
        db.commit()

    clock = [NOW]
    monkeypatch.setattr("app.main.now_utc", lambda: clock[0])
    previous = dict(app.dependency_overrides)

    def database():
        with factory() as db:
            yield db

    def owner():
        with factory() as db:
            return db.get(User, 1)

    app.dependency_overrides.update({get_db: database, require_user: owner})
    try:
        with TestClient(app) as client:
            yield client, factory, clock
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


def test_project_view_is_normalized_and_today_changes_with_current_computer_zone(auction_api):
    client, factory, clock = auction_api
    response = client.get("/api/projects/1/auction-lots?computer_timezone=Europe/Warsaw&today_only=true")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["computer_timezone"] == "Europe/Warsaw"
    assert payload["local_date"] == "2026-10-11"
    assert payload["generated_at"] == NOW.isoformat()
    lot = payload["lots"][0]
    assert (lot["source_key"], lot["source_listing_id"]) == ("copart", "same-id")
    assert lot["url"] == "https://copart.example.test/lot/1"
    assert lot["schedule"]["utc_at"] == "2026-10-11T01:00:00+00:00"
    assert lot["time"]["local_datetime"] == "2026-10-11T03:00:00+02:00"
    assert lot["time"]["remaining_seconds"] == 1800
    assert lot["round"]["evidence_kind"] == "SOURCE_SALE_ID"
    assert lot["details"]["mileage"]["unit"] == "mi"
    before = lot["schedule"]["id"]
    other = client.get("/api/projects/1/auction-lots?computer_timezone=Asia/Tokyo&today_only=true").json()
    assert other["lots"][0]["time"]["local_datetime"] == "2026-10-11T10:00:00+09:00"
    assert other["lots"][0]["schedule"]["id"] == before
    clock[0] = NOW + timedelta(hours=2)
    moved_day = client.get("/api/projects/1/auction-lots?computer_timezone=America/Los_Angeles&today_only=true").json()
    assert moved_day["local_date"] == "2026-10-10"
    assert moved_day["lots"][0]["time"]["time_state"] == "SCHEDULED_START_PASSED"
    assert moved_day["lots"][0]["time"]["remaining_seconds"] == 0
    with factory() as db:
        assert db.get(Car, 1).status == "NEW"
        assert len(list(db.scalars(select(AuctionScheduleVersion)))) == 3
        assert len(list(db.scalars(select(AuctionObservation)))) == 3


def test_midnight_uses_request_clock_and_current_round_not_old_occurrence(auction_api):
    client, factory, clock = auction_api
    with factory() as db:
        record_auction_observation(db, db.get(Car, 1), observation_key="next-round", observed_at=NOW,
            sale_id="sale-B", schedule={"local_datetime": "2026-10-12T09:00:00", "source_timezone": "America/Chicago", "event_kind": "START"})
        db.commit()
    assert client.get("/api/projects/1/auction-lots?computer_timezone=Europe/Warsaw&today_only=true").json()["lots"] == []
    clock[0] = datetime(2026, 10, 11, 22, 1, tzinfo=timezone.utc)
    result = client.get("/api/projects/1/auction-lots?computer_timezone=Europe/Warsaw&today_only=true").json()
    assert result["local_date"] == "2026-10-12"
    assert result["lots"][0]["round"]["source_sale_id"] == "sale-B"
    assert result["lots"][0]["schedule"]["version"] == 1


@pytest.mark.parametrize("zone", ["", "CST", "Windows Standard Time", "Europe/Unknown", "../etc/passwd", "a" * 65])
def test_unknown_computer_zone_is_an_explicit_error_not_server_timezone_fallback(auction_api, zone):
    client, _factory, _clock = auction_api
    response = client.get("/api/projects/1/auction-lots", params={"computer_timezone": zone})
    assert response.status_code in {400, 422}
    if response.status_code == 400:
        assert response.json()["detail"] == "invalid_computer_timezone"


def test_project_owner_site_tracking_and_exclusion_boundaries(auction_api):
    client, factory, _clock = auction_api
    assert client.get("/api/projects/4/auction-lots?computer_timezone=UTC").status_code == 404
    assert client.get("/api/projects/3/auction-lots?computer_timezone=UTC").status_code == 400
    assert client.get("/api/projects/1/auction-lots").status_code == 422
    with factory() as db:
        db.get(ProjectCar, (1, 1)).tracking_enabled = False
        db.commit()
    assert client.get("/api/projects/1/auction-lots?computer_timezone=UTC").json()["lots"] == []
    with factory() as db:
        db.get(ProjectCar, (1, 1)).tracking_enabled = True
        db.add(UserCarState(user_id=1, car_id=1, excluded=True))
        db.commit()
    assert client.get("/api/projects/1/auction-lots?computer_timezone=UTC").json()["lots"] == []
    assert [lot["source_key"] for lot in client.get("/api/projects/2/auction-lots?computer_timezone=UTC").json()["lots"]] == ["iaa"]


def test_single_lot_is_owner_protected_even_with_shared_listing_ids(auction_api):
    client, _factory, _clock = auction_api
    response = client.get("/api/cars/2/auction?project_id=2&computer_timezone=UTC")
    assert response.status_code == 200
    assert response.json()["lot"]["source_key"] == "iaa"
    assert client.get("/api/cars/3/auction?computer_timezone=UTC").status_code == 404
    assert client.get("/api/cars/1/auction?project_id=2&computer_timezone=UTC").status_code == 404


def test_no_time_and_date_only_are_visible_as_uncertain_not_in_today_selection(auction_api):
    client, factory, _clock = auction_api
    with factory() as db:
        # No version and an uncertain US date-only must not be silently inferred.
        db.add(Car(id=5, source_key="copart", source_listing_id="no-time", url="https://copart.example.test/5"))
        db.flush()
        db.add(ProjectCar(project_id=1, car_id=5))
        record_auction_observation(db, db.get(Car, 1), observation_key="date-only", observed_at=NOW,
            sale_id="sale-B", schedule={"local_date": "2026-10-11", "source_timezone": "America/Chicago", "event_kind": "END"})
        db.commit()
    payload = client.get("/api/projects/1/auction-lots?computer_timezone=Europe/Warsaw").json()
    by_id = {lot["id"]: lot for lot in payload["lots"]}
    assert by_id[5]["round"] is None
    assert by_id[5]["time"]["time_state"] == "TIME_UNKNOWN"
    assert by_id[1]["time"]["time_state"] == "LOCAL_DAY_UNCERTAIN"
    assert by_id[1]["time"]["remaining_seconds"] is None
    assert client.get("/api/projects/1/auction-lots?computer_timezone=Europe/Warsaw&today_only=true").json()["lots"] == []


def test_bid_buy_now_and_estimate_keep_currency_meaning_and_round(auction_api):
    client, factory, _clock = auction_api
    values = [quote(10000, "CURRENT_BID", "USD"), quote(2000000, "BUY_NOW", "USD"), quote(4000000, "ESTIMATED_RETAIL", "USD")]
    with factory() as db:
        round_record = db.scalar(select(AuctionRound).where(AuctionRound.car_id == 1))
        values[0]["auction_round"] = round_record.id
        store_money_values(db, db.get(Car, 1), values)
        db.commit()
    lot = client.get("/api/cars/1/auction?computer_timezone=UTC").json()["lot"]
    assert lot["money_values"] == sorted(values, key=lambda value: (value["meaning"], value["currency"]))
    assert "price" not in lot


def test_auction_routes_require_authentication(auction_api):
    client, _factory, _clock = auction_api
    app.dependency_overrides.pop(require_user)
    assert client.get("/api/projects/1/auction-lots?computer_timezone=UTC").status_code == 401
    assert client.get("/api/cars/1/auction?computer_timezone=UTC").status_code == 401


def test_unknown_source_evidence_is_preserved_without_filling_a_timezone(auction_api):
    client, factory, _clock = auction_api
    with factory() as db:
        record_auction_observation(db, db.get(Car, 1), observation_key="unknown-zone", observed_at=NOW,
            sale_id="sale-B", schedule={"local_date": "2026-10-11", "source_timezone": "CST",
                                        "raw": "October 11 CST", "event_kind": "START"})
        db.commit()
    result = client.get("/api/cars/1/auction?computer_timezone=Europe/Warsaw").json()["lot"]
    assert result["schedule"]["precision"] == "UNKNOWN"
    assert result["schedule"]["source_timezone"] is None
    assert result["schedule"]["issue"] == "UNKNOWN_TIMEZONE"
    assert result["schedule"]["evidence"]["source_timezone"] == "CST"
    assert result["time"]["remaining_seconds"] is None


@pytest.mark.parametrize("day, hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_api_day_bounds_follow_dst_not_a_fixed_24_hours(auction_api, day, hours):
    client, _factory, clock = auction_api
    clock[0] = datetime.fromisoformat(f"{day}T12:00:00+00:00")
    response = client.get("/api/projects/1/auction-lots?computer_timezone=Europe/Warsaw").json()
    start, end = (datetime.fromisoformat(response[field]) for field in ("day_start_utc", "day_end_utc"))
    assert (end - start).total_seconds() == hours * 3600


def test_date_only_today_and_end_anchor_are_not_start_or_telegram_eligibility(auction_api):
    client, factory, _clock = auction_api
    with factory() as db:
        record_auction_observation(db, db.get(Car, 1), observation_key="end-date", observed_at=NOW,
            sale_id="sale-B", schedule={"local_date": "2026-10-11", "source_timezone": "UTC", "event_kind": "END"})
        db.commit()
    lot = client.get("/api/projects/1/auction-lots?computer_timezone=UTC&today_only=true").json()["lots"][0]
    assert lot["time"]["time_state"] == "TIME_NOT_SPECIFIED"
    assert lot["time"]["remaining_seconds"] is None
    assert "telegram_eligible" not in lot
    with factory() as db:
        record_auction_observation(db, db.get(Car, 1), observation_key="end-time", observed_at=NOW + timedelta(minutes=1),
            sale_id="sale-B", schedule={"local_datetime": "2026-10-11T03:00:00", "source_timezone": "UTC", "event_kind": "END"})
        db.commit()
    lot = client.get("/api/cars/1/auction?computer_timezone=UTC").json()["lot"]
    assert lot["time"]["time_state"] == "UPCOMING_END"
    assert lot["time"]["remaining_seconds"] == 9000


def test_wrong_site_relation_is_not_exposed_and_enrichment_does_not_use_encar_parser(auction_api, monkeypatch):
    client, factory, _clock = auction_api
    monkeypatch.setattr("app.main.enriched_details", lambda _car: pytest.fail("Encar parser must not handle auction data"))
    with factory() as db:
        db.add(ProjectCar(project_id=1, car_id=2))  # Invalid old/imported cross-site relation.
        db.commit()
    result = client.get("/api/projects/1/auction-lots?computer_timezone=UTC").json()
    assert [lot["source_key"] for lot in result["lots"]] == ["copart"]
    assert client.get("/api/cars/2/auction?project_id=1&computer_timezone=UTC").status_code == 404


def test_internal_data_error_is_not_misreported_as_bad_computer_timezone(auction_api, monkeypatch):
    def fail(*_args, **_kwargs):
        raise ValueError("invalid stored schedule")

    monkeypatch.setattr("app.main.project_auction_view", fail)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/projects/1/auction-lots?computer_timezone=UTC").status_code == 500
