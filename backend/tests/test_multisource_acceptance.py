"""HTTP/worker acceptance with disposable data, not live auction adapters."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import require_csrf, require_user
from app.database import get_db
from app.job_queue import claim_next_job
from app.main import app
from app.models import Car, CarSnapshot, Project, ProjectCar, ProjectScanRun, ScanRun, User
from app.scanner import CaptchaError, SearchCollection
from app.sources.encar import EncarSource
from app.worker import process_job
from test_multisource_queue import queue
from test_parallel_project_scan import _fake_playwright


@pytest.fixture
def api(queue, monkeypatch):
    def database():
        with queue.factory() as db:
            yield db

    def user():
        with queue.factory() as db:
            return db.get(User, queue.owner_id)

    previous = dict(app.dependency_overrides)
    app.dependency_overrides.update({get_db: database, require_user: user, require_csrf: user})
    for adapter in queue.adapters.values():
        adapter.validate_search_url = lambda url: url
    monkeypatch.setattr("app.main.get_source", queue.adapters.__getitem__)
    try:
        # No lifespan, server, login or real user database is started. Auth itself
        # is covered separately; this boundary checks route validation/serialization.
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("outcome", ["success", "captcha", "cancel_site"])
def test_http_mixed_launch_worker_history_and_owner_boundary(api, queue, outcome):
    with queue.factory() as db:
        foreign = User(username="foreign", username_key="foreign", password_hash="test",
                       role="USER", status="ACTIVE")
        db.add(foreign)
        db.flush()
        foreign_project = Project(owner_id=foreign.id, source_key="alpha", name="Foreign",
                                  name_key="foreign", search_url="https://alpha.example/foreign")
        db.add(foreign_project)
        foreign_run = ScanRun(owner_id=foreign.id, kind="PROJECTS", status="SUCCEEDED", payload={})
        db.add(foreign_run)
        db.commit()
        foreign_project_id, foreign_run_id = foreign_project.id, foreign_run.id

    denied = api.post("/api/scans/projects", json={"project_ids": [foreign_project_id]})
    assert denied.status_code == 404
    assert api.get(f"/api/scans/{foreign_run_id}").status_code == 404
    response = api.post("/api/scans/projects", json={"all_sources": True})
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert api.post("/api/scans/projects", json={"all_sources": True}).json()["created"] is False
    with queue.factory() as db:
        # Editing a project after enqueue does not change the launch snapshot.
        db.get(Project, queue.plans["alpha1"]["project_id"]).search_url = "https://alpha.example/edited"
        db.commit()

    def hook(name, kwargs):
        if name == "alpha1" and outcome == "captcha":
            raise CaptchaError("fixture blocked alpha")
        if name == "alpha1" and outcome == "cancel_site":
            cancelled = api.post(f"/api/scans/{run_id}/cancel", json={"source_keys": ["alpha"]})
            assert cancelled.status_code == 202, cancelled.text
            kwargs["checkpoint"]()

    queue.hook = hook
    assert claim_next_job(queue.engine, "acceptance-worker") == run_id
    process_job(run_id)
    result = api.get(f"/api/scans/{run_id}/progress").json()
    assert result["status"] == ("SUCCEEDED" if outcome == "success" else "PARTIAL")
    assert result["progress"] == 100
    assert result["payload"]["project_plans"][0]["search_url"] == "https://alpha.example/1"
    assert len(result["payload"]["project_statuses"]) == 7
    assert [item["id"] for item in api.get("/api/scans").json()] == [run_id]
    assert ("collect", "bravo2") in queue.events
    assert ("collect", "charlie2") in queue.events
    assert not queue.active
    assert claim_next_job(queue.engine, "acceptance-worker") is None
    with queue.factory() as db:
        statuses = list(db.scalars(select(ProjectScanRun.status).where(ProjectScanRun.scan_run_id == run_id)))
        assert len(statuses) == 7
        assert not set(statuses) & {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}


def test_encar_http_worker_persists_new_and_price_changes_without_cross_source_update(api, queue, monkeypatch):
    prices = {"42688672": 20_000_000, "42791283": 25_000_000}

    class FixtureEncar(EncarSource):
        def collect_search_pages(self, _page, _url, name, **_kwargs):
            return SearchCollection({key: {"source_car_id": key, "url": self.listing_url(key),
                                          "source": name, "list_text": "2024/01 20,000 km"}
                                     for key in prices}, 1, 1, len(prices))

        @contextmanager
        def detail_reader(self, _storage, **_kwargs):
            def read(row, _known_price):
                key = row["source_car_id"]
                detail = {"canonical_car_id": key, "source_car_id": key, "url": row["url"],
                          "title": f"Encar {key}", "price_krw": prices[key], "offer_type": "SALE",
                          "price_source": "DETAIL_PRIMARY", "mileage_km": 20_000,
                          "options": {}, "condition": {}, "sold": False, "unavailable": False}
                detail["fingerprint"] = self.fingerprint_listing(detail)
                return detail
            yield read

    source = FixtureEncar()
    queue.adapters["encar"] = source
    queue.descriptors["encar"] = source.descriptor
    monkeypatch.setattr("app.worker.desktop_playwright", _fake_playwright)
    with queue.factory() as db:
        project = Project(owner_id=queue.owner_id, source_key="encar", name="Encar acceptance",
                          name_key="encar acceptance", search_url="https://www.encar.com/dc/dc_carsearchlist.do",
                          scan_mode="ACCURATE", search_page_mode="ALL_PAGES",
                          price_filter_revision=1, price_filter_baseline_revision=1)
        other = Car(source_key="alpha", source_listing_id="42688672", title="Untouched auction",
                    url="https://alpha.example/42688672", details={"fixture": "unchanged"})
        db.add_all([project, other])
        db.flush()
        db.add(ProjectCar(project_id=queue.plans["alpha1"]["project_id"], car_id=other.id))
        db.commit()
        project_id, other_id = project.id, other.id

    def run():
        response = api.post("/api/scans/projects", json={"project_ids": [project_id]})
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        assert claim_next_job(queue.engine, "acceptance-worker") == run_id
        process_job(run_id)
        result = api.get(f"/api/scans/{run_id}").json()
        assert result["status"] == "SUCCEEDED", result
        return result

    first = run()
    assert {item["change"] for item in first["payload"]["report"]} == {"NEW"}
    assert len(first["payload"]["report"]) == 2
    prices["42688672"] = 19_000_000
    second = run()
    assert [(item["encar_id"], item["change"]) for item in second["payload"]["report"]] == [
        ("42688672", "PRICE_DROP")]
    cars = api.get(f"/api/projects/{project_id}/cars").json()
    assert {item["encar_id"]: item["price"] for item in cars} == prices
    assert all(item["status"] != "NEW" for item in cars)
    with queue.factory() as db:
        stored = list(db.scalars(select(Car).where(Car.source_key == "encar")))
        assert len(stored) == 2
        # Snapshots describe data changes, not every unchanged observation.
        assert {car.source_listing_id: len(list(db.scalars(
            select(CarSnapshot).where(CarSnapshot.car_id == car.id)))) for car in stored} == {
                "42688672": 2, "42791283": 1}
        assert db.get(Car, other_id).details == {"fixture": "unchanged"}
        assert db.get(Car, other_id).canonical_encar_id is None


def test_read_api_filters_stored_site_and_exposes_listing_identity(api, queue):
    with queue.factory() as db:
        for key in ("alpha", "bravo"):
            car = Car(source_key=key, source_listing_id="same-id", title=key,
                      url=f"https://{key}.example/same-id")
            db.add(car)
            db.flush()
            db.add(ProjectCar(project_id=queue.plans[f"{key}1"]["project_id"], car_id=car.id,
                              favorite=True))
        db.commit()
    projects = api.get("/api/projects?source_key=alpha").json()
    assert {item["id"] for item in projects} == {
        queue.plans[f"alpha{number}"]["project_id"] for number in (1, 2, 3)}
    assert {item["source_key"] for item in projects} == {"alpha"}
    assert api.get("/api/projects?source_key=not-stored").json() == []
    favorites = api.get("/api/favorites?source_key=bravo").json()
    assert len(favorites) == 1
    assert (favorites[0]["source_key"], favorites[0]["source_listing_id"]) == ("bravo", "same-id")
    cards = api.get(f"/api/projects/{queue.plans['alpha1']['project_id']}/cars").json()
    assert len(cards) == 1
    assert (cards[0]["source_key"], cards[0]["source_listing_id"]) == ("alpha", "same-id")
    detail = api.get(f"/api/cars/{cards[0]['id']}").json()
    assert (detail["source_key"], detail["source_listing_id"]) == ("alpha", "same-id")


def test_mixed_history_projects_site_portion_without_mutating_shared_run(api, queue):
    response = api.post("/api/scans/projects", json={"project_ids": [
        queue.plans["alpha1"]["project_id"], queue.plans["bravo1"]["project_id"]]})
    run_id = response.json()["id"]

    def hook(name, _kwargs):
        if name == "alpha1":
            raise CaptchaError("fixture alpha blocked")
    queue.hook = hook
    assert claim_next_job(queue.engine, "acceptance") == run_id
    process_job(run_id)
    bravo = api.get(f"/api/scans/{run_id}?source_key=bravo").json()
    assert bravo["status"] == "SUCCEEDED"
    assert bravo["overall_status"] == "PARTIAL"
    assert bravo["payload"]["project_ids"] == [queue.plans["bravo1"]["project_id"]]
    assert bravo["payload"]["source_keys"] == ["bravo"]
    assert bravo["payload"]["failures"] == []
    assert bravo["payload"]["summary"]["failed"] == 0
    assert bravo["error"] is None
    alpha = api.get(f"/api/scans/{run_id}/progress?source_key=alpha").json()
    assert alpha["status"] == "FAILED"
    assert len(alpha["payload"]["failures"]) == 1
    assert api.get(f"/api/scans/{run_id}?source_key=charlie").status_code == 404
    assert api.get("/api/scans?source_key=charlie").json() == []
    assert [item["id"] for item in api.get("/api/scans?source_key=bravo").json()] == [run_id]
    with queue.factory() as db:
        stored = db.get(ScanRun, run_id)
        assert stored.status == "PARTIAL"
        assert len(stored.payload["project_ids"]) == 2
        assert stored.payload["failures"][0]["source_key"] == "alpha"
        assert db.query(ScanRun).count() == 1


def test_site_history_filter_precedes_limit_and_reads_legacy_jobs(api, queue):
    from datetime import datetime, timedelta, timezone
    with queue.factory() as db:
        start = datetime(2026, 10, 10, tzinfo=timezone.utc)
        legacy = ScanRun(owner_id=queue.owner_id, kind="PROJECTS", status="SUCCEEDED",
                         created_at=start, payload={"project_ids": [queue.plans["alpha1"]["project_id"]]})
        db.add(legacy)
        db.flush()
        legacy_id = legacy.id
        db.add_all([ScanRun(owner_id=queue.owner_id, kind="PROJECTS", status="SUCCEEDED",
                            created_at=start + timedelta(minutes=index + 1),
                            payload={"project_ids": [queue.plans["bravo1"]["project_id"]]})
                    for index in range(101)])
        db.commit()
    assert len(api.get("/api/scans").json()) == 100
    assert [item["id"] for item in api.get("/api/scans?source_key=alpha").json()] == [legacy_id]


@pytest.mark.parametrize("kind", ["PROJECTS", "CARS"])
def test_source_history_filters_nonempty_reports_and_preserves_deleted_frozen_projects(api, queue, kind):
    with queue.factory() as db:
        cars, report, plans = [], [], []
        for key in ("alpha", "bravo"):
            project_id = queue.plans[f"{key}1"]["project_id"]
            car = Car(source_key=key, source_listing_id="same-id", url=f"https://{key}.example/same-id")
            db.add(car)
            db.flush()
            db.add(ProjectCar(project_id=project_id, car_id=car.id))
            cars.append(car.id)
            report.append({"source_key": key, "source_listing_id": "same-id", "car_id": car.id,
                           "project_id": project_id, "project_name": f"{key}1", "change": "NEW"})
            plans.append(dict(queue.plans[f"{key}1"]))
        run = ScanRun(owner_id=queue.owner_id, kind=kind, status="SUCCEEDED", progress=100,
                      payload={"project_ids": [plan["project_id"] for plan in plans] if kind == "PROJECTS" else [],
                               "project_plans": plans if kind == "PROJECTS" else [],
                               "car_ids": cars if kind == "CARS" else [], "report": report,
                               "summary": {"changed": 2, "new": 2, "failed": 0}})
        db.add(run)
        db.flush()
        run_id = run.id
        if kind == "PROJECTS":
            # Keep history after deletion without writing a second ScanRun.
            db.delete(db.get(ProjectCar, {"project_id": plans[0]["project_id"], "car_id": cars[0]}))
            db.flush()
            db.delete(db.get(Project, plans[0]["project_id"]))
        db.commit()
    view = api.get(f"/api/scans/{run_id}?source_key=alpha").json()
    assert view["status"] == "SUCCEEDED"
    assert [item["car_id"] for item in view["payload"]["report"]] == [cars[0]]
    assert view["payload"]["summary"]["changed"] == 1
    assert view["payload"]["summary"]["new"] == 1
    assert [plan["source_key"] for plan in view["payload"]["project_plans"]] == (["alpha"] if kind == "PROJECTS" else [])
    assert view["payload"]["car_ids"] == ([cars[0]] if kind == "CARS" else [])
    with queue.factory() as db:
        assert len(db.get(ScanRun, run_id).payload["report"]) == 2


def test_legacy_single_project_history_has_source_scope(api, queue):
    with queue.factory() as db:
        run = ScanRun(owner_id=queue.owner_id, kind="PROJECTS", status="SUCCEEDED", progress=100,
                      payload={"project_id": queue.plans["alpha1"]["project_id"]})
        db.add(run)
        db.commit()
        run_id = run.id
    response = api.get(f"/api/scans/{run_id}?source_key=alpha")
    assert response.status_code == 200, response.text
    assert response.json()["payload"]["project_ids"] == [queue.plans["alpha1"]["project_id"]]
    assert api.get(f"/api/scans/{run_id}?source_key=bravo").status_code == 404


@pytest.mark.parametrize("legacy_format", ["distinct_names", "same_names", "renamed"])
def test_legacy_error_text_does_not_include_other_site(api, queue, legacy_format):
    ids = [queue.plans[name]["project_id"] for name in ("alpha1", "bravo1")]
    with queue.factory() as db:
        run = ScanRun(owner_id=queue.owner_id, kind="PROJECTS", status="FAILED", progress=100,
                      error="alpha1: alpha failure\nbravo1: bravo failure",
                      payload={"project_ids": ids})
        db.add(run)
        db.flush()
        db.add_all([ProjectScanRun(scan_run_id=run.id, project_id=project_id, status="FAILED",
                                  error_code="READ_ERROR") for project_id in ids])
        if legacy_format == "same_names":
            db.get(Project, ids[1]).name = "alpha1"
            run.error = "alpha1: alpha failure\nalpha1: bravo failure"
        elif legacy_format == "renamed":
            db.get(Project, ids[0]).name = "Renamed alpha"
        db.commit()
        run_id = run.id
    response = api.get(f"/api/scans/{run_id}?source_key=alpha").json()
    assert len(response["payload"]["failures"]) == 1
    if legacy_format == "distinct_names":
        assert "alpha failure" in response["error"]
    else:
        assert response["payload"]["failures"][0]["details_unavailable"] is True
    assert "bravo" not in response["error"]
