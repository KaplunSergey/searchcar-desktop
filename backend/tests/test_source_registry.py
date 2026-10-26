from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.sources.registry import get_source
from app.detail_workers import DetailTask, DetailWorkerPool
from app.scanner import PriceConfirmationError
from app.worker import _effective_search_url, _read_verified_detail


def test_builtin_encar_has_a_versioned_marketplace_contract():
    source = get_source("encar")
    assert source.descriptor.key == "encar"
    assert source.descriptor.family == "MARKETPLACE"
    assert source.descriptor.contract_version == 1
    assert "PRICE_STATUS" in source.descriptor.capabilities
    assert "fem.encar.com" in source.descriptor.domains


@pytest.mark.parametrize("key", ["copart", "iaa", "iaai", "ENCAR", "app.scanner", ""])
def test_unshipped_or_untrusted_source_never_falls_back_to_encar(key):
    with pytest.raises(ValueError, match="Unsupported listing source"):
        get_source(key)


def test_worker_applies_price_at_the_registered_source_boundary(monkeypatch):
    calls = []
    source = SimpleNamespace(
        apply_price_filter=lambda *args: calls.append(args) or "adapter-filtered-url"
    )
    monkeypatch.setattr("app.worker.get_source", lambda key: source, raising=False)
    project = SimpleNamespace(
        search_url='https://www.encar.com/dc/dc_carsearchlist.do#!{"action":"(And.Hidden.N.)"}',
        price_filter_mode="CUSTOM",
        price_min_krw=0,
        price_max_krw=26_000_000,
    )
    assert _effective_search_url(project) == "adapter-filtered-url"
    assert calls == [(project.search_url, 0, 26_000_000)]


def test_manual_read_dispatches_to_source_without_passing_orm(monkeypatch, tmp_path):
    calls = []
    source = SimpleNamespace(
        read_verified_detail=lambda *args, **kwargs: calls.append((args, kwargs)) or {"sold": True}
    )
    monkeypatch.setattr("app.worker.get_source", lambda key: source)
    page, row = object(), {"source_car_id": "42688672"}
    detail = _read_verified_detail(
        page, row, tmp_path, SimpleNamespace(current_price=26_000_000), read_kind="PRICE_STATUS"
    )
    assert detail == {"sold": True}
    assert calls == [((page, row, tmp_path, 26_000_000), {"read_kind": "PRICE_STATUS"})]


def test_registered_reader_reuses_pool_and_closes_source_session(monkeypatch, tmp_path):
    source = get_source("encar")
    events, reads = [], []
    page = object()
    context = SimpleNamespace(
        new_page=lambda: page, close=lambda: events.append("context-closed")
    )

    def new_context(**options):
        events.append(options)
        return context

    browser = SimpleNamespace(new_context=new_context, close=lambda: events.append("browser-closed"))

    def launch(**options):
        events.append(options)
        return browser

    @contextmanager
    def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    def read(read_page, row, storage, price, *, read_kind):
        reads.append((read_page, row, storage, price, read_kind))
        raise PriceConfirmationError("unconfirmed price")

    monkeypatch.setattr("app.sources.encar.desktop_playwright", playwright)
    monkeypatch.setattr(source, "read_verified_detail", read)
    row = {"source_car_id": "42688672", "_searchcar_read_kind": "PRICE_STATUS"}
    task = DetailTask("one", "encar", row, 26_000_000, read_kind="PRICE_STATUS")
    with DetailWorkerPool(
        lambda: source.detail_reader(tmp_path, headless=True, executable_path="test-chromium"),
        workers=1, min_start_interval_seconds=0,
    ) as pool:
        pool.submit_all([task])
        result = next(pool.results(1))
    assert isinstance(result.error, PriceConfirmationError)
    assert result.detail is None
    assert result.task is task
    assert reads == [(page, row, tmp_path, 26_000_000, "PRICE_STATUS")]
    assert events == [
        {"headless": True, "executable_path": "test-chromium"},
        {"viewport": {"width": 1440, "height": 1200}, "locale": "ko-KR"},
        "context-closed", "browser-closed",
    ]


def test_failed_source_page_creation_closes_context_and_browser():
    source = get_source("encar")
    closed = []

    def new_page():
        raise RuntimeError("page creation failed")

    context = SimpleNamespace(new_page=new_page, close=lambda: closed.append("context"))
    browser = SimpleNamespace(
        new_context=lambda **kwargs: context, close=lambda: closed.append("browser")
    )
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: browser))
    with pytest.raises(RuntimeError, match="page creation failed"):
        source.open_browser(playwright, headless=True, executable_path=None)
    assert closed == ["context", "browser"]


def test_source_price_confirmation_rejects_a_different_identity(monkeypatch, tmp_path):
    reads = iter([
        {"canonical_car_id": "42688672", "price_krw": 25_000_000},
        {"canonical_car_id": "42791283", "price_krw": 25_000_000},
    ])
    monkeypatch.setattr("app.sources.encar.scanner.read_detail", lambda *args: next(reads))
    with pytest.raises(PriceConfirmationError):
        get_source("encar").read_verified_detail(
            object(), {
                "source_car_id": "42688672",
                "url": "https://fem.encar.com/cars/detail/42688672",
            }, tmp_path, 26_000_000
        )


def test_sold_result_does_not_trigger_price_confirmation(monkeypatch, tmp_path):
    calls = []
    sold = {"canonical_car_id": "42688672", "price_krw": 25_000_000, "sold": True}
    monkeypatch.setattr(
        "app.sources.encar.scanner.read_price_status", lambda *args: calls.append(args) or sold
    )
    assert get_source("encar").read_verified_detail(
        object(), {
            "source_car_id": "42688672",
            "url": "https://fem.encar.com/cars/detail/42688672",
        }, tmp_path, 26_000_000, read_kind="PRICE_STATUS"
    ) is sold
    assert len(calls) == 1
