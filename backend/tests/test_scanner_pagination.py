import json
from urllib.parse import unquote, urlsplit

import pytest

from app import scanner
from app.scanner import (
    IncompletePaginationError,
    SearchPage,
    collect_search_pages,
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
