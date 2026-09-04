#!/usr/bin/env python3
"""Create a local CycloneDX dependency inventory without extra tooling.

The report deliberately stays out of Git and CI artifacts. It is a release-time
snapshot based on the repository's pinned lock files, not a vulnerability scan.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid5, NAMESPACE_URL


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts" / "sbom" / "searchcar-desktop.cdx.json"
PNPM_PACKAGE = re.compile(r"^  (?P<quoted>'(?P<value>[^']+)'|(?P<plain>[^:\s]+)):\s*$")
PYTHON_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[^]]+\])?==(?P<version>[^\s;]+)$")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def component(ecosystem: str, name: str, version: str, source: str) -> dict[str, object]:
    encoded_name = quote(name, safe="@/")
    return {
        "type": "library",
        "name": name,
        "version": version,
        "purl": f"pkg:{ecosystem}/{encoded_name}@{quote(version, safe='.~-')}",
        "properties": [{"name": "searchcar:lock-source", "value": source}],
    }


def npm_components(lock_path: Path) -> list[dict[str, object]]:
    components: list[dict[str, object]] = []
    in_packages = False
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        if line == "packages:":
            in_packages = True
            continue
        if not in_packages:
            continue
        match = PNPM_PACKAGE.match(line)
        if not match:
            continue
        key = match.group("value") or match.group("plain")
        if "@" not in key:
            continue
        name, version = key.rsplit("@", 1)
        if not name or not version:
            continue
        components.append(component("npm", name, version.split("(", 1)[0], "pnpm-lock.yaml"))
    return components


def python_components(requirements_path: Path) -> list[dict[str, object]]:
    components: list[dict[str, object]] = []
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        match = PYTHON_REQUIREMENT.fullmatch(line)
        if not match:
            raise ValueError(f"unrecognised pinned Python dependency: {line}")
        components.append(
            component("pypi", match.group("name"), match.group("version"), "backend/requirements.txt")
        )
    return components


def rust_components(lock_path: Path) -> list[dict[str, object]]:
    # Cargo.lock is TOML, but its package blocks are deliberately simple.  Do
    # not add a TOML dependency just for a release-time inventory; this parser
    # also keeps the script usable with the system Python on older macOS.
    components: list[dict[str, object]] = []
    for block in lock_path.read_text(encoding="utf-8").split("[[package]]"):
        name = re.search(r'^name = "(?P<value>[^"]+)"$', block, flags=re.MULTILINE)
        version = re.search(r'^version = "(?P<value>[^"]+)"$', block, flags=re.MULTILINE)
        if name and version:
            components.append(
                component("cargo", name.group("value"), version.group("value"), "desktop/src-tauri/Cargo.lock")
            )
    return components


def unique_sorted(components: list[dict[str, object]]) -> list[dict[str, object]]:
    by_purl = {str(item["purl"]): item for item in components}
    return [by_purl[purl] for purl in sorted(by_purl)]


def main() -> int:
    sources = [
        ROOT / "pnpm-lock.yaml",
        ROOT / "backend" / "requirements.txt",
        ROOT / "desktop" / "src-tauri" / "Cargo.lock",
    ]
    components = unique_sorted(
        npm_components(sources[0]) + python_components(sources[1]) + rust_components(sources[2])
    )
    source_hashes = {str(path.relative_to(ROOT)): file_hash(path) for path in sources}
    identity = json.dumps(source_hashes, sort_keys=True, separators=(",", ":"))
    report = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid5(NAMESPACE_URL, identity)}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tools": [{"vendor": "SearchCar", "name": "generate_sbom.py"}],
            "component": {
                "type": "application",
                "name": "searchcar-desktop",
                "version": json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"],
            },
            "properties": [
                {"name": f"searchcar:sha256:{name}", "value": digest}
                for name, digest in source_hashes.items()
            ],
        },
        "components": components,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} with {len(components)} components.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"SBOM generation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
