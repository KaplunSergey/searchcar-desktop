import argparse
from contextvars import ContextVar
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath


DESKTOP_COOKIE_NAME = "searchcar_desktop_session"
BROWSER_MANIFEST_NAME = "searchcar-browser-manifest.json"
PLAYWRIGHT_DRIVER_MANIFEST_NAME = "searchcar-playwright-driver-manifest.json"
BUNDLED_CHROMIUM_ENV = "SEARCHCAR_CHROMIUM_EXECUTABLE"
GRACEFUL_SHUTDOWN_SECONDS = 35
PARENT_WATCH_INTERVAL_SECONDS = 2.0
desktop_correlation_id: ContextVar[str] = ContextVar(
    "searchcar_desktop_correlation_id", default="desktop-process"
)


class PlaywrightRuntimeError(RuntimeError):
    """A recoverable local runtime issue, not an Encar parsing failure."""

    code = "PLAYWRIGHT_RUNTIME_UNAVAILABLE"


class DesktopPlaywrightSession:
    """Translate a driver startup crash into a useful scan failure."""

    def __init__(self) -> None:
        self._session = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._session = sync_playwright()
        try:
            return self._session.__enter__()
        except Exception as exc:
            logging.getLogger(__name__).exception("Playwright driver failed to start")
            raise PlaywrightRuntimeError(
                "Playwright driver could not start; restart SearchCar and try again"
            ) from exc

    def __exit__(self, exc_type, exc_value, traceback):
        if self._session is not None:
            return self._session.__exit__(exc_type, exc_value, traceback)
        return False


def desktop_playwright() -> DesktopPlaywrightSession:
    return DesktopPlaywrightSession()


class DesktopUpdateCheckRelay:
    """Pass one user-requested update check from localhost UI to Tauri."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requested = False

    def request(self) -> None:
        with self._lock:
            self._requested = True

    def consume(self) -> bool:
        with self._lock:
            requested = self._requested
            self._requested = False
        return requested


class DesktopUpdateStateRelay:
    """Expose a small, non-sensitive updater state snapshot to the local UI."""

    _ALLOWED_STATES = {
        "idle",
        "checking",
        "available",
        "preparing",
        "downloading",
        "retrying",
        "installing",
        "failed",
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = "idle"
        self._progress: int | None = None

    def set(self, state: str, progress: int | None = None) -> None:
        if state not in self._ALLOWED_STATES:
            raise ValueError("desktop_update_state_invalid")
        if progress is not None and not 0 <= progress <= 100:
            raise ValueError("desktop_update_progress_invalid")
        if state != "downloading":
            progress = None
        with self._lock:
            self._state = state
            self._progress = progress

    def snapshot(self) -> dict:
        with self._lock:
            return {"state": self._state, "progress": self._progress}


def prepare_desktop_update(data_dir: Path) -> dict:
    """Block new scans and create a verified rollback point for an update."""

    from sqlalchemy import func, select

    from .database import SessionLocal
    from .desktop_backup import export_backup
    from .maintenance import begin_update_install, finish_update_install
    from .models import ScanRun
    from .version import APP_VERSION

    def active_scan_count() -> int:
        with SessionLocal() as database:
            return int(
                database.scalar(
                    select(func.count())
                    .select_from(ScanRun)
                    .where(
                        ScanRun.status.in_(
                            ("QUEUED", "RUNNING", "CANCEL_REQUESTED")
                        )
                    )
                )
                or 0
            )

    active = active_scan_count()
    if active:
        return {"status": "active_scan", "count": active}

    begin_update_install()
    try:
        # Close the enqueue/claim race: once the guard exists, both manual and
        # scheduled scans are refused. Recheck work that may have committed
        # immediately before the guard became visible.
        active = active_scan_count()
        if active:
            finish_update_install()
            return {"status": "active_scan", "count": active}
        backup_path = data_dir / "backups" / (
            f"pre-update-{_utc_filename()}-{APP_VERSION}.searchcar-backup"
        )
        result = export_backup(
            data_dir / "data" / "searchcar.sqlite3",
            data_dir / "storage",
            backup_path,
            app_version=APP_VERSION,
        )
    except Exception:
        finish_update_install()
        raise
    return {
        "status": "ready",
        "backup": backup_path.name,
        "bytes": result.bytes,
    }


class DesktopInstanceLock:
    """Hold one backend process per desktop data directory."""

    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if self.path.stat().st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("desktop_instance_already_running") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n".encode("ascii"))
        handle.flush()
        self.file = handle

    def release(self) -> None:
        handle, self.file = self.file, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(
                record, "correlation_id", desktop_correlation_id.get()
            ),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _sqlite_url(database_path: Path) -> str:
    return f"sqlite+pysqlite:///{database_path.resolve().as_posix()}"


def _persistent_secret(data_dir: Path) -> str:
    secret_path = data_dir / "runtime-auth-secret"
    if secret_path.is_file():
        return secret_path.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(48)
    secret_path.write_text(value, encoding="utf-8")
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    return value


def _browser_manifest_executable(browser_dir: Path) -> Path:
    manifest_path = browser_dir / BROWSER_MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"playwright_browser_manifest_not_found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("playwright_browser_manifest_invalid") from exc
    relative_value = manifest.get("chromium_headless_shell")
    if not isinstance(relative_value, str) or not relative_value:
        raise ValueError("playwright_browser_manifest_executable_missing")
    relative = PurePosixPath(relative_value)
    windows_relative = PureWindowsPath(relative_value)
    if (
        "\\" in relative_value
        or relative.is_absolute()
        or windows_relative.is_absolute()
        or windows_relative.drive
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("playwright_browser_manifest_path_unsafe")
    executable = browser_dir.joinpath(*relative.parts).resolve()
    try:
        executable.relative_to(browser_dir)
    except ValueError as exc:
        raise ValueError("playwright_browser_manifest_path_unsafe") from exc
    if not executable.is_file():
        raise FileNotFoundError(
            f"playwright_chromium_executable_not_found: {executable}"
        )
    expected_bytes = manifest.get("chromium_headless_shell_bytes")
    if expected_bytes is not None and (
        not isinstance(expected_bytes, int)
        or expected_bytes < 1
        or executable.stat().st_size != expected_bytes
    ):
        raise ValueError("playwright_browser_manifest_size_mismatch")
    expected_sha256 = manifest.get("chromium_headless_shell_sha256")
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise ValueError("playwright_browser_manifest_checksum_invalid")
        digest = hashlib.sha256()
        with executable.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
            raise ValueError("playwright_browser_manifest_checksum_mismatch")
    return executable


def configure_playwright_environment(browser_dir: Path) -> Path:
    browser_dir = browser_dir.expanduser().resolve()
    if not browser_dir.is_dir():
        raise FileNotFoundError(f"playwright_browser_directory_not_found: {browser_dir}")
    executable = _browser_manifest_executable(browser_dir)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_dir)
    os.environ["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"
    os.environ[BUNDLED_CHROMIUM_ENV] = str(executable)
    return browser_dir


def configure_playwright_driver(driver_dir: Path) -> Path:
    """Use a resource-bundled Playwright driver instead of the onefile copy.

    The Python Playwright package normally locates both Node and ``cli.js``
    beside itself.  A Nuitka onefile sidecar puts that package in a temporary
    extraction directory, which macOS may purge while SearchCar is open.  Keep
    the complete driver in the signed Tauri resources and override both paths.
    """

    driver_dir = driver_dir.expanduser().resolve()
    if not driver_dir.is_dir():
        raise FileNotFoundError(
            f"playwright_driver_directory_not_found: {driver_dir}"
        )
    manifest_path = driver_dir / PLAYWRIGHT_DRIVER_MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"playwright_driver_manifest_not_found: {manifest_path}"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("playwright_driver_manifest_invalid") from exc
    relative_value = manifest.get("node_executable")
    if not isinstance(relative_value, str) or not relative_value:
        raise ValueError("playwright_driver_manifest_executable_missing")
    relative = PurePosixPath(relative_value)
    windows_relative = PureWindowsPath(relative_value)
    if (
        "\\" in relative_value
        or relative.is_absolute()
        or windows_relative.is_absolute()
        or windows_relative.drive
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("playwright_driver_manifest_path_unsafe")
    executable = driver_dir.joinpath(*relative.parts).resolve()
    try:
        executable.relative_to(driver_dir)
    except ValueError as exc:
        raise ValueError("playwright_driver_manifest_path_unsafe") from exc
    if not executable.is_file():
        raise FileNotFoundError(
            f"playwright_driver_executable_not_found: {executable}"
        )
    expected_bytes = manifest.get("node_executable_bytes")
    if expected_bytes is not None and (
        not isinstance(expected_bytes, int)
        or expected_bytes < 1
        or executable.stat().st_size != expected_bytes
    ):
        raise ValueError("playwright_driver_manifest_size_mismatch")
    expected_sha256 = manifest.get("node_executable_sha256")
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise ValueError("playwright_driver_manifest_checksum_invalid")
        digest = hashlib.sha256()
        with executable.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
            raise ValueError("playwright_driver_manifest_checksum_mismatch")
    cli_value = manifest.get("driver_cli")
    if not isinstance(cli_value, str) or not cli_value:
        raise ValueError("playwright_driver_manifest_cli_missing")
    cli_relative = PurePosixPath(cli_value)
    cli_windows_relative = PureWindowsPath(cli_value)
    if (
        "\\" in cli_value
        or cli_relative.is_absolute()
        or cli_windows_relative.is_absolute()
        or cli_windows_relative.drive
        or any(part in {"", ".", ".."} for part in cli_relative.parts)
    ):
        raise ValueError("playwright_driver_manifest_cli_path_unsafe")
    cli = driver_dir.joinpath(*cli_relative.parts).resolve()
    try:
        cli.relative_to(driver_dir)
    except ValueError as exc:
        raise ValueError("playwright_driver_manifest_cli_path_unsafe") from exc
    if not cli.is_file():
        raise FileNotFoundError(f"playwright_driver_cli_not_found: {cli}")
    expected_cli_bytes = manifest.get("driver_cli_bytes")
    if expected_cli_bytes is not None and (
        not isinstance(expected_cli_bytes, int)
        or expected_cli_bytes < 1
        or cli.stat().st_size != expected_cli_bytes
    ):
        raise ValueError("playwright_driver_manifest_cli_size_mismatch")
    expected_cli_sha256 = manifest.get("driver_cli_sha256")
    if expected_cli_sha256 is not None:
        if not isinstance(expected_cli_sha256, str) or len(expected_cli_sha256) != 64:
            raise ValueError("playwright_driver_manifest_cli_checksum_invalid")
        digest = hashlib.sha256()
        with cli.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), expected_cli_sha256.lower()):
            raise ValueError("playwright_driver_manifest_cli_checksum_mismatch")

    # Playwright has no public setting for its JavaScript entrypoint. Its
    # transport imports this helper by value, so patch both locations before
    # the first `sync_playwright()` call. This stays local to the sidecar and
    # avoids any dependency on Nuitka's temporary extraction tree.
    from playwright._impl import _driver, _transport

    def bundled_driver_executable() -> tuple[str, str]:
        return str(executable), str(cli)

    _driver.compute_driver_executable = bundled_driver_executable
    _transport.compute_driver_executable = bundled_driver_executable
    os.environ["PLAYWRIGHT_NODEJS_PATH"] = str(executable)
    return driver_dir


def bundled_headless_chromium(browser_dir: Path) -> Path:
    browser_dir = browser_dir.expanduser().resolve()
    if (browser_dir / BROWSER_MANIFEST_NAME).is_file():
        return _browser_manifest_executable(browser_dir)
    executable_names = {"headless_shell", "headless_shell.exe"}
    candidates = sorted(
        path
        for path in browser_dir.rglob("*")
        if path.is_file() and path.name in executable_names
    )
    if not candidates:
        raise FileNotFoundError(
            f"playwright_chromium_executable_not_found: {browser_dir}"
        )
    return candidates[0]


def configure_desktop_environment(
    data_dir: Path,
    port: int,
    browser_dir: Path | None = None,
    playwright_driver_dir: Path | None = None,
) -> dict[str, Path]:
    data_dir = data_dir.expanduser().resolve()
    database_dir = data_dir / "data"
    storage_dir = data_dir / "storage"
    logs_dir = data_dir / "logs"
    for path in (data_dir, database_dir, storage_dir, logs_dir):
        path.mkdir(parents=True, exist_ok=True)
    database_path = database_dir / "searchcar.sqlite3"
    os.environ["DATABASE_URL"] = _sqlite_url(database_path)
    os.environ["STORAGE_ROOT"] = str(storage_dir)
    os.environ["SEARCHCAR_DESKTOP_DATA_DIR"] = str(data_dir)
    os.environ["AUTH_COOKIE_NAME"] = "searchcar_session"
    os.environ["AUTH_CSRF_COOKIE_NAME"] = "searchcar_csrf"
    os.environ["AUTH_HASH_SECRET"] = _persistent_secret(data_dir)
    os.environ["ALLOWED_ORIGINS"] = f"http://127.0.0.1:{port}"
    paths = {
        "root": data_dir,
        "database": database_path,
        "storage": storage_dir,
        "logs": logs_dir,
    }
    if browser_dir is not None:
        paths["browsers"] = configure_playwright_environment(browser_dir)
        if playwright_driver_dir is None:
            bundled_driver_dir = browser_dir.expanduser().resolve().parent / "playwright-driver"
            if bundled_driver_dir.is_dir():
                playwright_driver_dir = bundled_driver_dir
    if playwright_driver_dir is not None:
        paths["playwright_driver"] = configure_playwright_driver(
            playwright_driver_dir
        )
    return paths


def configure_structured_logging(logs_dir: Path) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "searchcar-core.jsonl"
    formatter = JsonLogFormatter()
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)
    return log_path


def apply_pending_restore(paths: dict[str, Path]) -> dict | None:
    pending = paths["root"] / "runtime" / "pending-restore.searchcar-backup"
    if not pending.is_file():
        return None
    pending.parent.mkdir(parents=True, exist_ok=True)
    result_path = pending.parent / "last-restore-result.json"
    try:
        initialize_database()
        from .database import engine
        from .desktop_backup import interrupt_unfinished_jobs, restore_backup

        interrupt_unfinished_jobs(engine, reason="BACKUP_RESTORE_REQUESTED")
        engine.dispose()
        result = restore_backup(
            pending,
            paths["database"],
            paths["storage"],
            paths["root"] / "backups",
        )
        engine.dispose()
        pending.unlink()
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result
    except Exception as exc:
        failed = paths["root"] / "backups" / (
            f"failed-restore-{_utc_filename()}.searchcar-backup"
        )
        failed.parent.mkdir(parents=True, exist_ok=True)
        os.replace(pending, failed)
        result = {
            "status": "failed",
            "error": type(exc).__name__,
            "backup": str(failed),
        }
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logging.getLogger(__name__).exception("Pending backup restore failed")
        return result


def _utc_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def initialize_database() -> int | None:
    from . import models  # noqa: F401
    from .database import Base, engine

    if engine.dialect.name == "sqlite":
        from .sqlite_migrations import migrate_sqlite

        return migrate_sqlite(engine)
    Base.metadata.create_all(engine)
    return None


def normalize_desktop_workspace(paths: dict[str, Path]) -> str:
    """Remove legacy desktop account UX before workers can use its data."""

    from .database import SessionLocal, engine
    from .desktop_onboarding import migrate_legacy_desktop_workspace

    with SessionLocal.begin() as db:
        result = migrate_legacy_desktop_workspace(db)
    if result != "multiple":
        return result

    # The selected legacy policy is a clean desktop, but never a silent data
    # loss: export and verify the old database and images before replacing them.
    from .desktop_backup import export_backup
    from .version import APP_VERSION

    backup_path = paths["root"] / "backups" / (
        f"legacy-multi-user-{_utc_filename()}.searchcar-backup"
    )
    engine.dispose()
    export_backup(
        paths["database"],
        paths["storage"],
        backup_path,
        app_version=APP_VERSION,
    )
    for suffix in ("", "-wal", "-shm"):
        stale = Path(f"{paths['database']}{suffix}")
        if stale.exists():
            stale.unlink()
    cars_root = paths["storage"] / "cars"
    if cars_root.exists():
        shutil.rmtree(cars_root)
    initialize_database()
    logging.getLogger(__name__).warning(
        "Legacy multi-user desktop data reset after verified backup: %s",
        backup_path.name,
    )
    return "reset"


def _session_cookie(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_desktop_app(
    frontend_dir: Path,
    session_secret: str,
    shutdown_handler=None,
):
    if len(session_secret) < 32:
        raise ValueError("desktop_session_secret_too_short")
    frontend_dir = frontend_dir.expanduser().resolve()
    index_path = frontend_dir / "index.html"
    if not index_path.is_file():
        raise FileNotFoundError(f"desktop_frontend_not_found: {index_path}")

    from fastapi import HTTPException, Request
    from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.staticfiles import StaticFiles

    from .main import app

    expected_cookie = _session_cookie(session_secret)
    update_check_relay = DesktopUpdateCheckRelay()
    update_state_relay = DesktopUpdateStateRelay()

    class DesktopSessionMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.url.path in {
                "/api/health",
                "/desktop/bootstrap",
                "/desktop/shutdown",
                "/desktop/tray-status",
                "/desktop/scheduler/toggle",
                "/desktop/scheduler/resumed",
                "/desktop/update-check/consume",
                "/desktop/update/status",
                "/desktop/update/prepare",
                "/desktop/update/abort",
            }:
                return await call_next(request)
            supplied = request.cookies.get(DESKTOP_COOKIE_NAME, "")
            if not hmac.compare_digest(supplied, expected_cookie):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "desktop_session_required"},
                )
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response

    app.add_middleware(DesktopSessionMiddleware)

    @app.get("/desktop/bootstrap", include_in_schema=False)
    def desktop_bootstrap(token: str, request: Request):
        if not hmac.compare_digest(token, session_secret):
            raise HTTPException(403, "invalid_desktop_session")
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            DESKTOP_COOKIE_NAME,
            expected_cookie,
            httponly=True,
            secure=False,
            samesite="strict",
        )
        # Desktop always opens the hidden local workspace. A legacy one-user
        # database is normalized before the server starts; a clean database
        # with an existing device binding can recreate the workspace here.
        from .auth import create_session, now_utc, sync_csrf_cookie
        from .database import SessionLocal, settings
        from .desktop_license_client import has_local_license_binding
        from .desktop_onboarding import create_desktop_workspace, desktop_workspace_user

        with SessionLocal() as db:
            user = desktop_workspace_user(db)
            if user is None and has_local_license_binding(
                Path(os.environ["SEARCHCAR_DESKTOP_DATA_DIR"])
            ):
                user = create_desktop_workspace(
                    db,
                    source="EXISTING_LICENSE_BINDING",
                )
            if user is not None:
                auth_token, _, session = create_session(db, user, request)
                user.last_login_at = now_utc()
                user.last_activity_at = user.last_login_at
                sync_csrf_cookie(request, response, session)
                db.commit()
                response.set_cookie(
                    settings.auth_cookie_name,
                    auth_token,
                    httponly=True,
                    secure=settings.auth_cookie_secure,
                    samesite="lax",
                    max_age=settings.auth_session_days * 24 * 60 * 60,
                    path="/",
                )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/desktop/shutdown", include_in_schema=False, status_code=202)
    def desktop_shutdown(token: str):
        if not hmac.compare_digest(token, session_secret):
            raise HTTPException(403, "invalid_desktop_session")
        if shutdown_handler is None:
            raise HTTPException(503, "desktop_shutdown_unavailable")
        return shutdown_handler()

    @app.get("/desktop/tray-status", include_in_schema=False)
    def desktop_tray_status(token: str):
        if not hmac.compare_digest(token, session_secret):
            raise HTTPException(403, "invalid_desktop_session")
        from .database import SessionLocal
        from .desktop_scheduler import tray_scheduler_status

        with SessionLocal() as db:
            status = tray_scheduler_status(db)
        next_run = status["next_run_at"]
        next_value = next_run.isoformat() if next_run else ""
        return PlainTextResponse(
            f"{int(bool(status['enabled']))}|"
            f"{int(bool(status['paused']))}|{next_value}"
        )

    @app.post("/desktop/scheduler/toggle", include_in_schema=False)
    def desktop_scheduler_toggle(token: str):
        if not hmac.compare_digest(token, session_secret):
            raise HTTPException(403, "invalid_desktop_session")
        from .database import SessionLocal
        from .desktop_scheduler import toggle_all_schedulers

        with SessionLocal() as db:
            status = toggle_all_schedulers(db)
        next_run = status["next_run_at"]
        next_value = next_run.isoformat() if next_run else ""
        return PlainTextResponse(
            f"{int(bool(status['enabled']))}|"
            f"{int(bool(status['paused']))}|{next_value}"
        )

    @app.post("/desktop/scheduler/resumed", include_in_schema=False, status_code=202)
    def desktop_scheduler_resumed(token: str):
        if not hmac.compare_digest(token, session_secret):
            raise HTTPException(403, "invalid_desktop_session")
        from .database import SessionLocal, engine
        from .desktop_scheduler import prepare_overdue_scheduler_catch_up
        from .job_queue import request_sleep_interruption
        from .worker import enqueue_scheduled

        resumed_at = datetime.now(timezone.utc)
        interruption = request_sleep_interruption(engine, now=resumed_at)
        with SessionLocal() as db:
            due_scheduler_ids = prepare_overdue_scheduler_catch_up(
                db,
                now=resumed_at,
                force_user_ids=set(interruption["catch_up_owner_ids"]),
            )
        # Queue creation remains idempotent and refuses to overlap the scan
        # that is still unwinding after the sleep interruption.
        enqueue_scheduled(now=resumed_at)
        return {
            "status": "resume_processed",
            "cancel_requested": interruption["cancel_requested"],
            "catch_up_scheduler_ids": due_scheduler_ids,
        }

    @app.get("/api/desktop/runtime", include_in_schema=False)
    def desktop_runtime_info():
        return {
            "desktop": True,
            "updater": True,
            "update_status": update_state_relay.snapshot(),
        }

    @app.post("/api/desktop/update-check", include_in_schema=False, status_code=202)
    def desktop_update_check_request():
        update_state_relay.set("checking")
        update_check_relay.request()
        return {"status": "requested"}

    @app.get("/desktop/update-check/consume", include_in_schema=False)
    def desktop_update_check_consume(token: str):
        if not hmac.compare_digest(token, session_secret):
            raise HTTPException(403, "invalid_desktop_session")
        return PlainTextResponse("1" if update_check_relay.consume() else "0")

    @app.post("/desktop/update/status", include_in_schema=False)
    def desktop_update_status(
        token: str,
        state: str,
        progress: int | None = None,
    ):
        if not hmac.compare_digest(token, session_secret):
            raise HTTPException(403, "invalid_desktop_session")
        try:
            update_state_relay.set(state, progress)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return PlainTextResponse("updated")

    @app.post("/desktop/update/prepare", include_in_schema=False)
    def desktop_update_prepare(token: str):
        if not hmac.compare_digest(token, session_secret):
            raise HTTPException(403, "invalid_desktop_session")
        data_dir = Path(os.environ["SEARCHCAR_DESKTOP_DATA_DIR"])
        try:
            result = prepare_desktop_update(data_dir)
        except Exception as exc:
            logging.getLogger(__name__).exception("Could not prepare desktop update")
            return PlainTextResponse(f"error|{type(exc).__name__}")
        if result["status"] == "active_scan":
            return PlainTextResponse(f"active|{result['count']}")
        return PlainTextResponse(f"ready|{result['backup']}")

    @app.post("/desktop/update/abort", include_in_schema=False)
    def desktop_update_abort(token: str):
        if not hmac.compare_digest(token, session_secret):
            raise HTTPException(403, "invalid_desktop_session")
        from .maintenance import finish_update_install

        finish_update_install()
        return PlainTextResponse("aborted")

    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="desktop-ui")
    return app


class GracefulShutdownController:
    def __init__(self, worker_stop: threading.Event, worker_thread: threading.Thread):
        self.worker_stop = worker_stop
        self.worker_thread = worker_thread
        self.server = None
        self.lock = threading.Lock()
        self.requested = False
        self.result: dict = {"status": "not_requested"}

    def attach_server(self, server) -> None:
        self.server = server

    def request(self) -> dict:
        with self.lock:
            if self.requested:
                return self.result
            self.requested = True
            self.worker_stop.set()
            try:
                from .database import engine
                from .job_queue import request_shutdown_cancellation

                cancellation = request_shutdown_cancellation(engine)
                self.result = {
                    "status": "shutdown_requested",
                    **cancellation,
                }
            except Exception as exc:
                logging.getLogger(__name__).exception(
                    "Could not persist graceful scan cancellation"
                )
                self.result = {
                    "status": "shutdown_requested",
                    "cancelled": [],
                    "cancel_requested": [],
                    "warning": type(exc).__name__,
                }
            waiter = threading.Thread(
                target=self._finish,
                name="searchcar-graceful-shutdown",
                daemon=True,
            )
            waiter.start()
            return self.result

    def _finish(self) -> None:
        self.worker_thread.join(timeout=GRACEFUL_SHUTDOWN_SECONDS)
        if self.worker_thread.is_alive():
            logging.getLogger(__name__).warning(
                "Worker did not stop within %s seconds",
                GRACEFUL_SHUTDOWN_SECONDS,
            )
        if self.server is not None:
            self.server.should_exit = True


def parent_process_is_alive(parent_pid: int) -> bool:
    if parent_pid <= 1:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            parent_pid,
        )
        if not handle:
            # Access denied means the PID exists but cannot be inspected. Any
            # other failure is treated as gone so the orphan sidecar can stop.
            return ctypes.get_last_error() == 5
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def watch_parent_process(
    parent_pid: int,
    stop_event: threading.Event,
    shutdown_controller: GracefulShutdownController,
    *,
    interval_seconds: float = PARENT_WATCH_INTERVAL_SECONDS,
) -> None:
    while not stop_event.wait(max(0.05, interval_seconds)):
        if parent_process_is_alive(parent_pid):
            continue
        logging.getLogger(__name__).warning(
            "Desktop shell exited; stopping sidecar: parent_pid=%s",
            parent_pid,
        )
        shutdown_controller.request()
        return


def check_runtime(data_dir: Path, port: int) -> dict:
    paths = configure_desktop_environment(data_dir, port)
    schema_version = initialize_database()
    from sqlalchemy import text

    from .database import engine

    with engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
        connection.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": str(paths["database"]),
        "foreign_keys": foreign_keys,
        "journal_mode": str(journal_mode).lower(),
        "busy_timeout": busy_timeout,
        "schema_version": schema_version,
    }


def check_browser(
    browser_dir: Path,
    output_path: Path,
    playwright_driver_dir: Path | None = None,
) -> dict:
    browser_dir = configure_playwright_environment(browser_dir)
    if playwright_driver_dir is not None:
        configure_playwright_driver(playwright_driver_dir)
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    executable_path = bundled_headless_chromium(browser_dir)
    with desktop_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(executable_path),
        )
        try:
            page = browser.new_page(viewport={"width": 960, "height": 540})
            page.set_content(
                "<main style='font:32px system-ui;padding:48px'>"
                "SearchCar Desktop browser check</main>"
            )
            page.screenshot(path=str(output_path))
        finally:
            browser.close()
    return {
        "status": "ok",
        "browser_directory": str(browser_dir),
        "executable": str(executable_path),
        "screenshot": str(output_path),
    }


def backup_export(data_dir: Path, output_path: Path, port: int) -> dict:
    from .desktop_backup import export_backup

    paths = configure_desktop_environment(data_dir, port)
    initialize_database()
    return export_backup(
        paths["database"],
        paths["storage"],
        output_path,
    ).as_dict()


def backup_validate(input_path: Path) -> dict:
    from .desktop_backup import validate_backup

    return validate_backup(input_path).as_dict()


def backup_restore(data_dir: Path, input_path: Path, port: int) -> dict:
    from .desktop_backup import restore_backup

    paths = configure_desktop_environment(data_dir, port)
    return restore_backup(
        input_path,
        paths["database"],
        paths["storage"],
        paths["root"] / "backups",
    )


def convert_database(
    source_url: str,
    source_storage: Path,
    output_path: Path,
) -> dict:
    from .postgres_converter import convert_to_backup

    return convert_to_backup(
        source_url,
        source_storage,
        output_path,
    ).as_dict()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="SearchCar Desktop sidecar")
    commands = result.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Initialize and verify local SQLite")
    check.add_argument("--data-dir", type=Path, required=True)
    check.add_argument("--port", type=int, default=8765)
    browser_check = commands.add_parser(
        "browser-check",
        help="Launch bundled Chromium and save a diagnostic screenshot",
    )
    browser_check.add_argument("--browser-dir", type=Path, required=True)
    browser_check.add_argument("--playwright-driver-dir", type=Path)
    browser_check.add_argument("--output", type=Path, required=True)
    export = commands.add_parser(
        "backup-export",
        help="Create and verify a portable desktop backup",
    )
    export.add_argument("--data-dir", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--port", type=int, default=8765)
    validate = commands.add_parser(
        "backup-validate",
        help="Validate a portable desktop backup without restoring it",
    )
    validate.add_argument("--input", type=Path, required=True)
    restore = commands.add_parser(
        "backup-restore",
        help="Restore a backup while the desktop application is stopped",
    )
    restore.add_argument("--data-dir", type=Path, required=True)
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--port", type=int, default=8765)
    convert = commands.add_parser(
        "convert-database",
        help="Read a PostgreSQL/SQLite source and create a desktop backup",
    )
    convert.add_argument("--source-url", required=True)
    convert.add_argument("--source-storage", type=Path, required=True)
    convert.add_argument("--output", type=Path, required=True)
    serve = commands.add_parser("serve", help="Run the local desktop backend")
    serve.add_argument("--data-dir", type=Path, required=True)
    serve.add_argument("--frontend-dir", type=Path, required=True)
    serve.add_argument("--browser-dir", type=Path)
    serve.add_argument("--playwright-driver-dir", type=Path)
    serve.add_argument("--allow-device-key-file-fallback", action="store_true")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True)
    return result


def main() -> None:
    arguments = parser().parse_args()
    if arguments.command == "check":
        print(json.dumps(check_runtime(arguments.data_dir, arguments.port)))
        return
    if arguments.command == "browser-check":
        print(
            json.dumps(
                check_browser(
                    arguments.browser_dir,
                    arguments.output,
                    arguments.playwright_driver_dir,
                )
            )
        )
        return
    if arguments.command == "backup-export":
        print(
            json.dumps(
                backup_export(arguments.data_dir, arguments.output, arguments.port),
                ensure_ascii=False,
            )
        )
        return
    if arguments.command == "backup-validate":
        print(json.dumps(backup_validate(arguments.input), ensure_ascii=False))
        return
    if arguments.command == "backup-restore":
        print(
            json.dumps(
                backup_restore(arguments.data_dir, arguments.input, arguments.port),
                ensure_ascii=False,
            )
        )
        return
    if arguments.command == "convert-database":
        print(
            json.dumps(
                convert_database(
                    arguments.source_url,
                    arguments.source_storage,
                    arguments.output,
                ),
                ensure_ascii=False,
            )
        )
        return
    if arguments.allow_device_key_file_fallback:
        os.environ["SEARCHCAR_ALLOW_DEVICE_KEY_FILE_FALLBACK"] = "1"
    paths = configure_desktop_environment(
        arguments.data_dir,
        arguments.port,
        arguments.browser_dir,
        arguments.playwright_driver_dir,
    )
    configure_structured_logging(paths["logs"])
    instance_lock = DesktopInstanceLock(paths["root"] / "runtime" / "desktop.lock")
    instance_lock.acquire()
    from .maintenance import clear_stale_maintenance_lock

    clear_stale_maintenance_lock()
    apply_pending_restore(paths)
    initialize_database()
    normalize_desktop_workspace(paths)
    from sqlalchemy import select
    from .database import SessionLocal, engine
    from .desktop_scheduler import prepare_overdue_scheduler_catch_up
    from .job_queue import recover_interrupted_jobs
    from .models import ScanRun
    from .worker import run as run_worker

    recovered_jobs = recover_interrupted_jobs(engine)
    if recovered_jobs:
        logging.getLogger(__name__).warning(
            "Recovered interrupted scan jobs: %s",
            ",".join(str(job_id) for job_id in recovered_jobs),
        )
        with SessionLocal() as db:
            recovered_owner_ids = {
                run.owner_id
                for run in db.scalars(
                    select(ScanRun).where(ScanRun.id.in_(recovered_jobs))
                )
                if (run.payload or {}).get("trigger") == "AUTOMATIC"
                or (run.payload or {}).get("scheduled") is True
            }
            prepare_overdue_scheduler_catch_up(
                db,
                force_user_ids=recovered_owner_ids,
            )
    worker_stop = threading.Event()
    worker_thread = threading.Thread(
        target=run_worker,
        args=(worker_stop,),
        name="searchcar-scan-worker",
        daemon=True,
    )
    worker_thread.start()
    from .desktop_license_client import run_periodic_license_sync

    license_sync_thread = threading.Thread(
        target=run_periodic_license_sync,
        args=(worker_stop, paths["root"]),
        name="searchcar-license-sync",
        daemon=True,
    )
    license_sync_thread.start()
    shutdown_controller = GracefulShutdownController(worker_stop, worker_thread)
    session_secret = os.environ.get("SEARCHCAR_DESKTOP_SESSION_SECRET", "")
    desktop_app = create_desktop_app(
        arguments.frontend_dir,
        session_secret,
        shutdown_controller.request,
    )
    import uvicorn

    config = uvicorn.Config(
        desktop_app,
        host=arguments.host,
        port=arguments.port,
        access_log=False,
        log_level="info",
        log_config=None,
    )
    server = uvicorn.Server(config)
    shutdown_controller.attach_server(server)
    parent_pid_value = os.environ.get("SEARCHCAR_DESKTOP_PARENT_PID", "")
    if parent_pid_value.isdecimal() and int(parent_pid_value) > 1:
        threading.Thread(
            target=watch_parent_process,
            args=(int(parent_pid_value), worker_stop, shutdown_controller),
            name="searchcar-parent-watchdog",
            daemon=True,
        ).start()
    elif parent_pid_value:
        logging.getLogger(__name__).warning(
            "Ignoring invalid desktop parent PID"
        )
    try:
        server.run()
    finally:
        shutdown_controller.request()
        worker_thread.join(timeout=GRACEFUL_SHUTDOWN_SECONDS)
        instance_lock.release()


if __name__ == "__main__":
    main()
