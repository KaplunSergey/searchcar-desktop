"""Stored report snapshots only; no live sites, browser or user database."""
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.auction_time import normalize_schedule
from app.auction_rounds import record_auction_observation
from app.auth import require_user
from app.main import app
from app.models import Car, CarSnapshot, Project, ProjectCar, ScanRun
from test_auction_api import auction_api, NOW


@pytest.fixture
def history(auction_api):
    client, factory, clock = auction_api
    with factory() as db:
        for car_id, source, project_id in [(1, "copart", 1), (2, "iaa", 2), (3, "copart", 4)]:
            run_id = 70 if car_id != 3 else 80
            external_id = str(65507986 + car_id)
            car = db.get(Car, car_id)
            car.source_listing_id = external_id
            payload = dict(source_key=source, source_listing_id=external_id, scan_run_id=run_id,
                vehicle_title=f"Saved {source}", url=f"https://www.{source if source == 'copart' else 'iaai'}.com/lot/{external_id}",
                mileage={"value": 12345, "unit": "mi", "accuracy": "ACTUAL"}, keys=False, primary_damage="FRONT END",
                main_image_source_url="https://cs.copart.com/saved.jpg" if source == "copart" else "https://vis.iaai.com/saved.jpg",
                checked_at="2026-10-10T22:30:00+00:00", schedule=normalize_schedule(dict(event_kind="START",
                    local_datetime="2026-10-11T01:00:00", source_timezone="UTC", raw="Saved schedule")),
                money_values=[dict(amount_minor=12300, currency="USD", meaning="CURRENT_BID", state="KNOWN", auction_round="saved-round")])
            db.add(CarSnapshot(id=100 + car_id, car_id=car_id, kind="LIST_ONLY", payload=payload, created_at=NOW))
            car.title = "Current car must not appear"
            car.details = {"mileage": {"value": 999999, "unit": "km"}, "main_image_source_url": "https://cs.copart.com/current.jpg"}
        for run_id, owner, ids in [(70, 1, [1, 2]), (80, 2, [3])]:
            plans = [dict(project_id=1 if i == 1 else 2 if i == 2 else 4,
                          source_key="iaa" if i == 2 else "copart", name=f"Frozen {i}") for i in ids]
            report = [dict(car_id=i, project_id=plans[pos]["project_id"], source_key=plans[pos]["source_key"],
                           source_listing_id=str(65507986+i), after_snapshot_id=100+i, title=f"Saved {i}", change="NEW") for pos, i in enumerate(ids)]
            db.add(ScanRun(id=run_id, owner_id=owner, kind="PROJECTS", status="SUCCEEDED", created_at=NOW,
                           payload=dict(project_ids=[p["project_id"] for p in plans], project_plans=plans, report=report)))
        db.commit()
    return client, factory, clock


def url(source="copart", car_ids=(1,), zone="Europe/Warsaw", run_id=70):
    return f"/api/scans/{run_id}/auction-report?source_key={source}&computer_timezone={zone}" + "".join(f"&car_ids={i}" for i in car_ids)


@pytest.mark.parametrize("source,car_id", [("copart", 1), ("iaa", 2)])
def test_report_uses_exact_saved_snapshot_not_current_fields_money_photo_or_clock(history, source, car_id):
    client, factory, clock = history
    with factory() as db:
        before = deepcopy(db.get(ScanRun, 70).payload)
        count = db.scalar(select(func.count()).select_from(CarSnapshot))
    response = client.get(url(source, (car_id,)))
    assert response.status_code == 200, response.text
    result = response.json()
    item = result["items"][0]
    assert result["scan_id"] == 70 and result["source_key"] == source
    assert item["snapshot_id"] == 100 + car_id and item["issue"] is None
    assert item["recorded_at"] == "2026-10-10T22:30:00+00:00"
    view = item["view"]
    assert view["generated_at"] == item["recorded_at"]
    assert view["lot"]["title"] == f"Saved {source}"
    assert view["lot"]["details"]["mileage"]["value"] == 12345
    assert view["lot"]["details"]["keys"] is False
    assert view["lot"]["money_values"][0]["amount_minor"] == 12300
    assert view["lot"]["time"]["local_datetime"] == "2026-10-11T03:00:00+02:00"
    assert view["lot"]["image"].endswith("/saved.jpg")
    assert "Current car" not in response.text and "999999" not in response.text and "current.jpg" not in response.text
    clock[0] = NOW.replace(year=2027)
    assert client.get(url(source, (car_id,))).json() == result
    with factory() as db:
        assert db.get(ScanRun, 70).payload == before
        assert db.scalar(select(func.count()).select_from(CarSnapshot)) == count
        assert db.get(Car, car_id).details["mileage"]["value"] == 999999


@pytest.mark.parametrize("params", [dict(run_id=80), dict(car_ids=(3,)), dict(source="iaa"), dict(source="encar"), dict(car_ids=(99999,))])
def test_report_cannot_read_foreign_scan_car_or_other_source(history, params):
    client, _, _ = history
    response = client.get(url(**params))
    assert response.status_code in (400, 404), response.text
    assert "Saved copart" not in response.text and "Current car" not in response.text


@pytest.mark.parametrize("fault", ["missing_pointer", "missing_snapshot", "invalidated", "wrong_car", "wrong_run", "wrong_source", "wrong_listing", "bad_money", "bad_schedule", "bad_payload", "bad_evidence"])
def test_unavailable_or_unbound_snapshot_is_explicit_and_never_falls_back(history, fault):
    client, factory, _ = history
    with factory() as db:
        run, snapshot = db.get(ScanRun, 70), db.get(CarSnapshot, 101)
        if fault == "missing_pointer":
            payload = deepcopy(run.payload); payload["report"][0].pop("after_snapshot_id"); run.payload = payload
        elif fault == "missing_snapshot": db.delete(snapshot)
        elif fault == "invalidated": snapshot.integrity_status = "INVALIDATED"
        elif fault == "wrong_car": snapshot.car_id = 3
        elif fault == "bad_payload": snapshot.payload = ["not a saved observation"]
        else:
            payload = deepcopy(snapshot.payload)
            field, value = {"wrong_run": ("scan_run_id", 80), "wrong_source": ("source_key", "iaa"),
                            "wrong_listing": ("source_listing_id", "foreign"), "bad_money": ("money_values", [{"amount_minor": -1}]),
                            "bad_schedule": ("schedule", {"precision": "EXACT", "event_kind": "START", "utc_at": "invalid"}),
                            "bad_evidence": ("schedule", {"precision": "UNKNOWN", "event_kind": "UNKNOWN", "raw": {"private": "invalid"}})}[fault]
            payload[field] = value; snapshot.payload = payload
        db.commit()
    response = client.get(url())
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["view"] is None
    assert response.json()["items"][0]["issue"] == "SNAPSHOT_UNAVAILABLE"
    assert "Current car" not in response.text and "999999" not in response.text


def test_deleted_project_report_remains_readable_and_snapshot_batch_is_bounded(history):
    client, factory, _ = history
    with factory() as db:
        db.delete(db.get(ProjectCar, dict(project_id=1, car_id=1))); db.flush()
        db.delete(db.get(Project, 1)); db.commit()
    assert client.get(url()).status_code == 200
    assert client.get(url(car_ids=tuple(range(1, 22)))).status_code == 422
    assert client.get(url(car_ids=())).status_code == 422
    assert client.get(url(zone="CST")).status_code == 400


def test_report_only_enriches_requested_cars_not_the_whole_run(history, monkeypatch):
    client, factory, _ = history
    with factory() as db:
        db.add(Car(id=4, source_key="copart", source_listing_id="other", url="https://copart.com/lot/other")); db.flush()
        db.add(ProjectCar(project_id=1, car_id=4))
        run = db.get(ScanRun, 70)
        payload = deepcopy(run.payload)
        payload["report"].append(dict(car_id=4, project_id=1, source_key="copart", change="NEW"))
        run.payload = payload; db.commit()
    image_reads = []
    monkeypatch.setattr("app.main.image_payloads", lambda car_id, _db: image_reads.append(car_id) or {})
    assert client.get(url()).status_code == 200
    assert image_reads == [1]


def test_snapshot_round_uses_that_scan_observation_not_the_current_round(history):
    client, factory, _ = history
    with factory() as db:
        car, snapshot = db.get(Car, 1), db.get(CarSnapshot, 101)
        original = record_auction_observation(db, car, observation_key="scan:70:1", observed_at=NOW,
                                             schedule=snapshot.payload["schedule"])
        round_id = original.round_id
        payload = deepcopy(snapshot.payload); payload["money_values"][0]["auction_round"] = round_id
        snapshot.payload = payload
        later = record_auction_observation(db, car, observation_key="later", observed_at=NOW,
                    sale_id="future-sale", schedule={"event_kind":"START", "local_datetime":"2026-11-01T01:00:00", "source_timezone":"UTC"})
        assert later.round_id != round_id
        db.commit()
    view = client.get(url()).json()["items"][0]["view"]
    assert view["captured_round_id"] == round_id
    assert view["lot"]["money_values"][0]["auction_round"] == round_id


def test_snapshot_reads_require_authentication(history):
    client, _, _ = history
    override = app.dependency_overrides.pop(require_user)
    try:
        response = client.get(url())
        assert response.status_code == 401
        assert "Saved" not in response.text
    finally:
        app.dependency_overrides[require_user] = override
