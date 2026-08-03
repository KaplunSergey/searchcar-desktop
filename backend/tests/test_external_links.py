import pytest

from app.external_links import validated_external_url


@pytest.mark.parametrize(
    "url",
    [
        "https://fem.encar.com/cars/detail/123",
        "https://www.encar.com/dc/dc_carsearchlist.do",
        "https://t.me/example",
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
    ],
)
def test_unsupported_external_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="unsupported_external_url"):
        validated_external_url(url)
