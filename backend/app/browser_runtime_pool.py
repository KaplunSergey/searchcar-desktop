"""Bounded, worker-thread-owned browser reuse; never share Playwright across threads."""
from contextlib import AbstractContextManager
from dataclasses import dataclass
import logging
from time import monotonic

from playwright.sync_api import Error as BrowserError


@dataclass
class _Runtime:
    handles: tuple
    idle_since: float | None = None
    keep_windowed: bool = True
    keep_hidden: bool = False


class BrowserRuntimePool(AbstractContextManager):
    def __init__(self, driver_factory, *, idle_seconds=600, max_idle=2):
        self.driver_factory = driver_factory
        self.idle_seconds, self.max_idle = idle_seconds, max_idle
        self._driver_context = self._driver = None
        self._runtimes = {}

    def driver(self):
        if self._driver is None:
            context = self.driver_factory()
            self._driver = context.__enter__()
            self._driver_context = context
        return self._driver

    def acquire(self, owner_id, source, **options):
        key = (owner_id, source.descriptor.key, tuple(sorted(options.items())))
        for other in list(self._runtimes):
            if other[0] != owner_id or (other[:2] == key[:2] and other != key):
                self._close(other)
        self.reap()
        item = self._runtimes.get(key)
        if item is not None:
            try:
                closed = item.handles[2].is_closed()
            except BrowserError:
                closed = True
            if closed:
                self._close(key)
                item = None
        if item is None:
            item = _Runtime(source.open_browser(self.driver(), **options),
                            keep_windowed=getattr(source, 'reuse_windowed_browser_session', True),
                            keep_hidden=getattr(source, 'reuse_hidden_browser_session', False))
            self._runtimes[key] = item
        item.idle_since = None
        return item.handles

    def release(self, context):
        for key, item in list(self._runtimes.items()):
            if item.handles[1] is context:
                try:
                    if not item.keep_windowed and context.has_window:
                        if not item.keep_hidden or not context.park_hidden() or context.has_window:
                            self._close(key)
                            break
                    context.park()
                except BrowserError:
                    self._close(key)
                    break
                item.idle_since = monotonic()
                break
        self.reap()

    def reap(self):
        idle = sorted(((key, item) for key, item in self._runtimes.items()
                       if item.idle_since is not None), key=lambda pair: pair[1].idle_since)
        excess = max(0, len(idle) - self.max_idle)
        for index, (key, item) in enumerate(idle):
            context = item.handles[1]
            if (index < excess or monotonic() - item.idle_since >= self.idle_seconds
                    or not context.reuse_requested()):
                self._close(key)

    def close_idle(self):
        for key, item in list(self._runtimes.items()):
            if item.idle_since is not None:
                self._close(key)

    def _close(self, key):
        browser, context, _ = self._runtimes.pop(key).handles
        # Cleanup failures must not leave the other site's profile leased.
        for handle in (context, browser):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    logging.getLogger(__name__).exception('Could not close an idle site browser')

    def __exit__(self, *exc):
        try:
            for key in list(self._runtimes):
                self._close(key)
        finally:
            if self._driver_context is not None:
                self._driver_context.__exit__(*exc)
                self._driver_context = self._driver = None
        return False
