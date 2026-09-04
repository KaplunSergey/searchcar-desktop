import json
import zipfile
from datetime import datetime, timezone

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
                "message": "token=do-not-share password:also-secret /Users/alice/SearchCar?code=hidden",
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
