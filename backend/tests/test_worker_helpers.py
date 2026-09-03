from types import SimpleNamespace

import pytest

from app.scanner import TimeoutScanError
from app.schemas import ProjectIn
from app.services import merge_reliable_detail
from app.worker import (
    PreviousState,
    ScanCancelled,
    _change_report,
    _dedupe_report,
    _failure_detail,
    _finish_cancelled_job,
    _material_changes,
    _publish_project_results,
    _previous_state,
    _project_scan_modes,
    _raise_if_cancelled,
    _read_verified_detail,
    _should_apply_search_absence,
    _should_refresh_missing_car,
)


def test_only_complete_all_page_search_can_mark_listing_absent():
    assert _should_apply_search_absence("ALL_PAGES")
    assert not _should_apply_search_absence("FIRST_PAGE")


def test_initial_project_scan_is_accurate_and_reads_all_pages():
    project = SimpleNamespace(scan_mode="FAST", search_page_mode="FIRST_PAGE")

    assert _project_scan_modes(project, True) == ("ACCURATE", "ALL_PAGES")
    assert _project_scan_modes(project, False) == ("FAST", "FIRST_PAGE")


def test_new_project_defaults_to_all_pages():
    project = ProjectIn(
        name="Tucson",
        search_url="https://fem.encar.com/fc/fc_carsearchlist.html",
    )

    assert project.scan_mode == "FAST"
    assert project.search_page_mode == "ALL_PAGES"


def test_material_changes_are_concise():
    changes = _material_changes(
        {"mileage_km": 10_000, "options": {"sunroof": False}},
        {"mileage_km": 12_500, "options": {"sunroof": True}},
    )
    assert changes[0] == {"field": "mileage_km", "old": 10_000, "new": 12_500}
    assert changes[1]["field"] == "options"
    assert changes[1]["new"] == ["sunroof"]


def test_report_prefers_new_and_price_changes():
    report = _dedupe_report(
        [
            {"project_id": 1, "car_id": 2, "change": "MATERIAL_UPDATE"},
            {"project_id": 1, "car_id": 2, "change": "PRICE_DROP"},
            {"project_id": 1, "car_id": 3, "change": "NEW"},
        ]
    )
    assert [item["change"] for item in report] == ["NEW", "PRICE_DROP"]


def test_report_merges_duplicate_encar_publications_of_one_vehicle():
    report = _dedupe_report(
        [
            {
                "project_id": 6,
                "project_name": "Audi A4",
                "car_id": 153,
                "encar_id": "42498401",
                "registration_number": "155노2743",
                "change": "NEW",
            },
            {
                "project_id": 6,
                "project_name": "Audi A4",
                "car_id": 154,
                "encar_id": "42498400",
                "registration_number": "155 노2743",
                "change": "NEW",
            },
        ]
    )

    assert len(report) == 1
    assert report[0]["car_id"] == 153
    assert report[0]["encar_ids"] == ["42498401", "42498400"]


def test_report_omits_relisted_status():
    report = _dedupe_report(
        [
            {"project_id": 1, "car_id": 2, "change": "RELISTED"},
            {"project_id": 1, "car_id": 3, "change": "PRICE_DROP"},
        ]
    )

    assert [item["change"] for item in report] == ["PRICE_DROP"]


def test_sold_status_is_not_reported_after_project_observed_it():
    snapshot = SimpleNamespace(
        payload={"canonical_car_id": "42222946"},
        integrity_status="VALID",
    )

    class FakeDb:
        def get(self, _model, _record_id):
            return snapshot

    car = SimpleNamespace(
        id=93,
        canonical_encar_id="42222946",
        current_price=26_300_000,
        fingerprint="sold-fingerprint",
        details={
            "canonical_car_id": "42222946",
            "sold": True,
        },
        status="SOLD",
        title="Audi",
    )
    relation = SimpleNamespace(
        last_observed_snapshot_id=2673,
        last_observed_price=26_300_000,
        last_observed_fingerprint="sold-fingerprint",
        last_observed_status="SOLD",
        last_observed_at=SimpleNamespace(),
        search_status="NOT_FOUND_IN_SEARCH",
    )

    previous = _previous_state(FakeDb(), car, relation)
    report = _change_report(
        car,
        SimpleNamespace(id=6, name="Audi A4 2021+"),
        previous,
        search_status="NOT_FOUND_IN_SEARCH",
        after_snapshot_id=2673,
    )

    assert previous.status == "SOLD"
    assert report is None


def test_sold_transition_wins_over_price_and_material_changes():
    car = SimpleNamespace(
        id=55,
        canonical_encar_id="42123455",
        current_price=26_500_000,
        fingerprint="sold-page-fingerprint",
        details={
            "canonical_car_id": "42123455",
            "sold": True,
            "under_contract": False,
        },
        status="SOLD",
        title="Audi",
    )
    previous = PreviousState(
        car_id=55,
        canonical_id="42123455",
        price=27_000_000,
        fingerprint="active-page-fingerprint",
        status="UPDATED",
        details={
            "canonical_car_id": "42123455",
            "sold": False,
            "under_contract": True,
        },
        search_status="FOUND",
    )

    report = _change_report(
        car,
        SimpleNamespace(id=6, name="Audi A4 2021+"),
        previous,
        search_status="NOT_FOUND_IN_SEARCH",
    )

    assert report["change"] == "SOLD"
    assert report["changes"] == [
        {"field": "status", "old": "UPDATED", "new": "SOLD"}
    ]


def test_report_deduplication_never_replaces_sold_with_material_update():
    report = _dedupe_report(
        [
            {"project_id": 6, "car_id": 55, "change": "SOLD"},
            {"project_id": 6, "car_id": 55, "change": "MATERIAL_UPDATE"},
        ]
    )

    assert report == [{"project_id": 6, "car_id": 55, "change": "SOLD"}]


def test_timeout_failure_is_structured():
    failure = _failure_detail(
        exc=TimeoutScanError("net::ERR_TIMED_OUT"),
        scope="RUN",
    )
    assert failure["code"] == "TIMEOUT"
    assert failure["technical"] == "net::ERR_TIMED_OUT"


def test_car_failure_exposes_only_a_valid_external_url():
    car = SimpleNamespace(
        id=7,
        canonical_encar_id="42390937",
        url="https://fem.encar.com/cars/detail/42390937",
    )

    failure = _failure_detail(
        exc=TimeoutScanError("timeout"),
        scope="SEARCH_CAR",
        car=car,
    )
    invalid = _failure_detail(
        exc=TimeoutScanError("timeout"),
        scope="SEARCH_CAR",
        car=SimpleNamespace(
            id=8,
            canonical_encar_id="42390938",
            url="javascript:alert(1)",
        ),
    )

    assert failure["url"] == car.url
    assert invalid["url"] is None


def test_every_missing_car_is_refreshed_individually():
    car = SimpleNamespace(canonical_encar_id="42390937", excluded=False)

    assert _should_refresh_missing_car(
        SimpleNamespace(favorite=True), car, set()
    )
    assert _should_refresh_missing_car(
        SimpleNamespace(favorite=False), car, set()
    )
    assert not _should_refresh_missing_car(
        SimpleNamespace(favorite=True), car, {"42390937"}
    )

    car.excluded = True
    assert not _should_refresh_missing_car(
        SimpleNamespace(favorite=True), car, set()
    )


def test_removed_project_car_is_not_refreshed():
    car = SimpleNamespace(canonical_encar_id="42390937", excluded=False)
    relation = SimpleNamespace(favorite=False, tracking_enabled=False)
    assert not _should_refresh_missing_car(relation, car, set())


def test_report_never_compares_different_database_cars():
    project = SimpleNamespace(id=5, name="Project")
    current = SimpleNamespace(
        id=15,
        canonical_encar_id="42172299",
        current_price=27_700_000,
        fingerprint="current",
        details={"canonical_car_id": "42172299", "mileage_km": 86_474},
        status="UPDATED",
        title="Audi",
    )
    previous = PreviousState(
        car_id=16,
        canonical_id="42178983",
        price=27_700_000,
        fingerprint="other",
        status="UPDATED",
        details={"canonical_car_id": "42178983"},
        search_status="FOUND",
    )
    assert _change_report(current, project, previous, search_status="FOUND") is None


def test_empty_material_diff_is_not_reported():
    details = {
        "canonical_car_id": "1",
        "condition": {
            "inspection_report": {
                "status": "CONFIRMED",
                "original_evidence": ["new evidence"],
            }
        },
    }
    current = SimpleNamespace(
        id=1,
        canonical_encar_id="1",
        current_price=25_000_000,
        fingerprint="new-fingerprint",
        details=details,
        status="UPDATED",
        title="Car",
    )
    previous = PreviousState(
        car_id=1,
        canonical_id="1",
        price=25_000_000,
        fingerprint="old-fingerprint",
        status="UPDATED",
        details={
            **details,
            "condition": {
                "inspection_report": {
                    "status": "CONFIRMED",
                    "original_evidence": ["old evidence"],
                }
            },
        },
        search_status="FOUND",
    )
    assert _change_report(current, SimpleNamespace(id=1, name="P"), previous) is None


def test_report_references_exact_before_and_after_snapshots():
    current = SimpleNamespace(
        id=1,
        canonical_encar_id="1",
        current_price=24_000_000,
        fingerprint="new",
        details={"mileage_km": 20_000},
        status="PRICE_DROP",
        title="Car",
    )
    previous = PreviousState(
        car_id=1,
        canonical_id="1",
        price=25_000_000,
        fingerprint="old",
        status="UPDATED",
        details={"mileage_km": 20_000},
        search_status="FOUND",
        snapshot_id=101,
    )

    report = _change_report(
        current,
        SimpleNamespace(id=5, name="Project"),
        previous,
        after_snapshot_id=102,
    )

    assert report["before_snapshot_id"] == 101
    assert report["after_snapshot_id"] == 102


def test_price_change_requires_two_identical_detail_reads(monkeypatch, tmp_path):
    reads = iter(
        [
            {
                "canonical_car_id": "40586061",
                "price_krw": 25_890_000,
                "sold": False,
            },
            {
                "canonical_car_id": "40586061",
                "price_krw": 26_890_000,
                "sold": False,
            },
        ]
    )
    monkeypatch.setattr("app.worker.read_detail", lambda *_: next(reads))
    known = SimpleNamespace(current_price=26_890_000)
    with pytest.raises(Exception) as error:
        _read_verified_detail(
            object(),
            {"source_car_id": "40586061"},
            tmp_path,
            known,
        )
    assert "inconsistent" in str(error.value)


def test_partial_detail_does_not_erase_reliable_values():
    previous = {
        "body_type": "SEDAN",
        "exterior_color": "화이트",
        "options": {"memory_seat": True},
        "condition_summary": "NO_PROBLEMS_STATED",
        "condition": {
            "flood_history": {"status": "NOT_FOUND", "original_evidence": ["침수 없음"]}
        },
    }
    current = {
        "body_type": None,
        "exterior_color": None,
        "options": {},
        "condition_summary": "UNVERIFIED",
        "condition": {
            "flood_history": {"status": "UNVERIFIED", "original_evidence": []}
        },
    }
    merged = merge_reliable_detail(previous, current)
    assert merged["body_type"] == "SEDAN"
    assert merged["exterior_color"] == "화이트"
    assert merged["options"]["memory_seat"] is True
    assert merged["condition_summary"] == "NO_PROBLEMS_STATED"
    assert merged["condition"]["flood_history"]["status"] == "NOT_FOUND"


def test_cancel_request_interrupts_at_checkpoint():
    class FakeDb:
        def refresh(self, job, attribute_names):
            assert attribute_names == ["status", "payload"]

    with pytest.raises(ScanCancelled):
        _raise_if_cancelled(FakeDb(), SimpleNamespace(status="CANCEL_REQUESTED"))

    _raise_if_cancelled(FakeDb(), SimpleNamespace(status="RUNNING"))


def test_cancelled_job_keeps_partial_report_and_progress():
    project_run = SimpleNamespace(
        project_id=4,
        status="RUNNING",
        error_code="OLD_ERROR",
    )

    class FakeDb:
        committed = False

        def scalars(self, statement):
            return [project_run]

        def commit(self):
            self.committed = True

    db = FakeDb()
    job = SimpleNamespace(
        id=35,
        status="CANCEL_REQUESTED",
        progress=5,
        error=None,
        payload={"cancellation_requested_at": "requested"},
    )
    report = [
        {"project_id": 4, "car_id": 1, "change": "MATERIAL_UPDATE"},
        {"project_id": 4, "car_id": 1, "change": "PRICE_DROP"},
    ]

    _finish_cancelled_job(db, job, {}, report, [], [])

    assert db.committed
    assert job.status == "CANCELLED"
    assert job.progress == 5
    assert job.payload["report"] == [
        {"project_id": 4, "car_id": 1, "change": "PRICE_DROP"}
    ]
    assert job.payload["project_statuses"] == {"4": "CANCELLED"}
    assert project_run.status == "CANCELLED"
    assert project_run.error_code is None


def test_sleep_interrupted_job_keeps_partial_report_and_reason():
    project_run = SimpleNamespace(project_id=4, status="RUNNING", error_code=None)

    class FakeDb:
        committed = False

        def scalars(self, statement):
            return [project_run]

        def commit(self):
            self.committed = True

    job = SimpleNamespace(
        id=36,
        status="CANCEL_REQUESTED",
        progress=37,
        error=None,
        payload={"cancellation_reason": "SYSTEM_SLEEP"},
    )
    report = [{"project_id": 4, "car_id": 1, "change": "NEW"}]

    _finish_cancelled_job(FakeDb(), job, {}, report, [], [])

    assert job.status == "INTERRUPTED_SLEEP"
    assert job.payload["interruption_reason"] == "SYSTEM_SLEEP"
    assert job.payload["partial_report"] is True
    assert job.payload["report"] == report
    assert project_run.status == "INTERRUPTED_SLEEP"
    assert project_run.error_code == "SYSTEM_SLEEP"


def test_completed_project_publishes_a_live_report_before_the_scan_finishes():
    job = SimpleNamespace(
        payload={"project_ids": [4, 5], "pagination": {"4": {"found_count": 2}}}
    )
    report = [
        {"project_id": 4, "car_id": 1, "change": "MATERIAL_UPDATE"},
        {"project_id": 4, "car_id": 1, "change": "PRICE_DROP"},
        {"project_id": 4, "car_id": 2, "change": "NEW"},
    ]
    failures = ["Project 4: TIMEOUT"]
    failure_details = [{"scope": "PROJECT", "project_id": 4, "code": "TIMEOUT"}]

    _publish_project_results(
        job,
        {},
        report,
        failures,
        failure_details,
        {"4": "SUCCEEDED"},
    )

    assert job.payload["current_project_id"] is None
    assert job.payload["project_statuses"] == {"4": "SUCCEEDED"}
    assert job.payload["pagination"] == {"4": {"found_count": 2}}
    assert [item["change"] for item in job.payload["report"]] == ["NEW", "PRICE_DROP"]
    assert job.payload["summary"] == {
        "changed": 2,
        "new": 1,
        "price_changes": 1,
        "failed": 1,
    }
    assert job.payload["failures"] == failure_details
