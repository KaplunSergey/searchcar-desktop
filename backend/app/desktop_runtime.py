import argparse
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path


DESKTOP_COOKIE_NAME = "searchcar_desktop_session"


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


def configure_desktop_environment(data_dir: Path, port: int) -> dict[str, Path]:
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
    return {
        "root": data_dir,
        "database": database_path,
        "storage": storage_dir,
        "logs": logs_dir,
    }


def initialize_database() -> None:
    from . import models  # noqa: F401
    from .database import Base, engine

    Base.metadata.create_all(engine)


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
    initialize_database()
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
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="SearchCar Desktop sidecar")
    commands = result.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Initialize and verify local SQLite")
    check.add_argument("--data-dir", type=Path, required=True)
    check.add_argument("--port", type=int, default=8765)
    serve = commands.add_parser("serve", help="Run the local desktop backend")
    serve.add_argument("--data-dir", type=Path, required=True)
    serve.add_argument("--frontend-dir", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True)
    return result


def main() -> None:
    arguments = parser().parse_args()
    if arguments.command == "check":
        print(json.dumps(check_runtime(arguments.data_dir, arguments.port)))
        return
    configure_desktop_environment(arguments.data_dir, arguments.port)
    initialize_database()
    session_secret = os.environ.get("SEARCHCAR_DESKTOP_SESSION_SECRET", "")
    desktop_app = create_desktop_app(arguments.frontend_dir, session_secret)
    import uvicorn

    uvicorn.run(
        desktop_app,
        host=arguments.host,
        port=arguments.port,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
