"""Stable desktop navigation preferences, independent of the localhost port."""
import json

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.auth import create_session
from app.database import Base, create_database_engine, get_db, settings
from app.main import app
from app.main import user_out
from app.models import ScanRun, User
from app.workspace_preferences import WorkspacePreferences, load_workspace, save_workspace


def snapshot():
    route = {"source": "copart", "view": "car", "projectId": 4, "carId": 40}
    return {"current": route, "sites": {"copart": route}}


def test_desktop_profile_restores_navigation_from_its_owner_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))
    path = tmp_path / "ui" / "workspace-7.json"
    path.parent.mkdir()
    path.write_text(json.dumps(snapshot()), encoding="utf-8")
    user = User(id=7, username="fixture", username_key="fixture", password_hash="!fixture")
    result = user_out(user, None, include_usage=False)
    assert result.get("workspace") is not None
    assert json.loads(result["workspace"]) == snapshot()


def test_preferences_are_owner_scoped_and_not_exposed_in_web_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))
    save_workspace(7, WorkspacePreferences.model_validate(snapshot()))
    assert load_workspace(8) is None
    assert json.loads(load_workspace(7)) == snapshot()
    monkeypatch.delenv("SEARCHCAR_DESKTOP_DATA_DIR")
    user = User(id=7, username="fixture", username_key="fixture", password_hash="!fixture")
    assert "workspace" not in user_out(user, None, include_usage=False)


@pytest.mark.parametrize("change", [
    {"source": "other"}, {"view": "unknown"}, {"carId": -1}, {"carId": True},
    {"projectId": 2**54}, {"carId": None}, {"path": "../../license/lease.json"},
])
def test_preferences_reject_unsafe_routes_and_extra_fields(change):
    value = snapshot()
    value["current"] = {**value["current"], **change}
    with pytest.raises(ValidationError):
        WorkspacePreferences.model_validate(value)


def test_preferences_reject_foreign_site_routes_and_global_sections():
    for change in [{"source": "iaa"}, {"view": "settings", "projectId": None, "carId": None}]:
        value = snapshot()
        value["sites"]["copart"] = {**value["current"], **change}
        with pytest.raises(ValidationError):
            WorkspacePreferences.model_validate(value)


def test_unreadable_preferences_do_not_prevent_opening_the_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))
    path = tmp_path / "ui" / "workspace-7.json"
    path.parent.mkdir()
    for raw in ["{bad", "[]", "x" * 4097]:
        path.write_text(raw, encoding="utf-8")
        assert load_workspace(7) is None
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    assert load_workspace(7) is None


def test_failed_atomic_save_keeps_previous_route_and_cleans_temporary_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))
    save_workspace(7, WorkspacePreferences.model_validate(snapshot()))
    value = snapshot()
    value["current"] = {"source": "iaa", "view": "scans"}

    def fail_replace(*args):
        raise OSError("fixture disk failure")

    monkeypatch.setattr("app.workspace_preferences.os.replace", fail_replace)
    with pytest.raises(OSError):
        save_workspace(7, WorkspacePreferences.model_validate(value))
    assert json.loads(load_workspace(7)) == snapshot()
    assert not list((tmp_path / "ui").glob(".workspace-*"))


@pytest.fixture
def workspace_api(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path / "desktop"))
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'api.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    tokens = {}
    with factory() as db:
        for key in [7, 8]:
            user = User(id=key, username=f"fixture-{key}", username_key=f"fixture-{key}", password_hash="!fixture", status="ACTIVE")
            db.add(user)
            db.flush()
            token, _, _ = create_session(db, user, Request({"type": "http", "headers": []}))
            tokens[key] = token
        db.commit()

    def database():
        with factory() as db:
            yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = database
    try:
        yield factory, tokens
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


def test_authenticated_port_change_restores_all_sites_without_starting_jobs(workspace_api):
    factory, tokens = workspace_api
    value = snapshot()
    value["sites"]["encar"] = {"source": "encar", "view": "scans", "projectId": None, "carId": None}
    with TestClient(app, base_url="http://127.0.0.1:18101") as first:
        first.cookies.set(settings.auth_cookie_name, tokens[7])
        csrf = first.get("/api/auth/me").json()["csrf_token"]
        assert first.put("/api/auth/workspace", json=value, headers={"x-csrf-token": csrf}).status_code == 200
    with TestClient(app, base_url="http://127.0.0.1:18102") as restarted:
        restarted.cookies.set(settings.auth_cookie_name, tokens[7])
        assert json.loads(restarted.get("/api/auth/me").json()["user"]["workspace"]) == value
        restarted.cookies.set(settings.auth_cookie_name, tokens[8])
        assert restarted.get("/api/auth/me").json()["user"]["workspace"] is None
    with factory() as db:
        assert list(db.scalars(select(ScanRun))) == []


def test_workspace_writes_require_auth_csrf_origin_and_desktop(workspace_api, monkeypatch):
    _, tokens = workspace_api
    with TestClient(app) as client:
        assert client.put("/api/auth/workspace", json=snapshot()).status_code == 401
        client.cookies.set(settings.auth_cookie_name, tokens[7])
        assert client.put("/api/auth/workspace", json=snapshot()).status_code == 403
        csrf = client.get("/api/auth/me").json()["csrf_token"]
        headers = {"x-csrf-token": csrf}
        assert client.put("/api/auth/workspace", json=snapshot(), headers={**headers, "origin": "https://foreign.invalid"}).status_code == 403
        assert client.put("/api/auth/workspace", json={**snapshot(), "filename": "../lease.json"}, headers=headers).status_code == 422
        monkeypatch.delenv("SEARCHCAR_DESKTOP_DATA_DIR")
        assert client.put("/api/auth/workspace", json=snapshot(), headers=headers).status_code == 400


def test_storage_write_failure_is_not_reported_as_success(workspace_api, monkeypatch):
    _, tokens = workspace_api
    monkeypatch.setattr("app.main.save_workspace", lambda *_args: (_ for _ in ()).throw(OSError("fixture failure")))
    with TestClient(app) as client:
        client.cookies.set(settings.auth_cookie_name, tokens[7])
        csrf = client.get("/api/auth/me").json()["csrf_token"]
        assert client.put("/api/auth/workspace", json=snapshot(), headers={"x-csrf-token": csrf}).status_code == 503


def test_registered_extra_sites_restore_without_a_three_site_ceiling(workspace_api, monkeypatch):
    from app.sources import registry
    catalog = dict(getattr(registry, "_CATALOG", {}))
    for key, family in [("sampleauction", "AUCTION"), ("samplemarket", "MARKETPLACE")]:
        catalog[key] = {"key": key, "name": key, "family": family, "ui": "auction" if family == "AUCTION" else "fixture-market", "domains": [], "capabilities": []}
    monkeypatch.setattr(registry, "_CATALOG", catalog, raising=False)
    _, tokens = workspace_api
    value = snapshot()
    for source in ["encar", "iaa", "sampleauction", "samplemarket"]:
        value["sites"][source] = {"source": source, "view": "scans", "projectId": None, "carId": None}
    value["current"] = value["sites"]["samplemarket"]
    with TestClient(app) as client:
        client.cookies.set(settings.auth_cookie_name, tokens[7])
        csrf = client.get("/api/auth/me").json()["csrf_token"]
        response = client.put("/api/auth/workspace", json=value, headers={"x-csrf-token": csrf})
        assert response.status_code == 200, response.text
        assert json.loads(client.get("/api/auth/me").json()["user"]["workspace"]) == value
