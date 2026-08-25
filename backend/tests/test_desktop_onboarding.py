from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.desktop_onboarding import create_desktop_workspace
from app.main import (
    _require_desktop_data_access,
    activate_desktop_workspace,
    desktop_onboarding_status,
    download_desktop_backup,
    import_desktop_backup,
)
from app.desktop_backup import export_backup, validate_backup
from app.models import User
from app.schemas import DesktopOnboardingActivateIn


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/desktop/onboarding/activate",
            "headers": [(b"origin", b"http://localhost:3000")],
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("localhost", 8000),
            "query_string": b"",
        }
    )


def test_desktop_activation_creates_passwordless_workspace_and_session(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))
    calls: list[str] = []

    def redeemed(action, operation):
        calls.append(action)
        return {"status": "active"}

    monkeypatch.setattr("app.main._desktop_license_operation", redeemed)
    with Session(engine, expire_on_commit=False) as db:
        assert desktop_onboarding_status(db) == {"required": True}
        response = Response()
        result = activate_desktop_workspace(
            DesktopOnboardingActivateIn(
                activation_code="SC-ABCDE-FGHIJ-KLMNO-PQRST",
                preferred_locale="uk",
            ),
            _request(),
            response,
            db,
        )

        assert calls == ["desktop_onboarding_redeem"]
        assert result["user"]["username"] == "SearchCar"
        assert result["user"]["passwordless_workspace"] is True
        assert result["user"]["preferred_locale"] == "uk"
        assert result["csrf_token"]
        cookies = "\n".join(response.headers.getlist("set-cookie"))
        assert "encar_session=" in cookies
        assert "encar_csrf=" in cookies
        assert desktop_onboarding_status(db) == {"required": False}


def test_only_admin_or_passwordless_workspace_can_manage_desktop_data(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))
    with Session(engine, expire_on_commit=False) as db:
        workspace = create_desktop_workspace(db)
        regular = User(
            username="Regular",
            username_key="regular",
            password_hash="test-only",
            role="USER",
            status="ACTIVE",
            project_limit=1,
        )
        admin = User(
            username="Admin",
            username_key="admin",
            password_hash="test-only",
            role="ADMIN",
            status="ACTIVE",
            project_limit=None,
        )
        db.add_all([regular, admin])
        db.commit()

        _require_desktop_data_access(workspace, db)
        _require_desktop_data_access(admin, db)
        with pytest.raises(HTTPException) as denied:
            _require_desktop_data_access(regular, db)
        assert denied.value.status_code == 403
        assert denied.value.detail == "desktop_data_access_required"


def _upload_request(payload: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/desktop/backups/import",
            "headers": [(b"content-length", str(len(payload)).encode("ascii"))],
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("localhost", 8000),
            "query_string": b"",
        },
        receive,
    )


def test_passwordless_workspace_can_import_and_download_verified_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_root = tmp_path / "desktop"
    database_path = data_root / "data" / "searchcar.sqlite3"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO sample (name) VALUES ('portable')")
        connection.commit()
    source = tmp_path / "portable.searchcar-backup"
    export_backup(database_path, data_root / "storage", source)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(data_root))
    with Session(engine, expire_on_commit=False) as db:
        workspace = create_desktop_workspace(db)
        result = asyncio.run(
            import_desktop_backup(
                _upload_request(source.read_bytes()),
                name=source.name,
                current=workspace,
                db=db,
            )
        )

        imported = data_root / "backups" / result["name"]
        assert imported.is_file()
        assert result["bytes"] == imported.stat().st_size
        assert validate_backup(imported).table_counts["sample"] == 1
        download = download_desktop_backup(result["name"], workspace, db)
        assert Path(download.path) == imported
