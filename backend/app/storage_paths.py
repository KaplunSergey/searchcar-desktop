from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


def portable_storage_path(value: str | Path | None, storage_root: Path) -> str | None:
    """Return a storage-relative POSIX path suitable for DB persistence.

    Older installations persisted container paths rooted at ``/storage``.
    Desktop installations move between Windows and macOS, so only the part
    below the storage root is durable.
    """

    if value is None or not str(value).strip():
        return None
    raw = str(value).strip().replace("\\", "/")
    if raw.startswith("/storage/"):
        relative = PurePosixPath(raw.removeprefix("/storage/"))
        return relative.as_posix() if not relative.is_absolute() and ".." not in relative.parts else None

    path = Path(value).expanduser()
    if not path.is_absolute():
        if PureWindowsPath(raw).drive:
            return None
        relative = PurePosixPath(raw.lstrip("/"))
        if any(part in {"", ".", ".."} for part in relative.parts):
            return None
        return relative.as_posix()
    try:
        return path.resolve().relative_to(storage_root.expanduser().resolve()).as_posix()
    except ValueError:
        return None


def resolve_storage_path(value: str | Path | None, storage_root: Path) -> Path | None:
    portable = portable_storage_path(value, storage_root)
    if portable is None:
        return None
    candidate = storage_root.expanduser().resolve().joinpath(
        *PurePosixPath(portable).parts
    ).resolve()
    try:
        candidate.relative_to(storage_root.expanduser().resolve())
    except ValueError:
        return None
    return candidate
