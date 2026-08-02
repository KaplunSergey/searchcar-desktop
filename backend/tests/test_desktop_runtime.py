import os
from pathlib import Path

from sqlalchemy import text

from app.database import create_database_engine
from app.desktop_runtime import configure_desktop_environment


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
