import pytest

from app.external_links import validated_external_url


@pytest.mark.parametrize(
    "url",
    [
        "https://fem.encar.com/cars/detail/123",
        "https://www.encar.com/dc/dc_carsearchlist.do",
        "https://t.me/example",
        "https://www.copart.com/lot/123/example",
        "https://www.iaai.com/VehicleDetail/123~US",
        "https://calendar.google.com/calendar/render?action=TEMPLATE&text=Car&dates=20261022T130000Z%2F20261022T130000Z",
        "https://searchcar-auction-calendar-preview.pages.dev/#%7B%22v%22%3A1%7D",
        "https://searchcar-auction-calendar-preview.pages.dev:443/#%7B%22v%22%3A1%7D",
    ],
)
def test_supported_external_urls(url: str) -> None:
    assert validated_external_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://encar.com.example.org/car/123",
        "https://user:password@encar.com/car/123",
        "https://encar.com:8443/car/123",
        "http://www.copart.com/lot/123",
        "https://www.copart.com.example.org/lot/123",
        "https://user:password@www.iaai.com/VehicleDetail/123~US",
        "https://www.iaai.com:8443/VehicleDetail/123~US",
        "http://calendar.google.com/calendar/render?action=TEMPLATE",
        "https://calendar.google.com.example.org/calendar/render?action=TEMPLATE",
        "https://calendar.google.com/calendar/render?action=DELETE",
        "https://calendar.google.com/calendar/render?action=TEMPLATE&action=DELETE",
        "https://calendar.google.com/calendar/render?action=TEMPLATE&redirect=https://other.invalid",
        "https://calendar.google.com/other?action=TEMPLATE",
        "https://calendar.google.com:80/calendar/render?action=TEMPLATE",
        "https://calendar.google.com:invalid/calendar/render?action=TEMPLATE",
        "https://calendar.google.com/calendar/render?action=TEMPLATE\n&text=car",
        "http://searchcar-auction-calendar-preview.pages.dev/#event",
        "https://other.pages.dev/#event",
        "https://branch.searchcar-auction-calendar-preview.pages.dev/#event",
        "https://searchcar-auction-calendar-preview.pages.dev.evil.org/#event",
        "https://user:password@searchcar-auction-calendar-preview.pages.dev/#event",
        "https://searchcar-auction-calendar-preview.pages.dev:80/#event",
        "https://searchcar-auction-calendar-preview.pages.dev:8443/#event",
        "https://searchcar-auction-calendar-preview.pages.dev/other#event",
        "https://searchcar-auction-calendar-preview.pages.dev/?redirect=https://evil.org#event",
        "https://searchcar-auction-calendar-preview.pages.dev/",
        "https://searchcar-auction-calendar-preview.pages.dev/#ev\nent",
        "https://searchcar-auction-calendar-preview.pages.dev/#" + "x" * 2048,
    ],
)
def test_unsupported_external_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="unsupported_external_url"):
        validated_external_url(url)


def test_external_links_follow_developer_registered_domains(monkeypatch) -> None:
    from app.sources import registry

    monkeypatch.setattr(registry, "_CATALOG", {**registry._CATALOG, "sampleauction": {
        "key": "sampleauction", "family": "AUCTION", "domains": ["auction.example"],
    }})
    assert validated_external_url("https://auction.example/lot/1") == "https://auction.example/lot/1"
    with pytest.raises(ValueError, match="unsupported_external_url"):
        validated_external_url("https://other.auction.example/lot/1")
