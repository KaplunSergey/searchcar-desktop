"""Opt-in compiled HTTP acceptance; never uses the installed application's data."""

from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
from math import ceil
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from time import monotonic, sleep

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.database import create_database_engine
from app.desktop_onboarding import create_desktop_workspace
from app.auction_rounds import record_auction_observation
from app.models import AuthSession, AuctionObservation, AuctionRound, AuctionScheduleVersion, Car, Project, ProjectCar, ScanRun
from app.sqlite_migrations import migrate_sqlite


pytestmark = pytest.mark.skipif(not os.environ.get("SEARCHCAR_COMPILED_SIDECAR"), reason="opt-in compiled sidecar smoke")


def test_compiled_authenticated_reads_survive_a_busy_scan_writer(tmp_path):
    report_dir = Path(os.environ['SEARCHCAR_COMPILED_REPORT_DIR']).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    data = tmp_path / 'contention-desktop'
    database = data / 'data' / 'searchcar.sqlite3'
    database.parent.mkdir(parents=True)
    engine = create_database_engine(f'sqlite+pysqlite:///{database.as_posix()}')
    try:
        migrate_sqlite(engine)
        factory = sessionmaker(engine)
        with factory.begin() as db:
            create_desktop_workspace(db, preferred_locale='ru', source='CONTENTION_FIXTURE')
        with compiled_server(data, Path(os.environ['SEARCHCAR_COMPILED_FRONTEND']), report_dir, 'contention', []) as client:
            assert client.get('/api/projects').status_code == 200
            with factory.begin() as db:
                for session in db.scalars(select(AuthSession)):
                    session.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=10)
            with engine.connect() as writer:
                writer.exec_driver_sql('BEGIN IMMEDIATE')
                started = monotonic()
                for path in ('/api/projects', '/api/scans/active', '/api/desktop/runtime') * 5:
                    response = client.get(path)
                    assert response.status_code == 200, f'{path}: status {response.status_code} while writer busy'
                assert monotonic() - started < 3, 'reads waited for the scan write lock'
                writer.rollback()
            assert client.get('/api/projects').status_code == 200
    finally:
        engine.dispose()


def test_compiled_repeated_http_failures_do_not_kill_the_service(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    report_dir = Path(os.environ['SEARCHCAR_COMPILED_REPORT_DIR']).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    data = tmp_path / 'failure-desktop'
    database = data / 'data' / 'searchcar.sqlite3'
    database.parent.mkdir(parents=True)
    engine = create_database_engine(f'sqlite+pysqlite:///{database.as_posix()}')
    try:
        migrate_sqlite(engine)
        with sessionmaker(engine).begin() as db:
            create_desktop_workspace(db, preferred_locale='ru', source='HTTP_FAILURE_FIXTURE')
        with compiled_server(data, Path(os.environ['SEARCHCAR_COMPILED_FRONTEND']), report_dir, 'failures', []) as client:
            assert client.get('/api/projects').status_code == 200
            # Disposable schema fault exercises real 500/traceback cleanup, not a fake response.
            with engine.begin() as connection:
                connection.exec_driver_sql('ALTER TABLE projects RENAME TO fixture_missing_projects')
            try:
                def read(index):
                    path = '/api/projects' if index % 3 else '/api/health'
                    expected = 500 if index % 3 else 200
                    # Uvicorn closes failed-request connections; do not race a shared
                    # client's keep-alive reuse against that expected close.
                    assert client.get(path, headers={'connection': 'close'}).status_code == expected
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(read, range(3000)))
            finally:
                with engine.begin() as connection:
                    connection.exec_driver_sql('ALTER TABLE fixture_missing_projects RENAME TO projects')
            assert client.get('/api/projects').status_code == 200
    finally:
        engine.dispose()


@contextmanager
def compiled_server(data, frontend, report_dir, index, previous_ports, *, source_test_mode=False, delivery_preview=False):
    while True:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        if port not in previous_ports:
            break
    previous_ports.append(port)
    secret = secrets.token_hex(32)
    environment = os.environ.copy()
    # Do not inherit a developer's runtime session, binding or service settings.
    for key in list(environment):
        if key.startswith("SEARCHCAR_LICENSE_") or key in {"SEARCHCAR_DESKTOP_DATA_DIR", "SEARCHCAR_DESKTOP_SESSION_SECRET", "SEARCHCAR_SOURCE_TEST_MODE", "PYTHONPATH", "PYTHONHOME"}:
            environment.pop(key)
    # A host Python/IANA database must not hide missing compiled package data.
    environment["PYTHONTZPATH"] = ""
    environment["SEARCHCAR_DESKTOP_SESSION_SECRET"] = secret
    environment["SEARCHCAR_DESKTOP_PARENT_PID"] = str(os.getpid())
    sidecar = Path(os.environ["SEARCHCAR_COMPILED_SIDECAR"]).resolve()
    command = [str(sidecar), "serve", "--data-dir", str(data), "--frontend-dir", str(frontend), "--host", "127.0.0.1", "--port", str(port)]
    if source_test_mode:
        command.append("--source-test-mode")
        if delivery_preview:
            command.append("--telegram-delivery-preview")
        environment["SEARCHCAR_TELEGRAM_RELAY_URL"] = "https://must-not-use.fixture.test"
        environment["SEARCHCAR_TELEGRAM_DELIVERY_ENABLED"] = "1"
        environment["SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED"] = "0"
    with (report_dir / f"navigation-{index}.stdout.log").open("w") as stdout, (report_dir / f"navigation-{index}.stderr.log").open("w") as stderr:
        with subprocess.Popen(command, env=environment, stdout=stdout, stderr=stderr) as process:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client, ExitStack() as streams:
                ready = False
                try:
                    deadline = monotonic() + 120
                    while True:
                        assert process.poll() is None, "compiled server exited during startup; inspect navigation logs"
                        try:
                            if client.get("/api/health").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        assert monotonic() < deadline, "compiled server startup timeout"
                        sleep(0.2)
                    assert client.get("/").status_code == 403
                    assert client.get("/desktop/bootstrap", params={"token": secret}).status_code == 303
                    assert client.get("/").status_code == 200
                    events = streams.enter_context(client.stream("GET", "/api/events"))
                    assert events.status_code == 200
                    lines = events.iter_lines()
                    assert next(lines) == ": connected"
                    ready = True
                    yield client
                finally:
                    failure = sys.exception()
                    try:
                        if ready:
                            response = client.post("/desktop/shutdown", params={"token": secret})
                            assert response.status_code == 202
                            assert process.wait(timeout=5) == 0, "idle compiled shutdown waited for the open SSE connection"
                            assert list(lines) == [""], "SSE must finish before the UI disconnects"
                    except Exception as cleanup_error:
                        if failure is None:
                            raise
                        failure.add_note(f"Compiled server cleanup also failed: {cleanup_error}")
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=10)


def test_compiled_catalog_and_owner_navigation_survive_new_ports(tmp_path):
    started = monotonic()
    report_dir = Path(os.environ["SEARCHCAR_COMPILED_REPORT_DIR"]).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    frontend = Path(os.environ["SEARCHCAR_COMPILED_FRONTEND"]).resolve()
    data = tmp_path / "disposable-desktop"
    database = data / "data" / "searchcar.sqlite3"
    database.parent.mkdir(parents=True)
    engine = create_database_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        migrate_sqlite(engine)
        with sessionmaker(engine).begin() as db:
            user = create_desktop_workspace(db, preferred_locale="uk", source="COMPILED_NAVIGATION_FIXTURE")
            owner_id = user.id
        route = lambda source, view, project=None, car=None: {"source": source, "view": view, "projectId": project, "carId": car}
        snapshot = {"current": route("iaa", "source-settings"), "sites": {
            "encar": route("encar", "scans"), "copart": route("copart", "car", 4, 40), "iaa": route("iaa", "source-settings"),
        }}
        ports = []
        def profile(client):
            response = client.get("/api/auth/me")
            assert response.status_code == 200
            result = response.json()
            assert result["user"]["id"] == owner_id
            assert "workspace" in result["user"], "old compiled artifact: native workspace API absent"
            return result

        with compiled_server(data, frontend, report_dir, 1, ports) as client:
            me = profile(client)
            assert me["user"]["workspace"] is None
            recipients = client.get("/api/telegram/recipients")
            assert recipients.status_code == 200, recipients.text
            assert recipients.json() == {"available": False, "issue": "telegram_relay_not_configured", "recipients": []}
            headers = {"x-csrf-token": me["csrf_token"]}
            assert client.put("/api/auth/workspace", json=snapshot).status_code == 403
            invalid = {**snapshot, "current": route("unknown-source", "scans")}
            assert client.put("/api/auth/workspace", json=invalid, headers=headers).status_code == 422
            assert client.put("/api/auth/workspace", json=snapshot, headers=headers).status_code == 200
            options = client.get("/api/scans/launch-options")
            assert options.status_code == 200
            assert any(site["source_key"] == "encar" and site["family"] == "MARKETPLACE" for site in options.json()["sources"])
        saved = data / "ui" / f"workspace-{owner_id}.json"
        assert json.loads(saved.read_text(encoding="utf-8")) == snapshot
        with compiled_server(data, frontend, report_dir, 2, ports) as client:
            me = profile(client)
            assert json.loads(me["user"]["workspace"]) == snapshot
            snapshot["current"] = route("copart", "settings")
            assert client.put("/api/auth/workspace", json=snapshot, headers={"x-csrf-token": me["csrf_token"]}).status_code == 200
        with compiled_server(data, frontend, report_dir, 3, ports) as client:
            assert json.loads(profile(client)["user"]["workspace"]) == snapshot
        with sessionmaker(engine)() as db:
            assert db.scalar(select(func.count()).select_from(ScanRun)) == 0
        with Path(os.environ["SEARCHCAR_COMPILED_SIDECAR"]).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        report = {"status": "ok", "kind": "compiled-http-not-native-gui", "starts": 3, "ports": ports,
                  "catalog_validation": "ok", "navigation_restore": "ok", "csrf": "ok", "jobs_created": 0,
                  "open_sse_shutdown": "ok",
                  "sidecar_sha256": digest,
                  "elapsed_seconds": round(monotonic() - started, 2)}
        (report_dir / "compiled-navigation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    finally:
        engine.dispose()


def test_compiled_preview_restores_projects_and_allows_auction_project_creation(tmp_path):
    from urllib.parse import urlencode
    from app.source_test_profile import PROFILE_MARKER, PROFILE_SIGNATURE

    report_dir = Path(os.environ["SEARCHCAR_COMPILED_REPORT_DIR"]).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    frontend = Path(os.environ["SEARCHCAR_COMPILED_FRONTEND"]).resolve()
    data = tmp_path / "existing-desktop"
    profile = data / "source-tests"
    (profile / "data").mkdir(parents=True)
    (profile / PROFILE_MARKER).write_text(PROFILE_SIGNATURE, encoding="utf-8")
    # A distinct ordinary profile must remain untouched and invisible here.
    (data / "data").mkdir()
    sentinel = data / "data" / "searchcar.sqlite3"
    sentinel.write_bytes(b"ordinary profile must not be opened")
    database = profile / "data" / "searchcar.sqlite3"
    engine = create_database_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    factory = sessionmaker(engine)
    copart = "https://www.copart.com/lotSearchResults?" + urlencode({
        "searchCriteria": json.dumps({"query": ["*"], "filter": {"MAKE": ['search_lot_make_desc:"BMW"']}})})
    urls = {"copart": copart, "iaa": "https://www.iaai.com/Search?url=cHJldmlldw=="}
    try:
        migrate_sqlite(engine)
        with factory.begin() as db:
            owner = create_desktop_workspace(db, source="COMPILED_PREVIEW_FIXTURE")
            for key, url in urls.items():
                db.add(Project(owner_id=owner.id, source_key=key, name=f"Existing {key}",
                               name_key=f"existing {key}", search_url=url))
        ports = []
        for launch in (1, 2):
            with compiled_server(data, frontend, report_dir, f"preview-{launch}", ports,
                                 source_test_mode=True, delivery_preview=launch==2) as client:
                assert client.get("/api/desktop/runtime").json()["source_test_mode"] is True
                projects = client.get("/api/projects").json()
                assert {p["name"] for p in projects} >= {"Existing copart", "Existing iaa"}
                options = client.get("/api/scans/launch-options").json()
                availability = {s["source_key"]: s for s in options["sources"]}
                assert availability["copart"]["can_search"] is True
                assert availability["iaa"]["can_search"] is True
                pairing = client.get("/api/telegram/recipients")
                assert pairing.status_code == 200
                if launch == 1:
                    assert pairing.json() == {"available": False, "issue": "telegram_device_binding_required", "recipients": []}
                    (profile / "license").mkdir(exist_ok=True)
                    (profile / "license" / "binding.json").write_text(json.dumps({
                        "protocol_version": 1, "license_id": "10000000-0000-4000-8000-000000000001",
                        "device_id": "20000000-0000-4000-8000-000000000001"}), encoding="utf-8")
                assert client.get("/api/telegram/recipients").json() == {"available": True, "issue": None, "recipients": []}
                first_project = next(p for p in projects if p["source_key"] == "copart")
                status = client.get(f"/api/projects/{first_project['id']}/telegram?source_key=copart").json()
                assert status["available"] is True and status["issue"] is None
                assert status["delivery_enabled"] is (launch==2)
                assert status["chat_pairing_enabled"] is True
                assert client.get("/api/desktop/runtime").json()["telegram_delivery_enabled"] is (launch==2)
                if launch == 2:
                    reschedules_path = f"/api/projects/{first_project['id']}/telegram-reschedules?source_key=copart"
                    reschedules = client.get(reschedules_path)
                    assert reschedules.status_code == 200, reschedules.text
                    assert reschedules.json()["enabled"] is True
                    assert reschedules.json()["recipient"] is None
                    assert reschedules.json()["counts"] == {}
                    assert client.get(f"/api/projects/{first_project['id']}/telegram-automatic?source_key=copart").json()["enabled"] is False
                    csrf = client.get("/api/auth/me").json()["csrf_token"]
                    response = client.post(reschedules_path, json={"enabled": False,
                        "computer_timezone": "Europe/Warsaw", "binding_id": None},
                        headers={"x-csrf-token": csrf})
                    assert response.status_code == 200, response.text
                    assert response.json()["enabled"] is False
                    assert client.get(reschedules_path).json()["enabled"] is False
                if launch == 1:
                    csrf = client.get("/api/auth/me").json()["csrf_token"]
                    for key, url in urls.items():
                        response = client.post("/api/projects", json={"name": f"New {key}",
                            "source_key": key, "search_url": url + "&preview=new"},
                            headers={"x-csrf-token": csrf})
                        assert response.status_code == 201, response.text
                else:
                    assert {p["name"] for p in projects} == {"Existing copart", "Existing iaa", "New copart", "New iaa"}
                assert sentinel.read_bytes() == b"ordinary profile must not be opened"
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(ScanRun)) == 0
        from app.telegram_delivery import TelegramOutbox
        with TelegramOutbox(profile/'notifications'/'telegram-outbox.sqlite3')._db() as outbox:
            assert outbox.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
        (report_dir / "compiled-auction-profile.json").write_text(json.dumps({
            "status": "ok", "kind": "compiled-http-not-native-gui", "starts": 2,
            "existing_projects": 2, "projects_created": 2, "auction_modules": ["copart", "iaa"],
            "ordinary_profile_untouched": True, "jobs_created": 0,
            "telegram_relay": "staging", "telegram_delivery_modes": [False, True], "telegram_jobs_created": 0,
            "telegram_chat_pairing_enabled": True,
            "telegram_reschedules": {"default_on": True, "independent_today_off": True,
                "disable_persists": True, "jobs_created": 0},
        }, indent=2) + "\n", encoding="utf-8")
    finally:
        engine.dispose()


def test_compiled_auction_time_uses_bundled_timezone_data_without_mutating_rounds(tmp_path):
    """Characterization gate for packaged time projections, not a live adapter."""
    report_dir = Path(os.environ["SEARCHCAR_COMPILED_REPORT_DIR"]).resolve()
    frontend = Path(os.environ["SEARCHCAR_COMPILED_FRONTEND"]).resolve()
    data = tmp_path / "disposable-auctions"
    database = data / "data" / "searchcar.sqlite3"
    database.parent.mkdir(parents=True)
    engine = create_database_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    factory = sessionmaker(engine)
    now = datetime.now(timezone.utc)
    instant = (now + timedelta(hours=4)).replace(microsecond=0)
    # Fixed independent expectations: US spring gap and both fall repeated hours.
    dst = [
        ("2026-03-08T01:30:00-05:00", "2026-03-08T06:30:00+00:00", "2026-03-08T07:30:00+01:00", "2026-03-07T22:30:00-08:00", "2026-03-08T15:30:00+09:00"),
        ("2026-03-08T03:30:00-04:00", "2026-03-08T07:30:00+00:00", "2026-03-08T08:30:00+01:00", "2026-03-07T23:30:00-08:00", "2026-03-08T16:30:00+09:00"),
        ("2026-11-01T01:30:00-04:00", "2026-11-01T05:30:00+00:00", "2026-11-01T06:30:00+01:00", "2026-10-31T22:30:00-07:00", "2026-11-01T14:30:00+09:00"),
        ("2026-11-01T01:30:00-05:00", "2026-11-01T06:30:00+00:00", "2026-11-01T07:30:00+01:00", "2026-10-31T23:30:00-07:00", "2026-11-01T15:30:00+09:00"),
    ]
    schedules = [
        {"event_kind": kind, "source_timezone": "UTC", "local_datetime": instant.isoformat()}
        for kind in ("START", "END", "UNKNOWN")
    ] + [
        {"event_kind": "START", "source_timezone": "CST", "local_date": now.date().isoformat(), "raw": "Unconfirmed CST fixture"},
    ]
    schedules.extend({"event_kind": "START", "source_timezone": zone, "local_date": now.date().isoformat()} for zone in ("UTC", "America/New_York"))
    schedules.extend({"event_kind": "START", "source_timezone": "America/New_York", "local_datetime": row[0]} for row in dst)
    try:
        migrate_sqlite(engine)
        with factory.begin() as db:
            owner = create_desktop_workspace(db, source="COMPILED_TIME_FIXTURE")
            for project_id, source in [(1, "copart"), (2, "iaa")]:
                db.add(Project(id=project_id, owner_id=owner.id, source_key=source, name=source, name_key=source, search_url=f"https://{source}.example.test/search"))
            db.flush()
            for car_id, schedule in enumerate(schedules, 1):
                project_id, source = (2, "iaa") if car_id == 2 else (1, "copart")
                car = Car(id=car_id, source_key=source, source_listing_id="same-id" if car_id <= 2 else f"time-{car_id}", title="Compiled time fixture", url=f"https://{source}.example.test/lot/{car_id}")
                db.add(car)
                db.flush()
                db.add(ProjectCar(project_id=project_id, car_id=car_id))
                record_auction_observation(db, car, observation_key="fixture", observed_at=now, sale_id="fixture-sale", schedule=schedule)

        def snapshot():
            with factory() as db:
                return {
                    "rounds": [(row.id, row.car_id, row.is_current, row.evidence_kind, row.last_observed_at) for row in db.scalars(select(AuctionRound).order_by(AuctionRound.id))],
                    "versions": [(row.id, row.round_id, row.utc_at, row.precision, row.event_kind, row.evidence) for row in db.scalars(select(AuctionScheduleVersion).order_by(AuctionScheduleVersion.id))],
                    "observations": [(row.id, row.applied, row.payload) for row in db.scalars(select(AuctionObservation).order_by(AuctionObservation.id))],
                    "cars": [(row.id, row.status) for row in db.scalars(select(Car).order_by(Car.id))],
                    "jobs": db.scalar(select(func.count()).select_from(ScanRun)),
                }

        before = snapshot()
        projections = {}
        with compiled_server(data, frontend, report_dir, "time", []) as client:
            def lot(car_id, zone="UTC"):
                response = client.get(f"/api/cars/{car_id}/auction", params={"computer_timezone": zone})
                assert response.status_code == 200, response.text
                payload = response.json()
                return payload["lot"], datetime.fromisoformat(payload["generated_at"])

            for index, row in enumerate(dst, 7):
                projections[str(index)] = {}
                for zone, expected in zip(("UTC", "Europe/Warsaw", "America/Los_Angeles", "Asia/Tokyo"), row[1:]):
                    value, _clock = lot(index, zone)
                    assert value["schedule"]["utc_at"] == row[1]
                    assert value["time"]["local_datetime"] == expected
                    projections[str(index)][zone] = expected
            for car_id, state in [(1, "UPCOMING_START"), (2, "UPCOMING_END")]:
                value, clock = lot(car_id)
                assert value["time"]["time_state"] == state
                assert value["time"]["remaining_seconds"] == max(0, ceil((instant - clock).total_seconds()))
                assert value["source_listing_id"] == "same-id"
                assert value["source_key"] == ("iaa" if car_id == 2 else "copart")
            unknown_kind, _ = lot(3)
            assert unknown_kind["time"]["time_state"] == "EVENT_KIND_UNKNOWN"
            assert unknown_kind["time"]["remaining_seconds"] is None
            cst, _ = lot(4)
            assert cst["time"]["time_state"] == "TIME_UNKNOWN"
            assert cst["schedule"]["evidence"]["source_timezone"] == "CST"
            assert cst["time"]["remaining_seconds"] is None
            assert client.get("/api/cars/1/auction", params={"computer_timezone": "CST"}).status_code == 400
            date_only, clock = lot(5)
            assert date_only["time"]["is_today"] == (clock.date() == now.date())
            assert date_only["time"]["time_state"] == "TIME_NOT_SPECIFIED"
            assert date_only["time"]["remaining_seconds"] is None
            us_day, clock = lot(6)
            if clock.date() == now.date():
                assert us_day["time"]["time_state"] == "LOCAL_DAY_UNCERTAIN"
                assert us_day["time"]["is_today"] is None
            assert us_day["time"]["remaining_seconds"] is None
            response = client.get("/api/projects/1/auction-lots", params={"computer_timezone": "UTC", "today_only": "true"})
            assert response.status_code == 200, response.text
            today = response.json()
            expected = {5} if today["local_date"] == now.date().isoformat() else set()
            if today["local_date"] == instant.date().isoformat():
                expected.update([1, 3])
            expected.update(index for index, row in enumerate(dst, 7) if row[1][:10] == today["local_date"])
            assert {value["id"] for value in today["lots"]} == expected
        assert snapshot() == before, "read/time projection must not create rounds, change phase/status or start jobs"
        with Path(os.environ["SEARCHCAR_COMPILED_SIDECAR"]).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        report = {"status": "ok", "kind": "compiled-http-not-native-gui", "system_tzpath": "disabled", "external_pythonpath": "removed", "timezone_projections": projections,
                  "start_end_unknown": "ok", "date_only": "ok", "today_filter": "ok", "read_only_rounds": "ok", "jobs_created": 0, "open_sse_shutdown": "ok", "sidecar_sha256": digest}
        (report_dir / "compiled-auction-time.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    finally:
        engine.dispose()
