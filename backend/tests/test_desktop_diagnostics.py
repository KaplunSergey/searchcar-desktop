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
