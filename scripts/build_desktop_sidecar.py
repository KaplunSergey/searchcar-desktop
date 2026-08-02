#!/usr/bin/env python3
"""Build the platform-specific SearchCar backend sidecar with Nuitka."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPOSITORY_ROOT / "backend" / "searchcar_core.py"
WORK_ROOT = REPOSITORY_ROOT / "work" / "nuitka"
BINARY_ROOT = REPOSITORY_ROOT / "desktop" / "src-tauri" / "binaries"


def rust_target_triple() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "aarch64-apple-darwin"
    if system == "Windows" and machine in {"amd64", "x86_64"}:
        return "x86_64-pc-windows-msvc"
    raise RuntimeError(f"unsupported desktop build target: {system}/{machine}")


def output_name(target: str) -> str:
    suffix = ".exe" if platform.system() == "Windows" else ""
    return f"searchcar-core-{target}{suffix}"


def build(mode: str) -> dict[str, str]:
    target = rust_target_triple()
    work_dir = WORK_ROOT / target / mode
    work_dir.mkdir(parents=True, exist_ok=True)
    binary_name = output_name(target)
    command = [
        sys.executable,
        "-m",
        "nuitka",
        f"--mode={mode}",
        "--assume-yes-for-downloads",
        "--remove-output",
        f"--output-dir={work_dir}",
        f"--output-filename={binary_name}",
        "--include-package=app",
        "--include-package=pwdlib",
        "--include-package=argon2",
        "--include-package=_argon2_cffi_bindings",
        "--include-module=_cffi_backend",
        "--include-package-data=alembic",
        "--include-distribution-metadata=playwright",
        "--include-distribution-metadata=uvicorn",
        "--include-distribution-metadata=pydantic",
        "--include-distribution-metadata=SQLAlchemy",
        "--nofollow-import-to=pytest",
        str(ENTRYPOINT),
    ]
    environment = os.environ.copy()
    environment["NUITKA_CACHE_DIR"] = str(WORK_ROOT / "cache")
    subprocess.run(
        command,
        check=True,
        cwd=REPOSITORY_ROOT / "backend",
        env=environment,
    )

    candidates = list(work_dir.rglob(binary_name))
    if not candidates:
        candidates = list(work_dir.rglob(binary_name + ".bin"))
    if not candidates:
        raise FileNotFoundError(f"compiled sidecar not found in {work_dir}")
    compiled = min(candidates, key=lambda path: len(path.parts))

    result = {
        "mode": mode,
        "target": target,
        "compiled": str(compiled),
    }
    if mode == "onefile":
        BINARY_ROOT.mkdir(parents=True, exist_ok=True)
        destination = BINARY_ROOT / binary_name
        shutil.copy2(compiled, destination)
        destination.chmod(destination.stat().st_mode | 0o111)
        result["tauri_sidecar"] = str(destination)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("standalone", "onefile"), default="onefile")
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.mode), ensure_ascii=False))


if __name__ == "__main__":
    main()
