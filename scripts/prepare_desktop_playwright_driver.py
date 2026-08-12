#!/usr/bin/env python3
"""Stage Playwright's Node runtime as a separately executable app resource."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import shutil
import sys
from importlib.metadata import version
from pathlib import Path

import playwright


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DRIVER_DIR = REPOSITORY_ROOT / "desktop" / "runtime" / "playwright-driver"
MANIFEST_NAME = "searchcar-playwright-driver-manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(driver_dir: Path) -> dict[str, object]:
    driver_dir = driver_dir.expanduser().resolve()
    source_dir = Path(inspect.getfile(playwright)).resolve().parent / "driver"
    executable_name = "node.exe" if sys.platform == "win32" else "node"
    source = source_dir / executable_name
    if not source.is_file():
        raise FileNotFoundError(f"playwright Node executable not found: {source}")

    driver_dir.mkdir(parents=True, exist_ok=True)
    destination = driver_dir / executable_name
    shutil.copy2(source, destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    manifest = {
        "playwright_version": version("playwright"),
        "node_executable": executable_name,
        "node_executable_bytes": destination.stat().st_size,
        "node_executable_sha256": sha256_file(destination),
    }
    (driver_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**manifest, "driver_directory": str(driver_dir)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver-dir", type=Path, default=DEFAULT_DRIVER_DIR)
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.driver_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
