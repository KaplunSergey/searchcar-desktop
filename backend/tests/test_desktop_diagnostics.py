import json
import zipfile
from datetime import datetime, timezone

from app import desktop_diagnostics
from app.desktop_diagnostics import build_support_report


def test_support_report_keeps_only_redacted_structured_logs(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    now = datetime.now(timezone.utc).isoformat()
    (logs / "searchcar-core.jsonl").write_text(
        "not json\n"
        + json.dumps(
            {
                "timestamp": now,
                "level": "ERROR",
                "logger": "app.scanner",
                "message": (
                    "token=do-not-share password:also-secret "
                    "activation_code=unused-code device_key=private-device-key "
                    "private_key=private-signing-key comment=internal-note "
                    "C:\\Users\\Alice\\SearchCar\\data\\searchcar.sqlite3 "
                    "/Users/alice/SearchCar?code=hidden"
                ),
                "exception": "private stack trace",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_support_report(logs, tmp_path / "diagnostics", app_version="0.1.0", hours=24)

    with zipfile.ZipFile(tmp_path / "diagnostics" / report.name) as archive:
        assert set(archive.namelist()) == {"manifest.json", "logs.jsonl"}
        manifest = json.loads(archive.read("manifest.json"))
        output = archive.read("logs.jsonl").decode("utf-8")
    assert manifest["report_id"] == report.report_id
    assert report.entries == 1
    assert "do-not-share" not in output
    assert "also-secret" not in output
    assert "unused-code" not in output
    assert "private-device-key" not in output
    assert "private-signing-key" not in output
    assert "internal-note" not in output
    assert "alice" not in output
    assert "private stack trace" not in output
    assert "<redacted>" in output
    assert "<local-path>" in output


def test_support_report_accepts_one_hour_period(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "searchcar-core.jsonl").write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "INFO",
                "logger": "test",
                "message": "recent",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_support_report(logs, tmp_path / "diagnostics", app_version="0.1.0", hours=1)

    assert report.entries == 1


def test_capped_report_keeps_latest_events_across_rotations_in_time_order(tmp_path, monkeypatch):
    import os
    from datetime import timedelta

    logs = tmp_path / "logs"
    logs.mkdir()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(desktop_diagnostics, "MAX_REPORT_LOG_BYTES", 700)
    # File mtime is not an event timestamp (for example, after a log copy).
    for index, name in enumerate(("searchcar-core.jsonl.1", "searchcar-core.jsonl")):
        path = logs / name
        path.write_text("".join(json.dumps({
            "timestamp": (now - timedelta(minutes=20 - index * 10 - row)).isoformat(),
            "message": f"{'older' if index == 0 else 'latest'}-{row} token=secret",
        }) + "\n" for row in range(10)), encoding="utf-8")
        os.utime(path, (200 - index, 200 - index))
    report = build_support_report(logs, tmp_path / "reports", app_version="0.4.1", hours=24)
    with zipfile.ZipFile(tmp_path / "reports" / report.name) as archive:
        output = archive.read("logs.jsonl")
        manifest = json.loads(archive.read("manifest.json"))
    entries = [json.loads(line) for line in output.splitlines()]
    assert len(output) <= 700
    assert entries[-1]["message"].startswith("latest-9"), "current failure must not be dropped for older logs"
    assert [entry["timestamp"] for entry in entries] == sorted(entry["timestamp"] for entry in entries)
    assert manifest['logs_truncated'] is True
    assert manifest['logs_oldest_at'] == entries[0]['timestamp']
    assert manifest['logs_newest_at'] == entries[-1]['timestamp']
    assert b"secret" not in output


def test_reveal_support_report_uses_a_validated_macos_command(tmp_path, monkeypatch) -> None:
    reports = tmp_path / "diagnostics"
    reports.mkdir()
    name = "searchcar-support-20260904T120000Z-ABCDEF123456.zip"
    (reports / name).write_bytes(b"zip")
    commands: list[list[str]] = []
    monkeypatch.setattr(desktop_diagnostics.sys, "platform", "darwin")
    monkeypatch.setattr(
        desktop_diagnostics.subprocess,
        "Popen",
        lambda command, **_kwargs: commands.append(command),
    )

    desktop_diagnostics.reveal_support_report(reports, name)

    assert commands == [["open", "-R", str(reports / name)]]
