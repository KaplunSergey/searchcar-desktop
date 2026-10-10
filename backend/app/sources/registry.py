"""Only explicitly shipped adapters may execute; unknown keys never fall back."""

from .contracts import SourceAdapter, SourceDescriptor, SourceUnavailableError


_DESCRIPTORS = {
    "encar": SourceDescriptor(
        key="encar", family="MARKETPLACE",
        domains=("encar.com", "www.encar.com", "fem.encar.com", "m.encar.com"),
        capabilities=frozenset({
            "SEARCH", "DIRECT_LISTING", "PRICE_FILTER", "PRICE_STATUS", "MAIN_IMAGE"
        }),
    ),
}


def _load_encar() -> SourceAdapter:
    # Explicit built-ins only; no module names from user input.
    from .encar import EncarSource
    return EncarSource()


_FACTORIES = {"encar": _load_encar}
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
