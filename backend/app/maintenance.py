from __future__ import annotations

import ctypes
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


def desktop_update_install_path() -> Path | None:
    root = os.environ.get("SEARCHCAR_DESKTOP_DATA_DIR")
    return Path(root) / "runtime" / "update-install.lock" if root else None


def database_maintenance_path(database_path: Path) -> Path:
    # Desktop layout is <data-root>/data/searchcar.sqlite3.
    return database_path.expanduser().resolve().parent.parent / "runtime" / "maintenance.lock"


def maintenance_active() -> bool:
    return any(
        path is not None and path.exists()
        for path in (desktop_maintenance_path(), desktop_update_install_path())
    )


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        # Access denied means the process exists but cannot be queried with
        # the current token. Treat it as alive rather than removing its lock.
        return ctypes.get_last_error() == 5
    try:
        exit_code = wintypes.DWORD()
        return (
            bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
            and exit_code.value == still_active
        )
    finally:
        kernel32.CloseHandle(handle)


def _lock_owner_is_alive(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    return _process_is_alive(pid)


def clear_stale_maintenance_lock() -> bool:
    removed = False
    for path in (desktop_maintenance_path(), desktop_update_install_path()):
        if path is None or not path.exists() or _lock_owner_is_alive(path):
            continue
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            pass
    return removed


def begin_update_install() -> Path:
    path = desktop_update_install_path()
    if path is None:
        raise RuntimeError("desktop_update_install_unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "operation": "UPDATE_INSTALL",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
        if current.get("pid") == os.getpid():
            return path
        raise MaintenanceBusyError("desktop_update_install_already_active") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def finish_update_install() -> bool:
    path = desktop_update_install_path()
    if path is None:
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if payload.get("pid") not in {None, os.getpid()}:
        return False
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
