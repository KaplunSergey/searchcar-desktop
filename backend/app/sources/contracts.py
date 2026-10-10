"""The current collection boundary; database identity migration is separate."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ..detail_workers import DetailReader


class ScanError(RuntimeError):
    code = "READ_ERROR"


class InvalidSourceUrlError(ScanError):
    code = "INVALID_SOURCE_URL"


class SourceUnavailableError(ScanError):
    code = "SOURCE_UNAVAILABLE"


@dataclass(frozen=True)
class ListingReference:
    source_key: str
    external_id: str
    canonical_url: str


@dataclass(frozen=True)
class SearchCollection:
    rows: dict[str, dict]
    pages_visited: int
    total_pages: int
    total_results: int | None


@dataclass(frozen=True)
class SourceDescriptor:
    key: str
    family: str
    domains: tuple[str, ...]
    capabilities: frozenset[str]
    contract_version: int = 1
    adapter_version: int = 1


class SourceAdapter(Protocol):
    descriptor: SourceDescriptor

    def validate_search_url(self, url: str) -> str: ...
    def listing_reference(self, url: str) -> ListingReference: ...

    def apply_price_filter(
        self, url: str, minimum: int | None, maximum: int | None
    ) -> str: ...

    def collect_search_pages(
        self, page, url: str, search_name: str = "", *,
        all_pages: bool = False,
        checkpoint: Callable[[], None] | None = None,
        progress: Callable[[dict], None] | None = None,
        max_pages: int = 50,
    ) -> SearchCollection: ...

    def parse_list_row(self, row: dict) -> dict: ...
    def detail_read_kind(self, scan_mode: str, **kwargs) -> str | None: ...
    def fingerprint_listing(self, detail: dict) -> str: ...
    def material_changes(self, old: dict, new: dict) -> list[dict]: ...
    def listing_url(self, external_id: str) -> str: ...
    def open_browser(
        self, playwright, *, headless: bool, executable_path: str | None
    ): ...

    def read_verified_detail(
        self, page, row: dict, storage: Path, known_price: int | None, *,
        read_kind: str = "FULL",
    ) -> dict: ...

    def detail_reader(
        self, storage: Path, *, headless: bool, executable_path: str | None
    ) -> AbstractContextManager[DetailReader]: ...
