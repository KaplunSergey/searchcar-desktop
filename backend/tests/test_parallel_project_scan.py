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
        "app.worker.collect_search_pages",
        lambda *_args, **_kwargs: SearchCollection(
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
        ),
    )

    @contextmanager
    def fake_detail_reader(_storage):
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

    monkeypatch.setattr("app.worker._encar_detail_reader", fake_detail_reader)

    from app.worker import process_job

    process_job(run_id)

    with session_factory() as db:
        run = db.get(ScanRun, run_id)
        cars = list(db.scalars(select(Car).order_by(Car.canonical_encar_id)))
        assert run.status == "SUCCEEDED"
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
