"""Adapter-normalized ISO inputs, not proof of live Copart/IAA parsing."""
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys

import pytest

from app.auction_time import local_day_bounds, normalize_schedule, schedule_view


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def schedule(local="2026-10-06T08:30:00", zone="America/Chicago", **extra):
    return normalize_schedule({"local_datetime": local, "source_timezone": zone,
                               "raw": "06 October 08:30 CDT", "event_kind": "START", **extra})


@pytest.mark.parametrize("local,zone,expected", [
    ("2026-10-06T08:30:00", "America/Chicago", "2026-10-06T13:30:00+00:00"),
    ("2026-10-06T16:00:00", "Europe/Warsaw", "2026-10-06T14:00:00+00:00"),
    ("2026-10-27T08:30:00", "America/Chicago", "2026-10-27T13:30:00+00:00"),
    ("2026-11-03T08:30:00", "America/Chicago", "2026-11-03T14:30:00+00:00"),
])
def test_schedule_preserves_source_and_converts_to_utc(local, zone, expected):
    result = schedule(local, zone)
    assert result["utc_at"] == expected
    assert result["precision"] == "EXACT"
    assert result["source_timezone"] == zone
    assert result["local_datetime"] == local
    assert result["raw"] == "06 October 08:30 CDT"
    assert result["issue"] is None
    assert json.loads(json.dumps(result)) == result


def test_remaining_time_uses_current_computer_zone_without_changing_schedule():
    value = schedule()
    before = dict(value)
    now = utc("2026-10-06T10:15:00")
    warsaw = schedule_view(value, now=now, computer_timezone="Europe/Warsaw")
    assert warsaw["local_datetime"] == "2026-10-06T15:30:00+02:00"
    assert warsaw["remaining_seconds"] == 3 * 3600 + 15 * 60
    assert warsaw["is_today"] is True
    assert warsaw["time_state"] == "UPCOMING_START"
    hawaii = schedule_view(value, now=now, computer_timezone="Pacific/Honolulu")
    assert hawaii["local_datetime"] == "2026-10-06T03:30:00-10:00"
    assert hawaii["remaining_seconds"] == warsaw["remaining_seconds"]
    assert value == before  # A display change is not a schedule-change event.


def test_us_date_is_tomorrow_in_europe_and_completion_time_selects_the_day():
    value = schedule("2026-10-06T18:30:00", "America/Los_Angeles")
    assert schedule_view(value, now=utc("2026-10-06T20:00:00"),
                         computer_timezone="Europe/Warsaw")["is_today"] is False
    result = schedule_view(value, now=utc("2026-10-06T22:00:00"), computer_timezone="Europe/Warsaw")
    assert result["local_datetime"] == "2026-10-07T03:30:00+02:00"
    assert result["is_today"] is True


@pytest.mark.parametrize("now,hours", [("2026-03-29T10:00:00", 23), ("2026-10-25T10:00:00", 25)])
def test_today_bounds_are_calendar_midnights_not_next_24_hours(now, hours):
    start, end = local_day_bounds(now=utc(now), computer_timezone="Europe/Warsaw")
    assert end - start == timedelta(hours=hours)
    assert start.tzinfo == end.tzinfo == timezone.utc
    for instant, today in [(start, True), (end - timedelta(microseconds=1), True), (end, False)]:
        value = schedule(instant.isoformat(), "UTC")
        assert schedule_view(value, now=utc(now), computer_timezone="Europe/Warsaw")["is_today"] is today


@pytest.mark.parametrize("local,issue", [
    ("2026-03-08T02:30:00", "NONEXISTENT_LOCAL_TIME"),
    ("2026-11-01T01:30:00", "AMBIGUOUS_LOCAL_TIME"),
])
def test_dst_gap_and_repeated_hour_are_not_guessed(local, issue):
    result = schedule(local)
    assert result["utc_at"] is None
    assert result["precision"] == "UNKNOWN"
    assert result["issue"] == issue


@pytest.mark.parametrize("offset,expected", [
    ("-05:00", "2026-11-01T06:30:00+00:00"),
    ("-06:00", "2026-11-01T07:30:00+00:00"),
])
def test_source_offset_resolves_repeated_hour(offset, expected):
    assert schedule("2026-11-01T01:30:00" + offset)["utc_at"] == expected


def test_conflicting_offset_and_timezone_is_not_a_trusted_instant():
    result = schedule("2026-10-06T08:30:00-06:00")
    assert result["utc_at"] is None
    assert result["issue"] == "TIMEZONE_OFFSET_CONFLICT"


@pytest.mark.parametrize("zone", [None, "CDT", "CST", "Mars/Unknown", "../etc/passwd", "/etc/passwd"])
def test_unconfirmed_or_ambiguous_source_timezone_has_no_computer_fallback(zone):
    result = schedule(zone=zone)
    assert result["utc_at"] is None
    assert result["precision"] == "UNKNOWN"
    assert result["issue"] == "UNKNOWN_TIMEZONE"


@pytest.mark.parametrize("value,issue", [(None, "NO_DATE"), ({"raw": "Future"}, "NO_DATE"),
    ({"local_datetime": "10/06 08:30", "source_timezone": "America/Chicago"}, "INVALID_DATE"),
    ({"local_date": "2026-02-30", "source_timezone": "America/Chicago"}, "INVALID_DATE")])
def test_missing_year_or_date_is_never_replaced_with_current_year(value, issue):
    result = normalize_schedule(value)
    assert result["utc_at"] is None
    assert result["precision"] == "UNKNOWN"
    assert result["issue"] == issue


def test_date_only_can_be_today_only_if_its_whole_source_day_fits_local_day():
    value = normalize_schedule({"local_date": "2026-10-06", "source_timezone": "America/Chicago", "event_kind": "START"})
    assert value["precision"] == "DATE_ONLY"
    assert value["utc_at"] is None
    same_zone = schedule_view(value, now=utc("2026-10-06T10:15:00"), computer_timezone="America/Chicago")
    assert same_zone["is_today"] is True
    assert same_zone["remaining_seconds"] is None
    assert same_zone["time_state"] == "TIME_NOT_SPECIFIED"
    ambiguous = schedule_view(value, now=utc("2026-10-06T10:15:00"), computer_timezone="Europe/Warsaw")
    assert ambiguous["is_today"] is None
    assert ambiguous["time_state"] == "LOCAL_DAY_UNCERTAIN"


def test_date_only_day_that_does_not_overlap_today_is_not_today():
    value = normalize_schedule({"local_date": "2026-10-08", "source_timezone": "America/Chicago"})
    assert schedule_view(value, now=utc("2026-10-06T10:15:00"), computer_timezone="Europe/Warsaw")["is_today"] is False


def test_past_schedule_does_not_confirm_live_closed_or_sold():
    value = schedule()
    result = schedule_view(value, now=utc("2026-10-06T14:00:00"), computer_timezone="Europe/Warsaw")
    assert result["is_today"] is True
    assert result["remaining_seconds"] == 0
    assert result["time_state"] == "SCHEDULED_START_PASSED"
    assert "sold" not in result and "phase" not in result


@pytest.mark.parametrize("event_kind,time_state,remaining", [
    ("END", "UPCOMING_END", 11700), ("UNKNOWN", "EVENT_KIND_UNKNOWN", None),
])
def test_timed_or_unconfirmed_event_is_not_presented_as_a_start(event_kind, time_state, remaining):
    value = schedule(event_kind=event_kind)
    result = schedule_view(value, now=utc("2026-10-06T10:15:00"), computer_timezone="Europe/Warsaw")
    assert result["time_state"] == time_state
    assert result["remaining_seconds"] == remaining
    assert result["event_kind"] == event_kind


@pytest.mark.parametrize("computer_timezone", ["CDT", "../etc/passwd", "Mars/Unknown", None])
def test_invalid_computer_timezone_rejected_instead_of_assuming_warsaw(computer_timezone):
    with pytest.raises(ValueError, match="computer_timezone"):
        schedule_view(schedule(), now=utc("2026-10-06T10:15:00"), computer_timezone=computer_timezone)


def test_naive_clock_rejected_instead_of_using_host_timezone():
    with pytest.raises(ValueError, match="now"):
        schedule_view(schedule(), now=datetime(2026, 10, 6, 10, 15), computer_timezone="Europe/Warsaw")


def test_end_time_passing_does_not_confirm_auction_closed():
    result = schedule_view(schedule(event_kind="END"), now=utc("2026-10-06T14:00:00"),
                           computer_timezone="Europe/Warsaw")
    assert result["time_state"] == "SCHEDULED_END_PASSED"
    assert result["remaining_seconds"] == 0
    assert "phase" not in result


def test_raw_timezone_label_is_evidence_not_a_guessing_rule():
    value = schedule(raw_timezone="CDT")
    assert value["raw_timezone"] == "CDT"
    assert value["source_timezone"] == "America/Chicago"
    assert value["utc_at"] == "2026-10-06T13:30:00+00:00"


def test_dst_gap_cannot_be_made_valid_with_an_invented_numeric_offset():
    value = schedule("2026-03-08T02:30:00-06:00")
    assert value["precision"] == "UNKNOWN"
    assert value["issue"] == "NONEXISTENT_LOCAL_TIME"


@pytest.mark.parametrize("value", [[], {"raw": 1}, {"event_kind": "BID_NOW"},
                                    {"source_timezone": ["UTC"]}])
def test_invalid_contract_shape_rejected(value):
    with pytest.raises(ValueError):
        normalize_schedule(value)


def test_conflicting_source_dates_not_silently_reconciled():
    value = schedule(local_date="2026-10-07")
    assert value["precision"] == "UNKNOWN"
    assert value["issue"] == "DATE_CONFLICT"


def test_skipped_calendar_date_does_not_create_a_fake_midnight():
    value = normalize_schedule({"local_date": "2011-12-30", "source_timezone": "Pacific/Apia"})
    assert value["precision"] == "UNKNOWN"
    assert value["issue"] == "NONEXISTENT_LOCAL_DATE"


def test_timezone_package_works_without_system_iana_database_in_fresh_process():
    code = '''
import json
from datetime import datetime, timezone
from app.auction_time import normalize_schedule, schedule_view
value = normalize_schedule({"local_datetime": "2026-10-06T08:30:00",
                           "source_timezone": "America/Chicago", "event_kind": "START"})
print(json.dumps(schedule_view(value, now=datetime(2026,10,6,10,15,tzinfo=timezone.utc),
                               computer_timezone="Europe/Warsaw")))
'''
    process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             env={**os.environ, "PYTHONTZPATH": "", "TZ": "Asia/Tokyo"}, timeout=15)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["local_datetime"] == "2026-10-06T15:30:00+02:00"


def test_missing_bundled_database_is_an_explicit_runtime_error(monkeypatch):
    from app import auction_time

    def missing_database(*args):
        raise ModuleNotFoundError("tzdata")

    monkeypatch.setattr(auction_time, "files", missing_database)
    with pytest.raises(RuntimeError, match="database is unavailable"):
        schedule()
