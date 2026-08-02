#!/usr/bin/env python3
"""Download and validate the Chromium build bundled with SearchCar Desktop."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BROWSER_DIR = REPOSITORY_ROOT / "desktop" / "runtime" / "browsers"


def prepare(browser_dir: Path) -> dict[str, str]:
    browser_dir = browser_dir.expanduser().resolve()
    browser_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_dir)
    environment["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "playwright",
            "install",
            "--only-shell",
            "chromium",
        ],
        check=True,
        env=environment,
    )
    links_dir = browser_dir / ".links"
    if links_dir.is_dir():
        shutil.rmtree(links_dir)

    os.environ.update(
        {
            "PLAYWRIGHT_BROWSERS_PATH": str(browser_dir),
            "PLAYWRIGHT_SKIP_BROWSER_GC": "1",
        }
    )
    executable_names = {"headless_shell", "headless_shell.exe"}
    candidates = sorted(
        path
        for path in browser_dir.rglob("*")
        if path.is_file() and path.name in executable_names
    )
    if not candidates:
        raise FileNotFoundError(f"chromium headless shell not found: {browser_dir}")
    executable = candidates[0]

    persisted_manifest = {
        "playwright_version": version("playwright"),
        "chromium_headless_shell": executable.relative_to(browser_dir).as_posix(),
    }
    (browser_dir / "searchcar-browser-manifest.json").write_text(
        json.dumps(persisted_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        **persisted_manifest,
        "browser_directory": str(browser_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-dir", type=Path, default=DEFAULT_BROWSER_DIR)
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.browser_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
