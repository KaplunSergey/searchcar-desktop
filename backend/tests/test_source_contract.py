from dataclasses import replace
from types import SimpleNamespace
import subprocess
import sys

import pytest

from app import scanner
from app.sources import registry
from app.sources.contracts import InvalidSourceUrlError, SearchCollection, SourceUnavailableError


@pytest.mark.parametrize("url", [
    "https://copart.com/dc/dc_carsearchlist.do",
    "https://www.encar.com.evil.test/dc/dc_carsearchlist.do",
    "https://user:password@www.encar.com/dc/dc_carsearchlist.do",
    "http://localhost/dc/dc_carsearchlist.do",
    "file:///dc/dc_carsearchlist.do",
    "https://www.encar.com:8443/dc/dc_carsearchlist.do",
    "https://www.encar.com/cars/detail/42688672",
])
def test_search_url_rejected_before_any_network_call(url):
    with pytest.raises(InvalidSourceUrlError):
        registry.get_source("encar").validate_search_url(url)


@pytest.mark.parametrize("url", [
    "https://fem.encar.com/cars/detail/42688672?carid=42688672&view_type=normal",
    "http://www.encar.com/dc/dc_cardetailview.do?carid=42688672",
])
def test_listing_reference_has_source_scoped_id_and_canonical_url(url):
    reference = registry.get_source("encar").listing_reference(url)
    assert reference.source_key == "encar"
    assert reference.external_id == "42688672"
    assert reference.canonical_url == "https://fem.encar.com/cars/detail/42688672"


@pytest.mark.parametrize("url", [
    "https://www.iaai.com/cars/detail/42688672",
    "https://fem.encar.com/not-a-listing?carid=42688672",
    "https://fem.encar.com/cars/detail/42688672/other-car",
    "https://fem.encar.com/cars/detail/42688672?carid=42791283",
    "https://fem.encar.com/dc/dc_carsearchlist.do?carid=42688672",
])
def test_search_or_ambiguous_url_is_not_a_direct_listing(url):
    with pytest.raises(InvalidSourceUrlError):
        registry.get_source("encar").listing_reference(url)


def test_detail_does_not_open_url_of_another_identity(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(scanner, "read_detail", lambda *args: calls.append(args) or {})
    with pytest.raises(InvalidSourceUrlError):
        registry.get_source("encar").read_verified_detail(
            object(), {"source_car_id": "42688672", "url": "https://fem.encar.com/cars/detail/42791283"},
            tmp_path, None,
        )
    assert calls == []


@pytest.mark.parametrize("offer_type,meaning,price", [
    ("SALE", "SALE_PRICE", 26_000_000),
    ("RENT", "RENT_MONTHLY", 360_000),
    ("LEASE", "LEASE_MONTHLY", 480_000),
    ("SALE", "SALE_PRICE", None),
])
def test_detail_observation_records_origin_without_inventing_values(monkeypatch, tmp_path, offer_type, meaning, price):
    detail = {
        "canonical_car_id": "42688672", "price_krw": price, "offer_type": offer_type,
        "checked_at": "2026-10-09T09:00:00+00:00", "sold": False,
    }
    monkeypatch.setattr(scanner, "read_price_status", lambda *args: detail)
    row = {"source_car_id": "42688672", "url": "https://fem.encar.com/cars/detail/42688672"}
    result = registry.get_source("encar").read_verified_detail(
        object(), row, tmp_path, price, read_kind="PRICE_STATUS"
    )
    observation = result["source_observation"]
    assert observation["source_key"] == "encar"
    assert observation["source_listing_id"] == "42688672"
    assert observation["canonical_url"] == row["url"]
    assert observation["observed_at"] == detail["checked_at"]
    assert observation["source_updated_at"] is None
    assert observation["read_kind"] == "PRICE_STATUS"
    assert observation["money"] == {"amount": price, "currency": "KRW", "meaning": meaning}
    assert result["money_values"] == observation["money_values"] == [{
        "amount_minor": price, "currency": "KRW",
        "meaning": "SALE_TOTAL" if offer_type == "SALE" else meaning,
        "state": "KNOWN" if price is not None else "UNKNOWN", "auction_round": None,
    }]
    assert "mileage_km" not in observation["returned_fields"]
    assert result["price_krw"] == price
    assert observation["contract_version"] == observation["adapter_version"] == 1
    source = registry.get_source("encar")
    assert source.fingerprint_listing(result) == source.fingerprint_listing({
        key: value for key, value in result.items() if key != "source_observation"
    })


def test_failed_adapter_load_isolated_from_catalog_and_can_retry(monkeypatch):
    catalog = registry.list_sources()
    monkeypatch.setattr(registry, "_SOURCES", {})

    def failed_factory():
        raise RuntimeError("secret-token-in-internal-error")

    monkeypatch.setitem(registry._FACTORIES, "encar", failed_factory)
    with pytest.raises(SourceUnavailableError) as error:
        registry.get_source("encar")
    assert error.value.code == "SOURCE_UNAVAILABLE"
    assert "secret-token" not in str(error.value)
    assert registry.list_sources() == catalog
    source = SimpleNamespace(descriptor=catalog[0])
    monkeypatch.setitem(registry._FACTORIES, "encar", lambda: source)
    assert registry.get_source("encar") is source


def test_incompatible_contract_is_not_executed(monkeypatch):
    descriptor = replace(registry.get_source_descriptor("encar"), contract_version=99)
    monkeypatch.setattr(registry, "_SOURCES", {})
    monkeypatch.setitem(registry._FACTORIES, "encar", lambda: SimpleNamespace(descriptor=descriptor))
    with pytest.raises(SourceUnavailableError):
        registry.get_source("encar")


@pytest.mark.parametrize("external_id", ["../other", "42688672?carid=42791283", "not-an-id", ""])
def test_canonical_url_cannot_be_built_from_an_invalid_id(external_id):
    with pytest.raises(InvalidSourceUrlError):
        registry.get_source("encar").listing_url(external_id)


def test_blank_query_id_is_not_silently_ignored():
    with pytest.raises(InvalidSourceUrlError):
        registry.get_source("encar").listing_reference(
            "https://fem.encar.com/cars/detail/42688672?carid="
        )


def test_missing_availability_and_offer_type_remain_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(scanner, "read_price_status", lambda *args: {
        "canonical_car_id": "42688672", "price_krw": None,
    })
    result = registry.get_source("encar").read_verified_detail(
        object(), {"source_car_id": "42688672", "url": "https://fem.encar.com/cars/detail/42688672"},
        tmp_path, None, read_kind="PRICE_STATUS",
    )
    observation = result["source_observation"]
    assert observation["availability"]["status"] == "UNVERIFIED"
    assert observation["money"]["meaning"] == "UNKNOWN"
    assert observation["money"]["amount"] is None


def test_catalog_import_survives_a_broken_adapter_in_a_fresh_process():
    code = """
import sys
sys.modules['app.sources.encar'] = None
from app.main import app
from app.sources.registry import list_sources, get_source
from app.sources.contracts import SourceUnavailableError
assert [item.key for item in list_sources()] == ['encar']
try:
    get_source('encar')
except SourceUnavailableError:
    pass
else:
    raise AssertionError('broken adapter was not isolated')
assert [item.key for item in list_sources()] == ['encar']
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_collection_rejects_foreign_url_before_calling_scanner(monkeypatch):
    calls = []
    monkeypatch.setattr(scanner, "collect_search_pages", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(InvalidSourceUrlError):
        registry.get_source("encar").collect_search_pages(
            object(), "https://www.iaai.com/Search", "Project"
        )
    assert calls == []


@pytest.mark.parametrize("url", [
    'https://www.encar.com/dc/dc_carsearchlist.do?carType=kor#!%7B%22action%22%3A%22Price.range%281%2C600..2%2C900%29%22%7D',
    "https://www.encar.com/fc/fc_carsearchlist.do?carType=for",
    "https://fem.encar.com/fc/fc_carsearchlist.html",
])
def test_supported_search_urls_keep_encoding_and_selected_scope(monkeypatch, url):
    row = {"source_car_id": "42688672", "url": "https://fem.encar.com/cars/detail/42688672"}
    collection = SearchCollection({"42688672": row}, 1, 2, None)
    calls = []
    checkpoint, progress = lambda: None, lambda info: None

    def collect(page, received_url, name, **kwargs):
        calls.append((received_url, name, kwargs))
        return collection

    monkeypatch.setattr(scanner, "collect_search_pages", collect)
    result = registry.get_source("encar").collect_search_pages(
        object(), url, "Project", all_pages=False, checkpoint=checkpoint, progress=progress
    )
    assert result is collection
    assert result.total_results is None  # Unknown is not zero or failed coverage.
    assert calls == [(url, "Project", {"all_pages": False, "checkpoint": checkpoint, "progress": progress})]


def test_failed_pagination_is_not_normalized_to_an_empty_success(monkeypatch):
    failure = scanner.IncompletePaginationError("Page 2 unavailable")

    def collect(*args, **kwargs):
        raise failure

    monkeypatch.setattr(scanner, "collect_search_pages", collect)
    with pytest.raises(scanner.IncompletePaginationError) as error:
        registry.get_source("encar").collect_search_pages(
            object(), "https://www.encar.com/dc/dc_carsearchlist.do", all_pages=True
        )
    assert error.value is failure


def test_mismatched_search_row_does_not_escape_source_boundary(monkeypatch):
    monkeypatch.setattr(scanner, "collect_search_pages", lambda *args, **kwargs: SearchCollection({
        "42688672": {"source_car_id": "42688672", "url": "https://fem.encar.com/cars/detail/42791283"}
    }, 1, 1, 1))
    with pytest.raises(InvalidSourceUrlError):
        registry.get_source("encar").collect_search_pages(
            object(), "https://www.encar.com/dc/dc_carsearchlist.do"
        )
