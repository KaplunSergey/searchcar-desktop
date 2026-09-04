from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


MAX_REPORT_LOG_BYTES = 2 * 1024 * 1024
MAX_REPORTS = 10
REPORT_PREFIX = "searchcar-support-"
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(activation[_ -]?code|authorization|bearer|cookie|csrf|password|secret|token)"
    r"\s*([=:])\s*[^\s,;]+"
)
_URL_QUERY_VALUE = re.compile(r"([?&][^=&\s]+)=([^&\s]+)")
_LOCAL_PATH = re.compile(
    r"(?:[A-Za-z]:\\[^\s\"']+|/(?:Users|home|private|var)[^\s\"']*)"
)


@dataclass(frozen=True)
class SupportReport:
    name: str
    report_id: str
    created_at: str
    bytes: int
    entries: int

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "report_id": self.report_id,
            "created_at": self.created_at,
            "bytes": self.bytes,
            "entries": self.entries,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_message(value: object) -> str:
    text = str(value)
    text = _SENSITIVE_VALUE.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text)
    text = _URL_QUERY_VALUE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    return _LOCAL_PATH.sub("<local-path>", text)[:2000]


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _sanitized_entries(logs_dir: Path, since: datetime) -> list[dict]:
    entries: list[dict] = []
    used_bytes = 0
    for path in sorted(logs_dir.glob("searchcar-core.jsonl*"), key=lambda item: item.stat().st_mtime if item.is_file() else 0):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                raw = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(raw, dict):
                continue
            timestamp = _timestamp(raw.get("timestamp"))
            if timestamp is None or timestamp < since:
                continue
            entry = {
                "timestamp": timestamp.isoformat(),
                "level": str(raw.get("level", "INFO"))[:16],
                "logger": str(raw.get("logger", "searchcar"))[:160],
                "message": _safe_message(raw.get("message", "")),
            }
            correlation_id = raw.get("correlation_id")
            if isinstance(correlation_id, str) and correlation_id:
                entry["correlation_id"] = correlation_id[:80]
            encoded = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
            if used_bytes + len(encoded) > MAX_REPORT_LOG_BYTES:
                return entries
            used_bytes += len(encoded)
            entries.append(entry)
    return entries


def _remove_expired_reports(reports_dir: Path) -> None:
    reports = sorted(
        (path for path in reports_dir.glob(f"{REPORT_PREFIX}*.zip") if path.is_file() and not path.is_symlink()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in reports[MAX_REPORTS:]:
        path.unlink(missing_ok=True)


def build_support_report(
    logs_dir: Path,
    reports_dir: Path,
    *,
    app_version: str,
    hours: int,
) -> SupportReport:
    if hours not in {1, 24, 72, 168}:
        raise ValueError("diagnostic_period_invalid")
    reports_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    report_id = uuid4().hex[:12].upper()
    name = f"{REPORT_PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}-{report_id}.zip"
    destination = reports_dir / name
    temporary = reports_dir / f".{name}.tmp"
    entries = _sanitized_entries(logs_dir, now - timedelta(hours=hours))
    manifest = {
        "format": "searchcar-support-report",
        "format_version": 1,
        "report_id": report_id,
        "created_at": now.isoformat(),
        "period_hours": hours,
        "app_version": app_version,
        "entries": len(entries),
        "contains": ["manifest.json", "logs.jsonl"],
        "excludes": ["database", "backups", "images", "license_state", "cookies"],
    }
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            archive.writestr(
                "logs.jsonl",
                "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
            )
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != {"manifest.json", "logs.jsonl"}:
                raise ValueError("diagnostic_report_invalid")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    _remove_expired_reports(reports_dir)
    return SupportReport(
        name=name,
        report_id=report_id,
        created_at=now.isoformat(),
        bytes=destination.stat().st_size,
        entries=len(entries),
    )


def support_report_path(reports_dir: Path, name: str) -> Path:
    if Path(name).name != name or not name.startswith(REPORT_PREFIX) or not name.endswith(".zip"):
        raise ValueError("diagnostic_report_name_invalid")
    path = reports_dir / name
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(name)
    return path


def reveal_support_report(reports_dir: Path, name: str) -> None:
    """Reveal one validated local report without passing a path through a shell."""

    path = support_report_path(reports_dir, name)
    if sys.platform == "darwin":
        command = ["open", "-R", str(path)]
    elif sys.platform.startswith("win"):
        command = ["explorer.exe", f"/select,{path}"]
    else:
        command = ["xdg-open", str(path.parent)]
    subprocess.Popen(command, close_fds=True)
