from contextlib import contextmanager
from time import sleep

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
