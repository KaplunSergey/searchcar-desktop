#!/usr/bin/env python3
"""Build the platform-specific SearchCar backend sidecar with Nuitka."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPOSITORY_ROOT / "backend" / "searchcar_core.py"
WORK_ROOT = REPOSITORY_ROOT / "work" / "nuitka"
BINARY_ROOT = REPOSITORY_ROOT / "desktop" / "src-tauri" / "binaries"
TAURI_CONFIG = REPOSITORY_ROOT / "desktop" / "src-tauri" / "tauri.conf.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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


def product_version() -> str:
    config = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    version = config.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("desktop/src-tauri/tauri.conf.json has no product version")
    return version.strip()


def sidecar_source_fingerprint() -> str:
    """Identify payloads that must never share a persistent onefile cache."""

    backend_root = REPOSITORY_ROOT / "backend"
    inputs = [
        *backend_root.joinpath("app").rglob("*.py"),
        backend_root / "app" / "sources" / "catalog.json",
        *backend_root.joinpath("alembic").rglob("*.py"),
        backend_root / "alembic.ini",
        backend_root / "alembic" / "script.py.mako",
        backend_root / "requirements.txt",
        backend_root / "requirements-desktop-build.txt",
        ENTRYPOINT,
        TAURI_CONFIG,
    ]
    digest = hashlib.sha256()
    for path in sorted({item.resolve() for item in inputs}):
        if not path.is_file():
            continue
        digest.update(path.relative_to(REPOSITORY_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def run_nuitka(command: list[str], environment: dict[str, str]) -> None:
    """Run Nuitka with a periodic CI heartbeat while compiler output stays quiet."""

    started_at = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT / "backend",
        env=environment,
    )
    while True:
        try:
            return_code = process.wait(timeout=120)
            break
        except subprocess.TimeoutExpired:
            elapsed_minutes = int((time.monotonic() - started_at) // 60)
            print(
                f"Nuitka is still compiling ({elapsed_minutes} min elapsed); "
                "the C compiler may not emit progress lines.",
                flush=True,
            )
    if return_code:
        raise subprocess.CalledProcessError(return_code, command)


def build(mode: str) -> dict[str, object]:
    target = rust_target_triple()
    work_dir = WORK_ROOT / target / mode
    work_dir.mkdir(parents=True, exist_ok=True)
    report_path = work_dir / "nuitka-report.xml"
    binary_name = output_name(target)
    command = [
        sys.executable,
        "-m",
        "nuitka",
        f"--mode={mode}",
        "--assume-yes-for-downloads",
        "--remove-output",
        f"--output-dir={work_dir}",
        f"--report={report_path}",
        f"--output-filename={binary_name}",
        "--include-package=app",
        f"--include-data-files={REPOSITORY_ROOT / 'backend/app/sources/catalog.json'}=app/sources/catalog.json",
        "--include-package=pwdlib",
        "--include-package=argon2",
        "--include-package=_argon2_cffi_bindings",
        "--include-package=cryptography",
        "--include-package=tzdata",
        "--include-package-data=tzdata",
        "--include-module=_cffi_backend",
        "--include-package-data=alembic",
        "--include-distribution-metadata=playwright",
        "--include-distribution-metadata=uvicorn",
        "--include-distribution-metadata=pydantic",
        "--include-distribution-metadata=SQLAlchemy",
        "--nofollow-import-to=pytest",
        str(ENTRYPOINT),
    ]
    onefile_policy: dict[str, str | bool] = {}
    if mode == "onefile":
        cache_fingerprint = sidecar_source_fingerprint()
        command.remove("--remove-output")
        if platform.system() == "Darwin":
            # A persistent onefile extraction can be left locked by a killed
            # macOS app (or by an older pilot build with the same visible
            # version). The Nuitka stub then hangs before it can start Python,
            # and Tauri reports the generic "local service terminated" error.
            # Use a unique disposable directory on macOS; the Playwright
            # driver and browsers are bundled Tauri resources, so they do not
            # depend on Nuitka's extraction directory surviving a later run.
            cache_spec = (
                "{TEMP}/SearchCar/searchcar-core/"
                + product_version()
                + "-"
                + cache_fingerprint
                + "-{PID}-{RANDOM}"
            )
            cache_mode = "temporary"
        else:
            # Keep the durable, fingerprinted extraction for Windows where it
            # avoids a costly first start without the macOS stale-lock issue.
            cache_spec = (
                "{CACHE_DIR}/SearchCar/searchcar-core/"
                + product_version()
                + "-"
                + cache_fingerprint
            )
            cache_mode = "cached"
        command[3:3] = [
            f"--onefile-cache-mode={cache_mode}",
            f"--onefile-tempdir-spec={cache_spec}",
        ]
        onefile_policy = {
            "onefile_cache_mode": cache_mode,
            "onefile_cache_spec": cache_spec,
            "onefile_cache_fingerprint": cache_fingerprint,
        }
        if platform.system() == "Windows":
            # Disabling inner compression makes Windows' first extraction
            # I/O-bound; NSIS still compresses the final installer. Keep the
            # production sidecar console hidden when Tauri launches it.
            command[3:3] = [
                "--onefile-no-compression",
                "--windows-console-mode=hide",
            ]
            onefile_policy.update(
                onefile_compression=False,
                windows_console_mode="hide",
            )
    environment = os.environ.copy()
    environment["NUITKA_CACHE_DIR"] = str(WORK_ROOT / "cache")
    run_nuitka(command, environment)

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
        "compiled_bytes": compiled.stat().st_size,
        "compiled_sha256": sha256_file(compiled),
        "nuitka_report": str(report_path) if report_path.is_file() else None,
        **onefile_policy,
    }
    if mode == "onefile":
        BINARY_ROOT.mkdir(parents=True, exist_ok=True)
        destination = BINARY_ROOT / binary_name
        shutil.copy2(compiled, destination)
        destination.chmod(destination.stat().st_mode | 0o111)
        result["tauri_sidecar"] = str(destination)
        result["tauri_sidecar_bytes"] = destination.stat().st_size
        result["tauri_sidecar_sha256"] = sha256_file(destination)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("standalone", "onefile"), default="onefile")
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.mode), ensure_ascii=False))


if __name__ == "__main__":
    main()
