import pytest

from app import scanner
from app.sources.encar import EncarSource
from test_current_images import png
from test_scanner_pagination import SearchResultsPage, modern_search_url


def test_collection_does_not_crop_low_quality_list_thumbnail(monkeypatch):
    monkeypatch.setattr(scanner, "_open_page", lambda *_: None)
    monkeypatch.setattr(scanner, "_wait_for_search_results", lambda *_: None)
    anchors = [
        {"href": "https://fem.encar.com/cars/detail/41000001", "closestText": "Audi", "index": 0, "hasImage": True},
        {"href": "https://fem.encar.com/cars/detail/41000001", "closestText": "Audi A5 longer text", "index": 1},
    ]
    class Page(SearchResultsPage):
        class Locator(SearchResultsPage.Locator):
            def evaluate_all(self, script):
                if self.selector.endswith("a[href]"):
                    return anchors
                return super().evaluate_all(script)
        def locator(self, selector):
            return self.Locator(selector)
    rows = scanner.collect_search(Page(), modern_search_url())
    assert "_images" not in rows["41000001"]
    assert rows["41000001"]["list_text"] == "Audi A5 longer text"


class ImageResponse:
    def __init__(self, content, ok=True):
        self.content, self.ok = content, ok

    def body(self):
        return self.content


class ImagePage:
    def __init__(self, urls, responses):
        self.urls = urls
        self.responses = responses
        self.requested = []
        self.request = self

    def get(self, url, **kwargs):
        self.requested.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response

    def locator(self, selector):
        page = self
        class Locator:
            first = property(lambda self: self)

            def count(self):
                return int(selector == 'meta[property="og:image"]' and bool(page.urls))

            def get_attribute(self, name):
                return page.urls[0] if page.urls else None

            def evaluate_all(self, script):
                return [
                    {"src": url, "width": 1200, "height": 800}
                    for url in page.urls[1:]
                ]
        return Locator()


def test_detail_main_image_uses_verified_source_url_not_browser_crop():
    invalid = "https://ci.encar.com/photo-invalid.jpg"
    valid = "https://img.encar.com/photo-original.jpg"
    page = ImagePage([invalid, valid], {
        invalid: ImageResponse(b"not an image"),
        valid: ImageResponse(png()),
    })
    assert scanner.download_detail_main_image(page) == (png(), valid)
    assert page.requested == [invalid, valid]


def test_detail_main_image_rejects_untrusted_or_failed_urls():
    outside = "https://example.org/photo.jpg"
    failed = "https://ci.encar.com/photo.jpg"
    page = ImagePage([outside, "data:image/png;base64,AAAA", failed], {
        outside: ImageResponse(png()),
        failed: scanner.PlaywrightError("network error"),
    })
    assert scanner.download_detail_main_image(page) is None
    assert page.requested == [failed]


def test_verified_boundary_ignores_untrusted_list_thumbnail_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(scanner, "read_price_status", lambda *_: {
        "canonical_car_id": "41000001", "price_krw": 1000, "offer_type": "SALE", "sold": False,
    })
    detail = EncarSource.read_verified_detail(object(), {
        "source_car_id": "41000001", "url": EncarSource.listing_url("41000001"), "_images": {"MAIN": png()},
    }, tmp_path, 1000, read_kind="PRICE_STATUS")
    assert "_images" not in detail
    assert "_images" not in detail["source_observation"]["returned_fields"]


def test_unconfirmed_detail_never_writes_images(monkeypatch, tmp_path):
    reads = iter([{"canonical_car_id": "41000001", "price_krw": 2000, "_images": {"SCREENSHOT": png()}},
                  {"canonical_car_id": "41000001", "price_krw": 3000, "_images": {"SCREENSHOT": png(1)}}])
    monkeypatch.setattr(scanner, "read_detail", lambda *_: next(reads))
    with pytest.raises(scanner.PriceConfirmationError):
        EncarSource.read_verified_detail(object(), {"source_car_id": "41000001", "url": EncarSource.listing_url("41000001")}, tmp_path, 1000)
    assert not list(tmp_path.iterdir())


def test_pagination_duplicate_keeps_longer_list_data(monkeypatch):
    from app.scanner import SearchPage
    row = lambda value: {"source_car_id": value, "url": EncarSource.listing_url(value), "list_text": value}
    pages = iter([
        SearchPage(rows={"41000001": row("41000001"), "42000002": {**row("42000002"), "list_text": "short"}},
                   page_number=1, total_pages=2, total_results=3, has_next=True),
        SearchPage(rows={"42000002": {**row("42000002"), "list_text": "longer listing data"}, "43000003": row("43000003")},
                   page_number=2, total_pages=2, total_results=3, has_next=False),
    ])
    monkeypatch.setattr(scanner, "_collect_search_page", lambda *args, **kwargs: next(pages))
    from test_scanner_pagination import FakePage
    result = scanner.collect_search_pages(FakePage(), modern_search_url(), all_pages=True)
    assert result.rows["42000002"]["list_text"] == "longer listing data"


@pytest.mark.parametrize("photo_error", [False, True])
def test_full_detail_returns_optional_downloaded_main_image_without_file_mutation(monkeypatch, tmp_path, photo_error):
    from types import SimpleNamespace
    monkeypatch.setattr(scanner, "_open_page", lambda *_: None)
    monkeypatch.setattr(scanner, "_wait_for_detail_content", lambda *_: scanner.SOLD_MARKERS[0])
    monkeypatch.setattr(scanner, "_page_offer_price", lambda *_: (None, None, {}))
    source_url = "https://ci.encar.com/original.jpg"
    monkeypatch.setattr(scanner, "download_detail_main_image",
                        lambda *_: None if photo_error else (png(), source_url))
    page = SimpleNamespace(url=EncarSource.listing_url("41000001"), title=lambda: "Sold",
                           mouse=SimpleNamespace(wheel=lambda *_: None), wait_for_timeout=lambda *_: None,
                           screenshot=lambda **_: png(1))
    detail = scanner.read_detail(page, {"source_car_id": "41000001", "url": page.url}, tmp_path)
    assert detail["sold"] is True
    assert (detail.get("_images") or {}).get("MAIN") == (None if photo_error else png())
    assert detail.get("main_image_source_url") == (None if photo_error else source_url)
    assert detail["_images"]["SCREENSHOT"] == png(1)
    assert "main_image_path" not in detail and "screenshot_path" not in detail
    assert not list(tmp_path.iterdir())
