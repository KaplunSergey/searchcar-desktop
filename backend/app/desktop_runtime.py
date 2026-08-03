import argparse
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath


DESKTOP_COOKIE_NAME = "searchcar_desktop_session"
BROWSER_MANIFEST_NAME = "searchcar-browser-manifest.json"
BUNDLED_CHROMIUM_ENV = "SEARCHCAR_CHROMIUM_EXECUTABLE"
GRACEFUL_SHUTDOWN_SECONDS = 35


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
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
    from fastapi.responses import JSONResponse, RedirectResponse
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.staticfiles import StaticFiles

    from .main import app

    expected_cookie = _session_cookie(session_secret)

    class DesktopSessionMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.url.path in {
                "/api/health",
                "/desktop/bootstrap",
                "/desktop/shutdown",
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
    def desktop_bootstrap(token: str):
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


def check_browser(browser_dir: Path, output_path: Path) -> dict:
    browser_dir = configure_playwright_environment(browser_dir)
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    executable_path = bundled_headless_chromium(browser_dir)
    with sync_playwright() as playwright:
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
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True)
    return result


def main() -> None:
    arguments = parser().parse_args()
    if arguments.command == "check":
        print(json.dumps(check_runtime(arguments.data_dir, arguments.port)))
        return
    if arguments.command == "browser-check":
        print(json.dumps(check_browser(arguments.browser_dir, arguments.output)))
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
    paths = configure_desktop_environment(
        arguments.data_dir,
        arguments.port,
        arguments.browser_dir,
    )
    configure_structured_logging(paths["logs"])
    from .maintenance import clear_stale_maintenance_lock

    clear_stale_maintenance_lock()
    apply_pending_restore(paths)
    initialize_database()
    from .database import SessionLocal, engine
    from .desktop_onboarding import ensure_initial_admin
    from .job_queue import recover_interrupted_jobs
    from .worker import run as run_worker

    created_admin = ensure_initial_admin(
        SessionLocal,
        username=os.environ.get("SEARCHCAR_INITIAL_ADMIN_USERNAME", "Serhii"),
        password=os.environ.get(
            "SEARCHCAR_INITIAL_ADMIN_PASSWORD",
            "sergiokap09",
        ),
    )
    if created_admin:
        logging.getLogger(__name__).info(
            "Created initial desktop administrator: %s",
            created_admin.username,
        )
    recovered_jobs = recover_interrupted_jobs(engine)
    if recovered_jobs:
        logging.getLogger(__name__).warning(
            "Recovered interrupted scan jobs: %s",
            ",".join(str(job_id) for job_id in recovered_jobs),
        )
    worker_stop = threading.Event()
    worker_thread = threading.Thread(
        target=run_worker,
        args=(worker_stop,),
        name="searchcar-scan-worker",
        daemon=True,
    )
    worker_thread.start()
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
    try:
        server.run()
    finally:
        shutdown_controller.request()
        worker_thread.join(timeout=GRACEFUL_SHUTDOWN_SECONDS)


if __name__ == "__main__":
    main()
