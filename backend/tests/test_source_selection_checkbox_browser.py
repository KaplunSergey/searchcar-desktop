"""Actual checkbox events and intercepted search responses, never live auctions."""
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


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in bundled browser fixture')
@pytest.mark.parametrize('event,interaction,case', [
    ('click', 'mouse', 'apply'), ('click', 'label', 'apply'), ('click', 'keyboard', 'apply'),
    ('change', 'mouse', 'apply'), ('click', 'mouse', 'remove'),
    ('click', 'mouse', 'quick'), ('click', 'mouse', 'unfinished'),
    ('click', 'mouse', 'failed'), ('click', 'mouse', 'duplicate'),
    ('click', 'mouse', 'two-controls'),
])
def test_checkbox_application_enables_confirmation_only_after_its_search(tmp_path, event, interaction, case):
    root = Path(__file__).resolve().parents[2]
    configure_playwright_environment(root / 'desktop/runtime/browsers')
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    html = '''<!doctype html><html><body>
      <input type="checkbox" id="filter" aria-label="Run and Drive" CHECKED><label for="filter">Apply condition</label>
      <input type="checkbox" id="keys" aria-label="Keys Available">
      <div>0 VEHICLES <span id="ResultFoundOnPage">No vehicles</span></div><div id="ListingGrid"></div>
      <script>const filter=document.querySelector('#filter');
      const search=()=>{
        if(window.pauseSearch)return;
        const body=BASE;
        if(filter.checked)body.Searches.push({Facets:[{Group:'Condition',Value:'Run & Drive'}],FullSearch:null,LongRanges:null});
        if(document.querySelector('#keys').checked)body.Searches.push({Facets:[{Group:'Keys',Value:'Available'}],FullSearch:null,LongRanges:null});
        fetch('/Search?c=fixture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      }; filter.addEventListener(EVENT,search);document.querySelector('#keys').addEventListener(EVENT,search);
      </script></body></html>'''.replace('CHECKED', 'checked' if case == 'remove' else '').replace('EVENT', json.dumps(event)).replace('BASE', json.dumps(IAA))
    with desktop_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=str(bundled_headless_chromium(root / 'desktop/runtime/browsers')))
        context = browser.new_context()
        page = context.new_page()
        requests, held = [], []
        def site(route):
            if route.request.method == 'POST':
                requests.append(route.request.post_data_json)
                held.append(route)
            else:
                route.fulfill(status=200, content_type='text/html', body=html)
        page.route('**/*', site)
        session = SelectionSession(tmp_path, 1, 'iaa')
        step, ticks = ['apply'], [0]
        class OwnedWindow:
            def __getattr__(self, name):
                return getattr(page, name)
            def wait_for_timeout(self, ms):
                page.wait_for_timeout(ms)
                ticks[0] += 1
                button = page.locator('#searchcar-selection-panel').get_by_role('button', name='Использовать выбранные фильтры', exact=True)
                control = page.get_by_role('checkbox', name='Run and Drive', exact=True)
                if step[0] == 'apply':
                    assert button.is_disabled()
                    if interaction == 'label':
                        page.get_by_text('Apply condition', exact=True).click()
                    elif interaction == 'keyboard':
                        control.focus()
                        page.keyboard.press('Space')
                    elif case == 'remove':
                        control.uncheck()
                    else:
                        control.check()
                    if case == 'quick':
                        control.uncheck()
                    step[0] = 'response'
                elif step[0] == 'response' and held:
                    assert button.is_disabled() and not session.draft.can_use, 'A pending request cannot confirm filters'
                    assert len(held) == (2 if case == 'quick' else 1)
                    held.pop(0).fulfill(status=500 if case == 'failed' else 200, content_type='application/json', body='{}')
                    step[0] = 'latest-response' if case == 'quick' else 'ready'
                elif step[0] == 'latest-response':
                    assert button.is_disabled() and not session.draft.can_use, 'Old response enabled a newer pending selection'
                    held.pop().fulfill(status=200, content_type='application/json', body='{}')
                    step[0] = 'ready'
                elif step[0] == 'ready':
                    if case == 'failed':
                        assert not session.draft.can_use and session.draft.issue == 'SEARCH_REQUEST_FAILED'
                        assert button.is_disabled()
                        page.close()
                        return
                    if not button.is_enabled():
                        assert ticks[0] < 9, f'Checkbox was applied but confirmation stayed disabled: {session.draft.issue}'
                        return
                    expected = deepcopy(IAA)
                    if case not in ('remove', 'quick'):
                        expected['Searches'].append(dict(Facets=[dict(Group='Condition', Value='Run & Drive')], FullSearch=None, LongRanges=None))
                    assert session.draft.snapshot['criteria'] == expected
                    if case == 'unfinished':
                        page.evaluate('window.pauseSearch=true')
                        control.uncheck()
                        step[0] = 'unfinished'
                    elif case == 'duplicate':
                        control.evaluate("e=>{e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}")
                        step[0] = 'duplicate'
                    elif case == 'two-controls':
                        page.get_by_role('checkbox', name='Keys Available', exact=True).check()
                        step[0] = 'second-response'
                    else:
                        page.screenshot(path=str(tmp_path / 'applied-checkbox-ready.png'))
                        button.click()
                        step[0] = 'confirmed'
                elif step[0] == 'unfinished':
                    assert button.is_disabled() and not session.draft.can_use
                    assert session.draft.snapshot['criteria']['Searches'][-1]['Facets'][0]['Value'] == 'Run & Drive'
                    page.close()
                elif step[0] == 'duplicate':
                    assert button.is_enabled() and session.draft.can_use, 'Duplicate same-value events invalidated the applied request'
                    button.click()
                    step[0] = 'confirmed'
                elif step[0] == 'second-response' and held:
                    assert button.is_disabled() and not session.draft.can_use, 'A different checkbox with the same checked value was ignored'
                    held.pop().fulfill(status=200, content_type='application/json', body='{}')
                    step[0] = 'second-ready'
                elif step[0] == 'second-ready':
                    if not button.is_enabled():
                        assert ticks[0] < 12, f'Second applied checkbox stayed disabled: {session.draft.issue}'
                        return
                    assert session.draft.can_use
                    assert session.draft.snapshot['criteria']['Searches'][-1]['Facets'] == [dict(Group='Keys', Value='Available')]
                    button.click()
                    step[0] = 'confirmed'
                assert ticks[0] < 14, f'Unexpected stalled checkbox state: {step[0]}'
        try:
            SearchSelections.observe(session, IAASource(), OwnedWindow())
            assert len(requests) == (2 if case in ('quick', 'two-controls') else 1)
            if case in ('failed', 'unfinished'):
                assert not session.draft.can_use and not session.stop.is_set()
            else:
                assert step[0] == 'confirmed' and session.draft.can_use and session.stop.is_set()
        finally:
            context.close()
            browser.close()
