"""Real browser lifecycle, with every site response fulfilled by a local fixture."""
import json
import os
from pathlib import Path

import pytest

from app.desktop_runtime import (desktop_playwright, configure_playwright_environment,
    configure_playwright_driver, bundled_headless_chromium)
from app.source_search_selection import SearchSelections, SelectionSession, load_draft
from app.sources.iaa import IAASource
from test_source_search_selection import IAA
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in bundled browser fixture')
@pytest.mark.parametrize('case', ['rendered', 'loading', 'missing-count', 'empty'])
def test_iaa_filter_confirmation_has_its_own_dom_readiness(tmp_path, case):
    root = Path(__file__).resolve().parents[2]
    configure_playwright_environment(root / 'desktop/runtime/browsers')
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    with desktop_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=str(bundled_headless_chromium(root / 'desktop/runtime/browsers')))
        page = browser.new_page()
        try:
            page.set_content('''<div>COUNT <span id="ResultFoundOnPage">RESULT</span></div>
              <div id="ListingGrid">ROWS</div>'''
              .replace('COUNT', '' if case == 'missing-count' else ('0 VEHICLES' if case == 'empty' else '1 VEHICLES'))
              .replace('RESULT', 'No vehicles' if case == 'empty' else '1')
              .replace('ROWS', '' if case in ('loading', 'empty') else '<div class="table-row"><h4><a href="/VehicleDetail/123~US">Fixture</a></h4></div>'))
            source = IAASource()
            # Existing scans accept a positively identified empty result too.
            assert source.read_dom(page)['ready'] == (case == 'empty'), 'Non-empty scans must still require pagination/city proof'
            assert source.read_selection_dom(page)['ready'] == (case in ('rendered', 'empty'))
        finally:
            browser.close()


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in bundled browser fixture')
@pytest.mark.parametrize('case', ['close', 'confirm', 'unfinished', 'unknown'])
def test_actual_request_capture_and_window_lifecycle(tmp_path, case):
    root = Path(__file__).resolve().parents[2]
    configure_playwright_environment(root / 'desktop/runtime/browsers')
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    body = {**IAA, **({'UnexpectedAuth': 'must-not-be-saved'} if case == 'unknown' else {})}
    html = '''<!doctype html><html><body><input id="year" aria-label="Year" value="2017">
      <div>0 VEHICLES <span id="ResultFoundOnPage">No vehicles</span></div>
      <select id="ddlCity"><option selected>Chicago</option></select>
      <div id="ListingGrid"></div><span id="pageSizeHeader">100</span>
      <div id="divPagination"><button class="btn-page active">1</button><button aria-label="Next" disabled>Next</button></div>
      <script>setTimeout(()=>fetch('/Search?c=fixture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(BODY)}),50)</script>
      </body></html>'''.replace('BODY', json.dumps(body))
    with desktop_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=str(bundled_headless_chromium(root / 'desktop/runtime/browsers')))
        context = browser.new_context()
        page = context.new_page()
        calls = []
        def site(route):
            calls.append((route.request.method, route.request.url))
            route.fulfill(status=200, content_type='application/json' if route.request.method == 'POST' else 'text/html',
                          body='{}' if route.request.method == 'POST' else html)
        page.route('**/*', site)
        session = SelectionSession(tmp_path, 1, 'iaa')
        count = [0]
        class OwnedWindow:
            def __getattr__(self, name):
                return getattr(page, name)
            def wait_for_timeout(self, ms):
                page.wait_for_timeout(ms)
                count[0] += 1
                if session.draft.can_use:
                    if case == 'confirm':
                        session.stop.set()
                    else:
                        if case == 'unfinished':
                            page.get_by_label('Year').fill('2020')
                            page.wait_for_timeout(30)  # Deliver the edit binding, before a debounced search.
                        page.close()
                elif count[0] > 8:
                    page.close()
        try:
            SearchSelections.observe(session, IAASource(), OwnedWindow())
            session.persist()
            value = load_draft(tmp_path, 1, 'iaa')
            assert value['status'] == 'CLOSED'
            if case == 'unknown':
                assert value['snapshot'] is None and not value['can_use']
                assert 'must-not-be-saved' not in json.dumps(value)
            else:
                assert value['snapshot']['criteria'] == IAA
                assert value['can_use'] == (case != 'unfinished')
                if case == 'unfinished':
                    assert value['issue'] == 'LAST_CHANGE_UNCONFIRMED'
            assert [method for method, _ in calls].count('POST') == 1
        finally:
            context.close()
            browser.close()


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in bundled browser fixture')
@pytest.mark.parametrize('case', ['close', 'confirm', 'unfinished', 'denied'])
def test_copart_url_selection_saves_the_link_not_unsubmitted_controls(tmp_path, adapters, case):
    from urllib.parse import urlencode
    from app.sources.copart import CopartSource
    source = CopartSource()
    root = Path(__file__).resolve().parents[2]
    configure_playwright_environment(root / 'desktop/runtime/browsers')
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    criteria = {'query': ['Audi'], 'filter': {'YEAR': ['lot_year:[2021 TO 2027]']}}
    target = 'https://www.copart.com/lotSearchResults?' + urlencode({'searchCriteria': json.dumps(criteria)})
    html = '''<!doctype html><html><body><input aria-label="Year" value="2021">
      <table id="serverSideDataTable"><tbody><tr><td>No matching records</td></tr></tbody></table>
      <div id="serverSideDataTable_info">Showing 0 to 0 of 0 entries</div>
      <div id="serverSideDataTable_paginate"><li class="active"><a>1</a></li></div>
      </body></html>'''
    with desktop_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=str(bundled_headless_chromium(root / 'desktop/runtime/browsers')))
        context = browser.new_context()
        page = context.new_page()
        calls = []
        def site(route):
            calls.append(route.request.url)
            home = route.request.url == source.selection_entry_url
            body = '<script>setTimeout(()=>location.href=TARGET,30)</script>'.replace('TARGET', json.dumps(target)) if home else html
            route.fulfill(status=403 if case == 'denied' and not home else 200, content_type='text/html', body=body)
        page.route('**/*', site)
        session = SelectionSession(tmp_path, 1, 'copart')
        count = [0]
        class OwnedWindow:
            def __getattr__(self, name):
                return getattr(page, name)
            def wait_for_timeout(self, ms):
                page.wait_for_timeout(ms)
                count[0] += 1
                if session.draft.can_use:
                    if case == 'confirm':
                        page.locator('#searchcar-selection-panel').get_by_role('button', name='Использовать выбранные фильтры', exact=True).click()
                        return
                    if case == 'unfinished':
                        page.get_by_label('Year').fill('2020')
                        page.wait_for_timeout(30)
                        # Copart now explicitly saves the address-bar search,
                        # not a typed value absent from that public URL.
                    page.close()
                elif count[0] > 8 or case == 'unfinished' and count[0] > 3:
                    page.close()
        try:
            SearchSelections.observe(session, source, OwnedWindow())
            session.persist()
            value = load_draft(tmp_path, 1, 'copart')
            if case == 'denied':
                assert value['snapshot'] is None and not value['can_use']
            else:
                assert value['snapshot']['criteria'] == criteria
                assert value['can_use']
            assert len(calls) == 2  # No additional API or hidden endpoint calls.
        finally:
            context.close()
            browser.close()


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in bundled browser fixture')
@pytest.mark.parametrize('case', ['confirm', 'pending', 'unsupported', 'no-request', 'iframe', 'uk'])
def test_site_panel_confirms_only_completed_public_conditions(tmp_path, case):
    """Selection needs a rendered result, not auction-city/pagination metadata."""
    root = Path(__file__).resolve().parents[2]
    configure_playwright_environment(root / 'desktop/runtime/browsers')
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    body = {**IAA, **({'UnknownFilter': 'reject-me'} if case == 'unsupported' else {})}
    html = '''<!doctype html><html><body>
      <input aria-label="Year" value="2017">
      <div>1 VEHICLES <span id="ResultFoundOnPage">1</span></div>
      <div id="ListingGrid"><div class="table-row"><h4><a href="/VehicleDetail/123~US">Cadillac fixture</a></h4></div></div>
      <button id="apply">Apply Year</button>
      <script>
        function search(){fetch('/Search?c=fixture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(BODY)})}
        document.querySelector('#apply').onclick=search;
        if(SEND) setTimeout(search,50);
      </script></body></html>'''.replace('BODY', json.dumps(body)).replace('SEND', 'false' if case in ('no-request', 'iframe') else 'true')
    if case == 'iframe':
        from html import escape
        child = '<script>setTimeout(()=>fetch("/Search?c=frame",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(BODY)}),50)</script>'.replace('BODY', json.dumps(body))
        html += '<iframe srcdoc="' + escape(child, quote=True) + '"></iframe>'
    with desktop_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=str(bundled_headless_chromium(root / 'desktop/runtime/browsers')))
        context = browser.new_context(viewport={'width': 360, 'height': 740})
        page = context.new_page()
        calls = []
        page.on('request', lambda request: calls.append(request.method))
        page.route('**/*', lambda route: route.fulfill(status=200,
            content_type='application/json' if route.request.method == 'POST' else 'text/html',
            body='{}' if route.request.method == 'POST' else html))
        session = SelectionSession(tmp_path, 1, 'iaa')
        session.locale = 'uk' if case == 'uk' else 'ru'
        count = [0]
        observed = []
        class OwnedWindow:
            def __getattr__(self, name):
                return getattr(page, name)
            def wait_for_timeout(self, ms):
                page.wait_for_timeout(ms)
                count[0] += 1
                panel = page.locator('#searchcar-selection-panel')
                assert panel.count() == 1, 'Missing SearchCar panel over the site'
                button = panel.get_by_role('button', name='Використати вибрані фільтри' if case == 'uk' else 'Использовать выбранные фильтры', exact=True)
                assert button.count() == 1, 'Confirmation must stay visible even while disabled'
                assert panel.evaluate('(e)=>e.getBoundingClientRect().left>=0 && e.getBoundingClientRect().right<=innerWidth')
                assert panel.locator('section').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                if case in ('unsupported', 'no-request', 'iframe'):
                    assert button.is_disabled()
                    observed.append(panel.inner_text())
                    if count[0] >= 3:
                        page.close()
                elif button.is_enabled():
                    observed.append('ready')
                    if case == 'pending':
                        page.get_by_label('Year', exact=True).fill('2020')
                        page.wait_for_timeout(30)
                        # A panel action must not invalidate its own selection;
                        # a site edit must invalidate it before the next request.
                        return
                    toggle = panel.get_by_role('button', name='Згорнути панель' if case == 'uk' else 'Свернуть панель', exact=True)
                    toggle.click()
                    assert not button.is_visible() and session.draft.can_use
                    panel.get_by_role('button', name='Розгорнути панель' if case == 'uk' else 'Развернуть панель', exact=True).click()
                    button.focus()
                    page.screenshot(path=str(tmp_path / f'panel-{case}-360.png'))
                    page.set_viewport_size({'width': 1280, 'height': 900})
                    page.screenshot(path=str(tmp_path / f'panel-{case}-1280.png'))
                    button.click()
                elif case == 'pending' and observed:
                    observed.append('pending-disabled')
                    page.close()
                if count[0] > 8:
                    page.close()
        try:
            SearchSelections.observe(session, IAASource(), OwnedWindow())
            assert observed, 'Panel never reached an observable state'
            if case in ('confirm', 'uk'):
                assert session.draft.can_use and session.stop.is_set(), 'Panel did not confirm the ready draft'
                assert session.draft.snapshot['criteria'] == IAA
                assert session.draft.snapshot['creation_url'] is None
            elif case == 'pending':
                assert 'pending-disabled' in observed
                assert session.draft.snapshot['criteria'] == IAA and not session.draft.can_use
            else:
                assert session.draft.snapshot is None and not session.draft.can_use
            assert calls.count('POST') == (0 if case == 'no-request' else 1), 'Panel must not make a site/API request'
        finally:
            context.close()
            browser.close()
