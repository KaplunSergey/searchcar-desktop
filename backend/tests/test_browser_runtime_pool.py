"""Worker-owned reuse and disposal, independent of site access."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.browser_runtime_pool import BrowserRuntimePool


@pytest.fixture
def runtime(monkeypatch):
    events, clock = [], [10.0]
    monkeypatch.setattr('app.browser_runtime_pool.monotonic', lambda: clock[0])
    @contextmanager
    def driver():
        events.append('driver start')
        try:
            yield object()
        finally:
            events.append('driver stop')
    def source(key):
        def open_browser(*_args, **_options):
            page = SimpleNamespace(closed=False, requested=True)
            page.is_closed = lambda: page.closed
            page.reuse_requested = lambda: page.requested
            page.close = lambda: events.append(('close', key))
            page.park = lambda: events.append(('park', key))
            events.append(('open', key))
            return None, page, page
        return SimpleNamespace(descriptor=SimpleNamespace(key=key), open_browser=open_browser)
    return SimpleNamespace(pool=BrowserRuntimePool(driver, idle_seconds=10, max_idle=2),
                           source=source, events=events, clock=clock)


def test_idle_reuse_retains_context_but_parks_the_previous_search(runtime):
    with runtime.pool as pool:
        first = pool.acquire(1, runtime.source('alpha'), headless=True)
        pool.release(first[1])
        assert runtime.events[-1] == ('park', 'alpha')
        second = pool.acquire(1, runtime.source('alpha'), headless=True)
        assert second is first
        pool.release(second[1])
    assert runtime.events.count(('open', 'alpha')) == 1
    assert runtime.events.count(('close', 'alpha')) == 1
    assert runtime.events[-1] == 'driver stop'


@pytest.mark.parametrize('has_window', [False, True])
def test_source_can_close_windowed_browser_on_release_without_losing_headless_reuse(runtime, has_window):
    with runtime.pool as pool:
        source = runtime.source('alpha')
        source.reuse_windowed_browser_session = False
        first = pool.acquire(1, source)
        first[1].has_window = has_window
        pool.release(first[1])
        assert (('close', 'alpha') in runtime.events) is has_window
        assert (('park', 'alpha') in runtime.events) is not has_window
        second = pool.acquire(1, source)
        second[1].has_window = has_window
        assert (second is first) is not has_window
        pool.release(second[1])


def test_default_source_still_keeps_windowed_session(runtime):
    with runtime.pool as pool:
        source = runtime.source('bravo')
        first = pool.acquire(1, source)
        first[1].has_window = True
        pool.release(first[1])
        assert ('close', 'bravo') not in runtime.events
        assert pool.acquire(1, source) is first


@pytest.mark.parametrize('outcome', ['unsupported', 'hidden', 'error', 'still-visible'])
def test_source_can_retain_verified_browser_only_after_its_window_is_closed(runtime, outcome):
    from playwright.sync_api import Error as BrowserError
    supported = outcome == 'hidden'
    with runtime.pool as pool:
        source = runtime.source('alpha')
        source.reuse_windowed_browser_session = False
        source.reuse_hidden_browser_session = True
        first = pool.acquire(1, source)
        first[1].has_window = True
        def hide():
            runtime.events.append('hidden attempt')
            if outcome == 'error': raise BrowserError('Hidden targets unavailable')
            if supported: first[1].has_window = False
            return outcome in ('hidden', 'still-visible')
        first[1].park_hidden = hide
        pool.release(first[1])
        assert 'hidden attempt' in runtime.events
        assert (('close', 'alpha') in runtime.events) is not supported
        second = pool.acquire(1, source)
        assert (second is first) is supported



@pytest.mark.parametrize('reason', ['expired', 'selection', 'closed', 'owner', 'options'])
def test_unusable_or_mismatched_session_is_not_reused(runtime, reason):
    with runtime.pool as pool:
        source = runtime.source('alpha')
        first = pool.acquire(1, source, headless=True)
        pool.release(first[1])
        if reason == 'expired': runtime.clock[0] += 11
        if reason == 'selection': first[1].requested = False
        if reason == 'closed': first[1].closed = True
        second = pool.acquire(2 if reason == 'owner' else 1, source, headless=reason != 'options')
        assert second is not first
        if reason != 'options':
            assert ('close', 'alpha') in runtime.events
        pool.release(second[1])
    assert runtime.events.count(('close', 'alpha')) == 2


def test_idle_limit_evicts_oldest_but_never_an_active_runtime(runtime):
    with runtime.pool as pool:
        active = pool.acquire(1, runtime.source('active'))
        for key in ['alpha', 'bravo', 'charlie']:
            handles = pool.acquire(1, runtime.source(key))
            pool.release(handles[1])
            runtime.clock[0] += 1
        assert ('close', 'alpha') in runtime.events
        assert ('close', 'active') not in runtime.events
        pool.release(active[1])


def test_non_reusable_source_can_reclaim_idle_browser_budget(runtime):
    with runtime.pool as pool:
        idle = pool.acquire(1, runtime.source('idle'))
        pool.release(idle[1])
        pool.acquire(1, runtime.source('active'))
        pool.close_idle()
        assert ('close', 'idle') in runtime.events
        assert ('close', 'active') not in runtime.events


def test_driver_is_lazy_and_shutdown_after_failure_releases_every_profile(runtime):
    with pytest.raises(RuntimeError, match='job failed'):
        with runtime.pool as pool:
            assert not runtime.events
            pool.acquire(1, runtime.source('alpha'))
            pool.acquire(1, runtime.source('bravo'))
            raise RuntimeError('job failed')
    assert runtime.events[-3:] == [('close', 'alpha'), ('close', 'bravo'), 'driver stop']
