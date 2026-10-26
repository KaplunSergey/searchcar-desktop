"""Real routed Chromium lifecycle and IAA guard after a manual-window transition."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.desktop_runtime import (desktop_playwright, configure_playwright_environment,
    configure_playwright_driver, bundled_headless_chromium)
from app.sources.registry import get_source
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue
from test_source_search_selection import IAA
from test_iaa_search_restore import selected, URL


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in bundled browser fixture')
@pytest.mark.parametrize('changed', [False, True])
def test_iaa_native_guard_uses_the_same_verified_page_after_minimization(tmp_path, adapters, changed):
    source = get_source('iaa')
    config = selected(source)
    criteria = deepcopy(IAA)
    if changed: criteria['Searches'][0]['Facets'][0]['Value'] = 'Honda'
    html = '''<!doctype html><html><body>
      <div><span id="ResultFoundOnPage">0 VEHICLES No vehicles</span></div><div id="ListingGrid"></div>
      <select id="ddlCity"><option selected>Chicago</option></select><span id="pageSizeHeader">100</span>
      <div id="divPagination"><button id="PageNumber1" class="btn-page active">1</button></div>
      <script type="application/json" id="SortFields">SORT</script><script type="application/json" id="Sorts">[]</script>
      <a id="size" data-bind="click: $root.UpdatePageSize">100</a>
      <script>function search(){fetch('/Search?c=fixture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(CRITERIA)})}
      document.querySelector('#PageNumber1').onclick=search;document.querySelector('#size').onclick=search;</script>
      </body></html>'''.replace('CRITERIA', json.dumps(criteria)).replace('SORT', json.dumps(IAA['Sort']))
    root = Path(__file__).resolve().parents[2]
    configure_playwright_environment(root / 'desktop/runtime/browsers')
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    launches, requests = [], []
    with desktop_playwright() as pw:
        def launch(profile, **options):
            index = len(launches)
            launches.append((profile, options['headless']))
            # Native-window acceptance is separate from routed headless coverage.
            native = os.environ.get('RUN_SOURCE_NATIVE_WINDOW') == '1'
            context = pw.chromium.launch_persistent_context(profile, **(options if native else {**options, 'headless': True}))
            def site(route):
                if route.request.method == 'POST': requests.append((index, route.request.post_data_json))
                body = '<p>Verify you are human</p>' if index == 0 else html
                route.fulfill(content_type='text/html', body=body if route.request.method == 'GET' else 'ok')
            context.route('**/*', site)
            return context
        chromium = SimpleNamespace(launch_persistent_context=launch)
        _, session, _ = source.open_browser(SimpleNamespace(chromium=chromium), headless=True,
            executable_path=str(bundled_headless_chromium(root / 'desktop/runtime/browsers')))
        session.request_interval_seconds = 0
        progress = []
        try:
            if changed:
                from app.sources.contracts import ScanError
                with pytest.raises(ScanError) as caught:
                    source.collect_search_pages(session, URL, search_config=config, progress=progress.append)
                assert caught.value.code == 'SOURCE_SEARCH_SCOPE_CHANGED'
                assert not caught.value.partial_collection.rows
            else:
                result = source.collect_search_pages(session, URL, search_config=config, progress=progress.append)
                assert result.total_results == 0 and result.pages_visited == 1
            assert [headless for _, headless in launches] == [True, False]
            assert len({profile for profile, _ in launches}) == 1
            assert requests and all(index == 1 for index, _ in requests)
            assert all(body == criteria for _, body in requests)
            assert not session.listeners
            if os.environ.get('RUN_SOURCE_NATIVE_WINDOW') == '1' and not changed:
                assert progress[-1]['complete']
                if session.interactive:
                    assert progress[-1]['browser_notice'] == 'MANUAL_MINIMIZE'
        finally:
            session.close()
