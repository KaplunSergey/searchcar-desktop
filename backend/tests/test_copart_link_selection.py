"""Copart selection saves the public address, not IAA-style request bodies."""
import json
import os
from pathlib import Path
from urllib.parse import urlencode

import pytest

from app.desktop_runtime import (desktop_playwright, configure_playwright_environment,
    configure_playwright_driver, bundled_headless_chromium)
from app.source_search_selection import SearchSelections, SelectionSession
from app.sources.copart import CopartSource
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


pytestmark = pytest.mark.skipif(os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1',
                              reason='Opt-in bundled browser fixture')


@pytest.mark.parametrize('case', ['search', 'edit', 'new-link', 'native-link', 'optional-failure', 'failed-document',
                                 'captcha', 'consent', 'missing-filters', 'unsupported-link', 'close'])
def test_only_a_valid_loaded_copart_search_link_can_be_used(tmp_path, adapters, case):
    source = CopartSource()
    criteria = {'query': ['*'], 'filter': {'YEAR': ['lot_year:[2025 TO 2027]'], 'MODL': ['Durango']}}
    if case == 'native-link': criteria['vanityKeyword'] = False
    if case == 'unsupported-link': criteria['unknownPrivateField'] = 'must-not-be-saved'
    target = 'https://www.copart.com/lotSearchResults?' + urlencode({'searchCriteria': json.dumps(criteria)})
    if case == 'missing-filters': target = 'https://www.copart.com/lotSearchResults'
    html = '''<!doctype html><html><body><h1>Search results</h1>
      <input aria-label="Year" value="2025"><button id="optional">Optional data</button>
      <script>document.querySelector('#optional').onclick=()=>fetch('/optional-data').catch(()=>{})</script>
      </body></html>'''
    if case == 'captcha': html += '<p>Verify you are human</p>'
    if case == 'consent': html += '<div id="onetrust-banner-sdk">Choose privacy settings</div>'
    root = Path(__file__).resolve().parents[2]
    configure_playwright_environment(root / 'desktop/runtime/browsers')
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    with desktop_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=str(bundled_headless_chromium(root / 'desktop/runtime/browsers')))
        page = browser.new_page()
        requests, ticks, edited, observed = [], [0], [False], []
        def site(route):
            requests.append(route.request.url)
            if route.request.url == source.selection_entry_url:
                route.fulfill(content_type='text/html', body='<script>location.href=' + json.dumps(target) + '</script>')
            else:
                route.fulfill(status=403 if case == 'optional-failure' and '/optional-data' in route.request.url
                              else 500 if case == 'failed-document' else 200,
                              content_type='text/html', body=html)
        page.route('**/*', site)
        session = SelectionSession(tmp_path, 1, 'copart')
        class OwnedWindow:
            def __getattr__(self, name): return getattr(page, name)
            def wait_for_timeout(self, ms):
                page.wait_for_timeout(ms)
                ticks[0] += 1
                if '/lotSearchResults' not in page.url: return
                if ticks[0] >= 8:
                    page.close()
                    return
                if not edited[0]:
                    edited[0] = True
                    if case == 'edit': page.get_by_label('Year').fill('2026')
                    if case == 'optional-failure': page.locator('#optional').click()
                    if case == 'new-link':
                        page.evaluate('(url)=>history.pushState({},"",url)', target.replace('2025', '2024'))
                    return
                if session.draft.can_use:
                    observed.append(session.draft.public())
                    if case == 'close': page.close()
                    else:
                        panel = page.locator('#searchcar-selection-panel')
                        assert 'адресной строки' in panel.locator('section').inner_text()
                        panel.get_by_role('button', name='Использовать выбранные фильтры', exact=True).click()
        try:
            SearchSelections.observe(session, source, OwnedWindow())
            expected = case not in ('failed-document', 'captcha', 'consent', 'missing-filters', 'unsupported-link')
            assert session.draft.can_use is expected
            if expected:
                assert observed
                snapshot = session.draft.snapshot
                assert snapshot['criteria']['filter']['YEAR'] == [
                    'lot_year:[2024 TO 2027]' if case == 'new-link' else 'lot_year:[2025 TO 2027]']
                assert snapshot['creation_url'] == snapshot['entry_url']
                assert source.capture_search_url(snapshot['creation_url']) == snapshot
                if case == 'native-link': assert snapshot['criteria']['vanityKeyword'] is False
            if case == 'unsupported-link':
                assert session.draft.issue == 'SEARCH_FILTERS_UNSUPPORTED'
                assert 'must-not-be-saved' not in json.dumps(session.draft.public())
            assert len(requests) == (3 if case == 'optional-failure' else 2)
        finally:
            browser.close()
