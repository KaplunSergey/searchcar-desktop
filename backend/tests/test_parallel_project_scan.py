from contextlib import contextmanager
from time import sleep
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    Car,
    Project,
    ProjectCar,
    ScanRun,
    SchedulerSetting,
    User,
)
from app.sources.contracts import SearchCollection, SourceDescriptor


class _FakeContext:
    def new_page(self):
        return object()

    def close(self):
        pass


class _FakeBrowser:
    def new_context(self, **_kwargs):
        return _FakeContext()

    def close(self):
        pass


class _FakePlaywright:
    class chromium:
        @staticmethod
        def launch(**_kwargs):
            return _FakeBrowser()


@contextmanager
def _fake_playwright():
    yield _FakePlaywright()


def test_project_scan_saves_parallel_detail_results_in_completion_order(
    tmp_path, monkeypatch
):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'parallel.sqlite3'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        owner = User(
            username="desktop",
            username_key="desktop",
            password_hash="test-only",
            role="USER",
            status="ACTIVE",
            project_limit=10,
        )
        db.add(owner)
        db.flush()
        project = Project(
            owner_id=owner.id,
            name="Parallel test",
            name_key="parallel test",
            search_url="https://www.encar.com/dc/dc_carsearchlist.do",
            scan_mode="ACCURATE",
            search_page_mode="ALL_PAGES",
            price_filter_revision=1,
            price_filter_baseline_revision=1,
        )
        db.add(project)
        db.flush()
        missing = Car(
            canonical_encar_id="missing",
            url="https://fem.encar.com/cars/detail/missing",
            title="Missing",
            details={"mileage_km": 20_000},
            current_price=20_000_000,
            status="UPDATED",
        )
        db.add(missing)
        db.flush()
        db.add(
            ProjectCar(
                project_id=project.id,
                car_id=missing.id,
                search_status="FOUND",
                tracking_enabled=True,
            )
        )
        db.add(SchedulerSetting(user_id=owner.id, performance_mode="FAST"))
        run = ScanRun(
            owner_id=owner.id,
            kind="PROJECTS",
            status="QUEUED",
            payload={"project_ids": [project.id], "trigger": "MANUAL"},
        )
        db.add(run)
        db.commit()
        run_id = run.id

    monkeypatch.setattr("app.worker.SessionLocal", session_factory)
    monkeypatch.setattr("app.worker.desktop_playwright", _fake_playwright)
    progress_samples = []

    def record_progress(_owner_id, current_run_id):
        with session_factory() as snapshot_db:
            current = snapshot_db.get(ScanRun, current_run_id)
            progress_samples.append((current.status, current.progress))

    monkeypatch.setattr("app.worker.publish_scan_update", record_progress)
    monkeypatch.setattr(
        "app.desktop_license.require_search_entitlement", lambda *_args: None
    )

    from app.scanner import SearchCollection

    monkeypatch.setattr(
        "app.sources.encar.EncarSource.collect_search_pages",
        staticmethod(lambda *_args, **_kwargs: SearchCollection(
            rows={
                "slow": {
                    "source_car_id": "slow",
                    "url": "https://fem.encar.com/cars/detail/slow",
                    "source": "Parallel test",
                    "list_text": "slow 20,000 km 2024/01",
                },
                "fast": {
                    "source_car_id": "fast",
                    "url": "https://fem.encar.com/cars/detail/fast",
                    "source": "Parallel test",
                    "list_text": "fast 20,000 km 2024/01",
                },
            },
            pages_visited=1,
            total_pages=1,
            total_results=2,
        )),
    )

    @contextmanager
    def fake_detail_reader(_self, _storage, **_kwargs):
        def read(row, _known_price):
            if row["source_car_id"] == "slow":
                sleep(0.7)
            source_id = row["source_car_id"]
            return {
                "canonical_car_id": source_id,
                "source_car_id": source_id,
                "source": "Parallel test",
                "source_url": row["url"],
                "url": row["url"],
                "title": source_id.title(),
                "price_krw": 20_000_000,
                "price_source": "DETAIL_PRIMARY",
                "mileage_km": 20_000,
                "options": {},
                "condition": {},
                "sold": False,
                "unavailable": False,
                "fingerprint": source_id,
            }

        yield read

    monkeypatch.setattr("app.sources.encar.EncarSource.detail_reader", fake_detail_reader)

    from app.worker import process_job

    process_job(run_id)

    with session_factory() as db:
        run = db.get(ScanRun, run_id)
        cars = list(db.scalars(select(Car).order_by(Car.canonical_encar_id)))
        assert run.status == "SUCCEEDED"
        from app.version import APP_VERSION
        assert run.payload["execution_versions"] == {
            "app": APP_VERSION,
            "sources": {"encar": {"adapter": 1, "contract": 1}},
        }
        assert next(iter(run.payload["detail_progress"].values())) == {
            "completed": 3,
            "total": 3,
            "checking": 0,
        }
        assert [car.canonical_encar_id for car in cars] == [
            "fast",
            "missing",
            "slow",
        ]
        assert [item["encar_id"] for item in run.payload["report"]] == [
            "fast",
            "slow",
            "missing",
        ]
        assert ("RUNNING", 100) not in progress_samples


def test_project_scan_executes_the_configuration_frozen_at_enqueue(
    tmp_path, monkeypatch
):
    from app.models import ProjectScanRun
    from app.scanner import SearchCollection
    from app.worker import process_job

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'frozen-plan.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    original_url = "https://www.encar.com/dc/dc_carsearchlist.do?frozen=1"
    with factory() as db:
        owner = User(
            username="owner",
            username_key="owner",
            password_hash="test",
            role="USER",
            status="ACTIVE",
        )
        db.add(owner)
        db.flush()
        project = Project(
            owner_id=owner.id,
            name="Frozen name",
            name_key="frozen name",
            search_url=original_url,
            scan_mode="FAST",
            search_page_mode="FIRST_PAGE",
            price_filter_revision=1,
            price_filter_baseline_revision=1,
        )
        db.add(project)
        db.flush()
        baseline = ScanRun(owner_id=owner.id, kind="PROJECTS", status="SUCCEEDED")
        db.add(baseline)
        db.flush()
        db.add(
            ProjectScanRun(
                scan_run_id=baseline.id,
                project_id=project.id,
                status="SUCCEEDED",
            )
        )
        run = ScanRun(
            owner_id=owner.id,
            kind="PROJECTS",
            status="QUEUED",
            payload={
                "project_ids": [project.id],
                "source_keys": ["encar"],
                "project_plans": [
                    {
                        "project_id": project.id,
                        "source_key": "encar",
                        "name": project.name,
                        "search_url": project.search_url,
                        "scan_mode": project.scan_mode,
                        "search_page_mode": project.search_page_mode,
                        "price_filter_mode": "LINK",
                        "price_min_krw": None,
                        "price_max_krw": None,
                        "price_filter_revision": 1,
                        "price_filter_baseline_revision": 1,
                    }
                ],
            },
        )
        db.add(run)
        db.flush()
        run_id = run.id
        project.name = "Edited name"
        project.search_url = (
            "https://www.encar.com/dc/dc_carsearchlist.do?edited=1"
        )
        project.scan_mode = "ACCURATE"
        project.search_page_mode = "ALL_PAGES"
        db.commit()

    monkeypatch.setattr("app.worker.SessionLocal", factory)
    monkeypatch.setattr("app.worker.desktop_playwright", _fake_playwright)
    monkeypatch.setattr("app.worker.publish_scan_update", lambda *_: None)
    monkeypatch.setattr(
        "app.desktop_license.require_search_entitlement", lambda *_: None
    )
    calls = []

    def collect(_page, url, name="", **kwargs):
        calls.append((url, name, kwargs["all_pages"]))
        return SearchCollection({}, 1, 1, 0)

    monkeypatch.setattr(
        "app.sources.encar.EncarSource.collect_search_pages",
        staticmethod(collect),
    )

    process_job(run_id)

    with factory() as db:
        run = db.get(ScanRun, run_id)
        assert run.status == "SUCCEEDED", run.error
    assert calls == [(original_url, "Frozen name", False)]


@pytest.mark.parametrize("failure_phase", ["descriptor", "open", "captcha", "cancelled_then_failure"])
def test_source_failure_does_not_block_another_source(
    tmp_path, monkeypatch, failure_phase
):
    from app.models import ProjectScanRun
    from app.scanner import CaptchaError, TimeoutScanError
    from app.worker import process_job

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'sources.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        owner = User(
            username="owner",
            username_key="owner",
            password_hash="test",
            role="USER",
            status="ACTIVE",
        )
        db.add(owner)
        db.flush()
        blocked = Project(
            owner_id=owner.id,
            source_key="blocked",
            name="Blocked",
            name_key="blocked",
            search_url="https://blocked.example/search",
        )
        healthy = Project(
            owner_id=owner.id,
            source_key="healthy",
            name="Healthy",
            name_key="healthy",
            search_url="https://healthy.example/search",
        )
        db.add_all([blocked, healthy])
        db.flush()
        selected = [blocked, healthy]
        if failure_phase == "captcha":
            blocked_again = Project(
                owner_id=owner.id, source_key="blocked", name="Blocked again",
                name_key="blocked again", search_url="https://blocked.example/other",
            )
            db.add(blocked_again)
            db.flush()
            selected.append(blocked_again)
        plans = [
            {
                "project_id": project.id,
                "source_key": project.source_key,
                "name": project.name,
                "search_url": project.search_url,
                "scan_mode": "FAST",
                "search_page_mode": "FIRST_PAGE",
                "price_filter_mode": "LINK",
                "price_min_krw": None,
                "price_max_krw": None,
                "price_filter_revision": 1,
                "price_filter_baseline_revision": 1,
            }
            for project in selected
        ]
        run = ScanRun(
            owner_id=owner.id,
            kind="PROJECTS",
            status="QUEUED",
            payload={
                "project_ids": [project.id for project in selected],
                "source_keys": ["blocked", "healthy"],
                "project_plans": plans,
                "cancelled_project_ids": [blocked.id] if failure_phase == "cancelled_then_failure" else [],
            },
        )
        db.add(run)
        db.commit()
        run_id = run.id
        project_ids = [project.id for project in selected]

    descriptors = {
        key: SourceDescriptor(
            key=key,
            family="AUCTION",
            domains=(f"{key}.example",),
            capabilities=frozenset({"SEARCH"}),
        )
        for key in ("blocked", "healthy")
    }
    opened = []
    closed = []

    class Adapter:
        def __init__(self, key):
            self.descriptor = descriptors[key]

        def open_browser(self, *_args, **_kwargs):
            key = self.descriptor.key
            opened.append(key)
            if self.descriptor.key == "blocked" and failure_phase == "open":
                raise RuntimeError("source blocked")
            if key == "healthy" and failure_phase == "captcha":
                assert closed == ["context:blocked", "browser:blocked"]
            browser = SimpleNamespace(close=lambda: closed.append(f"browser:{key}"))
            context = SimpleNamespace(close=lambda: closed.append(f"context:{key}"))
            return browser, context, SimpleNamespace(source_key=key)

        def collect_search_pages(self, page, *_args, **_kwargs):
            assert page.source_key == self.descriptor.key
            if self.descriptor.key == "blocked" and failure_phase == "captcha":
                raise CaptchaError("source blocked")
            if failure_phase == "cancelled_then_failure":
                raise TimeoutScanError("source timed out")
            return SearchCollection({}, 1, 1, 0)

    adapters = {key: Adapter(key) for key in descriptors}
    monkeypatch.setattr("app.worker.SessionLocal", factory)
    monkeypatch.setattr("app.worker.desktop_playwright", _fake_playwright)
    monkeypatch.setattr("app.worker.publish_scan_update", lambda *_: None)
    def descriptor(key):
        if key == "blocked" and failure_phase == "descriptor":
            raise ValueError("Adapter no longer shipped")
        return descriptors[key]

    monkeypatch.setattr("app.worker.get_source_descriptor", descriptor)
    monkeypatch.setattr("app.worker.get_source", lambda key: adapters[key])
    monkeypatch.setattr(
        "app.desktop_license.require_search_entitlement", lambda *_: None
    )

    process_job(run_id)

    with factory() as db:
        run = db.get(ScanRun, run_id)
        statuses = {
            item.project_id: item.status
            for item in db.scalars(
                select(ProjectScanRun).where(ProjectScanRun.scan_run_id == run_id)
            )
        }
        assert run.status == ("FAILED" if failure_phase == "cancelled_then_failure" else "PARTIAL"), run.error
        assert statuses == {
            project_ids[0]: "CANCELLED" if failure_phase == "cancelled_then_failure" else "FAILED",
            project_ids[1]: "FAILED" if failure_phase == "cancelled_then_failure" else "SUCCEEDED",
            **({project_ids[2]: "FAILED"} if failure_phase == "captcha" else {}),
        }
        assert opened == (["healthy"] if failure_phase in {"descriptor", "cancelled_then_failure"}
                          else ["blocked", "healthy"])
        assert run.payload["failures"][0]["source_key"] == (
            "healthy" if failure_phase == "cancelled_then_failure" else "blocked"
        )
        assert closed[-2:] == ["context:healthy", "browser:healthy"]
        if failure_phase == "captcha":
            assert closed[:2] == ["context:blocked", "browser:blocked"]


@pytest.mark.parametrize("scope", ["project", "batch"])
def test_cancellation_during_search_preserves_results_and_closes_runtime(
    tmp_path, monkeypatch, scope
):
    from app.main import cancel_scan
    from app.models import ProjectScanRun
    from app.schemas import CancelScanIn
    from app.sources.encar import EncarSource
    from app.worker import process_job
    from test_money_values import detail

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'cancellation.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        owner = User(
            username="owner", username_key="owner", password_hash="test",
            role="USER", status="ACTIVE",
        )
        db.add(owner)
        db.flush()
        projects = [
            Project(
                owner_id=owner.id, name=name, name_key=name.lower(),
                search_url=f"https://www.encar.com/dc/dc_carsearchlist.do?name={name}",
                price_filter_revision=1, price_filter_baseline_revision=1,
            )
            for name in ("Completed", "Cancelled", "Remaining")
        ]
        db.add_all(projects)
        db.flush()
        project_ids = [project.id for project in projects]
        run = ScanRun(
            owner_id=owner.id, kind="PROJECTS", status="QUEUED",
            payload={"project_ids": project_ids, "trigger": "MANUAL"},
        )
        db.add(run)
        db.commit()
        run_id, owner_id = run.id, owner.id

    calls, closed = [], []

    def open_browser(*_args, **_kwargs):
        return (
            SimpleNamespace(close=lambda: closed.append("browser")),
            SimpleNamespace(close=lambda: closed.append("context")),
            object(),
        )

    def collect(_page, _url, name, **kwargs):
        calls.append(name)
        if name == "Cancelled":
            with factory() as request_db:
                cancel_scan(
                    run_id,
                    CancelScanIn(project_ids=[project_ids[1]])
                    if scope == "project" else None,
                    request_db.get(User, owner_id),
                    request_db,
                )
            kwargs["checkpoint"]()
            raise AssertionError("Cancelled search must stop at its checkpoint")
        if name == "Completed":
            return SearchCollection({"42688672": {
                "source_car_id": "42688672",
                "url": "https://fem.encar.com/cars/detail/42688672",
                "list_text": "Audi 2,600만원",
            }}, 1, 1, 1)
        return SearchCollection({}, 1, 1, 0)

    @contextmanager
    def reader(*_args, **_kwargs):
        yield lambda *_: detail(26_000_000)

    monkeypatch.setattr("app.worker.SessionLocal", factory)
    monkeypatch.setattr("app.worker.desktop_playwright", _fake_playwright)
    monkeypatch.setattr("app.worker.publish_scan_update", lambda *_: None)
    monkeypatch.setattr("app.desktop_license.require_search_entitlement", lambda *_: None)
    monkeypatch.setattr(EncarSource, "open_browser", staticmethod(open_browser))
    monkeypatch.setattr(EncarSource, "collect_search_pages", staticmethod(collect))
    monkeypatch.setattr(EncarSource, "detail_reader", reader)

    process_job(run_id)

    with factory() as db:
        run = db.get(ScanRun, run_id)
        project_statuses = {
            item.project_id: item.status
            for item in db.scalars(select(ProjectScanRun).where(
                ProjectScanRun.scan_run_id == run_id
            ))
        }
        assert run.status == ("PARTIAL" if scope == "project" else "CANCELLED"), run.error
        assert project_statuses == {
            project_ids[0]: "SUCCEEDED",
            project_ids[1]: "CANCELLED",
            project_ids[2]: "SUCCEEDED" if scope == "project" else "CANCELLED",
        }
        assert [item["project_id"] for item in run.payload["report"]] == [project_ids[0]]
        assert run.payload["report"][0]["change"] == "NEW"
        assert db.scalar(select(Car.id)) is not None
    assert calls == (["Completed", "Cancelled", "Remaining"] if scope == "project"
                     else ["Completed", "Cancelled"])
    # Details release the first runtime. Cancellation releases the second;
    # partial cancellation opens a clean third runtime for Remaining.
    assert closed == ["context", "browser"] * (3 if scope == "project" else 2)


def test_fast_queue_preserves_main_in_two_projects_without_extra_detail_browser(tmp_path, monkeypatch):
    from app.models import CarImage, ProjectScanRun
    from app.scanner import SearchCollection
    from app.services import upsert_detail
    from test_current_images import png
    from test_money_values import detail

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'fast-images.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.worker.SessionLocal", factory)
    monkeypatch.setattr("app.worker.desktop_playwright", _fake_playwright)
    monkeypatch.setattr("app.worker.publish_scan_update", lambda *_: None)
    monkeypatch.setattr("app.desktop_license.require_search_entitlement", lambda *_: None)
    monkeypatch.setattr("app.worker.settings.storage_root", str(tmp_path / "storage"))
    with factory() as db:
        owner = User(username="owner", username_key="owner", password_hash="test", role="USER", status="ACTIVE")
        db.add(owner)
        db.flush()
        projects = [Project(owner_id=owner.id, name=f"Test {i}", name_key=f"test {i}",
                            search_url=f"https://www.encar.com/dc/dc_carsearchlist.do?test={i}",
                            scan_mode="FAST", search_page_mode="FIRST_PAGE",
                            price_filter_revision=1, price_filter_baseline_revision=1) for i in range(2)]
        db.add_all(projects)
        db.flush()
        car = upsert_detail(db, projects[0], {**detail(26000000), "_images": {"MAIN": png()}})
        db.add(ProjectCar(project_id=projects[1].id, car_id=car.id, tracking_enabled=True, search_status="FOUND"))
        db.add(SchedulerSetting(user_id=owner.id, performance_mode="FAST"))
        # A seeded car alone isn't a completed baseline. The first scan is
        # intentionally accurate, regardless of the configured FAST mode.
        baseline = ScanRun(owner_id=owner.id, kind="PROJECTS", status="SUCCEEDED", payload={})
        db.add(baseline)
        db.flush()
        db.add_all([ProjectScanRun(scan_run_id=baseline.id, project_id=p.id, status="SUCCEEDED") for p in projects])
        run = ScanRun(owner_id=owner.id, kind="PROJECTS", status="QUEUED", payload={"project_ids": [p.id for p in projects]})
        db.add(run)
        db.commit()
        run_id, car_id = run.id, car.id
    monkeypatch.setattr("app.sources.encar.EncarSource.collect_search_pages", staticmethod(lambda *args, **kwargs: SearchCollection(
        rows={"42688672": {"source_car_id": "42688672", "url": "https://fem.encar.com/cars/detail/42688672",
                           "list_text": "Audi 2,600만원", "_images": {"MAIN": png(1)}}},
        pages_visited=1, total_pages=1, total_results=1,
    )))
    def no_detail(*args, **kwargs):
        raise AssertionError("Unchanged FAST listing must not open another browser for the photo")
    monkeypatch.setattr("app.sources.encar.EncarSource.detail_reader", no_detail)
    from app.worker import process_job
    process_job(run_id)
    with factory() as db:
        run = db.get(ScanRun, run_id)
        assert run.status == "SUCCEEDED", run.error
        images = list(db.scalars(select(CarImage).where(CarImage.car_id == car_id, CarImage.kind == "MAIN")))
        assert len(images) == 1
        assert (tmp_path / "storage" / images[0].payload["path"]).read_bytes() == png()
        assert len(list((tmp_path / "storage").rglob("main-*"))) == 1
        assert len(list(db.scalars(select(ProjectCar).where(ProjectCar.car_id == car_id)))) == 2
