"""Native host acceptance using prepared resources and disposable test storage.

No downloads, source requests, installed app changes or release operations.
"""

import argparse
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=Path("work/queue-smoke"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report_dir = args.report_dir.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(root / "backend"))
    with TemporaryDirectory(prefix="searchcar-queue-acceptance-") as temporary:
        data = Path(temporary)
        os.environ["STORAGE_ROOT"] = str(data / "storage")
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{(data / 'unused-default.sqlite3').as_posix()}"
        os.environ["SEARCHCAR_QUEUE_BROWSER_SMOKE"] = "1"
        os.environ["SEARCHCAR_QUEUE_SMOKE_REPORT_DIR"] = str(report_dir)
        from app.desktop_runtime import configure_playwright_environment, configure_playwright_driver
        configure_playwright_environment(root / "desktop/runtime/browsers")
        configure_playwright_driver(root / "desktop/runtime/playwright-driver")
        import pytest
        return pytest.main([
            str(root / "backend/tests/test_multisource_acceptance.py"),
            str(root / "backend/tests/test_multisource_browser_smoke.py"),
            "-q", "-s", "--tb=short", f"--junitxml={report_dir / 'acceptance.xml'}",
        ])


if __name__ == "__main__":
    raise SystemExit(main())
