"""Routed Chrome evidence: no external site, user data, or manual challenge solving."""
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.browser_runtime_pool import BrowserRuntimePool
from app.desktop_runtime import desktop_playwright, configure_playwright_driver
from app.sources.registry import get_source
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_NATIVE_WINDOW') != '1', reason='Opt-in real windowless Chrome lifecycle')
def test_verified_iaa_keeps_browser_without_window_and_restores_manual_page_in_same_context(adapters):
    from playwright.sync_api import Error as BrowserError
    configure_playwright_driver(Path(__file__).resolve().parents[2] / 'desktop/runtime/playwright-driver')
    source = get_source('iaa')
    launches, interactions, requests = [], [], []
    html = '''<span id="ResultFoundOnPage">1 VEHICLES</span>
      <select id="ddlCity"><option selected>Chicago</option></select><span id="pageSizeHeader">100</span>
      <div id="ListingGrid"><div class="table-row"><h4><a href="/VehicleDetail/ID~US">Fixture</a></h4>
      <a href="/SalesList/100~US/10122026">Sale</a><span class="data-list__value--action">Mon Oct 12, 8:30am CDT</span></div></div>
      <div id="divPagination"><button class="btn-page active">1</button><button aria-label="Next" disabled>Next</button></div>'''
    with desktop_playwright() as pw:
        def launch(profile, **options):
            launches.append(options['headless'])
            context = pw.chromium.launch_persistent_context(profile, **options)
            def site(route):
                url = route.request.url
                requests.append(url)
                challenge = url.endswith('=challenge') and requests.count(url) == 1
                route.fulfill(content_type='text/html', body='<p>Verify you are human</p>' if challenge else html.replace('ID', '20002' if url.endswith('=two') else '10001'))
            context.route('**/*', site)
            return context
        driver = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        original_open = source.open_browser
        adapters.monkeypatch.setattr(source, 'open_browser', lambda *args, **kwargs: original_open(*args, **kwargs, interactive=True))
        with BrowserRuntimePool(lambda: nullcontext(driver)) as pool:
            _, session, _ = pool.acquire(1, source, headless=True, executable_path=None)
            session.request_interval_seconds = 0
            original_context, first_visible = session.context, session.page
            for suffix, expected in [('one', '10001~US'), ('two', '20002~US'), ('challenge', '10001~US')]:
                result = source.collect_search_pages(session, 'https://www.iaai.com/Search?url=' + suffix,
                    progress=lambda info: interactions.append(info.get('interaction')))
                assert set(result.rows) == {expected}
                if suffix == 'one': session.page.evaluate("localStorage.setItem('verified-fixture','kept')")
                else: assert session.page.evaluate("localStorage.getItem('verified-fixture')") == 'kept'
                pool.release(session)
                assert first_visible.is_closed()
                assert session.context is original_context and not session.has_window
                assert len(session.context.pages) == 1
                cdp = session.context.new_cdp_session(session.page)
                with pytest.raises(BrowserError, match='window.*not found'):
                    cdp.send('Browser.getWindowForTarget')
                cdp.detach()
                assert pool.acquire(1, source, headless=True, executable_path=None)[1] is session
            assert launches == [False], 'No browser relaunch after verified context enters the background'
            assert any(info and info['reason'] == 'CAPTCHA' for info in interactions)
        assert session.context is None and not original_context.browser.is_connected()


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in installed Chrome fixture')
def test_hidden_chrome_waits_then_reuses_context_for_distinct_projects(adapters):
    configure_playwright_driver(Path(__file__).resolve().parents[2] / 'desktop/runtime/playwright-driver')
    source = get_source('iaa')
    html = '''<div>2 VEHICLES <span id="ResultFoundOnPage">1</span></div>
      <select id="ddlCity"><option selected>Chicago</option></select><span id="pageSizeHeader">1</span>
      <div id="ListingGrid"><div class="table-row"><h4><a id="car" href="/VehicleDetail/FIRST~US">Fixture car</a></h4>
        <a href="/SalesList/100~US/10122026">Sale</a><span class="data-list__value--action">Mon Oct 12, 8:30am CDT</span>
      </div></div><div id="divPagination"><button class="btn-page active">1</button>
      <button id="next" aria-label="Next" onclick="nextPage()">Next</button></div>'''
    launches, requests, interactions = [], [], []
    with desktop_playwright() as pw:
        def launch(profile, **options):
            launches.append(options['headless'])
            context = pw.chromium.launch_persistent_context(profile, **options)
            def site(route):
                requests.append(route.request.url)
                first, second = ('10001', '10002') if route.request.url.endswith('=one') else ('20001', '20002')
                grid = html.replace('FIRST', first)
                body = '<p>Verify you are human</p><script>setTimeout(()=>{document.body.innerHTML=' + repr(grid) + ''';
                  window.nextPage=()=>{document.querySelector('#car').href='/VehicleDetail/SECOND~US';
                  document.querySelector('.btn-page').textContent='2';document.querySelector('#next').disabled=true;};
                  },300);</script>'''.replace('SECOND', second)
                route.fulfill(content_type='text/html', body=body)
            context.route('**/*', site)
            return context
        driver = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        with BrowserRuntimePool(lambda: nullcontext(driver)) as pool:
            first_handle = None
            for name, ids in [('one', {'10001~US', '10002~US'}), ('two', {'20001~US', '20002~US'})]:
                _, session, page = pool.acquire(1, source, headless=True, executable_path='/bundled/headless_shell')
                if first_handle is None: first_handle = session
                assert session is first_handle
                result = source.collect_search_pages(page, 'https://www.iaai.com/Search?url=' + name,
                    all_pages=True, progress=lambda info: interactions.append(info.get('interaction')))
                assert set(result.rows) == ids and result.pages_visited == 2
                assert not session.interactive
                pool.release(session)
                assert session.url == 'about:blank'
            assert launches == [True]
            assert requests == ['https://www.iaai.com/Search?url=one', 'https://www.iaai.com/Search?url=two']
            assert not any(interactions)
        assert first_handle.context is None
