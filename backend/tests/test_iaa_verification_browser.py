"""Real DOM classification; all frames and documents are local fixtures."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.desktop_runtime import desktop_playwright, configure_playwright_driver
from app.sources.iaa import IAASource
from app.sources.contracts import ScanError
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


@pytest.fixture
def verification_page():
    if os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1':
        pytest.skip('Opt-in installed Chrome fixture')
    configure_playwright_driver(Path(__file__).resolve().parents[2] / 'desktop/runtime/playwright-driver')
    with desktop_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        page.route('**/*', lambda route: route.fulfill(content_type='text/html', body='Fixture frame'))
        try:
            yield page
        finally:
            browser.close()


EMPTY_GRID = '''<div>0 VEHICLES <span id="ResultFoundOnPage">No vehicles</span></div>
  <div id="ListingGrid"></div>'''


@pytest.mark.parametrize('style,wrapper', [
    ('display:none', ''), ('visibility:hidden', ''), ('opacity:0', ''),
    ('width:1px;height:1px;border:0', ''), ('width:1px;height:1px', ''),
    ('position:absolute;left:-9999px', ''),
    ('', 'display:none'), ('', 'opacity:0'),
])
@pytest.mark.parametrize('grid', [False, True])
def test_inert_security_frame_is_not_a_request_for_manual_verification(verification_page, style, wrapper, grid):
    page = verification_page
    page.set_content((EMPTY_GRID if grid else '<p>Loading vehicles</p>') +
        f'<div style="{wrapper}"><iframe style="{style}" src="https://www.iaai.com/_Incapsula_Resource?fixture=1"></iframe></div>')
    state = IAASource().read_dom(page)
    assert state['manual_required'] is False
    assert state['blocked'] is False
    assert state['ready'] is grid
    assert IAASource().read_selection_dom(page)['ready'] is grid


@pytest.mark.parametrize('path', ['_Incapsula_Resource', 'captcha/challenge'])
@pytest.mark.parametrize('grid', [False, True])
def test_visible_challenge_frame_blocks_even_an_existing_grid(verification_page, path, grid):
    verification_page.set_content((EMPTY_GRID if grid else '') +
        f'<iframe width="320" height="200" src="https://www.iaai.com/{path}"></iframe>')
    state = IAASource().read_dom(verification_page)
    assert state['manual_required'] is True
    assert state['blocked'] is True


@pytest.mark.parametrize('text,manual,blocked', [
    ('Verify you are human', True, True),
    ('Please verify that you are a human', True, True),
    ('Complete the CAPTCHA to continue', True, True),
    ('Complete this security check', True, True),
    ('Security check', False, True),
    ('Access Denied', False, True),
    ('Pardon Our Interruption', False, True),
    ('Request unsuccessful', False, True),
    ('Loading vehicles. Protected by reCAPTCHA.', False, False),
])
def test_manual_instructions_are_distinct_from_denial_or_attribution(verification_page, text, manual, blocked):
    verification_page.set_content('<p>' + text + '</p>')
    state = IAASource().read_dom(verification_page)
    assert state['manual_required'] is manual
    assert state['blocked'] is blocked
    assert state['ready'] is False


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in installed Chrome fixture')
@pytest.mark.parametrize('settles', [False, True])
def test_service_frame_waits_in_background_and_never_becomes_empty_success(adapters, settles):
    configure_playwright_driver(Path(__file__).resolve().parents[2] / 'desktop/runtime/playwright-driver')
    source = IAASource()
    source.silent_verification_seconds = 0.1
    source.read_timeout_seconds = 2 if settles else 0.5
    grid = '''<div>1 VEHICLES <span id="ResultFoundOnPage">1</span></div>
      <select id="ddlCity"><option selected>Chicago</option></select><span id="pageSizeHeader">1</span>
      <div id="ListingGrid"><div class="table-row"><h4><a href="/VehicleDetail/10001~US">Fixture car</a></h4>
      <span class="data-list__value--action">Auction Not Assigned</span></div></div>
      <div id="divPagination"><button class="btn-page active">1</button><button aria-label="Next" disabled>Next</button></div>'''
    body = '<p>Loading vehicles</p><iframe style="display:none" src="/_Incapsula_Resource"></iframe>'
    if settles:
        body += '<script>setTimeout(()=>{document.body.innerHTML=' + json.dumps(grid) + '},800)</script>'
    launches, interactions = [], []
    with desktop_playwright() as pw:
        def launch(profile, **options):
            launches.append(options['headless'])
            assert options['headless'], 'An inert service frame must never show a browser'
            context = pw.chromium.launch_persistent_context(profile, **options)
            context.route('**/*', lambda route: route.fulfill(content_type='text/html',
                body=body if '/Search?' in route.request.url else 'Fixture frame'))
            return context
        _, session, _ = source.open_browser(SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch)),
                                            headless=True, executable_path='/bundled/headless_shell')
        try:
            if settles:
                result = source.collect_search_pages(session, 'https://www.iaai.com/Search?url=fixture',
                    progress=lambda info: interactions.append(info.get('interaction')))
                assert set(result.rows) == {'10001~US'} and result.pages_visited == 1
            else:
                with pytest.raises(ScanError) as caught:
                    source.collect_search_pages(session, 'https://www.iaai.com/Search?url=fixture',
                        progress=lambda info: interactions.append(info.get('interaction')))
                assert caught.value.code == 'SEARCH_PAGE_NOT_READY'
                assert not caught.value.partial_collection.rows
            assert launches == [True]
            assert not any(interactions)
        finally:
            session.close()
