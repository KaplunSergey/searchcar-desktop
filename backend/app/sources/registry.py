"""Only explicitly shipped adapters may execute; unknown keys never fall back."""

import json
from pathlib import Path

from .contracts import SourceAdapter, SourceDescriptor, SourceUnavailableError


_CATALOG = {item["key"]: item for item in json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))}


def source_catalog() -> tuple[dict, ...]:
    """UI/family metadata includes read-only sites whose adapter is not shipped."""
    return tuple(_CATALOG.values())


def is_known_source(key: str) -> bool:
    return key in _CATALOG


def is_auction_source(key: str) -> bool:
    return _CATALOG.get(key, {}).get("family") == "AUCTION"


def _load_encar() -> SourceAdapter:
    # Explicit built-ins only; no module names from user input.
    from .encar import EncarSource
    return EncarSource()


_FACTORIES = {"encar": _load_encar}
_DESCRIPTORS = {
    key: SourceDescriptor(key=key, family=_CATALOG[key]["family"],
                          domains=tuple(_CATALOG[key]["domains"]),
                          capabilities=frozenset(_CATALOG[key]["capabilities"]))
    for key in _FACTORIES
}
_SOURCES: dict[str, SourceAdapter] = {}


def _load_copart():
    from .copart import CopartSource
    return CopartSource()


def _load_iaa():
    from .iaa import IAASource
    return IAASource()


_TEST_FACTORIES = {"copart": _load_copart, "iaa": _load_iaa}


def _test_sources_enabled():
    from ..database import settings
    from ..source_test_profile import validate_source_test_isolation
    if not settings.source_test_mode:
        return False
    validate_source_test_isolation(settings)
    return True


def list_sources() -> tuple[SourceDescriptor, ...]:
    return tuple(_DESCRIPTORS.values()) + (tuple(get_source_descriptor(key) for key in _TEST_FACTORIES) if _test_sources_enabled() else ())


def get_source_descriptor(key: str) -> SourceDescriptor:
    if key in _TEST_FACTORIES and _test_sources_enabled():
        return SourceDescriptor(key=key,family=_CATALOG[key]['family'],domains=tuple(_CATALOG[key]['domains']),capabilities=frozenset({'SEARCH','LIST_ONLY'}))
    try:
        return _DESCRIPTORS[key]
    except KeyError:
        raise ValueError(f"Unsupported listing source: {key}") from None


def get_source(key: str) -> SourceAdapter:
    descriptor = get_source_descriptor(key)
    try:
        if key not in _SOURCES:
            source = (_FACTORIES.get(key) or _TEST_FACTORIES[key])()
            if source.descriptor != descriptor or descriptor.contract_version != 1:
                raise ValueError("Incompatible source contract")
            _SOURCES[key] = source
        return _SOURCES[key]
    except Exception as exc:
        # The catalog and stored records stay readable. Failed loads are not
        # cached, so a subsequent explicit attempt can recover.
        raise SourceUnavailableError(f"Listing source unavailable: {key}") from exc
