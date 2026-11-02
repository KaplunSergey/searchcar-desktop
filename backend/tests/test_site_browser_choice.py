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
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(HKEY_CURRENT_USER=1,
        OpenKey=open_key, QueryValueEx=lambda key, name: (progid, 1)))
    assert choice._default_browser() == expected
    assert reads == [r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice']


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
