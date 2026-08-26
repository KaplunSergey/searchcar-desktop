import hashlib
import os
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

import app.database as database_module
from app.database import Base, create_database_engine
from app.desktop_runtime import (
    DesktopInstanceLock,
    DesktopUpdateCheckRelay,
    DesktopUpdateStateRelay,
    bundled_headless_chromium,
    configure_desktop_environment,
    configure_playwright_driver,
    configure_playwright_environment,
    parent_process_is_alive,
    prepare_desktop_update,
    watch_parent_process,
)
from app.maintenance import finish_update_install, maintenance_active
from app.models import ScanRun, User


def test_desktop_update_check_relay_collapses_requests_and_consumes_once() -> None:
    relay = DesktopUpdateCheckRelay()

    assert relay.consume() is False
    relay.request()
    relay.request()
    assert relay.consume() is True
    assert relay.consume() is False


def test_desktop_update_state_relay_validates_public_snapshot() -> None:
    relay = DesktopUpdateStateRelay()

    assert relay.snapshot() == {"state": "idle", "progress": None}
    relay.set("downloading", 45)
    assert relay.snapshot() == {"state": "downloading", "progress": 45}
    relay.set("installing", 45)
    assert relay.snapshot() == {"state": "installing", "progress": None}
    with pytest.raises(ValueError, match="desktop_update_state_invalid"):
        relay.set("arbitrary")
    with pytest.raises(ValueError, match="desktop_update_progress_invalid"):
        relay.set("downloading", 101)


def _update_test_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "SearchCar"
    database_path = data_dir / "data" / "searchcar.sqlite3"
    database_path.parent.mkdir(parents=True)
    (data_dir / "storage").mkdir(parents=True)
    engine = create_database_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(data_dir))
    return data_dir, engine


def test_update_preparation_creates_backup_and_blocks_new_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, engine = _update_test_engine(tmp_path, monkeypatch)
    observed = {}

    def fake_export(_database, _storage, destination, **_options):
        observed["guard"] = maintenance_active()
        observed["destination"] = destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"verified backup")
        return SimpleNamespace(bytes=destination.stat().st_size)

    monkeypatch.setattr("app.desktop_backup.export_backup", fake_export)
    result = prepare_desktop_update(data_dir)

    assert result["status"] == "ready"
    assert result["backup"].startswith("pre-update-")
    assert observed["guard"] is True
    assert observed["destination"].name == result["backup"]
    assert maintenance_active()
    assert finish_update_install()
    assert not maintenance_active()
    engine.dispose()


def test_update_preparation_refuses_an_active_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, engine = _update_test_engine(tmp_path, monkeypatch)
    with Session(engine) as database:
        user = User(
            username="Update owner",
            username_key="update-owner",
            password_hash="test-only",
        )
        database.add(user)
        database.flush()
        database.add(ScanRun(owner_id=user.id, kind="PROJECTS", status="RUNNING"))
        database.commit()

    result = prepare_desktop_update(data_dir)

    assert result == {"status": "active_scan", "count": 1}
    assert not maintenance_active()
    engine.dispose()


def test_desktop_instance_lock_allows_only_one_backend(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "desktop.lock"
    first = DesktopInstanceLock(path)
    second = DesktopInstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="desktop_instance_already_running"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_parent_watchdog_requests_shutdown_when_shell_disappears(
    monkeypatch,
) -> None:
    requested = []

    class Controller:
        def request(self):
            requested.append(True)

    monkeypatch.setattr(
        "app.desktop_runtime.parent_process_is_alive", lambda _pid: False
    )

    watch_parent_process(
        12345,
        threading.Event(),
        Controller(),
        interval_seconds=0.01,
    )

    assert requested == [True]
    assert parent_process_is_alive(os.getpid()) is True


def test_sqlite_engine_enables_desktop_safety_pragmas(tmp_path: Path) -> None:
    database_path = tmp_path / "searchcar.sqlite3"
    engine = create_database_engine(f"sqlite+pysqlite:///{database_path}")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000


def test_desktop_environment_uses_platform_data_directory(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "placeholder")
    paths = configure_desktop_environment(tmp_path / "SearchCar", 43123)

    assert paths["database"].name == "searchcar.sqlite3"
    assert paths["storage"].is_dir()
    assert paths["logs"].is_dir()
    assert os.environ["DATABASE_URL"].startswith("sqlite+pysqlite:///")
    assert os.environ["ALLOWED_ORIGINS"] == "http://127.0.0.1:43123"


def test_desktop_environment_configures_bundled_browser(
    tmp_path: Path,
) -> None:
    browser_dir = tmp_path / "browsers"
    browser_dir.mkdir()
    executable = browser_dir / "chromium-1" / "headless_shell"
    executable.parent.mkdir()
    executable.write_bytes(b"browser")
    (browser_dir / "searchcar-browser-manifest.json").write_text(
        json.dumps(
            {"chromium_headless_shell": "chromium-1/headless_shell"}
        ),
        encoding="utf-8",
    )
    paths = configure_desktop_environment(
        tmp_path / "SearchCar",
        43123,
        browser_dir,
    )

    assert paths["browsers"] == browser_dir.resolve()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_dir.resolve())
    assert os.environ["PLAYWRIGHT_SKIP_BROWSER_GC"] == "1"
    assert os.environ["SEARCHCAR_CHROMIUM_EXECUTABLE"] == str(executable.resolve())


def test_desktop_environment_configures_external_playwright_driver(
    tmp_path: Path,
) -> None:
    driver_dir = tmp_path / "playwright-driver"
    driver_dir.mkdir()
    executable = driver_dir / "node"
    executable.write_bytes(b"signed external node")
    (driver_dir / "searchcar-playwright-driver-manifest.json").write_text(
        json.dumps(
            {
                "node_executable": "node",
                "node_executable_bytes": executable.stat().st_size,
                "node_executable_sha256": hashlib.sha256(
                    executable.read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    paths = configure_desktop_environment(
        tmp_path / "SearchCar",
        43123,
        playwright_driver_dir=driver_dir,
    )

    assert paths["playwright_driver"] == driver_dir.resolve()
    assert os.environ["PLAYWRIGHT_NODEJS_PATH"] == str(executable.resolve())


def test_desktop_environment_discovers_driver_next_to_browser(
    tmp_path: Path,
) -> None:
    browser_dir = tmp_path / "Resources" / "browsers"
    browser_dir.mkdir(parents=True)
    chromium = browser_dir / "chromium" / "headless_shell"
    chromium.parent.mkdir()
    chromium.write_bytes(b"browser")
    (browser_dir / "searchcar-browser-manifest.json").write_text(
        json.dumps({"chromium_headless_shell": "chromium/headless_shell"}),
        encoding="utf-8",
    )
    driver_dir = browser_dir.parent / "playwright-driver"
    driver_dir.mkdir()
    node = driver_dir / "node"
    node.write_bytes(b"external node")
    (driver_dir / "searchcar-playwright-driver-manifest.json").write_text(
        json.dumps({"node_executable": "node"}),
        encoding="utf-8",
    )

    paths = configure_desktop_environment(
        tmp_path / "SearchCar",
        43123,
        browser_dir=browser_dir,
    )

    assert paths["playwright_driver"] == driver_dir.resolve()
    assert os.environ["PLAYWRIGHT_NODEJS_PATH"] == str(node.resolve())


def test_playwright_driver_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    (tmp_path / "searchcar-playwright-driver-manifest.json").write_text(
        json.dumps({"node_executable": "../node"}),
        encoding="utf-8",
    )

    try:
        configure_playwright_driver(tmp_path)
    except ValueError as error:
        assert "manifest_path_unsafe" in str(error)
    else:
        raise AssertionError("unsafe Playwright driver manifest was accepted")


def test_playwright_driver_manifest_verifies_checksum(tmp_path: Path) -> None:
    executable = tmp_path / "node"
    executable.write_bytes(b"trusted node")
    (tmp_path / "searchcar-playwright-driver-manifest.json").write_text(
        json.dumps(
            {
                "node_executable": "node",
                "node_executable_bytes": executable.stat().st_size,
                "node_executable_sha256": hashlib.sha256(
                    executable.read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    assert configure_playwright_driver(tmp_path) == tmp_path.resolve()

    executable.write_bytes(b"changed node")
    try:
        configure_playwright_driver(tmp_path)
    except ValueError as error:
        assert "manifest_checksum_mismatch" in str(error)
    else:
        raise AssertionError("modified Playwright driver was accepted")


def test_missing_bundled_browser_directory_is_rejected(tmp_path: Path) -> None:
    try:
        configure_playwright_environment(tmp_path / "missing")
    except FileNotFoundError as error:
        assert "playwright_browser_directory_not_found" in str(error)
    else:
        raise AssertionError("missing browser directory was accepted")


def test_bundled_headless_chromium_is_discovered(tmp_path: Path) -> None:
    executable = tmp_path / "chromium_headless_shell-1" / "headless_shell"
    executable.parent.mkdir()
    executable.write_bytes(b"test")

    assert bundled_headless_chromium(tmp_path) == executable


def test_bundled_browser_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    (tmp_path / "searchcar-browser-manifest.json").write_text(
        json.dumps({"chromium_headless_shell": "../outside/headless_shell"}),
        encoding="utf-8",
    )

    try:
        configure_playwright_environment(tmp_path)
    except ValueError as error:
        assert "manifest_path_unsafe" in str(error)
    else:
        raise AssertionError("unsafe browser manifest was accepted")


def test_bundled_browser_manifest_verifies_size_and_checksum(tmp_path: Path) -> None:
    executable = tmp_path / "chromium-1" / "headless_shell"
    executable.parent.mkdir()
    executable.write_bytes(b"trusted browser")
    (tmp_path / "searchcar-browser-manifest.json").write_text(
        json.dumps(
            {
                "chromium_headless_shell": "chromium-1/headless_shell",
                "chromium_headless_shell_bytes": executable.stat().st_size,
                "chromium_headless_shell_sha256": hashlib.sha256(
                    executable.read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    assert configure_playwright_environment(tmp_path) == tmp_path.resolve()

    executable.write_bytes(b"changed browser")
    try:
        configure_playwright_environment(tmp_path)
    except ValueError as error:
        assert "manifest_checksum_mismatch" in str(error)
    else:
        raise AssertionError("modified bundled browser was accepted")
