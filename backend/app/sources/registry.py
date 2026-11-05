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


def _load_copart():
    from .copart import CopartSource
    return CopartSource()


def _load_iaa():
    from .iaa import IAASource
    return IAASource()


_FACTORIES = {"encar": _load_encar, "copart": _load_copart, "iaa": _load_iaa}
_DESCRIPTORS = {
    key: SourceDescriptor(key=key, family=_CATALOG[key]["family"],
                          domains=tuple(_CATALOG[key]["domains"]),
                          capabilities=frozenset(_CATALOG[key]["capabilities"]))
    for key in _FACTORIES
}
_SOURCES: dict[str, SourceAdapter] = {}


def list_sources() -> tuple[SourceDescriptor, ...]:
    return tuple(_DESCRIPTORS.values())


def get_source_descriptor(key: str) -> SourceDescriptor:
    try:
        return _DESCRIPTORS[key]
    except KeyError:
        raise ValueError(f"Unsupported listing source: {key}") from None


def get_source(key: str) -> SourceAdapter:
    descriptor = get_source_descriptor(key)
    try:
        if key not in _SOURCES:
            source = _FACTORIES[key]()
            if source.descriptor != descriptor or descriptor.contract_version != 1:
                raise ValueError("Incompatible source contract")
            _SOURCES[key] = source
        return _SOURCES[key]
    except Exception as exc:
        # The catalog and stored records stay readable. Failed loads are not
        # cached, so a subsequent explicit attempt can recover.
        raise SourceUnavailableError(f"Listing source unavailable: {key}") from exc
