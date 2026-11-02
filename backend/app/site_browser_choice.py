"""Read-only discovery; never use the customer's personal browser profile."""
import os
import plistlib
import sys
from pathlib import Path


class SiteBrowserNotInstalledError(ValueError):
    pass


def _installed_browser(channel):
    if sys.platform == 'win32':
        suffix = ('Google/Chrome/Application/chrome.exe' if channel == 'chrome'
                  else 'Microsoft/Edge/Application/msedge.exe')
        candidates = [Path(root)/suffix for name in ('LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)')
                      if (root := os.environ.get(name))]
        for path in candidates:
            if path.is_file():
                return path
        import winreg
        executable = 'chrome.exe' if channel == 'chrome' else 'msedge.exe'
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, rf'Software\Microsoft\Windows\CurrentVersion\App Paths\{executable}',
                                        0, winreg.KEY_READ | view) as key:
                        path = Path(winreg.QueryValueEx(key, '')[0].strip('"'))
                        if path.is_file():
                            return path
                except OSError:
                    continue
    elif sys.platform == 'darwin':
        app = 'Google Chrome' if channel == 'chrome' else 'Microsoft Edge'
        for root in (Path('/Applications'), Path.home()/'Applications'):
            path = root/f'{app}.app'/'Contents'/'MacOS'/app
            if path.is_file():
                return path
    return None


def _default_browser():
    if sys.platform == 'win32':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice') as key:
                name = winreg.QueryValueEx(key, 'ProgId')[0].lower()
            return 'msedge' if name == 'msedgehtm' else 'chrome' if name.startswith('chromehtml') else None
        except OSError:
            return None
    if sys.platform == 'darwin':
        try:
            with (Path.home()/'Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist').open('rb') as stream:
                handlers = plistlib.load(stream).get('LSHandlers', [])
            for handler in handlers:
                if handler.get('LSHandlerURLScheme') == 'https':
                    return {'com.google.chrome': 'chrome', 'com.microsoft.edgemac': 'msedge'}.get(
                        handler.get('LSHandlerRoleAll', '').lower())
        except (OSError, ValueError, plistlib.InvalidFileException):
            pass
    return None


def site_browser_options():
    override = os.environ.get('SEARCHCAR_SITE_BROWSER_CHANNEL')
    if override is not None:
        if override not in ('chrome', 'msedge'):
            raise ValueError('Choose the installed Chrome or Edge browser')
        return override, {'channel': override}
    default = _default_browser()
    candidates = list(dict.fromkeys([default, 'chrome', 'msedge']))
    for channel in candidates:
        if channel in ('chrome', 'msedge') and (path := _installed_browser(channel)):
            return channel, {'executable_path': str(path)}
    raise SiteBrowserNotInstalledError('Install Google Chrome to select filters and search auction sites')
