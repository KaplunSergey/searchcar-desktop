"""Persist adapter evidence, not guesses from clocks or changing dates.

Called inside the shared queue's transaction; no commit, UI timezone, source
requests or Telegram delivery here. Provisional IDs are local deduplication
anchors, not claims that the source confirmed a sale occurrence. A transition
key must describe an explicit, stable source event, never a derived date.
"""
from datetime import date, datetime
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auction_time import _day_bounds, _timezone, _utc_now, normalize_schedule
from .models import AuctionObservation, AuctionRound, AuctionScheduleVersion, Car
from .sources.registry import is_auction_source


def _key(value, name, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"Invalid {name}")


def _latest_schedule(db, round_id):
    return db.scalar(select(AuctionScheduleVersion).where(
        AuctionScheduleVersion.round_id == round_id).order_by(AuctionScheduleVersion.version.desc()).limit(1))


def _schedule_key(value):
    if value["precision"] == "EXACT":
        time = value["utc_at"]
    elif value["precision"] == "DATE_ONLY":
        time = _day_bounds(date.fromisoformat(value["local_date"]), _timezone(value["source_timezone"]))
    else:
        time = tuple(value[field] for field in ("local_date", "local_datetime", "source_timezone", "issue"))
    return value["event_kind"], value["precision"], time


def _date_contains(date_only, exact):
    start, end = _day_bounds(date.fromisoformat(date_only["local_date"]), _timezone(date_only["source_timezone"]))
    return start <= datetime.fromisoformat(exact["utc_at"]) < end


def _updated_schedule(previous, incoming):
    if (incoming or {}).get('date_absent_confirmed') is True:
        return normalize_schedule({**incoming,'local_date':None,'local_datetime':None})
    # Missing fields do not clear a known value; raw failed evidence is retained
    # on the observation. Explicit date changes must not borrow an old time.
    merged = {**previous, **{key: value for key, value in (incoming or {}).items() if value is not None}}
    if previous["event_kind"] != "UNKNOWN" and merged.get("event_kind") == "UNKNOWN":
        merged["event_kind"] = previous["event_kind"]
    if previous["event_kind"] not in ("UNKNOWN", merged["event_kind"]):
        if not (incoming or {}).get("local_datetime") and not (incoming or {}).get("local_date"):
            return previous
        # END is a different anchor, not the previous START's inherited time.
        merged["local_datetime"] = incoming.get("local_datetime")
        merged["local_date"] = incoming.get("local_date")
    if (incoming or {}).get("local_datetime") and not (incoming or {}).get("local_date"):
        merged["local_date"] = None
    elif (incoming or {}).get("local_date") != previous["local_date"] and (incoming or {}).get("local_date"):
        if not (incoming or {}).get("local_datetime"):
            merged["local_datetime"] = None
    candidate = normalize_schedule(merged)
    if previous["precision"] != "UNKNOWN" and candidate["precision"] == "UNKNOWN":
        return previous
    if (previous["precision"] == "EXACT" and candidate["precision"] == "DATE_ONLY"
            and previous["event_kind"] == candidate["event_kind"] and _date_contains(candidate, previous)):
        return previous
    return candidate


def _change_kind(previous, candidate):
    if not previous:
        return "INITIAL"
    if (previous["precision"] == "UNKNOWN" or candidate["precision"] == "UNKNOWN"
            or previous["event_kind"] != candidate["event_kind"]):
        return "CONFIRMED"
    if previous["precision"] == "DATE_ONLY" and candidate["precision"] == "EXACT" and _date_contains(previous, candidate):
        return "CONFIRMED"
    return "RESCHEDULED"


def _typed_date(value):
    try:
        return date.fromisoformat(value).isoformat() if value else None
    except ValueError:
        return None


def record_auction_observation(db: Session, car: Car, *, observation_key: str,
                               observed_at: datetime, schedule: dict | None = None,
                               sale_id: str | None = None, new_round_evidence: str | None = None,
                               source_updated_at: datetime | None = None) -> AuctionObservation:
    """Record once per listing/check; return the durable observation/event IDs.

    New source sale IDs or explicit transition keys create rounds. The first
    sale ID confirms an existing provisional round without changing its ID.
    Older observations/known historical sale IDs never reactivate old rounds.
    """
    _key(observation_key, "observation_key")
    _key(sale_id, "sale_id", optional=True)
    _key(new_round_evidence, "new_round_evidence", optional=True)
    if sale_id is not None and new_round_evidence is not None:
        raise ValueError("Provide one round identity evidence kind")
    observed_at = _utc_now(observed_at)
    source_updated_at = _utc_now(source_updated_at) if source_updated_at is not None else None
    normalized = normalize_schedule(schedule)  # Validate before any writes.
    if car.id is None or not is_auction_source(car.source_key):
        raise ValueError("Expected a persisted auction source listing")
    try:
        payload = json.loads(json.dumps(dict(schedule=schedule, sale_id=sale_id,
                                            new_round_evidence=new_round_evidence), allow_nan=False))
    except (ValueError, TypeError) as exc:
        raise ValueError("Observation evidence must be JSON serializable") from exc
    # The queue serializes SQLite writes; PostgreSQL also serializes this listing.
    stored_car = db.scalar(select(Car).where(Car.id == car.id).with_for_update())
    if stored_car is None or stored_car.source_key != car.source_key:
        raise ValueError("Expected a persisted auction source listing")
    existing = db.scalar(select(AuctionObservation).where(
        AuctionObservation.car_id == car.id, AuctionObservation.observation_key == observation_key))
    if existing:
        if existing.payload != payload or existing.source_updated_at != source_updated_at:
            raise ValueError("conflicting observation key")
        return existing

    current = db.scalar(select(AuctionRound).where(AuctionRound.car_id == car.id, AuctionRound.is_current.is_(True)))
    kind = "SOURCE_SALE_ID" if sale_id is not None else "SOURCE_TRANSITION" if new_round_evidence is not None else "PROVISIONAL"
    evidence_key = sale_id if sale_id is not None else new_round_evidence
    identity = AuctionRound.source_sale_id == sale_id if sale_id is not None else (
        (AuctionRound.evidence_kind == kind) & (AuctionRound.evidence_key == evidence_key))
    identified = db.scalar(select(AuctionRound).where(AuctionRound.car_id == car.id, identity)) if evidence_key is not None else None
    stale = current is not None and (observed_at < current.last_observed_at or (
        source_updated_at is not None and current.source_updated_at is not None and source_updated_at < current.source_updated_at))
    historical = identified is not None and not identified.is_current
    applied = not stale and not historical
    round_record = identified if historical else current
    if applied:
        if identified:
            round_record = identified
        elif current and current.source_sale_id is None and kind == "SOURCE_SALE_ID":
            current.source_sale_id = sale_id
            if current.evidence_kind == "PROVISIONAL":
                current.evidence_kind, current.evidence_key = kind, evidence_key
        elif current is None or (evidence_key is not None and (
                current.evidence_kind != kind or current.evidence_key != evidence_key)):
            if current:
                current.is_current = False
                db.flush()  # Release the one-current-round uniqueness guard first.
            round_record = AuctionRound(id=uuid4().hex, car_id=car.id, evidence_kind=kind,
                                        evidence_key=evidence_key, source_sale_id=sale_id, is_current=True,
                                        created_at=observed_at, last_observed_at=observed_at)
            db.add(round_record)
            db.flush()

    previous = _latest_schedule(db, round_record.id)
    version = previous
    if applied:
        candidate = _updated_schedule(previous.evidence, schedule) if previous else normalized
        if previous is None or _schedule_key(candidate) != _schedule_key(previous.evidence):
            version = AuctionScheduleVersion(
                id=uuid4().hex, round_id=round_record.id, version=previous.version + 1 if previous else 1,
                change_kind=_change_kind(previous.evidence if previous else None, candidate),
                event_kind=candidate["event_kind"], precision=candidate["precision"],
                utc_at=datetime.fromisoformat(candidate["utc_at"]) if candidate["utc_at"] else None,
                local_date=_typed_date(candidate["local_date"]),
                source_timezone=candidate["source_timezone"] if candidate["precision"] != "UNKNOWN" else None,
                evidence=candidate, observed_at=observed_at)
            db.add(version)
            db.flush()
        round_record.last_observed_at = observed_at
        if source_updated_at is not None:
            round_record.source_updated_at = source_updated_at
    observation = AuctionObservation(id=uuid4().hex, car_id=car.id, round_id=round_record.id,
                                     schedule_version_id=version.id if version else None,
                                     observation_key=observation_key, observed_at=observed_at,
                                     source_updated_at=source_updated_at, applied=applied, payload=payload)
    db.add(observation)
    db.flush()
    return observation
