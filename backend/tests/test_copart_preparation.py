"""Classic navigation can outlive a click without being a failed search."""
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout

from app.sources.copart import CopartSource
from app.sources.auction_lists import AuctionListError


@pytest.fixture
def preparation(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('app.sources.copart.monotonic', lambda: clock[0])
    source = CopartSource()
    source.prepare_timeout_seconds = 2

    class Page:
        table_at = float('inf')
        clicks = 0
        waits = 0
        state = {}
        timeout = False
        failure = None
        def locator(self, selector):
            assert selector == '#serverSideDataTable'
            return SimpleNamespace(count=lambda: int(clock[0] >= self.table_at))
        def get_by_role(self, *_args, **_kwargs):
            def click(**options):
                self.clicks += 1
                self.options = options
                if self.failure:
                    raise self.failure
                if self.timeout:
                    raise BrowserTimeout('click done; waiting for scheduled navigations to finish')
            return SimpleNamespace(count=lambda: 1, click=click)
        def wait_for_timeout(self, ms):
            self.waits += 1
            clock[0] += ms / 1000
    page = Page()
    source.read_dom = lambda _: {'can_prepare': True, 'interface': {'classic_table': clock[0] >= page.table_at}, **page.state}
    return source, page, clock


@pytest.mark.parametrize('timeout', [False, True])
@pytest.mark.parametrize('table_at', [0.0, 0.75])
def test_classic_waits_for_table_after_one_click_even_when_navigation_times_out(preparation, timeout, table_at):
    source, page, clock = preparation
    # The table appears as an effect of clicking, not before prepare_page starts.
    page.timeout = timeout
    original = page.get_by_role
    def button(*args, **kwargs):
        click = original(*args, **kwargs).click
        def switch(**options):
            page.table_at = table_at
            click(**options)
        return SimpleNamespace(count=lambda: 1, click=switch)
    page.get_by_role = button
    source.prepare_page(page, checkpoint=lambda: None, verification=lambda _: pytest.fail('Unexpected verification'))
    assert clock[0] >= table_at
    assert page.clicks == 1
    assert page.options['no_wait_after'] is True


@pytest.mark.parametrize('timeout', [False, True])
def test_a_missing_table_has_a_bounded_failure_not_success_or_repeated_clicks(preparation, timeout):
    source, page, clock = preparation
    page.timeout = timeout
    with pytest.raises(AuctionListError) as error:
        source.prepare_page(page, checkpoint=lambda: None, verification=lambda _: None)
    assert error.value.code == 'SEARCH_PAGE_NOT_READY'
    assert clock[0] == source.prepare_timeout_seconds
    assert page.clicks == 1


def test_classic_wait_remains_cancellable(preparation):
    source, page, _ = preparation
    def checkpoint():
        if page.waits:
            raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        source.prepare_page(page, checkpoint=checkpoint, verification=lambda _: None)
    assert page.clicks == 1


def test_cookie_overlay_after_timeout_is_handled_before_retrying_the_switch(preparation):
    source, page, _ = preparation
    page.timeout = True
    original = page.get_by_role
    def button(*args, **kwargs):
        click = original(*args, **kwargs).click
        def switch(**options):
            page.state = {'consent_required': True}
            click(**options)
        return SimpleNamespace(count=lambda: 1, click=switch)
    page.get_by_role = button
    verified = []
    def verify(code):
        verified.append(code)
        page.state = {}
        page.timeout = False
        page.get_by_role = original
        page.table_at = 0.5
    source.prepare_page(page, checkpoint=lambda: None, verification=verify)
    assert verified == ['COOKIE_CONSENT']


def test_real_browser_errors_are_not_swallowed(preparation):
    source, page, _ = preparation
    page.failure = BrowserError('browser disconnected')
    with pytest.raises(BrowserError, match='disconnected'):
        source.prepare_page(page, checkpoint=lambda: None, verification=lambda _: None)


def test_challenge_after_click_stops_for_verification_instead_of_becoming_empty_success(preparation):
    source, page, _ = preparation
    def button(*_args, **_kwargs):
        def click(**_options):
            page.clicks += 1
            page.state = {'blocked': True}
            raise BrowserTimeout('navigation pending')
        return SimpleNamespace(count=lambda: 1, click=click)
    page.get_by_role = button
    def verify(code):
        assert code == 'CAPTCHA'
        raise AuctionListError('SOURCE_ACCESS_DENIED', 'Verification required')
    with pytest.raises(AuctionListError) as error:
        source.prepare_page(page, checkpoint=lambda: None, verification=verify)
    assert error.value.code == 'SOURCE_ACCESS_DENIED'
    assert page.clicks == 1
