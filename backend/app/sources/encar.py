"""Encar boundary, reusing the characterized parser/scanner during migration."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from .. import parser, scanner
from ..desktop_runtime import desktop_playwright
from ..money import encar_money_values
from .contracts import InvalidSourceUrlError, ListingReference
from .registry import get_source_descriptor


class EncarSource:
    descriptor = get_source_descriptor("encar")

    # These legacy implementations remain available to existing API/services.
    parse_list_row = staticmethod(scanner.parse_list_row)
    detail_read_kind = staticmethod(parser.detail_read_kind)
    fingerprint_listing = staticmethod(parser.fingerprint_listing)
    material_changes = staticmethod(parser.material_changes)

    @staticmethod
    def _validated_url(url: str):
        try:
            value = url.strip()
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname not in EncarSource.descriptor.domains
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in {None, 80, 443}
                or any(ord(char) < 32 for char in value)
            ):
                raise ValueError("Invalid URL")
        except (AttributeError, ValueError):
            raise InvalidSourceUrlError("Invalid Encar URL") from None
        return value, parsed

    @classmethod
    def validate_search_url(cls, url: str) -> str:
        value, parsed = cls._validated_url(url)
        if not re.fullmatch(r"/(?:dc|fc)/[df]c_carsearchlist\.(?:do|html)", parsed.path):
            raise InvalidSourceUrlError("Expected an Encar search URL")
        return value  # Keep the original encoded filters/fragment unchanged.

    @classmethod
    def listing_reference(cls, url: str) -> ListingReference:
        _, parsed = cls._validated_url(url)
        path_match = re.fullmatch(r"/cars/detail/(\d{6,})/?", parsed.path)
        query_ids = [
            value for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
            if key.lower() in {"carid", "carno"} for value in values
        ]
        if path_match:
            external_id = path_match.group(1)
        elif re.fullmatch(r"/(?:dc|fc)/[df]c_cardetailview\.do", parsed.path) and query_ids:
            external_id = query_ids[0]
        else:
            raise InvalidSourceUrlError("Expected an Encar listing URL")
        if not re.fullmatch(r"\d{6,}", external_id) or any(value != external_id for value in query_ids):
            raise InvalidSourceUrlError("Ambiguous Encar listing identity")
        return ListingReference("encar", external_id, cls.listing_url(external_id))

    @classmethod
    def apply_price_filter(cls, url: str, minimum: int | None, maximum: int | None) -> str:
        return scanner.apply_price_filter(cls.validate_search_url(url), minimum, maximum)

    @classmethod
    def collect_search_pages(cls, page, url: str, search_name: str = "", **kwargs):
        collection = scanner.collect_search_pages(
            page, cls.validate_search_url(url), search_name, **kwargs
        )
        for row in collection.rows.values():
            reference = cls.listing_reference(row["url"])
            if str(row["source_car_id"]) != reference.external_id:
                raise InvalidSourceUrlError("Encar search row identity does not match URL")
        return collection

    @staticmethod
    def listing_url(external_id: str) -> str:
        if not isinstance(external_id, str) or not re.fullmatch(r"\d{6,}", external_id):
            raise InvalidSourceUrlError("Invalid Encar listing identity")
        return f"https://fem.encar.com/cars/detail/{external_id}"

    @staticmethod
    def open_browser(playwright, *, headless: bool, executable_path: str | None):
        browser = playwright.chromium.launch(
            headless=headless, executable_path=executable_path or None
        )
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 1200}, locale="ko-KR"
            )
            try:
                return browser, context, context.new_page()
            except BaseException:
                context.close()
                raise
        except BaseException:
            browser.close()
            raise

    @classmethod
    def read_verified_detail(
        cls, page, row: dict, storage: Path, known_price: int | None, *, read_kind: str = "FULL"
    ) -> dict:
        if read_kind not in {"FULL", "PRICE_STATUS"}:
            raise ValueError("Unsupported Encar read kind")
        reference = cls.listing_reference(row.get("url", ""))
        if reference.external_id != str(row.get("source_car_id")):
            raise InvalidSourceUrlError("Encar listing identity does not match URL")
        read_once = (
            (lambda: scanner.read_detail(page, row, storage))
            if read_kind == "FULL"
            else (lambda: scanner.read_price_status(page, row))
        )
        detail = read_once()
        new_price = detail.get("price_krw")
        if (
            known_price and new_price and new_price != known_price
            and not detail.get("sold")
        ):
            confirmation = read_once()
            if (
                confirmation.get("canonical_car_id") != detail.get("canonical_car_id")
                or confirmation.get("price_krw") != new_price
            ):
                raise scanner.PriceConfirmationError(
                    "Encar returned inconsistent listing identity or price on confirmation"
                )
            detail = confirmation
        if str(detail.get("canonical_car_id")) != reference.external_id:
            raise scanner.IdentityMismatchError(
                reference.external_id, str(detail.get("canonical_car_id"))
            )
        if detail.get("url") and cls.listing_reference(detail["url"]).external_id != reference.external_id:
            raise scanner.IdentityMismatchError(reference.external_id, str(detail["canonical_car_id"]))
        offer_type = detail.get("offer_type")
        detail["money_values"] = encar_money_values({
            "offer_type": offer_type or "UNKNOWN",
            "price_krw": None if detail.get("sold") else detail.get("price_krw"),
        })
        detail["source_observation"] = {
            "source_key": reference.source_key,
            "source_listing_id": reference.external_id,
            "canonical_url": reference.canonical_url,
            "requested_url": row["url"],
            "observed_at": detail.get("checked_at") or datetime.now(timezone.utc).isoformat(),
            "source_updated_at": None,  # Encar does not supply a verified timestamp here.
            "read_kind": read_kind,
            "contract_version": cls.descriptor.contract_version,
            "adapter_version": cls.descriptor.adapter_version,
            "returned_fields": sorted(key for key in detail if key != "source_observation" and not key.startswith("_")),
            "unknown_fields": sorted(
                key for key, value in detail.items()
                if key != "source_observation" and not key.startswith("_") and value in (None, "", "UNVERIFIED")
            ),
            "availability": {
                "status": (
                    "SOLD_OR_REMOVED" if detail.get("sold") is True
                    else "AVAILABLE" if detail.get("sold") is False
                    else "UNVERIFIED"
                ),
                "basis": "DETAIL_PAGE",
            },
            "money": {
                "amount": None if detail.get("sold") else detail.get("price_krw"),
                "currency": "KRW",
                "meaning": {"SALE": "SALE_PRICE", "RENT": "RENT_MONTHLY", "LEASE": "LEASE_MONTHLY"}.get(offer_type, "UNKNOWN"),
            },
            "money_values": detail["money_values"],
        }
        return detail

    @contextmanager
    def detail_reader(
        self, storage: Path, *, headless: bool, executable_path: str | None
    ):
        """A browser/context owned by one existing detail-worker thread."""
        with desktop_playwright() as playwright:
            browser, context, page = self.open_browser(
                playwright, headless=headless, executable_path=executable_path
            )
            try:
                yield lambda row, known_price: self.read_verified_detail(
                    page, row, storage, known_price,
                    read_kind=row.get("_searchcar_read_kind", "FULL"),
                )
            finally:
                try:
                    context.close()
                finally:
                    browser.close()
