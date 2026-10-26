"""Shared sequential scheduling with fake sources, not live site evidence."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import create_database_engine
from app.desktop_license import require_search_entitlement as signed_search_entitlement
from app.job_queue import claim_next_job
from app.main import cancel_scan, enqueue
from app.models import Project, ProjectScanRun, ScanRun, User
from app.scanner import CaptchaError, SearchCollection
from app.schemas import CancelScanIn
from app.sources.contracts import SourceDescriptor
from app.sqlite_migrations import migrate_sqlite
from app.worker import process_job


@pytest.fixture
def queue(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'queue.sqlite3'}")
    migrate_sqlite(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        owner = User(username="owner", username_key="owner", password_hash="test",
                     role="USER", status="ACTIVE")
        db.add(owner)
        db.flush()
        projects = [
            Project(owner_id=owner.id, source_key=site, name=f"{site}{number}",
                    name_key=f"{site}{number}", search_url=f"https://{site}.example/{number}",
                    scan_mode="FAST", search_page_mode="FIRST_PAGE",
                    price_filter_revision=1, price_filter_baseline_revision=1)
            for site, number in (("alpha", 1), ("alpha", 2), ("alpha", 3),
                                 ("bravo", 1), ("bravo", 2), ("charlie", 1), ("charlie", 2))
        ]
        db.add_all(projects)
        db.commit()
        plans = {project.name: {
            "project_id": project.id, "source_key": project.source_key,
            "name": project.name, "search_url": project.search_url,
            "scan_mode": project.scan_mode, "search_page_mode": project.search_page_mode,
            "price_filter_mode": "LINK", "price_min_krw": None, "price_max_krw": None,
            "price_filter_revision": 1, "price_filter_baseline_revision": 1,
        } for project in projects}
        owner_id = owner.id

    state = SimpleNamespace(engine=engine, factory=factory, owner_id=owner_id,
                            plans=plans, events=[], active=set(), hook=lambda *_: None)

    def submit(names):
        with factory() as db:
            return enqueue("PROJECTS", {
                "project_ids": [plans[name]["project_id"] for name in names],
                "source_keys": list(dict.fromkeys(plans[name]["source_key"] for name in names)),
                "project_plans": [dict(plans[name]) for name in dict.fromkeys(names)],
                "trigger": "MANUAL",
            }, db.get(User, owner_id), db)

    state.submit = submit
    descriptors = {key: SourceDescriptor(key=key, family="AUCTION", domains=(f"{key}.example",),
                                         capabilities=frozenset({"SEARCH"}))
                   for key in ("alpha", "bravo", "charlie")}

    class Adapter:
        def __init__(self, key):
            self.descriptor = descriptors[key]

        def open_browser(self, *_args, **_kwargs):
            key = self.descriptor.key
            assert not state.active, "Previous site's runtime must close before opening the next"
            token = object()
            state.active.add(token)
            state.events.append(("open", key))

            def close_browser():
                state.active.remove(token)
                state.events.append(("close", key))

            return (SimpleNamespace(close=close_browser),
                    SimpleNamespace(close=lambda: state.events.append(("close_context", key))),
                    SimpleNamespace(source_key=key, locale=f"test-{key}", token=token))

        def collect_search_pages(self, page, _url, name, **kwargs):
            assert page.source_key == self.descriptor.key
            assert page.locale == f"test-{self.descriptor.key}"
            assert state.active == {page.token}
            with factory() as db:
                running = list(db.scalars(select(ProjectScanRun).where(ProjectScanRun.status == "RUNNING")))
                assert [row.project_id for row in running] == [plans[name]["project_id"]]
                assert db.get(ScanRun, running[0].scan_run_id).payload["current_project_id"] == plans[name]["project_id"]
            assert claim_next_job(engine, "competing-worker") is None
            state.events.append(("collect", name))
            state.hook(name, kwargs)
            return SearchCollection({}, 1, 1, 0)

    @contextmanager
    def playwright():
        yield object()

    adapters = {key: Adapter(key) for key in descriptors}
    state.adapters, state.descriptors = adapters, descriptors
    monkeypatch.setattr("app.worker.SessionLocal", factory)
    monkeypatch.setattr("app.worker.desktop_playwright", playwright)
    monkeypatch.setattr("app.worker.get_source_descriptor", descriptors.__getitem__)
    monkeypatch.setattr("app.worker.get_source", adapters.__getitem__)
    monkeypatch.setattr("app.worker.publish_scan_update", lambda *_: None)
    monkeypatch.setattr("app.desktop_license.require_search_entitlement", lambda *_: None)
    return state


GROUPED = ["alpha1", "alpha2", "alpha3", "bravo1", "bravo2", "charlie1"]
INTERLEAVED = ["alpha1", "bravo1", "charlie1", "alpha2", "bravo2", "alpha3"]


def test_worker_reuses_opt_in_browser_between_jobs_and_closes_it_on_shutdown(queue, monkeypatch):
    from threading import Event
    from app import worker
    adapter = queue.adapters['alpha']
    adapter.reuse_browser_session = True
    original = adapter.open_browser
    def open_browser(*args, **kwargs):
        browser, context, page = original(*args, **kwargs)
        page.is_closed = lambda: False
        context.reuse_requested = lambda: True
        context.park = lambda: None
        return browser, context, page
    monkeypatch.setattr(adapter, 'open_browser', open_browser)
    queue.submit(['alpha1'])
    queue.submit(['alpha2'])
    stop = Event()
    def claim(_engine, worker_id):
        job_id = claim_next_job(queue.engine, worker_id)
        if job_id is None:
            stop.set()
        return job_id
    monkeypatch.setattr(worker, 'claim_next_job', claim)
    monkeypatch.setattr(worker, 'enqueue_scheduled', lambda: None)
    worker.run(stop)
    assert [value for kind, value in queue.events if kind == 'collect'] == ['alpha1', 'alpha2']
    assert queue.events.count(('open', 'alpha')) == 1
    assert queue.events.count(('close', 'alpha')) == 1
    assert not queue.active


def test_scan_waits_for_selection_profile_without_starting_a_second_browser(queue, monkeypatch):
    from app.sources.auction_lists import AuctionListError
    adapter = queue.adapters['alpha']
    original = adapter.open_browser
    attempts = []
    def reserved(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise AuctionListError('SOURCE_BROWSER_BUSY', 'Selection is active')
        return original(*args, **kwargs)
    monkeypatch.setattr(adapter, 'open_browser', reserved)
    monkeypatch.setattr('app.worker.time.sleep', lambda *_: None)
    run_id = queue.submit(['alpha1'])['id']
    process_job(run_id)
    with queue.factory() as db:
        run = db.get(ScanRun, run_id)
        assert run.status == 'SUCCEEDED', (run.error, run.payload.get('failures'))
    assert len(attempts) == 2
    assert queue.events.count(('open', 'alpha')) == 1


def test_wait_for_selection_profile_is_cancellable(queue, monkeypatch):
    from app.sources.auction_lists import AuctionListError
    def reserved(*_args, **_kwargs):
        raise AuctionListError('SOURCE_BROWSER_BUSY', 'Selection is active')
    monkeypatch.setattr(queue.adapters['alpha'], 'open_browser', reserved)
    run_id = queue.submit(['alpha1'])['id']
    def cancel_wait(*_args):
        with queue.factory() as db:
            cancel_scan(run_id, CancelScanIn(all_projects=True), db.get(User, queue.owner_id), db)
    monkeypatch.setattr('app.worker.time.sleep', cancel_wait)
    process_job(run_id)
    with queue.factory() as db:
        assert db.get(ScanRun, run_id).status == 'CANCELLED'
    assert not any(event[0] == 'open' for event in queue.events)


def test_scheduler_runs_only_allowed_sites_without_losing_schedule(queue, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.models import SchedulerSetting, ScheduledProject
    from app.desktop_license import SearchEntitlementError
    from app.worker import enqueue_scheduled

    def entitlement(source_key=None):
        if source_key is not None and source_key != "bravo":
            raise SearchEntitlementError("LICENSE_SOURCE_NOT_ALLOWED")

    monkeypatch.setattr("app.desktop_license.require_search_entitlement", entitlement)
    current = datetime(2026, 10, 10, 10, tzinfo=timezone.utc)
    with queue.factory() as db:
        setting = SchedulerSetting(user_id=queue.owner_id, enabled=True,
                                   interval_minutes=60, next_run_at=current - timedelta(minutes=1))
        db.add(setting)
        db.flush()
        for name in ("alpha1", "bravo1"):
            project_id = queue.plans[name]["project_id"]
            db.get(Project, project_id).auto_update = True
            db.add(ScheduledProject(scheduler_id=setting.id, project_id=project_id))
        db.commit()
    enqueue_scheduled(now=current)
    with queue.factory() as db:
        run = db.scalar(select(ScanRun))
        assert run is not None
        assert run.payload["project_ids"] == [queue.plans["bravo1"]["project_id"]]
        assert len(list(db.scalars(select(ScheduledProject)))) == 2
        assert db.scalar(select(SchedulerSetting)).next_run_at == current + timedelta(hours=1)


def test_legacy_manual_enqueue_checks_real_project_source(queue, monkeypatch):
    from app.desktop_license import SearchEntitlementError
    from fastapi import HTTPException

    def entitlement(source_key=None):
        if source_key == "bravo":
            raise SearchEntitlementError("LICENSE_SOURCE_NOT_ALLOWED")

    monkeypatch.setattr("app.desktop_license.require_search_entitlement", entitlement)
    with queue.factory() as db:
        with pytest.raises(HTTPException) as caught:
            enqueue("PROJECTS", {"project_ids": [queue.plans["bravo1"]["project_id"]]},
                    db.get(User, queue.owner_id), db)
        assert caught.value.status_code == 402
        assert db.scalar(select(ScanRun)) is None


def test_queued_job_keeps_all_shipped_sites_during_temporary_client_policy(queue, tmp_path, monkeypatch):
    from test_desktop_license import license_state, install_sources
    state = license_state(tmp_path)
    monkeypatch.setattr('app.sources.registry.list_sources', lambda: tuple(queue.descriptors.values()))
    install_sources(state, ["alpha", "bravo"])
    monkeypatch.setattr("app.desktop_license.require_search_entitlement", lambda key=None:
        signed_search_entitlement(key, now=state["now"], data_dir=tmp_path,
                                   public_keys=state["public_keys"], mode="required"))
    run_id = queue.submit(["alpha1", "bravo1"])["id"]
    install_sources(state, ["bravo"])
    process_job(run_id)
    with queue.factory() as db:
        run = db.get(ScanRun, run_id)
        assert not run.payload.get("failures")
        assert db.get(Project, queue.plans["alpha1"]["project_id"]) is not None
        rows = list(db.scalars(select(ProjectScanRun).where(ProjectScanRun.scan_run_id == run_id)))
        assert {row.project_id: row.status for row in rows} == {
            queue.plans["alpha1"]["project_id"]: "SUCCEEDED",
            queue.plans["bravo1"]["project_id"]: "SUCCEEDED",
        }
    assert ("collect", "alpha1") in queue.events
    assert ("collect", "bravo1") in queue.events


def test_manual_car_update_checks_real_car_source(queue, monkeypatch):
    from app.models import Car, ProjectCar
    from app.desktop_license import SearchEntitlementError
    from fastapi import HTTPException

    def entitlement(key=None):
        if key == "bravo":
            raise SearchEntitlementError("LICENSE_SOURCE_NOT_ALLOWED")

    monkeypatch.setattr("app.desktop_license.require_search_entitlement", entitlement)
    with queue.factory() as db:
        car = Car(source_key="bravo", source_listing_id="test-bravo",
                  url="https://bravo.example/car/test-bravo")
        db.add(car)
        db.flush()
        db.add(ProjectCar(project_id=queue.plans["bravo1"]["project_id"], car_id=car.id))
        db.commit()
        with pytest.raises(HTTPException) as caught:
            enqueue("CARS", {"car_ids": [car.id]}, db.get(User, queue.owner_id), db)
        assert caught.value.detail == "license_source_not_allowed"
        assert db.scalar(select(ScanRun)) is None


def test_scheduler_handles_license_revoked_between_preflight_and_enqueue(queue, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.models import SchedulerSetting, ScheduledProject
    from app.desktop_license import SearchEntitlementError
    from app.worker import enqueue_scheduled
    calls = 0

    def entitlement(key=None):
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise SearchEntitlementError("LICENSE_SOURCE_NOT_ALLOWED")

    monkeypatch.setattr("app.desktop_license.require_search_entitlement", entitlement)
    current = datetime(2026, 10, 10, 10, tzinfo=timezone.utc)
    with queue.factory() as db:
        setting = SchedulerSetting(user_id=queue.owner_id, enabled=True, interval_minutes=60,
                                   next_run_at=current - timedelta(minutes=1))
        db.add(setting)
        db.flush()
        db.add(ScheduledProject(scheduler_id=setting.id, project_id=queue.plans["alpha1"]["project_id"]))
        db.commit()
    enqueue_scheduled(now=current)
    with queue.factory() as db:
        assert db.scalar(select(ScanRun)) is None
        assert db.scalar(select(SchedulerSetting)).enabled is True
        assert db.scalar(select(SchedulerSetting)).next_run_at == current + timedelta(hours=1)


def test_enqueue_freezes_interleaved_order_without_duplicates(queue):
    result = queue.submit([*GROUPED, "alpha2", "bravo1"])
    with queue.factory() as db:
        run = db.get(ScanRun, result["id"])
        assert run.payload["project_ids"] == [queue.plans[name]["project_id"] for name in INTERLEAVED]
        assert [plan["name"] for plan in run.payload["project_plans"]] == INTERLEAVED
    repeated = queue.submit(GROUPED)
    assert repeated["created"] is False
    assert repeated["id"] == result["id"]


def test_single_site_keeps_submitted_project_order(queue):
    names = ["alpha3", "alpha1", "alpha2", "alpha1"]
    result = queue.submit(names)
    assert claim_next_job(queue.engine, "worker") == result["id"]
    process_job(result["id"])
    assert [value for kind, value in queue.events if kind == "collect"] == names[:3]
    assert not queue.active


def test_busy_projects_are_removed_before_interleaving(queue):
    busy = queue.submit(["bravo1"])
    result = queue.submit(GROUPED)
    with queue.factory() as db:
        run = db.get(ScanRun, result["id"])
        expected = ["alpha1", "bravo2", "charlie1", "alpha2", "alpha3"]
        assert run.payload["project_ids"] == [queue.plans[name]["project_id"] for name in expected]
        assert [plan["name"] for plan in run.payload["project_plans"]] == expected
        assert db.query(ScanRun).count() == 2
    assert next(item for item in result["projects"] if item["project_id"] == queue.plans["bravo1"]["project_id"]) == {
        "project_id": queue.plans["bravo1"]["project_id"], "status": "ALREADY_ACTIVE",
        "scan_run_id": busy["id"],
    }


def test_frozen_sources_keep_execution_order_after_database_changes(queue):
    result = queue.submit(GROUPED)
    with queue.factory() as db:
        db.get(Project, queue.plans["alpha2"]["project_id"]).source_key = "charlie"
        db.commit()
    assert claim_next_job(queue.engine, "worker") == result["id"]
    process_job(result["id"])
    assert [value for kind, value in queue.events if kind == "collect"] == INTERLEAVED
    assert not queue.active
    with queue.factory() as db:
        assert db.get(ScanRun, result["id"]).status == "SUCCEEDED"


@pytest.mark.parametrize("outcome", ["success", "captcha", "cancel_site", "queued_cancel_site", "cancel_batch"])
def test_mixed_batch_is_sequential_and_releases_fifo_queue(queue, outcome):
    first = queue.submit(GROUPED)
    second = queue.submit(["charlie2"])
    if outcome == "queued_cancel_site":
        with queue.factory() as db:
            cancel_scan(first["id"], CancelScanIn(source_keys=["alpha"]),
                        db.get(User, queue.owner_id), db)

    def hook(name, kwargs):
        if name != "alpha1":
            return
        if outcome == "captcha":
            raise CaptchaError("fake alpha blocked")
        if outcome in {"cancel_site", "cancel_batch"}:
            with queue.factory() as db:
                cancel_scan(first["id"], CancelScanIn(source_keys=["alpha"])
                            if outcome == "cancel_site" else None,
                            db.get(User, queue.owner_id), db)
            kwargs["checkpoint"]()
            raise AssertionError("Cancellation checkpoint did not stop the project")

    queue.hook = hook
    assert claim_next_job(queue.engine, "worker") == first["id"]
    process_job(first["id"])
    assert not queue.active
    collected = [value for kind, value in queue.events if kind == "collect"]
    expected = {
        "success": INTERLEAVED,
        "cancel_batch": ["alpha1"],
        "queued_cancel_site": ["bravo1", "charlie1", "bravo2"],
    }.get(outcome, ["alpha1", "bravo1", "charlie1", "bravo2"])
    assert collected == expected
    with queue.factory() as db:
        run = db.get(ScanRun, first["id"])
        assert run.status == ("SUCCEEDED" if outcome == "success" else "CANCELLED"
                              if outcome == "cancel_batch" else "PARTIAL")
        portions = list(db.scalars(select(ProjectScanRun).where(ProjectScanRun.scan_run_id == run.id)))
        assert len(portions) == len(GROUPED)
        assert all(row.status not in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"} for row in portions)
        assert db.get(ScanRun, second["id"]).status == "QUEUED"

    assert claim_next_job(queue.engine, "worker") == second["id"]
    process_job(second["id"])
    assert not queue.active
    assert claim_next_job(queue.engine, "worker") is None
    assert [value for kind, value in queue.events if kind == "collect"] == [*expected, "charlie2"]
    assert [value for kind, value in queue.events if kind == "open"] == [
        value for kind, value in queue.events if kind == "close"
    ]
    with queue.factory() as db:
        assert db.get(ScanRun, second["id"]).status == "SUCCEEDED"


def test_legacy_batch_uses_owned_project_sources_for_fair_order(queue):
    with queue.factory() as db:
        run = ScanRun(owner_id=queue.owner_id, kind="PROJECTS", status="QUEUED",
                      payload={"project_ids": [queue.plans[name]["project_id"] for name in GROUPED]})
        db.add(run)
        db.commit()
        run_id = run.id
    assert claim_next_job(queue.engine, "worker") == run_id
    process_job(run_id)
    assert [value for kind, value in queue.events if kind == "collect"] == INTERLEAVED
    assert not queue.active
