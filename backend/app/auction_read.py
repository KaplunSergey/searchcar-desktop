"""Read-only auction projection for stored data, not Telegram eligibility.

UI supplies its current IANA zone on every request. The backend's aware UTC
clock is read at request time, never at search start. No site parser, status
inference, archive mutation or source activation belongs to this boundary.
"""
from sqlalchemy import and_, func, or_, select

from .auction_time import _timezone, _utc_now, local_day_bounds, normalize_schedule, schedule_view
from .models import AuctionRound, AuctionScheduleVersion, Car, ProjectCar, UserCarState
from .money import money_values_out


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
        # ponytail: reuse the existing money serializer; batch ancillary rows if
        # profiling shows this per-lot read dominates large project responses.
        money_values=money_values_out(car, db),
        round=dict(id=round_record.id, evidence_kind=round_record.evidence_kind,
                   evidence_key=round_record.evidence_key, source_sale_id=round_record.source_sale_id,
                   last_observed_at=round_record.last_observed_at.isoformat(),
                   source_updated_at=round_record.source_updated_at.isoformat() if round_record.source_updated_at else None) if round_record else None,
        schedule=schedule, time=schedule_view(schedule, now=now, computer_timezone=computer_timezone),
    )


def project_auction_view(db, project, *, now, computer_timezone, today_only=False):
    context = auction_clock_context(now=now, computer_timezone=computer_timezone)
    query = (_current_schedule_query()
             .join(ProjectCar, ProjectCar.car_id == Car.id)
             .outerjoin(UserCarState, and_(UserCarState.car_id == Car.id, UserCarState.user_id == project.owner_id))
             .where(ProjectCar.project_id == project.id, ProjectCar.tracking_enabled.is_(True),
                    Car.source_key == project.source_key, Car.excluded.is_(False),
                    or_(UserCarState.user_id.is_(None), UserCarState.excluded.is_(False)))
             .order_by(AuctionScheduleVersion.utc_at.asc().nulls_last(), Car.id))
    lots = []
    for car, round_record, version in db.execute(query):
        lot = _lot_out(db, car, round_record, version, now=now, computer_timezone=computer_timezone)
        if not today_only or lot["time"]["is_today"] is True:
            lots.append(lot)
    return {**context, "project_id": project.id, "source_key": project.source_key,
            "today_only": today_only, "lots": lots}


def single_auction_view(db, car_id, *, now, computer_timezone):
    context = auction_clock_context(now=now, computer_timezone=computer_timezone)
    car, round_record, version = db.execute(_current_schedule_query().where(Car.id == car_id)).one()
    return {**context, "lot": _lot_out(db, car, round_record, version, now=now, computer_timezone=computer_timezone)}
