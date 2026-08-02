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
from pathlib import Path


DESKTOP_COOKIE_NAME = "searchcar_desktop_session"


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


def configure_playwright_environment(browser_dir: Path) -> Path:
    browser_dir = browser_dir.expanduser().resolve()
    if not browser_dir.is_dir():
        raise FileNotFoundError(f"playwright_browser_directory_not_found: {browser_dir}")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_dir)
    os.environ["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"
    return browser_dir


def bundled_headless_chromium(browser_dir: Path) -> Path:
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


def create_desktop_app(frontend_dir: Path, session_secret: str):
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
            if request.url.path in {"/api/health", "/desktop/bootstrap"}:
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

    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="desktop-ui")
    return app


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
        browser = playwright.chromium.launch(headless=True)
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
    paths = configure_desktop_environment(
        arguments.data_dir,
        arguments.port,
        arguments.browser_dir,
    )
    configure_structured_logging(paths["logs"])
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
    session_secret = os.environ.get("SEARCHCAR_DESKTOP_SESSION_SECRET", "")
    desktop_app = create_desktop_app(arguments.frontend_dir, session_secret)
    worker_stop = threading.Event()
    worker_thread = threading.Thread(
        target=run_worker,
        args=(worker_stop,),
        name="searchcar-scan-worker",
        daemon=True,
    )
    worker_thread.start()
    import uvicorn

    try:
        uvicorn.run(
            desktop_app,
            host=arguments.host,
            port=arguments.port,
            access_log=False,
            log_level="info",
            log_config=None,
        )
    finally:
        worker_stop.set()
        worker_thread.join(timeout=5)


if __name__ == "__main__":
    main()
