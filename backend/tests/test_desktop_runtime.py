import hashlib
import os
import json
from pathlib import Path

from sqlalchemy import text

from app.database import create_database_engine
from app.desktop_runtime import (
    bundled_headless_chromium,
    configure_desktop_environment,
    configure_playwright_environment,
)


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
