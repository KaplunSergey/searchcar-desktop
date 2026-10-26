"""Real localhost/SSE shutdown on disposable data; no installed app or live sites."""

import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
from time import monotonic, sleep
from types import SimpleNamespace

import httpx
from sqlalchemy.orm import Session

from app.database import Base, create_database_engine
from app.desktop_onboarding import create_desktop_workspace
from app.desktop_runtime import GracefulShutdownController
from app.live_updates import LiveUpdateBroker
from app.models import ScanRun
from app.sqlite_migrations import migrate_sqlite


def test_idle_desktop_exits_with_an_open_event_stream_and_releases_data_lock(tmp_path):
    data = tmp_path / "desktop"
    database = data / "data" / "searchcar.sqlite3"
    database.parent.mkdir(parents=True)
    engine = create_database_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        migrate_sqlite(engine)
        with Session(engine) as db:
            create_desktop_workspace(db, preferred_locale="ru", source="SHUTDOWN_FIXTURE")
            db.commit()
    finally:
        engine.dispose()
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html><title>Shutdown fixture</title>", encoding="utf-8")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    secret = secrets.token_hex(32)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("SEARCHCAR_LICENSE_")}
    environment.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                       SEARCHCAR_DESKTOP_SESSION_SECRET=secret,
                       SEARCHCAR_DESKTOP_PARENT_PID=str(os.getpid()))
    command = [sys.executable, "-m", "app.desktop_runtime", "serve", "--data-dir", str(data),
               "--frontend-dir", str(frontend), "--host", "127.0.0.1", "--port", str(port)]
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
                deadline = monotonic() + 30
                while True:
                    assert process.poll() is None, "server exited before startup; inspect server.log"
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert monotonic() < deadline, "server startup timed out"
                    sleep(0.05)
                assert client.get("/desktop/bootstrap", params={"token": secret}).status_code == 303
                with client.stream("GET", "/api/events") as events:
                    assert events.status_code == 200
                    lines = events.iter_lines()
                    assert next(lines) == ": connected"
                    shutdown_started = monotonic()
                    assert client.post("/desktop/shutdown", params={"token": secret}).status_code == 202
                    # Keep the UI connection open until AFTER the server exits.
                    assert process.wait(timeout=5) == 0, "idle shutdown must not wait for the UI/SSE connection"
                    assert list(lines) == [""], "event stream must end cleanly, not require client disconnect"
                    print(f"Idle desktop shutdown with open SSE: {monotonic() - shutdown_started:.3f}s")
        finally:
            if process.poll() is None:
                process.kill()  # Only the disposable source-runtime process owned by this test.
                process.wait(timeout=5)
    from app.desktop_runtime import DesktopInstanceLock
    assert (data / "runtime" / "desktop.lock").is_file()
    restarted = DesktopInstanceLock(data / "runtime" / "desktop.lock")
    restarted.acquire()
    restarted.release()
    assert "timeout graceful shutdown exceeded" not in (tmp_path / "server.log").read_text()


def test_shutdown_waits_for_partial_report_before_exiting(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'partial.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = create_desktop_workspace(db, preferred_locale="ru", source="SHUTDOWN_FIXTURE")
        run = ScanRun(owner_id=owner.id, kind="PROJECTS", status="RUNNING",
                      payload={"results": [{"car_id": 1}]})
        db.add(run)
        db.commit()
        run_id = run.id
    monkeypatch.setattr("app.database.engine", engine)
    broker = LiveUpdateBroker()
    monkeypatch.setattr("app.live_updates.live_updates", broker)
    events = broker.subscribe(1)
    stop, persist = threading.Event(), threading.Event()

    def worker():
        stop.wait()
        persist.wait()
        with Session(engine) as db:
            run = db.get(ScanRun, run_id)
            assert run.status == "CANCEL_REQUESTED"
            run.status = "CANCELLED"
            db.commit()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    server = SimpleNamespace(should_exit=False)
    controller = GracefulShutdownController(stop, thread)
    controller.attach_server(server)
    try:
        assert controller.request()["cancel_requested"] == [run_id]
        assert events.get(timeout=0.2) is None
        assert not server.should_exit, "server must wait for the worker's committed partial report"
        persist.set()
        thread.join(timeout=2)
        deadline = monotonic() + 2
        while not server.should_exit and monotonic() < deadline:
            sleep(0.01)
        assert server.should_exit
        with Session(engine) as db:
            run = db.get(ScanRun, run_id)
            assert run.status == "CANCELLED"
            assert run.payload["results"] == [{"car_id": 1}]
        assert controller.request()["cancel_requested"] == [run_id]
    finally:
        persist.set()
        thread.join(timeout=2)
        engine.dispose()
