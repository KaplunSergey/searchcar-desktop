"""A failed compiled gate must not leave a previous success as current evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

import pytest


@pytest.mark.parametrize("missing_sidecar", [False, True])
def test_compiled_runner_removes_previous_green_and_preserves_failure_logs(tmp_path, missing_sidecar):
    root = Path(__file__).resolve().parents[2]
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("test frontend", encoding="utf-8")
    report = tmp_path / "reports"
    report.mkdir()
    markers = [report / name for name in ("compiled-navigation.json", "compiled-auction-time.json", "compiled-auction-profile.json")]
    for path in markers:
        path.write_text(json.dumps({"status": "ok", "old": True}), encoding="utf-8")
    unrelated = report / "keep.txt"
    unrelated.write_text("preserve unrelated evidence", encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "backend")
    result = subprocess.run(
        [sys.executable, str(root / "scripts/compiled_navigation_smoke.py"),
         "--sidecar", str(tmp_path / "missing-sidecar") if missing_sidecar else sys.executable,
         "--frontend", str(frontend), "--report-dir", str(report)],
        env=environment, capture_output=True, text=True, timeout=40,
    )
    # Python is deliberately not a sidecar: it exits on the serve argument.
    assert result.returncode == (2 if missing_sidecar else 1), result.stdout + result.stderr
    assert all(not path.exists() for path in markers), "stale green report survived a failed run"
    if missing_sidecar:
        assert "compiled sidecar and built frontend must exist" in result.stderr
    else:
        assert "compiled server exited during startup" in result.stdout
        suite = ElementTree.parse(report / "compiled-navigation.xml").getroot().find("testsuite")
        cases = suite.findall("testcase")
        assert cases and int(suite.attrib["tests"]) == int(suite.attrib["failures"]) == len(cases)
        assert int(suite.attrib["errors"]) == int(suite.attrib["skipped"]) == 0
        assert all(case.find("failure") is not None and
                   "compiled server exited during startup" in case.find("failure").attrib["message"]
                   for case in cases)
        assert (report / "navigation-time.stderr.log").stat().st_size > 0
    assert unrelated.read_text(encoding="utf-8") == "preserve unrelated evidence"
