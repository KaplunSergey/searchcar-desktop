from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.database import Base, create_database_engine
from app.main import cancel_scan, enqueue, scan_projects
from app.models import Project, ProjectScanRun, ScanRun, User
from app.schemas import CancelScanIn, ScanProjectsIn


def database(tmp_path: Path):
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'launch-plan.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    return engine


def owner(db: Session, name: str = "owner") -> User:
    record = User(
        username=name,
        username_key=name,
        password_hash="test-only",
        role="USER",
        status="ACTIVE",
        project_limit=None,
    )
    db.add(record)
    db.flush()
    return record


def project(
    db: Session,
    owner_id: int,
    name: str,
    *,
    source_key: str = "encar",
) -> Project:
    record = Project(
        owner_id=owner_id,
        source_key=source_key,
        name=name,
        name_key=name.casefold(),
        search_url=(
            f"https://www.encar.com/dc/dc_carsearchlist.do?project={name}"
            if source_key == "encar"
            else f"https://{source_key}.example.test/search/{name}"
        ),
        scan_mode="FAST",
        search_page_mode="FIRST_PAGE",
        price_filter_mode="LINK",
        price_filter_revision=3,
        price_filter_baseline_revision=2,
    )
    db.add(record)
    db.flush()
    return record


def test_explicit_project_plan_is_owned_and_frozen_at_enqueue(tmp_path: Path) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        selected = project(db, current.id, "Audi A5")
        original_url = selected.search_url

        result = scan_projects(
            ScanProjectsIn(project_ids=[selected.id, selected.id]),
            current,
            db,
        )
        run = db.get(ScanRun, result["id"])
        selected.name = "Edited after enqueue"
        selected.search_url = "https://www.encar.com/dc/dc_carsearchlist.do?edited=1"
        selected.scan_mode = "ACCURATE"
        db.commit()

        assert run.payload["project_ids"] == [selected.id]
        assert run.payload["source_keys"] == ["encar"]
        assert run.payload["launch_scope"] == {
            "mode": "PROJECTS",
            "project_ids": [selected.id],
            "source_keys": [],
        }
        assert run.payload["project_plans"] == [
            {
                "project_id": selected.id,
                "source_key": "encar",
                "name": "Audi A5",
                "search_url": original_url,
                "search_config": None,
                "scan_mode": "FAST",
                "search_page_mode": "FIRST_PAGE",
                "price_filter_mode": "LINK",
                "price_min_krw": None,
                "price_max_krw": None,
                "price_filter_revision": 3,
                "price_filter_baseline_revision": 2,
            }
        ]


@pytest.mark.parametrize(
    ("body", "scope_mode"),
    [
        (ScanProjectsIn(source_keys=["encar"]), "SITES"),
        (ScanProjectsIn(all_sources=True), "ALL_SITES"),
    ],
)
def test_site_scopes_include_only_owned_projects(
    tmp_path: Path,
    body: ScanProjectsIn,
    scope_mode: str,
) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        first = project(db, current.id, "First")
        second = project(db, current.id, "Second")
        another_owner = owner(db, "another")
        project(db, another_owner.id, "Foreign")

        result = scan_projects(body, current, db)
        run = db.get(ScanRun, result["id"])

        assert run.payload["project_ids"] == [first.id, second.id]
        assert run.payload["source_keys"] == ["encar"]
        assert run.payload["launch_scope"]["mode"] == scope_mode


def test_explicit_project_from_another_owner_is_rejected(tmp_path: Path) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        another_owner = owner(db, "another")
        foreign = project(db, another_owner.id, "Foreign")

        with pytest.raises(HTTPException) as caught:
            scan_projects(ScanProjectsIn(project_ids=[foreign.id]), current, db)

        assert caught.value.status_code == 404
        assert caught.value.detail == "project_not_found"
        assert db.query(ScanRun).count() == 0


def test_expected_site_cannot_silently_select_a_project_from_another_site(
    tmp_path: Path,
) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        selected = project(db, current.id, "Audi A5")

        with pytest.raises(HTTPException) as caught:
            scan_projects(
                ScanProjectsIn(
                    project_ids=[selected.id],
                    source_keys=["copart"],
                ),
                current,
                db,
            )

        assert caught.value.status_code == 409
        assert caught.value.detail == "project_source_mismatch"
        assert db.query(ScanRun).count() == 0


def test_project_for_an_unshipped_source_is_not_queued(tmp_path: Path) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        disabled = project(db, current.id, "Copart", source_key="copart")

        with pytest.raises(HTTPException) as caught:
            scan_projects(ScanProjectsIn(project_ids=[disabled.id]), current, db)

        assert caught.value.status_code == 422
        assert caught.value.detail == "unsupported_source"
        assert db.query(ScanRun).count() == 0


def test_batch_scan_skips_busy_project_and_queues_free_project(tmp_path: Path) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        busy = project(db, current.id, "Busy")
        free = project(db, current.id, "Free")
        active = ScanRun(
            owner_id=current.id,
            kind="PROJECTS",
            status="RUNNING",
            payload={"project_ids": [busy.id], "trigger": "MANUAL"},
        )
        db.add(active)
        db.commit()

        result = scan_projects(
            ScanProjectsIn(project_ids=[busy.id, free.id]),
            current,
            db,
        )

        queued = db.get(ScanRun, result["id"])
        assert result["created"] is True
        assert result["projects"] == [
            {
                "project_id": busy.id,
                "status": "ALREADY_ACTIVE",
                "scan_run_id": active.id,
            },
            {
                "project_id": free.id,
                "status": "QUEUED",
                "scan_run_id": queued.id,
            },
        ]
        assert queued.payload["project_ids"] == [free.id]
        assert [
            plan["project_id"] for plan in queued.payload["project_plans"]
        ] == [free.id]


def test_repeated_project_scan_returns_existing_run_without_duplicate(
    tmp_path: Path,
) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        selected = project(db, current.id, "Audi A5")
        active = ScanRun(
            owner_id=current.id,
            kind="PROJECTS",
            status="RUNNING",
            payload={"project_ids": [selected.id], "trigger": "MANUAL"},
        )
        db.add(active)
        db.commit()

        result = scan_projects(
            ScanProjectsIn(project_ids=[selected.id]),
            current,
            db,
        )

        assert result == {
            "id": active.id,
            "status": "ALREADY_ACTIVE",
            "created": False,
            "projects": [
                {
                    "project_id": selected.id,
                    "status": "ALREADY_ACTIVE",
                    "scan_run_id": active.id,
                }
            ],
        }
        assert db.query(ScanRun).count() == 1


def test_simultaneous_project_commands_create_only_one_run(tmp_path: Path) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        selected = project(db, current.id, "Audi A5")
        db.commit()
        owner_id = current.id
        project_id = selected.id

    ready = Barrier(2)

    def submit() -> dict:
        with Session(engine) as db:
            current = db.get(User, owner_id)
            ready.wait()
            return enqueue(
                "PROJECTS",
                {"project_ids": [project_id], "trigger": "MANUAL"},
                current,
                db,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))

    assert sorted(result["created"] for result in results) == [False, True]
    assert {result["id"] for result in results} == {results[0]["id"]}
    with Session(engine) as db:
        assert db.query(ScanRun).count() == 1


def test_manual_scan_removes_only_overlap_from_frozen_automatic_plan(
    tmp_path: Path,
) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        manual = project(db, current.id, "Manual")
        scheduled = project(db, current.id, "Scheduled")
        automatic = ScanRun(
            owner_id=current.id,
            kind="PROJECTS",
            status="QUEUED",
            payload={
                "project_ids": [manual.id, scheduled.id],
                "source_keys": ["encar"],
                "project_plans": [
                    {"project_id": manual.id, "source_key": "encar"},
                    {"project_id": scheduled.id, "source_key": "encar"},
                ],
                "trigger": "AUTOMATIC",
            },
        )
        db.add(automatic)
        db.commit()

        result = enqueue(
            "PROJECTS",
            {
                "project_ids": [manual.id],
                "source_keys": ["encar"],
                "project_plans": [
                    {
                        "project_id": manual.id,
                        "source_key": "encar",
                        "search_url": "https://www.encar.com/manual-config",
                    }
                ],
                "trigger": "MANUAL",
            },
            current,
            db,
        )

        db.refresh(automatic)
        assert automatic.status == "QUEUED"
        assert automatic.payload["project_ids"] == [scheduled.id]
        assert automatic.payload["project_plans"] == [
            {"project_id": scheduled.id, "source_key": "encar"}
        ]
        queued = db.get(ScanRun, result["id"])
        assert queued.payload["project_plans"][0]["search_url"].endswith(
            "manual-config"
        )


def test_queued_batch_can_cancel_one_project_without_cancelling_the_job(
    tmp_path: Path,
) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        cancelled = project(db, current.id, "Cancelled")
        remaining = project(db, current.id, "Remaining")
        run = ScanRun(
            owner_id=current.id,
            kind="PROJECTS",
            status="QUEUED",
            payload={
                "project_ids": [cancelled.id, remaining.id],
                "source_keys": ["encar"],
                "project_plans": [
                    {"project_id": cancelled.id, "source_key": "encar"},
                    {"project_id": remaining.id, "source_key": "encar"},
                ],
                "report": [{"project_id": remaining.id, "change": "NEW"}],
            },
        )
        db.add(run)
        db.commit()

        result = cancel_scan(
            run.id,
            CancelScanIn(project_ids=[cancelled.id]),
            current,
            db,
        )

        db.refresh(run)
        assert result["status"] == "QUEUED"
        assert result["projects"] == [
            {"project_id": cancelled.id, "status": "CANCELLED"}
        ]
        assert run.status == "QUEUED"
        assert run.payload["cancelled_project_ids"] == [cancelled.id]
        assert run.payload["project_statuses"] == {
            str(cancelled.id): "CANCELLED"
        }
        assert run.payload["report"] == [
            {"project_id": remaining.id, "change": "NEW"}
        ]
        cancelled_run = db.scalar(select(ProjectScanRun).where(
            ProjectScanRun.scan_run_id == run.id,
            ProjectScanRun.project_id == cancelled.id,
        ))
        assert cancelled_run is not None
        assert cancelled_run.status == "CANCELLED"
        requeued = enqueue(
            "PROJECTS",
            {"project_ids": [cancelled.id], "trigger": "MANUAL"},
            current,
            db,
        )
        assert requeued["created"] is True


def test_project_cancellation_survives_stale_worker_progress_payload(tmp_path: Path) -> None:
    from app.worker import ProjectScanCancelled, _raise_if_project_cancelled

    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        selected = project(db, current.id, "Selected")
        remaining = project(db, current.id, "Remaining")
        run = ScanRun(owner_id=current.id, kind="PROJECTS", status="RUNNING",
                      payload={"project_ids": [selected.id, remaining.id]})
        db.add(run)
        db.flush()
        db.add(ProjectScanRun(scan_run_id=run.id, project_id=selected.id, status="RUNNING"))
        db.commit()
        run_id, owner_id, project_id = run.id, current.id, selected.id

    with Session(engine) as worker_db, Session(engine) as request_db:
        stale_run = worker_db.get(ScanRun, run_id)
        stale_payload = dict(stale_run.payload)
        cancel_scan(run_id, CancelScanIn(project_ids=[project_id]),
                    request_db.get(User, owner_id), request_db)
        # A progress commit started before the request must not lose its stop signal.
        stale_run.payload = {**stale_payload, "progress_note": "details"}
        worker_db.commit()
        with pytest.raises(ProjectScanCancelled):
            _raise_if_project_cancelled(worker_db, stale_run, project_id)


@pytest.mark.parametrize(("scope", "status_code", "error"), [
    (CancelScanIn(project_ids=[999]), 404, "project_not_in_scan"),
    (CancelScanIn(source_keys=["absent"]), 404, "source_not_in_scan"),
    (CancelScanIn(project_ids=[999], source_keys=["encar"]), 422, "ambiguous_cancel_scope"),
    (CancelScanIn(project_ids=[999], all_projects=True), 422, "ambiguous_cancel_scope"),
])
def test_invalid_cancel_scope_does_not_mutate_job(tmp_path, scope, status_code, error):
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        selected = project(db, current.id, "Selected")
        run = ScanRun(owner_id=current.id, kind="PROJECTS", status="QUEUED",
                      payload={"project_ids": [selected.id]})
        db.add(run)
        db.commit()
        with pytest.raises(HTTPException) as caught:
            cancel_scan(run.id, scope, current, db)
        assert (caught.value.status_code, caught.value.detail) == (status_code, error)
        db.refresh(run)
        assert run.status == "QUEUED"
        assert "cancelled_project_ids" not in run.payload


def test_foreign_scan_cannot_be_cancelled(tmp_path):
    engine = database(tmp_path)
    with Session(engine) as db:
        current, foreign = owner(db), owner(db, "foreign")
        run = ScanRun(owner_id=foreign.id, kind="PROJECTS", status="RUNNING", payload={})
        db.add(run)
        db.commit()
        with pytest.raises(HTTPException) as caught:
            cancel_scan(run.id, None, current, db)
        assert caught.value.status_code == 404
        db.refresh(run)
        assert run.status == "RUNNING"


def test_driver_start_failure_terminalizes_pending_projects(tmp_path, monkeypatch):
    from app.worker import process_job

    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        selected = project(db, current.id, "Selected")
        run = ScanRun(owner_id=current.id, kind="PROJECTS", status="QUEUED",
                      payload={"project_ids": [selected.id]})
        db.add(run)
        db.commit()
        run_id, project_id = run.id, selected.id

    def unavailable():
        raise RuntimeError("Playwright driver unavailable")

    monkeypatch.setattr("app.worker.SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr("app.worker.desktop_playwright", unavailable)
    monkeypatch.setattr("app.worker.publish_scan_update", lambda *_: None)
    process_job(run_id)
    with Session(engine) as db:
        run = db.get(ScanRun, run_id)
        portion = db.scalar(select(ProjectScanRun).where(ProjectScanRun.scan_run_id == run_id))
        assert run.status == "FAILED"
        assert portion.status == "FAILED"
        assert run.payload["project_statuses"] == {str(project_id): "FAILED"}


def test_running_batch_can_cancel_one_site_and_keep_completed_results(
    tmp_path: Path,
) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        current = owner(db)
        completed = project(db, current.id, "Completed")
        cancelled = project(db, current.id, "Copart", source_key="copart")
        remaining = project(db, current.id, "Remaining")
        run = ScanRun(
            owner_id=current.id,
            kind="PROJECTS",
            status="RUNNING",
            payload={
                "project_ids": [completed.id, cancelled.id, remaining.id],
                "source_keys": ["encar", "copart"],
                "project_plans": [
                    {"project_id": completed.id, "source_key": "encar"},
                    {"project_id": cancelled.id, "source_key": "copart"},
                    {"project_id": remaining.id, "source_key": "encar"},
                ],
                "report": [{"project_id": completed.id, "change": "NEW"}],
            },
        )
        db.add(run)
        db.flush()
        db.add_all(
            [
                ProjectScanRun(
                    scan_run_id=run.id,
                    project_id=completed.id,
                    status="SUCCEEDED",
                ),
                ProjectScanRun(
                    scan_run_id=run.id,
                    project_id=cancelled.id,
                    status="QUEUED",
                ),
                ProjectScanRun(
                    scan_run_id=run.id,
                    project_id=remaining.id,
                    status="QUEUED",
                ),
            ]
        )
        db.commit()

        result = cancel_scan(
            run.id,
            CancelScanIn(source_keys=["copart"]),
            current,
            db,
        )

        db.refresh(run)
        project_runs = {
            item.project_id: item
            for item in db.scalars(
                select(ProjectScanRun).where(ProjectScanRun.scan_run_id == run.id)
            )
        }
        assert result["projects"] == [
            {"project_id": cancelled.id, "status": "CANCELLED"}
        ]
        assert run.status == "RUNNING"
        assert run.payload["report"] == [
            {"project_id": completed.id, "change": "NEW"}
        ]
        assert project_runs[completed.id].status == "SUCCEEDED"
        assert project_runs[cancelled.id].status == "CANCELLED"
        assert project_runs[remaining.id].status == "QUEUED"
