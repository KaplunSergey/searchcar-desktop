"""Background reading, bounded manual fallback and polite navigation pacing."""
from types import SimpleNamespace
from contextlib import nullcontext

import pytest
from playwright.sync_api import Error as BrowserError

from app.sources.contracts import ScanError
from app.sources.registry import get_source
from app.browser_runtime_pool import BrowserRuntimePool
from test_auction_list_adapters import Page, ROWS, adapters, snapshot
from test_source_test_profile import isolated_environment, profile_queue


def runtime(adapters, key, states):
    source = get_source(key)
    calls, closed, clock = [], [], [100.0]
    adapters.monkeypatch.setattr('app.sources.auction_lists.monotonic', lambda: clock[0])
    pages = [Page(items) for items in states]
    for page in pages:
        page.wait_for_timeout = lambda ms: clock.__setitem__(0, clock[0] + ms / 1000)
        page.window_state = 'normal'
        page.window_commands = []
        page.bring_to_front = lambda: None
    def launch(path, **options):
        index = len(calls)
        calls.append((path, options, clock[0]))
        actual = pages[index]
        def send(method, params=None):
            actual.window_commands.append((method, params))
            if method == 'Browser.setWindowBounds': actual.window_state = params['bounds']['windowState']
            return {'windowId': index, 'bounds': {'windowState': actual.window_state}}
        cdp = SimpleNamespace(send=send, detach=lambda: None)
        return SimpleNamespace(browser=None, pages=[actual], close=lambda: closed.append(index), new_cdp_session=lambda page: cdp)
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    opened = source.open_browser(playwright, headless=False, executable_path='/test/chromium')
    return source, opened, pages, calls, closed, clock


def search_url(key):
    return 'https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D' if key == 'copart' else 'https://www.iaai.com/Search?url=x'


@pytest.mark.parametrize('minimize_works', [False, True])
def test_iaa_manual_window_closes_on_job_release_even_if_minimized(adapters, minimize_works):
    ready = snapshot('iaa', [ROWS['iaa'][0]], total=1)
    blocked = {**ready, 'ready': False, 'blocked': True, 'manual_required': True}
    source, opened, pages, calls, closed, _ = runtime(adapters, 'iaa', [[blocked], [ready]])
    if not minimize_works:
        from app.sources.auction_lists import AuctionListError
        def unsupported():
            raise AuctionListError('SOURCE_BACKGROUND_BROWSER_UNAVAILABLE', 'Minimization unavailable')
        opened[1].hide_verified = unsupported
    adapters.monkeypatch.setattr(source, 'open_browser', lambda *_args, **_kwargs: opened)
    with BrowserRuntimePool(lambda: nullcontext(object())) as pool:
        _, context, page = pool.acquire(1, source)
        assert len(source.collect_search_pages(page, search_url('iaa')).rows) == 1
        assert pages[1].window_state == ('minimized' if minimize_works else 'normal')
        assert len(calls) == 2, 'Do not change the existing verification/search behavior'
        pool.release(context)
        assert context.context is None, 'The IAA native window must close immediately, not stay parked'
        assert pages[1].navigations == [search_url('iaa')], 'Do not leave an about:blank window'
        assert closed == [0, 1]
    assert closed == [0, 1], 'Shutdown must not close an already released window twice'


@pytest.mark.parametrize('key', ['copart', 'iaa'])
def test_transient_verification_finishes_without_showing_or_reloading(adapters, key):
    ready = snapshot(key, [ROWS[key][0]], total=1)
    blocked = {**ready, 'ready': False, 'blocked': True, 'manual_required': True}
    source, (_, context, page), pages, calls, _, clock = runtime(adapters, key, [[blocked, ready], [ready]])
    original_wait = pages[0].wait_for_timeout
    def settle(ms):
        original_wait(ms)
        if clock[0] >= 100.5:
            pages[0].index = 1
    pages[0].wait_for_timeout = settle
    progress = []
    try:
        assert len(source.collect_search_pages(page, search_url(key), progress=progress.append).rows) == 1
        assert len(calls) == 1, 'A self-resolving page must not open a visible browser'
        assert pages[0].navigations == [search_url(key)]
        assert not any(p.get('interaction') for p in progress)
    finally:
        context.close()


@pytest.mark.parametrize('reason', ['cancel', 'rate_limit'])
def test_silent_verification_is_cancellable_and_does_not_retry_rate_limits(adapters, reason):
    ready = snapshot('iaa', [ROWS['iaa'][0]], total=1)
    blocked = {**ready, 'ready': False, 'blocked': True, 'manual_required': True}
    source, (_, context, page), pages, calls, _, clock = runtime(adapters, 'iaa', [[blocked], [ready]])
    def checkpoint():
        if clock[0] > 100:
            if reason == 'cancel':
                raise RuntimeError('cancel silent wait')
            pages[0].listeners['response'](SimpleNamespace(status=429, url=search_url('iaa'),
                request=SimpleNamespace(resource_type='document')))
    try:
        with pytest.raises(RuntimeError if reason == 'cancel' else ScanError) as caught:
            source.collect_search_pages(page, search_url('iaa'), checkpoint=checkpoint)
        if reason == 'rate_limit':
            assert caught.value.code == 'SOURCE_RATE_LIMITED'
        assert len(calls) == 1 and clock[0] < 101
        assert not caught.value.partial_collection.rows
    finally:
        context.close()


def test_bare_http_denial_is_not_erased_by_a_still_readable_dom(adapters):
    state = {**snapshot('iaa', [ROWS['iaa'][0]], total=1), 'manual_required': False}
    source, (_, context, page), pages, calls, _, _ = runtime(adapters, 'iaa', [[state]])
    original_goto = pages[0].goto
    def denied(url, **kwargs):
        original_goto(url, **kwargs)
        return SimpleNamespace(status=403)
    pages[0].goto = denied
    try:
        with pytest.raises(ScanError) as caught:
            source.collect_search_pages(page, search_url('iaa'))
        assert caught.value.code == 'SOURCE_ACCESS_DENIED'
        assert not caught.value.partial_collection.rows
        assert len(calls) == 1
    finally:
        context.close()


@pytest.mark.parametrize('key', ['copart', 'iaa'])
def test_transient_check_between_pages_preserves_cursor_and_stays_hidden(adapters, key):
    first = snapshot(key, [ROWS[key][0]], total=2, has_next=True)
    blocked = {**first, 'ready': False, 'blocked': True, 'manual_required': True}
    second = snapshot(key, [ROWS[key][1]], page=2, total=2)
    source, (_, context, page), pages, calls, _, _ = runtime(adapters, key, [[first, blocked, second]])
    original_wait = pages[0].wait_for_timeout
    def settle(ms):
        original_wait(ms)
        if pages[0].index == 1:
            pages[0].index = 2
    pages[0].wait_for_timeout = settle
    try:
        result = source.collect_search_pages(page, search_url(key), all_pages=True)
        assert result.pages_visited == 2 and len(result.rows) == 2
        assert len(calls) == 1 and pages[0].navigations == [search_url(key)]
    finally:
        context.close()


def test_warm_minimized_session_keeps_cursor_after_a_later_manual_check(adapters):
    key = 'iaa'
    ready = snapshot(key, [ROWS[key][0]], total=1)
    blocked = {**ready, 'ready': False, 'blocked': True, 'manual_required': True}
    source, (_, context, page), pages, calls, _, _ = runtime(adapters, key, [[blocked], [ready]])
    try:
        source.collect_search_pages(page, search_url(key))
        assert pages[1].window_state == 'minimized'
        first = snapshot(key, [ROWS[key][0]], total=2, has_next=True)
        pages[1].snapshots = [first, {**first, 'ready': False, 'blocked': True, 'manual_required': True},
                              snapshot(key, [ROWS[key][1]], page=2, total=2)]
        original_wait = pages[1].wait_for_timeout
        def user_confirms(ms):
            original_wait(ms)
            if pages[1].window_state == 'normal' and pages[1].index == 1:
                pages[1].index = 2
        pages[1].wait_for_timeout = user_confirms
        result = source.collect_search_pages(page, search_url(key), all_pages=True)
        assert result.pages_visited == 2 and len(result.rows) == 2
        assert [call[1]['headless'] for call in calls] == [True, False]
        assert pages[1].navigations == [search_url(key), search_url(key)]
        assert pages[1].window_state == 'minimized'
    finally:
        context.close()


def cookie_button(page, *, label='Reject All', blocked_click=False, stays=False):
    original = page.locator
    clicks = []
    class Reject:
        def count(self): return 1
        def is_visible(self): return True
        def inner_text(self, **kwargs): return label
        def click(self, **kwargs):
            assert not kwargs.get('force')
            clicks.append(label)
            if blocked_click:
                raise BrowserError('intercepted')
            if not stays:
                page.snapshots[page.index]['consent_required'] = False
    page.locator = lambda selector: Reject() if selector == '#onetrust-banner-sdk #onetrust-reject-all-handler' else original(selector)
    return clicks


@pytest.mark.parametrize('key', ['copart', 'iaa'])
@pytest.mark.parametrize('banner_page', [1, 2])
def test_recognized_reject_stays_in_background_and_keeps_pagination(adapters, key, banner_page):
    states = [snapshot(key, [ROWS[key][0]], total=2, has_next=True),
              snapshot(key, [ROWS[key][1]], page=2, total=2)]
    states[banner_page - 1]['consent_required'] = True
    source, (_, context, page), pages, calls, _, _ = runtime(adapters, key, [states, [snapshot(key, [ROWS[key][0]], total=1)]])
    clicks = cookie_button(pages[0])
    progress = []
    result = source.collect_search_pages(page, search_url(key), all_pages=True, progress=progress.append)
    context.close()
    assert len(result.rows) == 2 and result.pages_visited == 2
    assert clicks == ['Reject All']
    assert len(calls) == 1 and calls[0][1]['headless'] is True
    assert not any(p.get('interaction') for p in progress)


@pytest.mark.parametrize('case', ['accept', 'intercepted', 'retained', 'captcha'])
def test_cookie_auto_choice_is_bounded_and_never_accepts_or_solves_a_challenge(adapters, case):
    key = 'iaa'
    ready = snapshot(key, [ROWS[key][0]], total=1)
    prompt = {**ready, 'consent_required': True, 'blocked': case == 'captcha', 'manual_required': case == 'captcha'}
    source, (_, context, page), pages, calls, _, _ = runtime(adapters, key, [[prompt], [ready], [ready]])
    clicks = cookie_button(pages[0], label='Accept All Cookies' if case == 'accept' else 'Reject All',
                          blocked_click=case == 'intercepted', stays=case == 'retained')
    progress = []
    result = source.collect_search_pages(page, search_url(key), progress=progress.append)
    context.close()
    assert len(result.rows) == 1
    assert clicks == ([] if case in ('accept', 'captcha') else ['Reject All'])
    assert [p['interaction']['reason'] for p in progress if p.get('interaction')] == [
        'CAPTCHA' if case == 'captcha' else 'COOKIE_CONSENT']
    assert [call[1]['headless'] for call in calls] == [True, False]
    assert pages[1].window_state == 'minimized'


@pytest.mark.parametrize('user_finishes', [False, True])
def test_late_second_cookie_banner_reuses_the_same_manual_deadline(adapters, user_finishes):
    key = 'iaa'
    ready = snapshot(key, [ROWS[key][0]], total=1)
    prompt = {**ready, 'consent_required': True}
    source, (_, context, page), pages, calls, _, clock = runtime(adapters, key,
        [[prompt], [ready, prompt, ready]])
    adapters.monkeypatch.setattr(source, 'verification_timeout_seconds', 5)
    # Isolate the manual deadline from the separately tested navigation pacing.
    page.request_interval_seconds = 0
    original_wait = pages[1].wait_for_timeout
    def user_choice(ms):
        original_wait(ms)
        if user_finishes:
            pages[1].index = 2
    pages[1].wait_for_timeout = user_choice
    original_hide = page.hide_verified
    hides = []
    def hide():
        original_hide()
        if not hides: pages[1].index = 1
        hides.append(True)
    page.hide_verified = hide
    progress = []
    try:
        if user_finishes:
            assert len(source.collect_search_pages(page, search_url(key), progress=progress.append).rows) == 1
        else:
            with pytest.raises(ScanError) as caught:
                source.collect_search_pages(page, search_url(key), progress=progress.append)
            assert caught.value.code == 'SOURCE_VERIFICATION_TIMEOUT'
            assert clock[0] == pytest.approx(105)  # Relaunch and both banners share the original 5s budget.
        deadlines = [p['interaction']['expires_at'] for p in progress if p.get('interaction')]
        assert len(set(deadlines)) == 1
        assert len(calls) == 2  # A later consent prompt restores the same owned window.
        assert all(call[0] == calls[0][0] for call in calls)
    finally:
        context.close()


def test_auto_reject_after_mid_search_challenge_still_requires_a_fresh_scan(adapters):
    key = 'iaa'
    first = snapshot(key, [ROWS[key][0]], total=2, has_next=True)
    blocked = {**first, 'ready': False, 'blocked': True, 'manual_required': True}
    prompt = {**first, 'consent_required': True}
    source, (_, context, page), pages, calls, _, _ = runtime(adapters, key, [[first, blocked], [prompt]])
    clicks = cookie_button(pages[1])
    try:
        with pytest.raises(ScanError) as caught:
            source.collect_search_pages(page, search_url(key), all_pages=True)
        assert caught.value.code == 'SEARCH_VERIFICATION_RESTART_REQUIRED'
        assert len(caught.value.partial_collection.rows) == 1
        assert caught.value.partial_collection.pages_visited == 1
        assert clicks == ['Reject All']
        assert len(calls) == 2
    finally:
        context.close()


def test_auto_reject_wait_is_cancellable(adapters):
    key = 'iaa'
    prompt = {**snapshot(key, [ROWS[key][0]], total=1), 'consent_required': True}
    source, (_, context, page), pages, calls, _, _ = runtime(adapters, key, [[prompt]])
    clicks = cookie_button(pages[0], stays=True)
    def cancel():
        if clicks:
            raise RuntimeError('cancelled during cookie choice')
    try:
        with pytest.raises(RuntimeError, match='cancelled during cookie choice'):
            source.collect_search_pages(page, search_url(key), checkpoint=cancel)
        assert clicks == ['Reject All'] and len(calls) == 1
    finally:
        context.close()


@pytest.mark.parametrize('key', ['copart', 'iaa'])
def test_normal_search_has_no_visible_window_even_when_general_browser_setting_is_visible(adapters, key):
    source, (_, context, page), _, calls, closed, _ = runtime(adapters, key, [[snapshot(key, [ROWS[key][0]], total=1)]])
    result = source.collect_search_pages(page, search_url(key))
    context.close()
    assert len(result.rows) == 1
    assert [call[1]['headless'] for call in calls] == [True]
    assert closed == [0]


@pytest.mark.parametrize('key', ['copart', 'iaa'])
@pytest.mark.parametrize('reason', ['COOKIE_CONSENT', 'CAPTCHA'])
def test_after_manual_choice_both_pages_are_read_in_background(adapters, key, reason):
    ready = snapshot(key, [ROWS[key][0]], total=2, has_next=True)
    prompt = {**ready, 'ready': False, 'blocked': reason == 'CAPTCHA',
              'manual_required': reason == 'CAPTCHA', 'consent_required': reason == 'COOKIE_CONSENT'}
    second = snapshot(key, [ROWS[key][1]], page=2, total=2)
    source, (_, context, page), pages, calls, closed, _ = runtime(adapters, key,
        [[prompt], [ready, second], [ready, second]])
    original_parse = source.parse_list_row
    def parse(row):
        assert pages[1].window_state == 'minimized', 'Do not collect from the visible manual window'
        return original_parse(row)
    adapters.monkeypatch.setattr(source, 'parse_list_row', parse)
    progress = []
    try:
        result = source.collect_search_pages(page, search_url(key), all_pages=True, progress=progress.append)
        assert result.pages_visited == 2 and len(result.rows) == 2
        assert [call[1]['headless'] for call in calls] == [True, False]
        assert pages[1].window_state == 'minimized'
        assert pages[1].index == 1, 'The verified page paginates only after being minimized'
        assert pages[1].navigations == [search_url(key)], 'Do not reload after manual verification'
        assert len({call[0] for call in calls}) == 1
        assert [item['interaction']['reason'] for item in progress if item.get('interaction')] == [reason]
    finally:
        context.close()
    assert closed == [0, 1]


@pytest.mark.parametrize('case', ['denied', 'rate-limit', 'challenge', 'cancelled'])
def test_failed_return_to_background_never_reads_from_the_manual_window(adapters, case):
    key = 'iaa'
    ready = snapshot(key, [ROWS[key][0]], total=1)
    prompt = {**ready, 'ready': False, 'blocked': True, 'manual_required': True}
    source, (_, context, page), pages, calls, closed, _ = runtime(adapters, key,
        [[prompt], [ready], [prompt if case == 'challenge' else ready]])
    original = page.hide_verified
    def hide():
        original()
        if case in ('denied', 'rate-limit'):
            pages[1].listeners['response'](SimpleNamespace(status=429 if case == 'rate-limit' else 403, url=search_url(key),
                request=SimpleNamespace(resource_type='document')))
        if case == 'challenge':
            pages[1].snapshots[0] = prompt
    page.hide_verified = hide
    def checkpoint():
        if case == 'cancelled' and len(calls) == 2:
            raise RuntimeError('cancelled before window minimization')
    try:
        if case == 'cancelled':
            with pytest.raises(RuntimeError, match='cancelled before window minimization') as caught:
                source.collect_search_pages(page, search_url(key), checkpoint=checkpoint)
        else:
            with pytest.raises(ScanError) as caught:
                source.collect_search_pages(page, search_url(key), checkpoint=checkpoint)
            assert caught.value.code == ('SOURCE_RATE_LIMITED' if case == 'rate-limit'
                                         else 'SOURCE_ACCESS_DENIED' if case == 'denied' else 'CAPTCHA')
        assert not caught.value.partial_collection.rows
        assert len(calls) == 2
        assert pages[1].index == 0
    finally:
        context.close()


def test_cookie_reject_after_access_confirmation_also_returns_to_background(adapters):
    key = 'iaa'
    ready = snapshot(key, [ROWS[key][0]], total=1)
    blocked = {**ready, 'ready': False, 'blocked': True, 'manual_required': True}
    consent = {**ready, 'consent_required': True}
    source, (_, context, page), pages, calls, _, _ = runtime(adapters, key,
        [[blocked], [consent], [ready]])
    clicks = cookie_button(pages[1])
    try:
        assert len(source.collect_search_pages(page, search_url(key)).rows) == 1
        assert clicks == ['Reject All']
        assert [call[1]['headless'] for call in calls] == [True, False]
        assert pages[1].window_state == 'minimized'
    finally:
        context.close()


def test_unsupported_window_minimize_continues_the_verified_search_with_a_notice(adapters):
    key = 'iaa'
    first = snapshot(key, [ROWS[key][0]], total=2, has_next=True)
    blocked = {**first, 'ready': False, 'blocked': True, 'manual_required': True}
    second = snapshot(key, [ROWS[key][1]], page=2, total=2)
    source, (_, context, page), pages, calls, closed, _ = runtime(adapters, key, [[blocked], [first, second]])
    def unsupported():
        from app.sources.auction_lists import AuctionListError
        raise AuctionListError('SOURCE_BACKGROUND_BROWSER_UNAVAILABLE', 'Native window state unchanged')
    page.hide_verified = unsupported
    progress = []
    try:
        result = source.collect_search_pages(page, search_url(key), all_pages=True, progress=progress.append)
        assert result.pages_visited == 2 and len(result.rows) == 2
        assert pages[1].navigations == [search_url(key)]
        assert len(calls) == 2
        assert progress[-1]['browser_notice'] == 'MANUAL_MINIMIZE'
        assert progress[-1]['complete']
    finally:
        context.close()
    assert closed == [0, 1]


@pytest.mark.parametrize('reason', ['COOKIE_CONSENT', 'CAPTCHA'])
def test_confirmed_prompt_opens_same_owned_profile_once_and_cleanup_closes_replacement(adapters, reason):
    key = 'copart'
    ready = snapshot(key, [ROWS[key][0]], total=1)
    blocked = {**ready, 'ready': False, 'blocked': reason == 'CAPTCHA', 'manual_required': reason == 'CAPTCHA', 'consent_required': reason == 'COOKIE_CONSENT'}
    source, (_, context, page), pages, calls, closed, _ = runtime(adapters, key, [[blocked], [blocked, ready], [ready]])
    original_wait = pages[1].wait_for_timeout
    def user_confirms(ms):
        original_wait(ms)
        pages[1].index = 1
    pages[1].wait_for_timeout = user_confirms
    progress = []
    result = source.collect_search_pages(page, search_url(key), progress=progress.append)
    context.close()
    context.close()  # Worker cleanup must be idempotent even after a relaunch.
    assert len(result.rows) == 1
    assert [call[1]['headless'] for call in calls] == [True, False]
    assert calls[0][0] == calls[1][0]
    assert calls[1][1]['no_viewport'] is True
    assert closed == [0, 1]
    assert pages[1].navigations == [search_url(key)]
    assert [p['interaction']['reason'] for p in progress if p.get('interaction')] == [reason]


@pytest.mark.parametrize('case', ['parse', 'denied', 'rate_limit'])
def test_error_without_a_manual_prompt_does_not_open_a_window_or_retry(adapters, case):
    key = 'iaa'
    state = {**snapshot(key, [], total=1), 'ready': False, 'blocked': case == 'denied', 'manual_required': False}
    source, (_, context, page), pages, calls, closed, clock = runtime(adapters, key, [[state]])
    adapters.monkeypatch.setattr(source, 'read_timeout_seconds', 0.1)
    if case == 'rate_limit':
        original = pages[0].goto
        def rate_limited(url, **options):
            original(url, **options)
            return SimpleNamespace(status=429)
        pages[0].goto = rate_limited
    expected = {'parse': 'SEARCH_PAGE_NOT_READY', 'denied': 'SOURCE_ACCESS_DENIED', 'rate_limit': 'SOURCE_RATE_LIMITED'}[case]
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page, search_url(key))
    context.close()
    assert caught.value.code == expected
    assert not caught.value.partial_collection.rows
    assert len(calls) == 1 and calls[0][1]['headless'] is True
    assert len(pages[0].navigations) == 1
    assert closed == [0]


def test_page_and_project_requests_share_a_cancellable_minimum_interval(adapters):
    key = 'iaa'
    states = [snapshot(key, [ROWS[key][0]], total=2, has_next=True), snapshot(key, [ROWS[key][1]], page=2, total=2)]
    source, (_, context, page), pages, calls, closed, clock = runtime(adapters, key, [states])
    starts = []
    original_goto, original_locator = pages[0].goto, pages[0].locator
    def goto(url, **options):
        starts.append(clock[0])
        pages[0].index = 0
        return original_goto(url, **options)
    def locator(selector):
        inner = original_locator(selector)
        def click(**options):
            starts.append(clock[0])
            pages[0].index += 1
        inner.click = click
        return inner
    pages[0].goto, pages[0].locator = goto, locator
    source.collect_search_pages(page, search_url(key), all_pages=True)
    source.collect_search_pages(page, search_url(key))
    assert len(starts) == 3
    assert all(b - a >= 3 for a, b in zip(starts, starts[1:]))
    before_pause = clock[0]
    def cancelled():
        if clock[0] > before_pause:
            raise RuntimeError('cancelled during pause')
    with pytest.raises(RuntimeError, match='cancelled during pause'):
        source.collect_search_pages(page, search_url(key), checkpoint=cancelled)
    assert len(starts) == 3
    assert 0 < clock[0] - before_pause < 3
    context.close()


@pytest.mark.parametrize('consent_after_challenge', [False, True])
def test_mid_pagination_confirmation_preserves_partial_rows_and_next_project_returns_to_background(adapters, consent_after_challenge):
    key = 'iaa'
    ready = snapshot(key, [ROWS[key][0]], total=1)
    blocked = {**ready, 'ready': False, 'blocked': True, 'manual_required': True}
    source, (_, context, page), pages, calls, closed, _ = runtime(adapters, key, [
        [snapshot(key, [ROWS[key][0]], total=2, has_next=True), blocked],
        [{**ready, 'consent_required': True}, ready] if consent_after_challenge else [ready], [ready],
    ])
    original_wait = pages[1].wait_for_timeout
    def user_chooses_cookies(ms):
        original_wait(ms)
        if consent_after_challenge:
            pages[1].index = 1
    pages[1].wait_for_timeout = user_chooses_cookies
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page, search_url(key), all_pages=True)
    assert caught.value.code == 'SEARCH_VERIFICATION_RESTART_REQUIRED'
    assert len(caught.value.partial_collection.rows) == 1
    assert caught.value.partial_collection.pages_visited == 1
    assert source.collect_search_pages(page, search_url(key)).pages_visited == 1
    context.close()
    assert [call[1]['headless'] for call in calls] == [True, False]
    assert pages[1].window_state == 'minimized'
    assert pages[1].navigations == [search_url(key), search_url(key)]
    assert closed == [0, 1]
    assert all(not actual.listeners for actual in pages)


def test_replacement_browser_keeps_rate_limit_monitoring_and_never_opens_another_window(adapters):
    key = 'iaa'
    ready = snapshot(key, [ROWS[key][0]], total=1)
    blocked = {**ready, 'ready': False, 'blocked': True, 'manual_required': True}
    source, (_, context, page), pages, calls, closed, _ = runtime(adapters, key, [[blocked], [ready]])
    def rate_limit(url, **options):
        pages[1].url = url
        pages[1].listeners['response'](SimpleNamespace(status=429, url='https://www.iaai.com/runtime.js', request=SimpleNamespace(resource_type='script')))
    pages[1].goto = rate_limit
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page, search_url(key))
    context.close()
    assert caught.value.code == 'SOURCE_RATE_LIMITED'
    assert len(calls) == 2
    assert closed == [0, 1]


def test_visible_launch_failure_does_not_leak_the_background_context(adapters):
    key = 'iaa'
    blocked = {**snapshot(key, [], total=1), 'ready': False, 'blocked': True, 'manual_required': True}
    source, (_, context, page), _, calls, closed, _ = runtime(adapters, key, [[blocked]])
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page, search_url(key))
    context.close()
    assert caught.value.code == 'SOURCE_VISIBLE_BROWSER_UNAVAILABLE'
    assert closed == [0]


def test_confirmation_then_manual_window_close_is_an_error_not_empty_success(adapters):
    key = 'iaa'
    blocked = {**snapshot(key, [], total=1), 'ready': False, 'blocked': True, 'manual_required': True}
    source, (_, context, page), pages, _, closed, _ = runtime(adapters, key, [[blocked], [blocked]])
    pages[1].is_closed = lambda: True
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page, search_url(key))
    context.close()
    assert caught.value.code == 'SOURCE_BROWSER_CLOSED'
    assert not caught.value.partial_collection.rows
    assert closed == [0, 1]


def test_cookie_prompt_arriving_during_classic_view_click_requests_manual_choice(adapters):
    source = get_source('copart')
    state = {'ready': False, 'consent_required': False, 'can_prepare': True}
    clicks, confirmations = [], []
    class Classic:
        def count(self): return 1
        def click(self, **options):
            clicks.append(True)
            if len(clicks) == 1:
                state['consent_required'] = True
                raise BrowserError('Cookie banner intercepts pointer events')
            state['ready'] = True
    page = SimpleNamespace(
        locator=lambda selector: SimpleNamespace(count=lambda: int(state['ready'])),
        evaluate=lambda script: dict(state), get_by_role=lambda *args, **options: Classic(),
    )
    def verification(reason):
        confirmations.append(reason)
        state['consent_required'] = False  # User selects their desired option.
    source.prepare_page(page, checkpoint=lambda: None, verification=verification)
    assert confirmations == ['COOKIE_CONSENT']
    assert len(clicks) == 2 and state['ready']


@pytest.mark.parametrize('outcome', ['loaded', 'timeout', 'cancel'])
def test_copart_cold_interface_has_its_own_bounded_cancellable_load_budget(adapters, outcome):
    source = get_source('copart')
    clock, clicks = [0.0], []
    adapters.monkeypatch.setattr('app.sources.copart.monotonic', lambda: clock[0])
    class Classic:
        def count(self): return int(outcome == 'loaded' and clock[0] >= 40)
        def click(self, **kwargs): clicks.append(clock[0])
    page = SimpleNamespace(
        locator=lambda selector: SimpleNamespace(count=lambda: 0),
        evaluate=lambda script: {'ready':False,'blocked':False,'consent_required':False,'document_loaded':True,
                                 'interface':{'modern_table':True,'unrendered_template':True,'visible_frames':0}},
        get_by_role=lambda *args, **kwargs: Classic(),
        wait_for_timeout=lambda ms: clock.__setitem__(0, clock[0]+ms/1000),
    )
    def checkpoint():
        if outcome == 'cancel' and clock[0] >= 1:
            raise RuntimeError('cancel loading')
    def verification(reason):
        pytest.fail('Loading alone must not be presented as a CAPTCHA')
    if outcome == 'loaded':
        source.prepare_page(page,checkpoint=checkpoint,verification=verification)
        assert clicks == [40.0]
    elif outcome == 'timeout':
        with pytest.raises(ScanError) as caught:
            source.prepare_page(page,checkpoint=checkpoint,verification=verification)
        assert caught.value.code == 'SEARCH_PAGE_NOT_READY'
        assert clock[0] == 90.0 and not clicks
        assert 'modern_table=True' in str(caught.value)
        assert 'unrendered_template=True' in str(caught.value)
        assert 'visible_frames=0' in str(caught.value)
    else:
        with pytest.raises(RuntimeError,match='cancel loading'):
            source.prepare_page(page,checkpoint=checkpoint,verification=verification)
        assert clock[0] == 1.0 and not clicks
    assert source.read_timeout_seconds == 30, 'Later result pages must retain their existing budget'


@pytest.mark.parametrize('outcome', ['ready', 'timeout', 'cancel', 'scope_changed', 'closed', 'rate_limit', 'denied', 'cookies'])
def test_copart_stalled_first_page_opens_one_diagnostic_window_with_the_original_budget(adapters, outcome):
    ready = snapshot('copart', [ROWS['copart'][0]], total=1)
    loading = {**ready, 'ready':False, 'document_loaded':True, 'manual_required':False, 'rows':[], 'total':None}
    window = ready if outcome == 'ready' else loading
    states = [[loading], [{**loading,'consent_required':True}, ready] if outcome == 'cookies' else [window]]
    source, (_, context, page), pages, calls, closed, clock = runtime(adapters, 'copart', states)
    adapters.monkeypatch.setattr('app.sources.copart.monotonic', lambda: clock[0])
    for actual in pages:
        original_locator = actual.locator
        actual.locator = lambda selector, actual=actual, original=original_locator: (
            SimpleNamespace(count=lambda: int(actual.snapshots[actual.index]['ready']))
            if selector == '#serverSideDataTable' else original(selector))
        actual.get_by_role = lambda *args, **kwargs: SimpleNamespace(count=lambda: 0)
    if outcome == 'cookies':
        original_wait = pages[1].wait_for_timeout
        def close_cookie_dialog(ms):
            original_wait(ms)
            pages[1].index = 1  # The user makes their own choice in the visible site.
        pages[1].wait_for_timeout = close_cookie_dialog
    original_goto = pages[1].goto
    def window_goto(url, **kwargs):
        original_goto(url, **kwargs)
        if outcome == 'scope_changed': pages[1].url = url.replace('%7B%7D','%7B%22query%22%3A%5B%22OTHER%22%5D%7D')
        if outcome == 'closed': pages[1].is_closed = lambda: True
        if outcome in ('rate_limit', 'denied'):
            pages[1].listeners['response'](SimpleNamespace(status=429 if outcome == 'rate_limit' else 403,
                url=url, request=SimpleNamespace(resource_type='document')))
    pages[1].goto = window_goto
    progress = []
    def checkpoint():
        if outcome == 'cancel' and clock[0] >= 116: raise RuntimeError('cancel diagnostic window')
    try:
        if outcome in ('ready', 'cookies'):
            result = source.collect_search_pages(page, search_url('copart'), progress=progress.append, checkpoint=checkpoint)
            assert len(result.rows) == 1 and result.pages_visited == 1
            assert pages[1].window_state == 'minimized'
        else:
            with pytest.raises(RuntimeError if outcome == 'cancel' else ScanError) as caught:
                source.collect_search_pages(page, search_url('copart'), progress=progress.append, checkpoint=checkpoint)
            assert not caught.value.partial_collection.rows
            if outcome != 'cancel':
                assert caught.value.code == {'timeout':'SEARCH_PAGE_NOT_READY','scope_changed':'SOURCE_SEARCH_SCOPE_CHANGED',
                    'closed':'SOURCE_BROWSER_CLOSED','rate_limit':'SOURCE_RATE_LIMITED','denied':'SOURCE_ACCESS_DENIED'}[outcome]
        assert len(calls) == 2 and calls[1][2] == 115.0, 'Show the owned window once, after 15s'
        assert pages[0].navigations == pages[1].navigations == [search_url('copart')]
        assert any(info.get('stage') == 'SEARCH_WINDOW_LOADING' for info in progress)
        assert not any(info.get('interaction') for info in progress) if outcome != 'cookies' else any(info.get('interaction') for info in progress)
        if outcome == 'timeout': assert clock[0] == 190.0, 'Do not restart the 90-second loading budget'
    finally:
        context.close()
    assert closed == [0,1]
