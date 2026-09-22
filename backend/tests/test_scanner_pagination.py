import json
from urllib.parse import unquote, urlsplit

import pytest

from app import scanner
from app.scanner import (
    CaptchaError,
    IncompletePaginationError,
    PriceFilterUrlError,
    SearchPage,
    TimeoutScanError,
    _collect_search_page,
    _open_page,
    apply_price_filter,
    collect_search_pages,
    extract_price_filter,
    search_page_number,
    search_url_for_page,
)


def modern_search_url(page: int = 1) -> str:
    state = {
        "action": "(And.Year.range(202400..)._.Manufacturer.현대.)",
        "sort": "ModifiedDate",
        "page": page,
        "limit": 20,
        "cursor": "old-cursor",
    }
    from urllib.parse import quote

    return (
        "https://www.encar.com/dc/dc_carsearchlist.do?carType=kor#!"
        + quote(json.dumps(state, ensure_ascii=False, separators=(",", ":")), safe="")
    )


def decoded_state(url: str) -> dict:
    return json.loads(unquote(urlsplit(url).fragment[1:]))


def row(encar_id: str) -> dict:
    return {
        "source_car_id": encar_id,
        "url": f"https://fem.encar.com/cars/detail/{encar_id}",
        "list_text": encar_id,
    }


class FakePage:
    def __init__(self):
        self.waits = []

    def wait_for_timeout(self, milliseconds: int):
        self.waits.append(milliseconds)


class OutagePage(FakePage):
    def goto(self, *_args, **_kwargs):
        raise scanner.PlaywrightTimeout("net::ERR_TIMED_OUT")


class CaptchaPage(FakePage):
    class Body:
        def inner_text(self, **_kwargs):
            return "자동입력 방지 보안문자"

    def locator(self, selector):
        assert selector == "body"
        return self.Body()


def test_search_page_url_preserves_filters_and_resets_cursor():
    original = modern_search_url()
    changed = search_url_for_page(original, 3)

    before = decoded_state(original)
    after = decoded_state(changed)
    assert after["action"] == before["action"]
    assert after["sort"] == before["sort"]
    assert after["limit"] == 20
    assert after["page"] == 3
    assert after["cursor"] is None
    assert search_page_number(changed) == 3


def test_price_filter_replaces_existing_encar_range_without_touching_other_filters():
    from urllib.parse import quote

    state = decoded_state(modern_search_url())
    state["action"] = state["action"][:-2] + "._.Price.range(1,000..3,000).)"
    state["toggle"] = {"4": 1}
    original = (
        "https://www.encar.com/dc/dc_carsearchlist.do?carType=kor#!"
        + quote(json.dumps(state, ensure_ascii=False, separators=(",", ":")), safe="")
    )

    changed = apply_price_filter(original, 15_000_000, 25_000_000)
    after = decoded_state(changed)

    assert extract_price_filter(changed) == (15_000_000, 25_000_000)
    assert after["toggle"] == {"4": 1}
    assert after["action"].replace("._.Price.range(1,500..2,500)", "") == state[
        "action"
    ].replace("._.Price.range(1,000..3,000)", "")


def test_price_filter_is_added_to_encar_url_that_has_no_price_range():
    original = modern_search_url()

    changed = apply_price_filter(original, 12_000_000, 18_000_000)

    assert extract_price_filter(changed) == (12_000_000, 18_000_000)
    assert decoded_state(changed)["toggle"] == {"4": 1}
    assert extract_price_filter(search_url_for_page(changed, 2)) == (
        12_000_000,
        18_000_000,
    )


def test_price_filter_is_added_to_foreign_car_url_with_nested_action_groups():
    from urllib.parse import quote

    state = {
        "action": "(And.Year.range(..201799)._.Hidden.N._.Category.준중형차._.(C.CarType.N._.(C.Manufacturer.아우디._.ModelGroup.A3.)))",
        "toggle": {},
        "sort": "ModifiedDate",
        "page": 1,
        "limit": 20,
    }
    original = (
        "https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!"
        + quote(json.dumps(state, ensure_ascii=False, separators=(",", ":")), safe="")
    )

    changed = apply_price_filter(original, 7_000_000, 8_000_000)

    assert extract_price_filter(changed) == (7_000_000, 8_000_000)
    assert decoded_state(changed)["toggle"] == {"4": 1}
    assert decoded_state(changed)["action"] == (
        "(And.Year.range(..201799)._.Hidden.N._.Category.준중형차._."
        "(C.CarType.N._.(C.Manufacturer.아우디._.ModelGroup.A3.))_."
        "Price.range(700..800).)"
    )


def test_price_filter_can_be_removed_without_changing_other_conditions():
    original = apply_price_filter(modern_search_url(), 10_000_000, 30_000_000)

    removed = apply_price_filter(original, None, None)

    assert extract_price_filter(removed) == (None, None)
    assert decoded_state(removed)["toggle"] == {"4": 0}
    assert decoded_state(removed)["action"] == decoded_state(modern_search_url())["action"]


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(-1, None), (None, 100_005_000), (20_000_000, 10_000_000)],
)
def test_price_filter_rejects_invalid_krw_ranges(minimum, maximum):
    with pytest.raises(PriceFilterUrlError):
        apply_price_filter(modern_search_url(), minimum, maximum)


def test_first_page_mode_never_requests_page_two(monkeypatch):
    requested = []

    def fake_collect(page, url, search_name, requested_page):
        requested.append(requested_page)
        return SearchPage(
            rows={"1": row("1")},
            page_number=1,
            total_pages=2,
            total_results=21,
            has_next=True,
        )

    monkeypatch.setattr(scanner, "_collect_search_page", fake_collect)
    result = collect_search_pages(
        FakePage(),
        modern_search_url(),
        all_pages=False,
    )

    assert requested == [1]
    assert result.pages_visited == 1
    assert list(result.rows) == ["1"]


def test_all_pages_are_merged_and_deduplicated(monkeypatch):
    pages = {
        1: SearchPage(
            rows={"1": row("1"), "2": row("2")},
            page_number=1,
            total_pages=2,
            total_results=3,
            has_next=True,
        ),
        2: SearchPage(
            rows={"2": row("2"), "3": row("3")},
            page_number=2,
            total_pages=2,
            total_results=3,
            has_next=False,
        ),
    }
    updates = []
    checkpoints = []
    fake_page = FakePage()
    monkeypatch.setattr(
        scanner,
        "_collect_search_page",
        lambda page, url, search_name, requested_page: pages[requested_page],
    )

    result = collect_search_pages(
        fake_page,
        modern_search_url(),
        all_pages=True,
        checkpoint=lambda: checkpoints.append(True),
        progress=updates.append,
    )

    assert list(result.rows) == ["1", "2", "3"]
    assert result.pages_visited == 2
    assert result.total_pages == 2
    assert updates[-1]["complete"] is True
    assert updates[-1]["found_count"] == 3
    assert len(checkpoints) == 4
    assert fake_page.waits == [800]


def test_repeated_page_is_an_incomplete_pagination_error(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "_collect_search_page",
        lambda page, url, search_name, requested_page: SearchPage(
            rows={"1": row("1")},
            page_number=requested_page,
            total_pages=2,
            total_results=2,
            has_next=requested_page == 1,
        ),
    )

    with pytest.raises(IncompletePaginationError, match="repeated"):
        collect_search_pages(
            FakePage(),
            modern_search_url(),
            all_pages=True,
        )


def test_expected_empty_second_page_is_not_treated_as_complete(monkeypatch):
    def fake_collect(page, url, search_name, requested_page):
        return SearchPage(
            rows={"1": row("1")} if requested_page == 1 else {},
            page_number=requested_page,
            total_pages=2,
            total_results=21,
            has_next=requested_page == 1,
        )

    monkeypatch.setattr(scanner, "_collect_search_page", fake_collect)

    with pytest.raises(IncompletePaginationError, match="contained no listings"):
        collect_search_pages(
            FakePage(),
            modern_search_url(),
            all_pages=True,
        )


def test_network_outage_retries_once_then_returns_timeout() -> None:
    page = OutagePage()

    with pytest.raises(TimeoutScanError, match="ERR_TIMED_OUT"):
        _open_page(page, "https://www.encar.com/list")

    assert page.waits == [2500]


def test_captcha_search_page_is_reported_before_any_listing_is_read(monkeypatch) -> None:
    monkeypatch.setattr(scanner, "_open_page", lambda *_args: None)
    monkeypatch.setattr(scanner, "_wait_for_search_results", lambda *_args: None)

    with pytest.raises(CaptchaError, match="CAPTCHA"):
        _collect_search_page(CaptchaPage(), modern_search_url())
