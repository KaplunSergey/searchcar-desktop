from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auction_rounds import record_auction_observation
from app.database import Base, create_database_engine
from app.models import Car


NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def schedule(day="2026-10-10", time="09:30:00", **extra):
    return dict(local_datetime=f"{day}T{time}", source_timezone="America/Chicago",
                event_kind="START", raw=f"{day} {time} CDT", **extra)


@pytest.fixture
def db(tmp_path):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'rounds.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add(Car(id=1, source_key="copart", source_listing_id="123",
                        url="https://copart.example.test/lot/123"))
        session.commit()
        yield session
    engine.dispose()


def observe(db, key="check-1", *, car_id=1, minute=0, **values):
    return record_auction_observation(db, db.get(Car, car_id), observation_key=key,
                                      observed_at=NOW + timedelta(minutes=minute), **values)


def rows(db, model):
    return list(db.scalars(select(model)))


def test_repeat_observation_and_unchanged_search_do_not_duplicate_versions(db):
    first = observe(db, sale_id="sale-A", schedule=schedule())
    assert observe(db, sale_id="sale-A", schedule=schedule()).id == first.id
    second = observe(db, "check-2", minute=1, sale_id="sale-A", schedule=schedule())
    from app.models import AuctionRound, AuctionScheduleVersion, AuctionObservation
    assert first.round_id == second.round_id
    assert first.schedule_version_id == second.schedule_version_id
    assert len(rows(db, AuctionRound)) == 1
    assert len(rows(db, AuctionScheduleVersion)) == 1
    assert len(rows(db, AuctionObservation)) == 2
    assert rows(db, AuctionScheduleVersion)[0].utc_at == datetime(2026, 10, 10, 14, 30, tzinfo=timezone.utc)


def test_two_reschedules_are_stable_events_not_new_rounds(db):
    first = observe(db, sale_id="sale-A", schedule=schedule())
    second = observe(db, "check-2", minute=1, sale_id="sale-A", schedule=schedule("2026-10-11"))
    third = observe(db, "check-3", minute=2, sale_id="sale-A", schedule=schedule("2026-10-12"))
    repeat = observe(db, "check-4", minute=3, sale_id="sale-A", schedule=schedule("2026-10-12"))
    from app.models import AuctionRound, AuctionScheduleVersion
    assert len(rows(db, AuctionRound)) == 1
    versions = rows(db, AuctionScheduleVersion)
    assert [item.change_kind for item in versions] == ["INITIAL", "RESCHEDULED", "RESCHEDULED"]
    assert [item.version for item in versions] == [1, 2, 3]
    assert len({item.id for item in versions}) == 3
    assert first.round_id == second.round_id == third.round_id
    assert third.schedule_version_id == repeat.schedule_version_id
    assert versions[0].local_date == "2026-10-10"


def test_new_sale_id_starts_a_new_round_and_missing_fields_do_not_copy_old_schedule(db):
    first = observe(db, sale_id="sale-A", schedule=schedule())
    second = observe(db, "check-2", minute=1, sale_id="sale-B")
    from app.models import AuctionRound, AuctionScheduleVersion
    assert first.round_id != second.round_id
    assert db.get(AuctionRound, first.round_id).is_current is False
    assert db.get(AuctionRound, second.round_id).is_current is True
    assert db.get(AuctionScheduleVersion, second.schedule_version_id).precision == "UNKNOWN"
    assert db.get(AuctionRound, second.round_id).evidence_key == "sale-B"


def test_unknown_identity_keeps_local_round_across_dates_and_confirmation(db):
    first = observe(db, schedule=schedule())
    second = observe(db, "check-2", minute=1, schedule=schedule("2026-10-11"))
    confirmed = observe(db, "check-3", minute=2, sale_id="sale-A", schedule=schedule("2026-10-11"))
    from app.models import AuctionRound
    assert first.round_id == second.round_id == confirmed.round_id
    assert db.get(AuctionRound, first.round_id).evidence_kind == "SOURCE_SALE_ID"
    assert db.get(AuctionRound, first.round_id).evidence_key == "sale-A"


def test_confirmed_source_transition_is_idempotent_and_not_a_clock_inference(db):
    first = observe(db, schedule=schedule())
    second = observe(db, "check-2", minute=60 * 48, new_round_evidence="closed-to-scheduled:42", schedule=schedule("2026-10-14"))
    repeat = observe(db, "check-3", minute=60 * 48 + 1, new_round_evidence="closed-to-scheduled:42", schedule=schedule("2026-10-14"))
    assert first.round_id != second.round_id == repeat.round_id
    assert db.get(Car, 1).status == "NEW"  # No LIVE/SOLD derived from elapsed time.


def test_same_sale_id_and_dates_on_different_lots_and_sites_are_independent(db):
    db.add_all([Car(id=2, source_key="iaa", source_listing_id="123", url="https://iaa.example.test/123"),
                Car(id=3, source_key="copart", source_listing_id="456", url="https://copart.example.test/456")])
    db.flush()
    results = [observe(db, car_id=car_id, sale_id="sale-A", schedule=schedule()) for car_id in (1, 2, 3)]
    assert len({item.round_id for item in results}) == 3


@pytest.mark.parametrize("incomplete", [None, {}, {"raw": "unavailable"},
    {"source_timezone": None}, {"local_datetime": None, "event_kind": "UNKNOWN"},
    {"local_datetime": "bad-date", "source_timezone": "CST"}])
def test_missing_or_unconfirmed_fields_do_not_erase_known_schedule(db, incomplete):
    first = observe(db, sale_id="sale-A", schedule=schedule())
    second = observe(db, "check-2", minute=1, schedule=incomplete)
    assert second.round_id == first.round_id
    assert second.schedule_version_id == first.schedule_version_id
    assert second.payload["schedule"] == incomplete


def test_partial_time_update_uses_known_zone_and_changed_date_does_not_keep_old_time(db):
    observe(db, schedule=schedule())
    changed = observe(db, "check-2", minute=1, schedule={"local_datetime": "2026-10-10T10:30:00"})
    date_only = observe(db, "check-3", minute=2, schedule={"local_date": "2026-10-12"})
    from app.models import AuctionScheduleVersion
    assert db.get(AuctionScheduleVersion, changed.schedule_version_id).utc_at.hour == 15
    version = db.get(AuctionScheduleVersion, date_only.schedule_version_id)
    assert (version.local_date, version.precision, version.utc_at) == ("2026-10-12", "DATE_ONLY", None)
    assert version.change_kind == "RESCHEDULED"


def test_precision_improvement_is_not_a_reschedule_and_weaker_same_day_does_not_erase_time(db):
    observe(db, schedule={"local_date": "2026-10-10", "source_timezone": "America/Chicago", "event_kind": "START"})
    exact = observe(db, "check-2", minute=1, schedule=schedule())
    weaker = observe(db, "check-3", minute=2, schedule={"local_date": "2026-10-10"})
    from app.models import AuctionScheduleVersion
    assert db.get(AuctionScheduleVersion, exact.schedule_version_id).change_kind == "CONFIRMED"
    assert weaker.schedule_version_id == exact.schedule_version_id


def test_raw_text_and_equivalent_timezone_representation_do_not_create_reschedule(db):
    first = observe(db, schedule=schedule())
    equivalent = observe(db, "check-2", minute=1, schedule={"local_datetime": "2026-10-10T14:30:00+00:00",
                         "source_timezone": "UTC", "raw": "10 October 14:30 UTC"})
    assert equivalent.schedule_version_id == first.schedule_version_id


@pytest.mark.parametrize("mode", ["late_observation", "old_source_update", "historical_round"])
def test_delayed_observation_cannot_roll_back_current_round_or_schedule(db, mode):
    first = observe(db, sale_id="sale-A", schedule=schedule(), source_updated_at=NOW)
    latest = observe(db, "check-2", minute=2, sale_id="sale-B", schedule=schedule("2026-10-12"), source_updated_at=NOW + timedelta(minutes=2))
    delayed = observe(db, "check-3", minute=1 if mode == "late_observation" else 3,
                      sale_id="sale-A" if mode == "historical_round" else "sale-B",
                      schedule=schedule(), source_updated_at=NOW if mode == "old_source_update" else None)
    from app.models import AuctionRound, AuctionScheduleVersion
    assert delayed.applied is False
    assert db.get(AuctionRound, latest.round_id).is_current is True
    versions = rows(db, AuctionScheduleVersion)
    assert len(versions) == 2
    assert first.round_id != latest.round_id


def test_same_observation_key_with_different_data_is_rejected_without_writes(db):
    observe(db, sale_id="sale-A", schedule=schedule())
    with pytest.raises(ValueError, match="conflicting observation"):
        observe(db, sale_id="sale-B", schedule=schedule("2026-10-12"))
    from app.models import AuctionRound, AuctionObservation
    assert len(rows(db, AuctionRound)) == len(rows(db, AuctionObservation)) == 1


@pytest.mark.parametrize("bad", [dict(observation_key=""), dict(observed_at=NOW.replace(tzinfo=None)),
    dict(sale_id=""), dict(sale_id="a", new_round_evidence="b"), dict(schedule={"local_datetime": 10}),
    dict(source_updated_at=NOW.replace(tzinfo=None))])
def test_invalid_contract_is_rejected_before_writes(db, bad):
    values = dict(observation_key="check", observed_at=NOW, schedule=schedule())
    with pytest.raises(ValueError):
        record_auction_observation(db, db.get(Car, 1), **{**values, **bad})
    from app.models import AuctionRound, AuctionObservation
    assert rows(db, AuctionRound) == rows(db, AuctionObservation) == []


def test_encar_is_not_written_as_an_auction(db):
    car = Car(canonical_encar_id="789", url="https://encar.example.test/789")
    db.add(car)
    db.flush()
    with pytest.raises(ValueError, match="auction source"):
        record_auction_observation(db, car, observation_key="check", observed_at=NOW, schedule=schedule())


def test_sale_id_enriches_a_transition_round_without_losing_either_identity(db):
    first = observe(db, new_round_evidence="relisted:42", schedule=schedule())
    confirmed = observe(db, "check-2", minute=1, sale_id="sale-A", schedule=schedule())
    repeat = observe(db, "check-3", minute=2, new_round_evidence="relisted:42", schedule=schedule())
    next_sale = observe(db, "check-4", minute=3, sale_id="sale-B", schedule=schedule())
    assert first.round_id == confirmed.round_id == repeat.round_id
    assert next_sale.round_id != first.round_id


def test_changed_anchor_kind_is_confirmation_not_a_reschedule(db):
    first = observe(db, schedule=schedule())
    end = observe(db, "check-2", minute=1, schedule={**schedule(), "event_kind": "END"})
    from app.models import AuctionScheduleVersion
    assert first.round_id == end.round_id
    assert db.get(AuctionScheduleVersion, end.schedule_version_id).change_kind == "CONFIRMED"
    assert db.get(AuctionScheduleVersion, end.schedule_version_id).event_kind == "END"


def test_new_anchor_without_its_time_does_not_borrow_time_from_start(db):
    first = observe(db, schedule=schedule())
    end = observe(db, "check-2", minute=1, schedule={"event_kind": "END"})
    assert end.schedule_version_id == first.schedule_version_id
    assert end.payload["schedule"] == {"event_kind": "END"}


def test_equivalent_date_only_timezone_alias_does_not_create_event(db):
    first = observe(db, schedule={"local_date": "2026-10-10", "source_timezone": "America/Chicago", "event_kind": "START"})
    alias = observe(db, "check-2", minute=1, schedule={"source_timezone": "US/Central"})
    assert alias.schedule_version_id == first.schedule_version_id


def test_invalid_unconfirmed_date_is_kept_as_evidence_not_a_typed_date(db):
    value = {"local_date": "This date is not yet announced", "source_timezone": "UNKNOWN SOURCE LABEL"}
    observation = observe(db, schedule=value)
    from app.models import AuctionScheduleVersion
    version = db.get(AuctionScheduleVersion, observation.schedule_version_id)
    assert version.local_date is None
    assert version.source_timezone is None
    assert version.evidence["local_date"] == value["local_date"]
    assert observation.payload["schedule"] == value


def test_unserializable_evidence_is_rejected_before_writes(db):
    with pytest.raises(ValueError, match="JSON"):
        observe(db, schedule={"raw": "unknown", "extra": object()})
    from app.models import AuctionRound
    assert rows(db, AuctionRound) == []
