"""Local opt-in isolation for source experiments; never grants source access."""

import os
from pathlib import Path

from sqlalchemy.engine import make_url

from .sources.contracts import ScanError


PROFILE_MARKER = ".source-test-profile"
PROFILE_SIGNATURE = "searchcar-source-tests-v1\n"


class SourceTestModeError(ScanError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def prepare_source_test_root(data_dir: Path) -> Path:
    """Choose the profile before SQLite, secrets, logs or locks are opened."""
    mode = os.environ.get("SEARCHCAR_SOURCE_TEST_MODE", "0")
    if mode not in {"0", "1"}:
        raise ValueError("source_test_mode_invalid")
    if mode == "0":
        return data_dir
    root = data_dir / "source-tests"
    if root.is_symlink():
        raise ValueError("source_test_profile_unsafe")
    marker = root / PROFILE_MARKER
    if root.exists():
        if marker.is_symlink() or not marker.is_file():
            raise ValueError("source_test_profile_unrecognized")
        if marker.read_text(encoding="utf-8") != PROFILE_SIGNATURE:
            raise ValueError("source_test_profile_unrecognized")
    else:
        root.mkdir(parents=True)
        marker.write_text(PROFILE_SIGNATURE, encoding="utf-8")
    for name in ("data", "storage", "logs", "runtime", "runtime-auth-secret"):
        if (root / name).is_symlink():
            raise ValueError("source_test_profile_unsafe")
    if (root / "data" / "searchcar.sqlite3").is_symlink():
        raise ValueError("source_test_profile_unsafe")
    return root


def validate_source_test_isolation(config, *, database_url=None) -> None:
    if not config.source_test_mode:
        return
    root = Path(os.environ.get("SEARCHCAR_DESKTOP_DATA_DIR", ""))
    marker = root / PROFILE_MARKER
    url = make_url(database_url or config.database_url)
    if (not root.is_absolute() or root.name != "source-tests" or root.is_symlink()
            or marker.is_symlink() or not marker.is_file()
            or marker.read_text(encoding="utf-8") != PROFILE_SIGNATURE
            or url.get_backend_name() != "sqlite" or not url.database
            or Path(url.database).resolve() != (root / "data" / "searchcar.sqlite3").resolve()
            or Path(config.storage_root).resolve() != (root / "storage").resolve()
            or any((root / name).is_symlink() for name in ("data", "storage", "logs", "runtime"))
            or (root / "data" / "searchcar.sqlite3").is_symlink()):
        raise SourceTestModeError("SOURCE_TEST_ISOLATION_REQUIRED")


def validate_source_test_scan(config, kind: str, payload: dict, *, database_url=None) -> None:
    if not config.source_test_mode:
        if payload.get("source_test_mode"):
            raise SourceTestModeError("SOURCE_TEST_PROFILE_REQUIRED")
        return
    validate_source_test_isolation(config, database_url=database_url)
    if kind != "PROJECTS" or payload.get("trigger") != "MANUAL" or payload.get("scheduled"):
        raise SourceTestModeError("SOURCE_TEST_MANUAL_ONLY")
