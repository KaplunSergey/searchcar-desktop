from pathlib import Path

from app.parser import (
    detect_fuel,
    parse_condition,
    parse_contract_status,
    parse_detail_price_krw,
    parse_mileage_km,
    parse_vehicle_fields,
)
from app.scanner import is_sold_page, resolve_listing_identity


FIXTURES = Path(__file__).parent / "fixtures" / "encar"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_detail_fixture_keeps_primary_price_and_vehicle_fields() -> None:
    body = fixture("tucson-detail-v1.txt")

    assert parse_detail_price_krw(body) == 26_500_000
    assert parse_mileage_km(body) == 52_100
    assert detect_fuel(body) == "GASOLINE"
    assert parse_vehicle_fields(body)["registration_number"] == "219주1935"
    condition, summary = parse_condition(body)
    assert summary == "INSURANCE_CLAIM"
    assert condition["third_party_payments"]["value"] == 3_965_235


def test_similar_car_fixture_cannot_replace_primary_listing_price() -> None:
    body = fixture("detail-with-similar-cars-v1.txt")

    assert parse_detail_price_krw(body) == 26_500_000
    assert parse_contract_status(body) is True


def test_sold_fixture_preserves_requested_listing_identity() -> None:
    body = fixture("sold-listing-v1.txt")

    assert is_sold_page(body)
    assert resolve_listing_identity(
        "41093659",
        body,
        sold=True,
        resolved_url="https://fem.encar.com/cars/detail/99999999",
    ) == ("41093659", "99999999")
