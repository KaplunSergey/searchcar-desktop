"""Shared metadata must survive native packaging and invalidate payload caches."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from app.sources.registry import source_catalog
from app.sources import registry


def test_shipped_catalog_has_unique_safe_keys_and_complete_ui_metadata():
    catalog = source_catalog()
    raw = json.loads(Path(registry.__file__).with_name("catalog.json").read_text(encoding="utf-8"))
    assert tuple(raw) == catalog, "duplicate keys must not silently disappear during registration"
    assert len({source["key"] for source in catalog}) == len(catalog)
    for source in catalog:
        assert re.fullmatch(r"[a-z][a-z0-9-]{0,31}", source["key"])
        assert source["family"] in {"MARKETPLACE", "AUCTION"}
        assert source["name"] and source["ui"]
        assert source["domains"] and all(
            re.fullmatch(r"[a-z0-9.-]+", host) and "." in host for host in source["domains"]
        )
        assert isinstance(source["capabilities"], list)


@pytest.fixture
def builder(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts" / "build_desktop_sidecar.py"
    spec = importlib.util.spec_from_file_location("catalog_sidecar_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(module, "WORK_ROOT", tmp_path / "work")
    monkeypatch.setattr(module, "ENTRYPOINT", tmp_path / "backend" / "searchcar_core.py")
    monkeypatch.setattr(module, "TAURI_CONFIG", tmp_path / "desktop" / "tauri.conf.json")
    module.TAURI_CONFIG.parent.mkdir(parents=True)
    module.TAURI_CONFIG.write_text(json.dumps({"version": "0.4.0"}), encoding="utf-8")
    catalog = tmp_path / "backend" / "app" / "sources" / "catalog.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text("[]", encoding="utf-8")
    return module, catalog


def test_catalog_changes_invalidate_onefile_cache_without_a_version_bump(builder):
    module, catalog = builder
    before = module.sidecar_source_fingerprint()
    catalog.write_text(json.dumps([{"key": "additional-source"}]), encoding="utf-8")
    assert module.sidecar_source_fingerprint() != before


@pytest.mark.parametrize("system,machine", [("Darwin", "arm64"), ("Windows", "AMD64")])
def test_native_builder_explicitly_includes_shared_catalog(builder, monkeypatch, system, machine):
    module, catalog = builder
    monkeypatch.setattr(module.platform, "system", lambda: system)
    monkeypatch.setattr(module.platform, "machine", lambda: machine)
    command = []

    class CapturedCommand(Exception):
        pass

    def capture(args, environment):
        command.extend(args)
        raise CapturedCommand

    monkeypatch.setattr(module, "run_nuitka", capture)
    with pytest.raises(CapturedCommand):
        module.build("onefile")
    assert f"--include-data-files={catalog}=app/sources/catalog.json" in command
    assert any(arg.startswith("--onefile-tempdir-spec=") and module.sidecar_source_fingerprint() in arg for arg in command)


@pytest.mark.parametrize(
    ("system", "expected_mode", "expected_marker"),
    [
        ("Darwin", "temporary", "{TEMP}"),
        ("Windows", "cached", "{CACHE_DIR}"),
    ],
)
def test_onefile_extraction_policy_matches_platform(builder, monkeypatch, system, expected_mode, expected_marker):
    module, _ = builder
    monkeypatch.setattr(module.platform, "system", lambda: system)
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64" if system == "Darwin" else "AMD64")
    command = []

    class CapturedCommand(Exception):
        pass

    def capture(args, environment):
        command.extend(args)
        raise CapturedCommand

    monkeypatch.setattr(module, "run_nuitka", capture)
    with pytest.raises(CapturedCommand):
        module.build("onefile")

    assert f"--onefile-cache-mode={expected_mode}" in command
    tempdir = next(arg for arg in command if arg.startswith("--onefile-tempdir-spec="))
    assert expected_marker in tempdir
    if system == "Darwin":
        assert "-{PID}-{RANDOM}" in tempdir
