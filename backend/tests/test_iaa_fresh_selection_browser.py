"""Routed IAA fixture with a previous search retained in localStorage."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from app.desktop_runtime import (desktop_playwright, configure_playwright_environment,
    configure_playwright_driver, bundled_headless_chromium)
from app.source_search_selection import SearchSelections, SelectionSession
from app.sources.iaa import IAASource
from test_source_search_selection import IAA
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in routed browser')
@pytest.mark.parametrize('case', ['new', 'resume', 'reset-failed', 'after-verification', 'server-rendered', 'edit-legacy', 'edit-preset'])
def test_fresh_iaa_selection_uses_native_clear_without_erasing_other_browser_state(tmp_path, adapters, case):
    root = Path(__file__).resolve().parents[2]
    configure_playwright_environment(root / 'desktop/runtime/browsers')
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    fresh = deepcopy(IAA)
    fresh['Searches'] = [{'Facets': [{'Group': 'Default', 'Value': 'True'}], 'FullSearch': None, 'LongRanges': None}]
    html = '''<a href="#" id="clear">Clear All Filters</a>
      <div>0 VEHICLES <span id="ResultFoundOnPage">No vehicles</span></div>
      <div id="ListingGrid"></div><div id="ResultHeaderContainer"><input name="RequestUri" value="fixture"></div>
      <script>
      localStorage.setItem('unrelated-preference','keep');
      localStorage.setItem('filters',JSON.stringify(OLD));
      function search(body){return fetch('/Search?c=fixture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}
      document.querySelector('#clear').onclick=()=>{localStorage.setItem('filters',JSON.stringify(FRESH));search(FRESH)};
      if(SEND_INITIAL) setTimeout(()=>search(OLD),20);
      </script>'''.replace('OLD', json.dumps(IAA)).replace('FRESH', json.dumps(fresh)).replace('SEND_INITIAL', 'false' if case == 'server-rendered' else 'true')
    source = IAASource()
    session = SelectionSession(tmp_path, 1, 'iaa', draft_id='a' * 32)
    if case.startswith('edit-'):
        session.project_id = 9
        session.entry_url = 'https://www.iaai.com/Search?url=storedProject'
    if case == 'edit-preset':
        session.draft.snapshot = source.complete_selection_snapshot(source.capture_search_request(source.selection_entry_url, 'POST', IAA), {'native_search_uri': 'storedProject'})
    if case == 'resume':
        session.draft.snapshot = source.capture_search_request(source.selection_entry_url, 'POST', IAA)
    with desktop_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=False) if os.environ.get('RUN_SOURCE_NATIVE_WINDOW') == '1' else pw.chromium.launch(headless=True, executable_path=str(bundled_headless_chromium(root / 'desktop/runtime/browsers')))
        page = browser.new_page()
        requests = []
        def site(route):
            body = route.request.post_data_json if route.request.method == 'POST' else None
            if body is not None: requests.append(body)
            route.fulfill(status=500 if case == 'reset-failed' and body == fresh else 200,
                          content_type='text/html', body='ok' if body is not None else html)
        page.route('**/*', site)
        polls = [0]
        class OwnedWindow:
            def __getattr__(self, name): return getattr(page, name)
            def wait_for_timeout(self, ms):
                page.wait_for_timeout(ms)
                polls[0] += 1
                if polls[0] >= 5: session.stop.set()
        original_read = source.read_selection_dom
        def read(actual):
            state = original_read(actual)
            if case == 'after-verification' and polls[0] < 2:
                return {**state, 'manual_required': True, 'ready': False}
            return state
        source.read_selection_dom = read
        try:
            SearchSelections.observe(session, source, OwnedWindow())
            assert requests[0] == (fresh if case == 'server-rendered' else IAA)
            assert len(requests) == (1 if case in ('resume', 'server-rendered', 'edit-legacy', 'edit-preset') else 2)
            assert session.draft.can_use == (case != 'reset-failed')
            if case == 'reset-failed':
                assert session.draft.snapshot is None
            else:
                assert session.draft.snapshot['criteria'] == (IAA if case in ('resume', 'edit-legacy', 'edit-preset') else fresh)
            if case.startswith('edit-'):
                assert page.url == session.entry_url, 'Editing must open its own saved search, not the general catalog'
            assert page.evaluate("localStorage.getItem('unrelated-preference')") == 'keep'
        finally:
            browser.close()
