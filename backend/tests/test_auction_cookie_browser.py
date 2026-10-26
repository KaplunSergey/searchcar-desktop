"""Opt-in real Chrome checks on routed local fixtures, not live-site proof."""
import os
from pathlib import Path

import pytest

from app.desktop_runtime import desktop_playwright, configure_playwright_driver, configure_playwright_environment
from app.sources.registry import get_source
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


pytestmark = pytest.mark.skipif(os.environ.get('SEARCHCAR_COOKIE_BROWSER_SMOKE') != '1',
                                reason='opt-in local Chrome cookie smoke')


@pytest.fixture
def browser_runtime():
    root = Path(__file__).resolve().parents[2]
    configure_playwright_environment(root / 'desktop/runtime/browsers')
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    with desktop_playwright() as playwright:
        yield playwright


def banner(provider, *, label=None):
    if provider == 'google':
        return '<div class="fc-consent-root"><div class="fc-dialog-overlay"><button class="fc-cta-do-not-consent">' + (label or 'Do not consent') + '</button></div></div>'
    if provider == 'preferences':
        return '<div id="onetrust-pc-sdk"><button class="ot-pc-refuse-all-handler">Reject All</button></div>'
    return '<div id="onetrust-banner-sdk"><button id="onetrust-reject-all-handler">' + (label or 'Reject All') + '</button><button id="onetrust-accept-btn-handler">Accept All Cookies</button></div>'


@pytest.mark.parametrize('key', ['copart', 'iaa'])
@pytest.mark.parametrize('provider', ['google', 'onetrust', 'preferences'])
def test_normal_reject_click_and_choice_survives_owned_profile_reopen(adapters, browser_runtime, key, provider):
    source = get_source(key)
    url = 'https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D' if key == 'copart' else 'https://www.iaai.com/Search?url=x'
    html = banner(provider) + """<script>
      window.clicks=[];
      if(document.cookie.includes('fixtureChoice=necessary')) document.querySelector('div').remove();
      document.querySelectorAll('button').forEach(b=>b.onclick=()=>{
        clicks.push(b.textContent); document.cookie='fixtureChoice=necessary; Max-Age=3600; Path=/; Secure';
        document.querySelector('div').remove();
      });
    </script>"""
    for run in range(2):
        _, session, page = source.open_browser(browser_runtime, headless=True, executable_path='/bundle/headless_shell')
        try:
            session.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
            page.goto(url)
            assert bool(source.read_dom(page)['consent_required']) is (run == 0)
            assert source.reject_optional_cookies(page, checkpoint=lambda: None, attempted=set())
            assert page.evaluate('window.clicks') == ([{'google':'Do not consent'}.get(provider, 'Reject All')] if run == 0 else [])
            assert not source.read_dom(page)['consent_required']
            assert not session.interactive
        finally:
            session.close()


@pytest.mark.parametrize('case', ['outside', 'unknown', 'manage', 'accept', 'duplicate', 'hidden', 'captcha', 'retained'])
def test_unrecognized_or_unsafe_controls_are_not_chosen(adapters, browser_runtime, case):
    source = get_source('iaa')
    html = banner('onetrust', label='Accept All Cookies' if case == 'accept' else None)
    if case == 'outside':
        html = '<div id="onetrust-banner-sdk">Unknown choices</div><button id="onetrust-reject-all-handler">Reject All</button>'
    elif case == 'unknown':
        html = html.replace('onetrust-reject-all-handler', 'unknown-reject')
    elif case == 'manage':
        html = banner('google').replace('fc-cta-do-not-consent', 'fc-cta-manage-options').replace('Do not consent', 'Manage options')
        html += '<button class="fc-confirm-choices">Confirm choices</button>'
    elif case == 'duplicate':
        html += html
    elif case == 'hidden':
        html = html.replace('<button id="onetrust-reject-all-handler"', '<button style="display:none" id="onetrust-reject-all-handler"')
    elif case == 'captcha':
        html += '<p>Verify you are human</p>'
    html += '<script>window.clicks=[];document.querySelectorAll("button").forEach(b=>b.onclick=()=>clicks.push(b.textContent))</script>'
    _, session, page = source.open_browser(browser_runtime, headless=True, executable_path='/bundle/headless_shell')
    try:
        session.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
        page.goto('https://www.iaai.com/Search?url=x')
        attempted = set()
        assert not source.reject_optional_cookies(page, checkpoint=lambda: None, attempted=attempted)
        assert not source.reject_optional_cookies(page, checkpoint=lambda: None, attempted=attempted)
        assert page.evaluate('window.clicks') == (['Reject All'] if case == 'retained' else [])
    finally:
        session.close()
