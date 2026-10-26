"""Check compiled navigation and bundled auction time on disposable data."""
import argparse
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, default=Path("desktop/dist"))
    parser.add_argument("--report-dir", type=Path, default=Path("work/compiled-navigation"))
    args = parser.parse_args()
    sidecar, frontend = args.sidecar.resolve(), args.frontend.resolve()
    root = Path(__file__).resolve().parents[1]
    report = args.report_dir.resolve()
    report.mkdir(parents=True, exist_ok=True)
    # Success markers belong to this run, not an earlier binary/check.
    for name in ("compiled-navigation.json", "compiled-auction-time.json", "compiled-auction-profile.json"):
        (report / name).unlink(missing_ok=True)
    if not sidecar.is_file() or not (frontend / "index.html").is_file():
        parser.error("compiled sidecar and built frontend must exist")
    sys.path.insert(0, str(root / "backend"))
    with TemporaryDirectory(prefix="searchcar-compiled-navigation-") as temporary:
        data = Path(temporary)
        os.environ["STORAGE_ROOT"] = str(data / "unused-storage")
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{(data / 'unused.sqlite3').as_posix()}"
        os.environ["SEARCHCAR_COMPILED_SIDECAR"] = str(sidecar)
        os.environ["SEARCHCAR_COMPILED_FRONTEND"] = str(frontend)
        os.environ["SEARCHCAR_COMPILED_REPORT_DIR"] = str(report)
        import pytest
        return pytest.main([str(root / "backend/tests/test_compiled_navigation_smoke.py"), "-q", "--tb=short", f"--junitxml={report / 'compiled-navigation.xml'}"])


if __name__ == "__main__":
    raise SystemExit(main())
