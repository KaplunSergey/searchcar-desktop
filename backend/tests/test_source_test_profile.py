import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, Settings, create_database_engine, settings
from app.desktop_runtime import configure_desktop_environment, create_desktop_app
from app.main import enqueue
from app.models import Project, ScanRun, ScheduledProject, SchedulerSetting, User
from app.worker import enqueue_scheduled, process_job


@pytest.fixture
def isolated_environment(monkeypatch):
    # configure_desktop_environment changes these; do not leak a profile to other tests.
    for key in ("DATABASE_URL", "STORAGE_ROOT", "SEARCHCAR_DESKTOP_DATA_DIR",
                "AUTH_COOKIE_NAME", "AUTH_CSRF_COOKIE_NAME", "AUTH_HASH_SECRET",
                "ALLOWED_ORIGINS", "SEARCHCAR_SOURCE_TEST_MODE",
                "SEARCHCAR_TELEGRAM_RELAY_URL", "SEARCHCAR_TELEGRAM_DELIVERY_ENABLED",
                "SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED"):
        if key in os.environ:
            monkeypatch.setenv(key, os.environ[key])
        else:
            # Record an originally absent key before native main sets it directly.
            monkeypatch.setenv(key, "")
            monkeypatch.delenv(key)
    return monkeypatch


def test_opt_in_uses_separate_database_storage_and_secret(tmp_path, isolated_environment):
    isolated_environment.setenv("SEARCHCAR_SOURCE_TEST_MODE", "1")
    working = tmp_path / "SearchCar"
    (working / "data").mkdir(parents=True)
    (working / "data" / "searchcar.sqlite3").write_bytes(b"working database")
    (working / "runtime-auth-secret").write_text("working secret")
    paths = configure_desktop_environment(working, 9876)

    assert paths["root"] == working / "source-tests"
    assert paths["database"] == paths["root"] / "data" / "searchcar.sqlite3"
    assert paths["storage"] == paths["root"] / "storage"
    assert os.environ["AUTH_HASH_SECRET"] != "working secret"
    assert (working / "data" / "searchcar.sqlite3").read_bytes() == b"working database"
    assert (working / "runtime-auth-secret").read_text() == "working secret"
    assert (paths["root"] / ".source-test-profile").is_file()


def test_normal_profile_keeps_existing_paths(tmp_path, isolated_environment):
    isolated_environment.delenv("SEARCHCAR_SOURCE_TEST_MODE", raising=False)
    paths = configure_desktop_environment(tmp_path / "SearchCar", 9876)
    assert paths["root"] == tmp_path / "SearchCar"
    assert paths["storage"] == tmp_path / "SearchCar" / "storage"


def test_native_serve_flag_reopens_existing_auction_profile_without_environment(tmp_path, isolated_environment):
    """Finder launches must not depend on an export in the developer's shell."""
    import app.desktop_runtime as runtime

    isolated_environment.setenv("SEARCHCAR_SOURCE_TEST_MODE", "1")
    root = tmp_path / "SearchCar"
    original = configure_desktop_environment(root, 9876)
    original["database"].write_bytes(b"existing auction projects")
    isolated_environment.delenv("SEARCHCAR_SOURCE_TEST_MODE")
    isolated_environment.setattr("sys.argv", ["searchcar-core", "serve",
        "--data-dir", str(root), "--frontend-dir", str(tmp_path), "--port", "9876",
        "--source-test-mode"])
    selected = {}

    class Configured(Exception):
        pass

    def capture(*args):
        selected.update(configure_desktop_environment(*args))
        raise Configured

    isolated_environment.setattr(runtime, "configure_desktop_environment", capture)
    with pytest.raises(Configured):
        runtime.main()

    assert selected["root"] == original["root"]
    assert selected["database"].read_bytes() == b"existing auction projects"
    assert not (root / "data").exists()
    assert Settings(_env_file=None).source_test_mode is True


@pytest.mark.parametrize("mode", ["yes", "true", "2", ""])
def test_invalid_opt_in_fails_before_opening_working_data(tmp_path, isolated_environment, mode):
    isolated_environment.setenv("SEARCHCAR_SOURCE_TEST_MODE", mode)
    root = tmp_path / "SearchCar"
    with pytest.raises(ValueError, match="source_test_mode_invalid"):
        configure_desktop_environment(root, 9876)
    assert not root.exists()


@pytest.mark.parametrize("existing", ["symlink", "unmarked", "wrong_marker", "storage_symlink", "database_symlink"])
def test_experiment_never_adopts_an_existing_or_redirected_profile(tmp_path, isolated_environment, existing):
    isolated_environment.setenv("SEARCHCAR_SOURCE_TEST_MODE", "1")
    working = tmp_path / "SearchCar"
    working.mkdir()
    profile = working / "source-tests"
    if existing == "symlink":
        profile.symlink_to(working, target_is_directory=True)
    elif existing == "unmarked":
        profile.mkdir()
        (profile / "working.sqlite3").write_bytes(b"keep")
    else:
        paths = configure_desktop_environment(working, 9876)
        if existing == "wrong_marker":
            (profile / ".source-test-profile").write_text("other profile")
        elif existing == "storage_symlink":
            paths["storage"].rmdir()  # Empty disposable fixture only.
            paths["storage"].symlink_to(working, target_is_directory=True)
        else:
            paths["database"].symlink_to(working / "working.sqlite3")
    with pytest.raises(ValueError, match="source_test_profile_"):
        configure_desktop_environment(working, 9876)
    assert not (working / "data").exists()


def test_settings_reject_flag_without_isolated_database(tmp_path, isolated_environment):
    from app.source_test_profile import SourceTestModeError
    isolated_environment.setenv("SEARCHCAR_SOURCE_TEST_MODE", "1")
    paths = configure_desktop_environment(tmp_path / "SearchCar", 9876)
    assert Settings(_env_file=None).source_test_mode is True
    isolated_environment.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'working.sqlite3'}")
    with pytest.raises(SourceTestModeError, match="SOURCE_TEST_ISOLATION_REQUIRED"):
        Settings(_env_file=None)
    assert paths["database"].parent == paths["root"] / "data"


@pytest.fixture
def profile_queue(tmp_path, isolated_environment):
    isolated_environment.setenv("SEARCHCAR_SOURCE_TEST_MODE", "1")
    paths = configure_desktop_environment(tmp_path / "SearchCar", 9876)
    engine = create_database_engine(os.environ["DATABASE_URL"])
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    config = SimpleNamespace(**{**settings.model_dump(), "source_test_mode": True,
        "database_url": os.environ["DATABASE_URL"], "storage_root": str(paths["storage"])})
    isolated_environment.setattr("app.main.settings", config)
    isolated_environment.setattr("app.worker.settings", config)
    isolated_environment.setattr("app.worker.SessionLocal", factory)
    isolated_environment.setattr("app.worker.publish_scan_update", lambda *_: None)
    with factory() as db:
        user = User(username="test", username_key="test", password_hash="test",
                    role="USER", status="ACTIVE", project_limit=None)
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, name="Encar test", name_key="encar test",
            source_key="encar", auto_update=True,
            search_url="https://www.encar.com/dc/dc_carsearchlist.do")
        db.add(project)
        db.commit()
        user_id, project_id = user.id, project.id
    yield SimpleNamespace(factory=factory, user_id=user_id, project_id=project_id,
                          settings=config, paths=paths, monkeypatch=isolated_environment)
    engine.dispose()


@pytest.mark.parametrize("payload", [
    {"trigger": "AUTOMATIC"}, {"trigger": "MANUAL", "scheduled": True}, {},
])
def test_test_profile_refuses_automatic_or_implicit_commands(profile_queue, payload):
    with profile_queue.factory() as db:
        with pytest.raises(HTTPException) as caught:
            enqueue("PROJECTS", {**payload, "project_ids": [profile_queue.project_id]},
                    db.get(User, profile_queue.user_id), db)
        assert (caught.value.status_code, caught.value.detail) == (409, "source_test_manual_only")
        assert db.scalar(select(ScanRun)) is None


def test_explicit_manual_command_still_uses_common_queue(profile_queue):
    with profile_queue.factory() as db:
        result = enqueue("PROJECTS", {"trigger": "MANUAL", "project_ids": [profile_queue.project_id]},
                         db.get(User, profile_queue.user_id), db)
        assert result["created"] is True
        run = db.get(ScanRun, result["id"])
        assert run.status == "QUEUED"
        assert run.payload["source_test_mode"] is True


def test_profile_does_not_grant_license_or_register_unshipped_adapters(profile_queue):
    from app.desktop_license import SearchEntitlementError
    from app.sources.registry import get_source

    with pytest.raises(ValueError, match="Unsupported listing source"):
        get_source("not-shipped")

    def denied(*_args):
        raise SearchEntitlementError("LICENSE_SOURCE_NOT_ALLOWED")

    profile_queue.monkeypatch.setattr("app.desktop_license.require_search_entitlement", denied)
    with profile_queue.factory() as db:
        with pytest.raises(HTTPException) as caught:
            enqueue("PROJECTS", {"trigger": "MANUAL", "project_ids": [profile_queue.project_id]},
                    db.get(User, profile_queue.user_id), db)
        assert (caught.value.status_code, caught.value.detail) == (402, "license_source_not_allowed")
        assert db.scalar(select(ScanRun)) is None


def test_queue_checks_actual_database_not_only_configuration(profile_queue, tmp_path):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'working.sqlite3'}")
    with Session(engine) as db:
        with pytest.raises(HTTPException) as caught:
            enqueue("PROJECTS", {"trigger": "MANUAL"}, None, db)
        assert (caught.value.status_code, caught.value.detail) == (409, "source_test_isolation_required")
    engine.dispose()


def test_payload_cannot_enable_experiment_in_normal_profile(profile_queue):
    profile_queue.settings.source_test_mode = False
    with profile_queue.factory() as db:
        with pytest.raises(HTTPException) as caught:
            enqueue("PROJECTS", {"trigger": "MANUAL", "source_test_mode": True,
                    "project_ids": [profile_queue.project_id]}, db.get(User, profile_queue.user_id), db)
        assert (caught.value.status_code, caught.value.detail) == (409, "source_test_profile_required")
        assert db.scalar(select(ScanRun)) is None


def test_runtime_reports_profile_and_hides_updater(profile_queue, tmp_path):
    from fastapi import FastAPI
    profile_queue.monkeypatch.setattr("app.main.app", FastAPI())
    profile_queue.monkeypatch.setattr("app.database.settings", profile_queue.settings)
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>Test</html>")
    app = create_desktop_app(frontend, "s" * 40)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/desktop/runtime")
    snapshot = endpoint()
    assert snapshot["source_test_mode"] is True
    assert snapshot["updater"] is False
    profile_queue.settings.source_test_mode = False
    assert endpoint()["source_test_mode"] is False
    assert endpoint()["updater"] is True


def test_test_profile_scheduler_does_not_enqueue_or_reanchor(profile_queue):
    now = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
    due = now - timedelta(minutes=1)
    with profile_queue.factory() as db:
        schedule = SchedulerSetting(user_id=profile_queue.user_id, enabled=True,
                                    interval_minutes=60, next_run_at=due)
        db.add(schedule)
        db.flush()
        db.add(ScheduledProject(scheduler_id=schedule.id, project_id=profile_queue.project_id))
        db.commit()
    enqueue_scheduled(now=now)
    with profile_queue.factory() as db:
        assert db.scalar(select(ScanRun)) is None
        assert db.scalar(select(SchedulerSetting)).next_run_at == due


def test_worker_refuses_persisted_automatic_job_before_browser(profile_queue):
    browser_calls = []
    profile_queue.monkeypatch.setattr("app.worker.desktop_playwright", lambda: browser_calls.append(True))
    with profile_queue.factory() as db:
        run = ScanRun(owner_id=profile_queue.user_id, kind="PROJECTS", status="QUEUED",
            payload={"trigger": "AUTOMATIC", "project_ids": [profile_queue.project_id]})
        db.add(run)
        db.commit()
        run_id = run.id
    process_job(run_id)
    with profile_queue.factory() as db:
        run = db.get(ScanRun, run_id)
        assert run.status == "FAILED"
        assert "SOURCE_TEST_MANUAL_ONLY" in run.error
    assert browser_calls == []
