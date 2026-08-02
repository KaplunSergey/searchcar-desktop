from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


class MaintenanceBusyError(RuntimeError):
    pass


def desktop_maintenance_path() -> Path | None:
    root = os.environ.get("SEARCHCAR_DESKTOP_DATA_DIR")
    return Path(root) / "runtime" / "maintenance.lock" if root else None


def database_maintenance_path(database_path: Path) -> Path:
    # Desktop layout is <data-root>/data/searchcar.sqlite3.
    return database_path.expanduser().resolve().parent.parent / "runtime" / "maintenance.lock"


def maintenance_active() -> bool:
    path = desktop_maintenance_path()
    return bool(path and path.exists())


def clear_stale_maintenance_lock() -> bool:
    path = desktop_maintenance_path()
    if path is None or not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pid = 0
    if pid > 0:
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            pass
        except PermissionError:
            return False
        except OSError:
            pass
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


@contextmanager
def maintenance_lock(database_path: Path, operation: str) -> Iterator[Path]:
    path = database_maintenance_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise MaintenanceBusyError("desktop_maintenance_already_active") from exc
    try:
        payload = {
            "pid": os.getpid(),
            "operation": operation,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
        yield path
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
