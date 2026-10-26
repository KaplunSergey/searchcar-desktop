"""The site, not a shared browser profile, owns the selected project filters."""
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as BrowserError

from app.sources.auction_lists import AuctionBrowserSession, AuctionListError
from app.sources.iaa import IAASource


@pytest.mark.parametrize('case', ['ok', 'error', 'ignored'])
def test_verified_window_is_minimized_without_replacing_its_page_or_context(case):
    commands, closed, navigations = [], [], []
    bounds = {'windowState': 'normal'}
    page = SimpleNamespace(goto=lambda *a, **k: navigations.append(a), wait_for_timeout=lambda ms: None)
    def send(method, params=None):
        commands.append((method, params))
        if method == 'Browser.getWindowForTarget':
            return {'windowId': 7, 'bounds': dict(bounds)}
        if method == 'Browser.setWindowBounds':
            if case == 'error':
                raise BrowserError('window unavailable')
            if case != 'ignored': bounds.update(params['bounds'])
        return {'bounds': dict(bounds)}
    cdp = SimpleNamespace(send=send, detach=lambda: None)
    context = SimpleNamespace(pages=[page], close=lambda: closed.append(True), new_cdp_session=lambda actual: cdp)
    chromium = SimpleNamespace(launch_persistent_context=lambda *a, **k: context)
    session = AuctionBrowserSession(chromium, '/owned/test/profile', {}, interactive=True)
    try:
        if case != 'ok':
            with pytest.raises(AuctionListError) as caught:
                session.hide_verified()
            assert caught.value.code == 'SOURCE_BACKGROUND_BROWSER_UNAVAILABLE'
            assert session.interactive
        else:
            session.hide_verified()
            assert not session.interactive
            assert bounds['windowState'] == 'minimized'
        assert session.page is page and session.context is context
        assert ('Browser.setWindowBounds', {'windowId': 7, 'bounds': {'windowState': 'minimized'}}) in commands
        assert not closed and not navigations
    finally:
        session.close()


def test_hidden_native_reset_cannot_silently_accept_previous_filters():
    invalidations = []
    hidden = SimpleNamespace(is_visible=lambda: False, get_attribute=lambda name: None)
    clear = SimpleNamespace(count=lambda: 1, nth=lambda index: hidden)
    page = SimpleNamespace(get_by_text=lambda *a, **k: clear)
    with pytest.raises(AuctionListError) as caught:
        IAASource().prepare_fresh_selection(page, invalidate=lambda: invalidations.append(True))
    assert caught.value.code == 'SOURCE_SELECTION_RESET_UNAVAILABLE'
    assert not invalidations
