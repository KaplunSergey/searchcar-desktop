from pathlib import Path, PurePosixPath, PureWindowsPath
import sys
from types import SimpleNamespace

import pytest

from app import site_browser_choice as choice
from app.sources.auction_lists import AuctionBrowserSession


@pytest.mark.parametrize('chrome', [False, True])
@pytest.mark.parametrize('edge', [False, True])
@pytest.mark.parametrize('default', ['chrome', 'msedge', 'firefox', None])
@pytest.mark.parametrize('path_type', [PurePosixPath, PureWindowsPath])
def test_supported_default_then_chrome_then_edge(monkeypatch, chrome, default, edge, path_type):
    monkeypatch.setattr(choice.sys, 'platform', 'win32')
    monkeypatch.delenv('SEARCHCAR_SITE_BROWSER_CHANNEL', raising=False)
    monkeypatch.setattr(choice, '_installed_browser', lambda channel: path_type('/installed/'+channel) if
                        (chrome if channel == 'chrome' else edge) else None)
    monkeypatch.setattr(choice, '_default_browser', lambda: default)
    installed = {'chrome': chrome, 'msedge': edge}
    expected = default if installed.get(default) else 'chrome' if chrome else 'msedge' if edge else None
    if expected is None:
        with pytest.raises(ValueError, match='Install Google Chrome'):
            choice.site_browser_options()
    else:
        key, options = choice.site_browser_options()
        assert key == expected
        assert options == {'executable_path': str(path_type('/installed/'+expected))}


def test_explicit_engineering_override_is_validated(monkeypatch):
    monkeypatch.setenv('SEARCHCAR_SITE_BROWSER_CHANNEL', 'msedge')
    assert choice.site_browser_options() == ('msedge', {'channel': 'msedge'})
    monkeypatch.setenv('SEARCHCAR_SITE_BROWSER_CHANNEL', 'arbitrary-command')
    with pytest.raises(ValueError):
        choice.site_browser_options()


def test_windows_detects_per_user_chrome_without_launching_it(monkeypatch, tmp_path):
    monkeypatch.setattr(choice.sys, 'platform', 'win32')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    chrome = tmp_path/'Google'/'Chrome'/'Application'/'chrome.exe'
    chrome.parent.mkdir(parents=True)
    chrome.touch()
    assert choice._installed_browser('chrome') == chrome


@pytest.mark.parametrize('progid,expected', [('MSEdgeHTM', 'msedge'), ('ChromeHTML.123', 'chrome'),
                                          ('FirefoxURL-123', None), (None, None)])
def test_windows_reads_https_default_without_changing_associations(monkeypatch, progid, expected):
    from contextlib import contextmanager
    reads = []
    @contextmanager
    def open_key(hive, key):
        reads.append(key)
        if progid is None:
            raise FileNotFoundError()
        yield object()
    monkeypatch.setattr(choice.sys, 'platform', 'win32')
    monkeypatch.setattr(choice, '_windows_https_progid', lambda: None, raising=False)
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(HKEY_CURRENT_USER=1,
        OpenKey=open_key, QueryValueEx=lambda key, name: (progid, 1)))
    assert choice._default_browser() == expected
    assert reads[0].endswith(r'https\UserChoiceLatest')


@pytest.mark.parametrize('progid,expected', [('ChromeHTML', 'chrome'), ('MSEdgeHTM', 'msedge'),
                                          ('FirefoxURL-123', None)])
def test_windows_effective_association_wins_over_stale_registry(monkeypatch, progid, expected):
    monkeypatch.setattr(choice.sys, 'platform', 'win32')
    monkeypatch.setattr(choice, '_windows_https_progid', lambda: progid, raising=False)
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace())  # No registry read on API success.
    assert choice._default_browser() == expected


def test_windows_latest_choice_wins_when_native_query_unavailable(monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def open_key(hive, key):
        yield key
    monkeypatch.setattr(choice.sys, 'platform', 'win32')
    monkeypatch.setattr(choice, '_windows_https_progid', lambda: None, raising=False)
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=open_key,
        QueryValueEx=lambda key, name: ('ChromeHTML' if key.endswith('UserChoiceLatest') else 'MSEdgeHTM', 1)))
    assert choice._default_browser() == 'chrome'


@pytest.mark.parametrize('size,status,expected', [(11, 0, 'ChromeHTML'), (11, -1, None),
                                               (0, 0, None), (5000, 0, None)])
def test_windows_native_association_query_is_bounded_and_checks_hresult(monkeypatch, size, status, expected):
    import ctypes
    calls = []
    def query(flags, field, protocol, verb, output, length):
        calls.append(output is not None)
        assert (flags, field, protocol, verb) == (0x1000, 20, 'https', None)
        if output is None:
            length._obj.value = size
            return 1  # S_FALSE: required length, including terminator.
        output.value = 'ChromeHTML'
        return status
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name: SimpleNamespace(AssocQueryStringW=query), raising=False)
    assert choice._windows_https_progid() == expected
    assert calls == ([False, True] if 1 < size <= 4096 else [False])


def test_windows_detects_registered_custom_install_and_ignores_missing_files(monkeypatch, tmp_path):
    from contextlib import contextmanager
    monkeypatch.setattr(choice.sys, 'platform', 'win32')
    for key in ('LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)'):
        monkeypatch.delenv(key, raising=False)
    exe = tmp_path/'custom chrome.exe'
    @contextmanager
    def open_key(*args):
        yield object()
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(HKEY_CURRENT_USER=1, HKEY_LOCAL_MACHINE=2,
        KEY_WOW64_64KEY=256, KEY_WOW64_32KEY=512, KEY_READ=1, OpenKey=open_key,
        QueryValueEx=lambda *args: ('"'+str(exe)+'"', 1)))
    assert choice._installed_browser('chrome') is None
    exe.touch()
    assert choice._installed_browser('chrome') == exe


def test_mac_chrome_in_user_applications_is_detected(monkeypatch, tmp_path):
    monkeypatch.setattr(choice.sys, 'platform', 'darwin')
    monkeypatch.setattr(choice.Path, 'home', lambda: tmp_path)
    chrome = tmp_path/'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    chrome.parent.mkdir(parents=True)
    chrome.touch()
    monkeypatch.setattr(choice.Path, 'is_file', lambda path: path == chrome)
    assert choice._installed_browser('chrome') == chrome


def test_repeated_show_of_same_visible_page_does_not_reload_or_restart():
    calls = []
    page = SimpleNamespace(url='https://www.copart.com/lotSearchResults?x=1',
        bring_to_front=lambda: calls.append('focus'))
    session = object.__new__(AuctionBrowserSession)
    session.page, session.interactive, session._hidden_cdp, session._minimized = page, True, None, False
    session.next_request_at = 0
    session._launch = lambda **kwargs: pytest.fail('must retain the existing visible browser')
    assert session.show(page.url, lambda: calls.append('checkpoint')) is False
    assert calls == ['checkpoint', 'focus']


def test_windowed_background_minimize_failure_keeps_visible_context_and_reports_manual_action(tmp_path):
    from playwright.sync_api import Error as BrowserError
    calls = []
    def unavailable(page):
        raise BrowserError('minimize unsupported')
    context = SimpleNamespace(pages=[SimpleNamespace()], new_cdp_session=unavailable, close=lambda:None)
    chromium = SimpleNamespace(launch_persistent_context=lambda *args, **kwargs:
        (calls.append(kwargs) or context))
    session = AuctionBrowserSession(chromium, tmp_path, {}, windowed_background=True)
    try:
        assert session.interactive and session.has_window and not session._minimized
        assert session.background() == 'MANUAL_MINIMIZE'
        assert len(calls) == 1 and calls[0]['headless'] is False
    finally:
        session.close()
