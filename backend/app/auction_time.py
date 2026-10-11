"""Auction time only: no site parsing, round identity, phase or sold inference.

Adapters supply full ISO dates and a confirmed IANA zone, not a display label
such as CST. Projections receive the current computer IANA zone and aware clock
on every call; neither is frozen at search start. All results are JSON-safe.
"""
from datetime import date, datetime, time, timedelta, timezone
from importlib.resources import files
from math import ceil
from zoneinfo import ZoneInfo


def _timezone(key):
    if (not isinstance(key, str) or ("/" not in key and key != "UTC")
            or "\\" in key or any(part in {"", ".", ".."} for part in key.split("/"))):
        raise ValueError("Unknown IANA timezone")
    # Read the same pinned database on both platforms, even when the host has
    # different system rules. Do not reset global zoneinfo paths/caches.
    try:
        root = files("tzdata.zoneinfo")
    except ModuleNotFoundError as exc:
        raise RuntimeError("Bundled timezone database is unavailable") from exc
    try:
        with root.joinpath(*key.split("/")).open("rb") as stream:
            return ZoneInfo.from_file(stream, key=key)
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise ValueError("Unknown IANA timezone") from exc


def _utc_now(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    return now.astimezone(timezone.utc)


def _day_bounds(day, zone):
    return tuple(datetime.combine(value, time.min, zone).astimezone(timezone.utc)
                 for value in (day, day + timedelta(days=1)))


def local_day_bounds(*, now, computer_timezone):
    """Half-open UTC bounds of the current local calendar day (not now + 24h)."""
    now = _utc_now(now)
    try:
        zone = _timezone(computer_timezone)
    except ValueError as exc:
        raise ValueError("Invalid computer_timezone") from exc
    return _day_bounds(now.astimezone(zone).date(), zone)


def normalize_schedule(value: dict | None) -> dict:
    """Normalize adapter ISO inputs once; uncertain inputs retain their evidence.

    A numeric source offset resolves a repeated hour only if it agrees with the
    supplied zone at that date. An omitted year/zone is never filled from now.
    Raw timezone labels are provenance, not a timezone-guessing dictionary.
    """
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("schedule must be a dict or None")
    result = {field: value.get(field) for field in
              ("raw", "raw_timezone", "source_timezone", "local_date", "local_datetime")}
    if any(item is not None and not isinstance(item, str) for item in result.values()):
        raise ValueError("schedule evidence must be text or None")
    kind = value.get("event_kind", "UNKNOWN")
    if kind not in ("START", "END", "UNKNOWN"):
        raise ValueError("Invalid event_kind")
    result.update(event_kind=kind, precision="UNKNOWN", utc_at=None, issue="NO_DATE")
    if not result["local_datetime"] and not result["local_date"]:
        return result
    try:
        local = datetime.fromisoformat(result["local_datetime"]) if result["local_datetime"] else None
        if local is not None and "T" not in result["local_datetime"]:
            raise ValueError("Date without time is not exact")
        day = local.date() if local is not None else date.fromisoformat(result["local_date"])
        if local is not None and result["local_date"] and date.fromisoformat(result["local_date"]) != day:
            result["issue"] = "DATE_CONFLICT"
            return result
        result["local_date"] = day.isoformat()
    except ValueError:
        result["issue"] = "INVALID_DATE"
        return result
    try:
        zone = _timezone(result["source_timezone"])
    except ValueError:
        result["issue"] = "UNKNOWN_TIMEZONE"
        return result
    if local is None:
        start, end = _day_bounds(day, zone)
        if start >= end:
            result["issue"] = "NONEXISTENT_LOCAL_DATE"
        else:
            result.update(precision="DATE_ONLY", issue=None)
        return result

    naive = local.replace(tzinfo=None)
    candidates = set()
    for fold in (0, 1):
        instant = naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if instant.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(instant)
    if not candidates:
        result["issue"] = "NONEXISTENT_LOCAL_TIME"
    elif local.tzinfo is not None:
        instant = local.astimezone(timezone.utc)
        if instant not in candidates:
            result["issue"] = "TIMEZONE_OFFSET_CONFLICT"
        else:
            result.update(precision="EXACT", utc_at=instant.isoformat(), issue=None)
    elif len(candidates) > 1:
        result["issue"] = "AMBIGUOUS_LOCAL_TIME"
    else:
        result.update(precision="EXACT", utc_at=candidates.pop().isoformat(), issue=None)
    return result


def schedule_view(schedule: dict, *, now: datetime, computer_timezone: str) -> dict:
    """Project a normalized schedule; dates are not auction phase evidence.

    is_today=None means a local day cannot be confirmed. END/UNKNOWN never
    produce a start countdown. This is not Telegram eligibility selection.
    """
    now = _utc_now(now)
    try:
        zone = _timezone(computer_timezone)
    except ValueError as exc:
        raise ValueError("Invalid computer_timezone") from exc
    result = dict(computer_timezone=computer_timezone, event_kind=schedule["event_kind"],
                  precision=schedule["precision"], local_datetime=None, local_date=None,
                  is_today=None, remaining_seconds=None, time_state="TIME_UNKNOWN")
    day_start, day_end = _day_bounds(now.astimezone(zone).date(), zone)
    if schedule["precision"] == "EXACT":
        instant = _utc_now(datetime.fromisoformat(schedule["utc_at"]))
        local = instant.astimezone(zone)
        result.update(local_datetime=local.isoformat(), local_date=local.date().isoformat(),
                      is_today=day_start <= instant < day_end)
        kind = schedule["event_kind"]
        if kind == "UNKNOWN":
            result["time_state"] = "EVENT_KIND_UNKNOWN"
        else:
            result.update(remaining_seconds=max(0, ceil((instant - now).total_seconds())),
                          time_state=f"UPCOMING_{kind}" if instant > now else f"SCHEDULED_{kind}_PASSED")
    elif schedule["precision"] == "DATE_ONLY":
        start, end = _day_bounds(date.fromisoformat(schedule["local_date"]), _timezone(schedule["source_timezone"]))
        if day_start <= start < end <= day_end:
            result.update(is_today=True, local_date=start.astimezone(zone).date().isoformat(),
                          time_state="TIME_NOT_SPECIFIED")
        elif end <= day_start or start >= day_end:
            result.update(is_today=False, time_state="TIME_NOT_SPECIFIED")
        else:
            result["time_state"] = "LOCAL_DAY_UNCERTAIN"
    return result
