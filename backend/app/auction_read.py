"""Read-only auction projection for stored data, not Telegram eligibility.

UI supplies its current IANA zone on every request. The backend's aware UTC
clock is read at request time, never at search start. No site parser, status
inference, archive mutation or source activation belongs to this boundary.
"""
from datetime import date, datetime
from sqlalchemy import and_, func, or_, select

from .auction_time import _day_bounds, _timezone, _utc_now, local_day_bounds, normalize_schedule, schedule_view
from .models import AuctionRound, AuctionScheduleVersion, Car, ProjectCar, UserCarState
from .money import money_values_out, payload_money_values
from .external_links import validated_source_image_url


def auction_clock_context(*, now, computer_timezone):
    now = _utc_now(now)
    start, end = local_day_bounds(now=now, computer_timezone=computer_timezone)
    return dict(generated_at=now.isoformat(), computer_timezone=computer_timezone,
                local_date=now.astimezone(_timezone(computer_timezone)).date().isoformat(),
                day_start_utc=start.isoformat(), day_end_utc=end.isoformat())


def _current_schedule_query():
    latest = select(AuctionScheduleVersion.round_id, func.max(AuctionScheduleVersion.version).label("version")).group_by(
        AuctionScheduleVersion.round_id).subquery()
    return (select(Car, AuctionRound, AuctionScheduleVersion)
            .outerjoin(AuctionRound, and_(AuctionRound.car_id == Car.id, AuctionRound.is_current.is_(True)))
            .outerjoin(latest, latest.c.round_id == AuctionRound.id)
            .outerjoin(AuctionScheduleVersion, and_(AuctionScheduleVersion.round_id == latest.c.round_id,
                                                    AuctionScheduleVersion.version == latest.c.version)))


def _lot_out(db, car, round_record, version, *, now, computer_timezone):
    schedule = dict(version.evidence) if version else normalize_schedule(None)
    # Keep unconfirmed/invalid source text separately from typed projection
    # columns. A raw CST label must not disappear or become an inferred IANA.
    schedule["evidence"] = dict(version.evidence) if version else None
    if version:
        schedule.update(id=version.id, version=version.version, change_kind=version.change_kind,
                        observed_at=version.observed_at.isoformat(), precision=version.precision,
                        event_kind=version.event_kind, utc_at=version.utc_at.isoformat() if version.utc_at else None,
                        local_date=version.local_date, source_timezone=version.source_timezone)
    else:
        schedule.update(id=None, version=None, change_kind=None, observed_at=None)
    return dict(
        id=car.id, source_key=car.source_key, source_listing_id=car.source_listing_id,
        url=car.url, title=car.title, details=car.details or {},
        image=validated_source_image_url(car.source_key,(car.details or {}).get('main_image_source_url')),
        # ponytail: reuse the existing money serializer; batch ancillary rows if
        # profiling shows this per-lot read dominates large project responses.
        money_values=money_values_out(car, db),
        round=dict(id=round_record.id, evidence_kind=round_record.evidence_kind,
                   evidence_key=round_record.evidence_key, source_sale_id=round_record.source_sale_id,
                   last_observed_at=round_record.last_observed_at.isoformat(),
                   source_updated_at=round_record.source_updated_at.isoformat() if round_record.source_updated_at else None) if round_record else None,
        schedule=schedule, time=schedule_view(schedule, now=now, computer_timezone=computer_timezone),
    )


def project_auction_view(db, project, *, now, computer_timezone, today_only=False, collection="active"):
    context = auction_clock_context(now=now, computer_timezone=computer_timezone)
    if today_only:
        collection = "today"
    status = "NOT_FOUND_IN_SEARCH" if collection == "archive" else "FOUND"
    query = (_current_schedule_query().add_columns(ProjectCar.search_status)
             .join(ProjectCar, ProjectCar.car_id == Car.id)
             .outerjoin(UserCarState, and_(UserCarState.car_id == Car.id, UserCarState.user_id == project.owner_id))
             .where(ProjectCar.project_id == project.id, ProjectCar.tracking_enabled.is_(True),
                    Car.source_key == project.source_key, Car.excluded.is_(False),ProjectCar.search_status==status,
                    or_(UserCarState.user_id.is_(None), UserCarState.excluded.is_(False)))
             .order_by(AuctionScheduleVersion.utc_at.asc().nulls_last(), Car.id))
    lots = []
    for car, round_record, version, search_status in db.execute(query):
        lot = _lot_out(db, car, round_record, version, now=now, computer_timezone=computer_timezone)
        if collection == "today" and lot["time"]["is_today"] is not True:
            continue
        if collection == "undated" and lot["schedule"]["precision"] != "UNKNOWN":
            continue
        if collection == "future":
            schedule = lot["schedule"]
            if schedule["precision"] == "EXACT":
                start = datetime.fromisoformat(schedule["utc_at"])
            elif schedule["precision"] == "DATE_ONLY":
                start, _ = _day_bounds(date.fromisoformat(schedule["local_date"]), _timezone(schedule["source_timezone"]))
            else:
                continue
            if start < datetime.fromisoformat(context["day_end_utc"]):
                continue
        lot["project_search_status"] = search_status
        lots.append(lot)
    return {**context, "project_id": project.id, "source_key": project.source_key,
            "today_only": collection == "today", "collection": collection, "lots": lots}


def single_auction_view(db, car_id, *, now, computer_timezone):
    context = auction_clock_context(now=now, computer_timezone=computer_timezone)
    car, round_record, version = db.execute(_current_schedule_query().where(Car.id == car_id)).one()
    return {**context, "lot": _lot_out(db, car, round_record, version, now=now, computer_timezone=computer_timezone)}


def report_snapshot_view(snapshot, item, *, scan_id, source_key, computer_timezone, captured_round_id=None):
    """Project exactly the report's saved observation, without current Car reads."""
    if snapshot is None or snapshot.integrity_status != "VALID" or snapshot.kind != "LIST_ONLY" or snapshot.car_id != item["car_id"]:
        raise ValueError("Snapshot unavailable")
    payload = snapshot.payload
    if not isinstance(payload, dict):
        raise ValueError("Invalid saved observation")
    if (payload.get("scan_run_id") != scan_id or payload.get("source_key") != source_key
            or not payload.get("source_listing_id") or payload.get("source_listing_id") != item.get("source_listing_id")):
        raise ValueError("Snapshot does not belong to this report")
    recorded = _utc_now(datetime.fromisoformat(payload["checked_at"])) if payload.get("checked_at") else _utc_now(snapshot.created_at)
    saved = payload.get("schedule")
    if not isinstance(saved, dict):
        raise ValueError("Snapshot schedule unavailable")
    schedule = {**normalize_schedule(None), **{key: saved[key] for key in
        ("precision", "event_kind", "utc_at", "local_date", "local_datetime", "source_timezone", "raw", "raw_timezone", "issue") if key in saved}}
    if schedule["precision"] not in {"EXACT", "DATE_ONLY", "UNKNOWN"} or schedule["event_kind"] not in {"START", "END", "UNKNOWN"}:
        raise ValueError("Invalid saved schedule")
    if any(value is not None and not isinstance(value, str) for value in schedule.values()):
        raise ValueError("Invalid saved schedule fields")
    if not isinstance(payload["source_listing_id"], str) or not isinstance(payload.get("vehicle_title", ""), (str, type(None))):
        raise ValueError("Invalid saved identity")
    schedule.update(id=None, version=None, change_kind=None, observed_at=recorded.isoformat(), evidence=dict(saved))
    context = auction_clock_context(now=recorded, computer_timezone=computer_timezone)
    lot = dict(id=item["car_id"], source_key=source_key, source_listing_id=payload["source_listing_id"],
        title=payload.get("vehicle_title"), url=payload.get("url") or "", round=None,
        image=validated_source_image_url(source_key, payload.get("main_image_source_url")),
        details={key: payload[key] for key in ("mileage", "title", "primary_damage", "secondary_damage", "engine_status", "keys", "auction_phase") if key in payload},
        money_values=payload_money_values(payload, source_key), schedule=schedule,
        time=schedule_view(schedule, now=recorded, computer_timezone=computer_timezone))
    return {**context, "lot": lot, "captured_round_id": captured_round_id}
