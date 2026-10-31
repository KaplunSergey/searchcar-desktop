"""Shared mechanics for the two opt-in, public-list-only auction adapters."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from time import monotonic
from threading import Event, Lock
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import Error as BrowserError

from ..auction_time import normalize_schedule, _timezone
from ..external_links import validated_source_image_url
from .contracts import InvalidSourceUrlError, ListingReference, ScanError, SearchCollection


_BROWSER_LOCKS = {}
_BROWSER_LOCKS_GUARD = Lock()


class _ProfileLease:
    def __init__(self):
        self.lock, self.yield_requested = Lock(), Event()
        self.yield_until = 0

    def acquire(self, *, interactive):
        if not interactive and monotonic() < self.yield_until:
            return False
        if self.lock.acquire(blocking=False):
            self.yield_requested.clear()
            self.yield_until = 0
            return True
        if interactive:
            self.yield_requested.set()
            self.yield_until = monotonic() + 1
        return False

    def release(self):
        self.lock.release()


class AuctionListError(ScanError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class AuctionBrowserSession:
    """Stable page/cleanup handle while an owned browser changes visibility.

    Persistent contexts own their browser. The worker closes this handle, not
    a stale context captured before the manual fallback.
    """
    request_interval_seconds = 3

    def __init__(self, chromium, profile, options, *, interactive=False, lease=None):
        self.chromium, self.profile, self.options = chromium, str(profile), options
        self.context = self.page = None
        self.interactive = False
        self._minimized = False
        self._hidden_cdp = None
        self.listeners = []
        self.next_request_at = 0
        self._lease = lease
        self._launch(interactive=interactive)

    def _launch(self, *, interactive):
        if self.page is not None:
            for event, callback in self.listeners:
                self.page.remove_listener(event, callback)
        self._close_context()
        try:
            self.context = self.chromium.launch_persistent_context(
                self.profile, headless=not interactive, **self.options,
                locale='en-US', timezone_id='UTC',
                **({'no_viewport': True} if interactive else {'viewport': {'width': 1280, 'height': 800}}),
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.interactive = interactive
            self._minimized = False
            for event, callback in self.listeners:
                self.page.on(event, callback)
        except BaseException as exc:
            self.close()
            if not isinstance(exc, Exception):
                raise
            code = 'SOURCE_VISIBLE_BROWSER_UNAVAILABLE' if interactive else 'SOURCE_BROWSER_UNAVAILABLE'
            raise AuctionListError(code, 'Could not start the site browser. Install Chrome/Edge or close the previous SearchCar site window') from exc

    def background(self):
        if self.interactive:
            try:
                self.hide_verified()
            except AuctionListError as exc:
                if exc.code != 'SOURCE_BACKGROUND_BROWSER_UNAVAILABLE':
                    raise
                return 'MANUAL_MINIMIZE'

    @property
    def has_window(self):
        return self.interactive or self._minimized

    def reuse_requested(self):
        if self._lease is None or self._lease.yield_requested.is_set() or self.page is None:
            return False
        try:
            self.page.wait_for_timeout(1)  # Dispatch a user-closed window before the next job.
            return not self.page.is_closed()
        except BrowserError:
            return False

    def park(self):
        # Stop the site's live polling while idle, retaining this owned context.
        self.page.goto('about:blank', wait_until='commit', timeout=5000)

    def _replace_page(self, page):
        for event, callback in self.listeners:
            self.page.remove_listener(event, callback)
            page.on(event, callback)
        self.page = page

    def park_hidden(self):
        """Close the owned UI, retaining the verified browser/context, not cookies copied elsewhere."""
        if self.context.browser is None:
            return False
        cdp = self.context.browser.new_browser_cdp_session()
        old_pages = list(self.context.pages)
        try:
            url = 'about:blank#searchcar-idle'
            with self.context.expect_page(predicate=lambda page: page.url == url, timeout=2000) as opened:
                cdp.send('Target.createTarget', {'url': url, 'hidden': True, 'background': True})
            hidden = opened.value
            hidden.set_viewport_size({'width': 1280, 'height': 800})
            self._replace_page(hidden)
            for page in old_pages:
                page.close(run_before_unload=False)
            if any(page is not hidden for page in self.context.pages):
                raise BrowserError('Unexpected site page remains visible')
            self._hidden_cdp = cdp  # Hidden target lifetime is bound to this CDP session.
            self.interactive, self._minimized = False, False
            return True
        except BaseException:
            try:
                cdp.detach()
            except BrowserError:
                pass
            raise

    def _window_state(self, state):
        cdp = self.context.new_cdp_session(self.page)
        try:
            window = cdp.send('Browser.getWindowForTarget')
            cdp.send('Browser.setWindowBounds', {'windowId': window['windowId'], 'bounds': {'windowState': state}})
            for _ in range(20):
                bounds = cdp.send('Browser.getWindowBounds', {'windowId': window['windowId']})['bounds']
                if bounds['windowState'] == state:
                    return
                self.page.wait_for_timeout(50)
            raise BrowserError('Site window did not change state')
        finally:
            cdp.detach()

    def hide_verified(self):
        # Keep the verified page/context; a headless relaunch can challenge it again.
        try:
            self._window_state('minimized')
        except BrowserError as exc:
            raise AuctionListError('SOURCE_BACKGROUND_BROWSER_UNAVAILABLE', 'Could not minimize the verified site window; search stopped') from exc
        self.interactive, self._minimized = False, True

    def wait_for_request(self, checkpoint):
        # Deterministic load reduction, not human imitation. DOM polling does
        # not navigate; only searches/page turns share this start interval.
        while True:
            checkpoint()
            remaining = self.next_request_at - monotonic()
            if remaining <= 0:
                break
            self.page.wait_for_timeout(min(250, remaining * 1000))
        self.next_request_at = monotonic() + self.request_interval_seconds

    def show(self, url, checkpoint):
        if self._hidden_cdp is not None:
            self.wait_for_request(checkpoint)
            hidden, cdp = self.page, self._hidden_cdp
            self._replace_page(self.context.new_page())
            self._hidden_cdp = None
            self.interactive, self._minimized = True, False
            try:
                hidden.close()
            finally:
                cdp.detach()
            checkpoint()
            self.page.goto(url, wait_until='domcontentloaded', timeout=20000)
            return True  # New page: a challenge after partial reading still needs a restart.
        if self._minimized:
            checkpoint()
            self._window_state('normal')
            self.page.bring_to_front()
            self.interactive, self._minimized = True, False
            return False
        self.wait_for_request(checkpoint)
        self._launch(interactive=True)
        checkpoint()
        self.page.goto(url, wait_until='domcontentloaded', timeout=20000)
        return True

    def on(self, event, callback):
        self.listeners.append((event, callback))
        self.page.on(event, callback)

    def remove_listener(self, event, callback):
        self.listeners.remove((event, callback))
        if self.page is not None:
            self.page.remove_listener(event, callback)

    def _close_context(self):
        context, self.context, self.page = self.context, None, None
        cdp, self._hidden_cdp = self._hidden_cdp, None
        try:
            if cdp is not None:
                cdp.detach()
        except BrowserError:
            pass  # Context/browser may already have been closed by the user.
        finally:
            if context is not None:
                context.close()

    def close(self):
        try:
            self._close_context()
        finally:
            lease, self._lease = self._lease, None
            if lease is not None:
                lease.release()

    def __getattr__(self, name):
        return getattr(self.page, name)


def amount(raw, meaning):
    if not raw:
        return None
    found = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)(?![\d.])", raw)
    if not found or re.search(r"\b(?:CAD|KRW|EUR)\b", raw):
        return None
    try:
        minor = int(Decimal(found[1].replace(',', '')) * 100)
    except (InvalidOperation, ValueError):
        return None
    if not 0 <= minor <= 9223372036854775807:
        return None
    return dict(amount_minor=minor, currency="USD", meaning=meaning, state="KNOWN", auction_round=None)


def mileage(raw, accuracy=None, *, unit=None):
    raw = raw or ""
    found = re.match(r"\s*([\d,]+)", raw)
    reported_unit = re.search(r"\b(mi|km)\b", raw, re.I)
    label = re.search(r"\(([^)]+)\)", raw)
    return dict(value=int(found[1].replace(',', '')) if found else None,
                unit=reported_unit[1].lower() if reported_unit else unit,
                accuracy=accuracy or (label[1].upper() if label else None))


def copart_schedule(raw):
    result = dict(raw=raw, event_kind="START")
    match = re.fullmatch(r"\s*(\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2})\s*(am|pm)\s+(.+?)\s*", raw or "", re.I)
    if not match:
        date_only=re.fullmatch(r'\s*(\d{2}/\d{2}/\d{4})\s*',raw or '')
        if date_only:
            try:
                result['local_date']=datetime.strptime(date_only[1],'%m/%d/%Y').date().isoformat()
            except ValueError:
                pass
        return normalize_schedule(result)
    try:
        local = datetime.strptime(f"{match[1]} {match[2]} {match[3]}", "%m/%d/%Y %I:%M %p")
    except ValueError:
        return normalize_schedule(result)
    label = match[4].upper()
    result.update(local_date=local.date().isoformat(), raw_timezone=label)
    # Copart Classic displays the browser's clock, not the US yard clock.
    # Explicit display offsets resolve this observation only; no yard IANA is guessed.
    offset = {"UTC":0, "GMT":0, "CET":60, "CEST":120}.get(label)
    numeric = re.fullmatch(r"(?:GMT|UTC)([+-])(\d{1,2})(?::(\d{2}))?", label)
    if numeric:
        hours, minutes = int(numeric[2]), int(numeric[3] or 0)
        if hours <= 14 and minutes < 60:
            offset = (hours * 60 + minutes) * (1 if numeric[1] == '+' else -1)
    if offset is not None:
        instant = local.replace(tzinfo=timezone(timedelta(minutes=offset))).astimezone(timezone.utc)
        result.update(source_timezone="UTC", local_date=instant.date().isoformat(), local_datetime=instant.isoformat())
    return normalize_schedule(result)


def iaa_schedule(raw, sale_list_url, city, *, event_kind="START"):
    result = dict(raw=raw, event_kind=event_kind)
    reference = urlsplit(sale_list_url or "")
    sale_day = re.fullmatch(r"/SalesList/\d+~US/(\d{8})/?", reference.path)
    match = re.fullmatch(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\w{3})\s+(\d{1,2}),\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(?([A-Z]+)\)?", raw or "", re.I)
    if not sale_day or reference.hostname != "www.iaai.com" or not match:
        return normalize_schedule(result)
    try:
        day = datetime.strptime(sale_day[1], "%m%d%Y").date()
        hour = int(match[3]) % 12 + (12 if match[5].lower() == 'pm' else 0)
        local = datetime.strptime(f"{day.year} {match[1]} {match[2]}", "%Y %b %d").replace(hour=hour, minute=int(match[4] or 0))
    except ValueError:
        return normalize_schedule(result)
    if local.date() != day:
        # In observed Timed Auctions the action and sale-list dates refer to
        # different events. The latter must not supply an invented start time.
        return {**normalize_schedule(result), 'issue':'DATE_CONFLICT'}
    if not 1 <= int(match[3]) <= 12:
        return normalize_schedule(result)
    label = match[6].upper()
    result.update(local_date=day.isoformat(), local_datetime=local.isoformat(), raw_timezone=label)
    # Verified public preference: IAA USA's default City selector is Chicago.
    # A different/missing preference remains unknown until its mapping is verified.
    if city == "Chicago":
        zone = _timezone("America/Chicago")
        matching = [local.replace(tzinfo=zone, fold=fold) for fold in (0,1)
                    if local.replace(tzinfo=zone, fold=fold).tzname() == label]
        if matching:
            result.update(source_timezone="America/Chicago", local_datetime=matching[0].isoformat())
    return normalize_schedule(result)


class AuctionListSource:
    reuse_browser_session = True
    read_timeout_seconds = 30
    verification_timeout_seconds = 180
    silent_verification_seconds = 8
    # Only explicit provider rejection controls inside their own banner.
    # No generic page-wide text match, accept-all, preferences save or API calls.
    cookie_reject_selectors = (
        '#onetrust-banner-sdk #onetrust-reject-all-handler',
        '#onetrust-pc-sdk .ot-pc-refuse-all-handler',
        '.fc-consent-root button.fc-cta-do-not-consent',
    )

    def reject_optional_cookies(self, page, *, checkpoint, attempted):
        for selector in self.cookie_reject_selectors:
            checkpoint()
            state = self.read_dom(page)
            if state.get('blocked') or state.get('manual_required'):
                return False
            if not state.get('consent_required'):
                return True
            if selector in attempted:
                continue
            button = page.locator(selector)
            try:
                if button.count() != 1 or not button.is_visible():
                    continue
                label = ' '.join(button.inner_text(timeout=1000).split())
                if not re.fullmatch(r'Reject all(?: cookies)?|Do not consent|Only necessary(?: cookies)?', label, re.I):
                    continue
                attempted.add(selector)
                button.click(timeout=2000)  # Normal actionability, never force.
            except BrowserError:
                return False
            deadline = monotonic() + 2
            while True:
                checkpoint()
                state = self.read_dom(page)
                if state.get('blocked') or state.get('manual_required'):
                    return False
                if not state.get('consent_required'):
                    return True
                if monotonic() >= deadline:
                    break
                page.wait_for_timeout(250)
        return False

    def read_dom(self, page):
        try:
            return page.evaluate(self.dom_script)
        except BrowserError as exc:
            if page.is_closed():
                raise AuctionListError('SOURCE_BROWSER_CLOSED', 'The site browser was closed; search stopped') from exc
            if 'Execution context was destroyed' in str(exc) or 'Cannot find context with specified id' in str(exc):
                # The site itself navigated while reading. The existing timed,
                # cancellable polling loop waits; no new request/reload is made.
                return {'ready':False, 'empty':False, 'rows':[]}
            raise

    @property
    def descriptor(self):
        from .registry import get_source_descriptor
        return get_source_descriptor(self.key)

    def validate_search_url(self, url):
        value, parsed = self._validated_url(url)
        if not re.fullmatch(self.search_path, parsed.path) or not parsed.query:
            raise InvalidSourceUrlError(f"Expected a {self.key} search URL with filters")
        query=parse_qs(parsed.query,keep_blank_values=True)
        field=self.search_filter_key
        if len(query.get(field,[]))!=1 or not query[field][0].strip():
            raise InvalidSourceUrlError(f"Expected one {field} filter value")
        return value

    def _validated_url(self, url):
        try:
            value = url.strip()
            parsed = urlsplit(value)
            if (parsed.scheme != 'https' or parsed.hostname not in self.descriptor.domains
                    or parsed.username or parsed.password or parsed.port not in (None,443)
                    or any(ord(c)<32 for c in value)):
                raise ValueError()
        except (AttributeError, ValueError):
            raise InvalidSourceUrlError(f"Invalid {self.key} URL") from None
        return value, parsed

    def search_scope(self, url):
        return parse_qs(urlsplit(self.validate_search_url(url)).query)[self.search_filter_key][0]

    def advance_page(self, page):
        page.locator(self.next_selector).click(timeout=10000)

    def listing_reference(self, url):
        value, parsed = self._validated_url(url)
        match = re.fullmatch(self.listing_path, parsed.path)
        if not match:
            raise InvalidSourceUrlError(f"Expected a {self.key} USA listing URL")
        return ListingReference(self.key, match[1], value.split('?',1)[0].split('#',1)[0])

    def parse_list_row(self, row):
        if not self.row_is_in_scope(row):
            raise AuctionListError('SOURCE_MARKET_UNSUPPORTED','The row is not a confirmed USA listing')
        reference = self.listing_reference(row['url'])
        if reference.external_id != row.get('source_car_id') or not row.get('title') or '{{' in row['title']:
            raise AuctionListError("IDENTITY_MISMATCH", "Search row identity/title was not confirmed")
        schedule = self.parse_schedule(row)
        money = [value for raw, meaning in ((row.get('bid'), 'CURRENT_BID'), (row.get('buy_now'), 'BUY_NOW'),
                                             (row.get('retail'), 'ESTIMATED_RETAIL')) if (value := amount(raw, meaning))]
        keys = {'Key Available':True, 'Keys available':True, 'Key Not Available':False, 'No keys':False}.get(row.get('keys'))
        return dict(source_key=self.key, source_listing_id=reference.external_id, url=reference.canonical_url,
                    vehicle_title=row['title'], title=row.get('document'), primary_damage=row.get('damage'),
                    secondary_damage=row.get('secondary_damage'), engine_status=row.get('engine_status'), keys=keys,
                    mileage=mileage(row.get('odometer'), row.get('accuracy'), unit=self.default_mileage_unit),
                    location=row.get('location'), stock_number=row.get('stock_number'), source_action=row.get('source_action'),
                    schedule=schedule, money_values=money, main_image_source_url=validated_source_image_url(self.key,row.get('image')),
                    checked_at=datetime.now(timezone.utc).isoformat(), read_kind="LIST_ONLY")

    @staticmethod
    def detail_read_kind(scan_mode, **kwargs):
        return None

    def listing_url(self, external_id):
        return self.listing_reference(f'https://{self.descriptor.domains[0]}{self.listing_prefix}{external_id}').canonical_url

    @staticmethod
    def read_verified_detail(*args, **kwargs):
        raise AuctionListError('SOURCE_OPERATION_UNSUPPORTED','This test adapter only reads the search list')

    @staticmethod
    def detail_reader(*args, **kwargs):
        raise AuctionListError('SOURCE_OPERATION_UNSUPPORTED','Detail pages are not opened in list-only mode')

    def apply_price_filter(self, url, minimum, maximum):
        if minimum is not None or maximum is not None:
            raise InvalidSourceUrlError("Test auctions preserve link filters; KRW price overrides are unsupported")
        return self.validate_search_url(url)

    @staticmethod
    def fingerprint_listing(detail):
        fields = {key: detail.get(key) for key in ('vehicle_title','primary_damage','secondary_damage','engine_status','keys','mileage','schedule','money_values')}
        return hashlib.sha256(json.dumps(fields,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def material_changes(old, new):
        return [dict(field=key,old=old.get(key),new=new[key]) for key in ('primary_damage','engine_status','mileage','schedule')
                if key in new and old.get(key)!=new[key]]

    def open_browser(self, playwright, *, headless, executable_path, interactive=False):
        from ..database import settings
        from ..source_test_profile import validate_source_test_isolation

        descriptor = self.descriptor
        validate_source_test_isolation(settings)
        if not settings.source_test_mode:
            raise AuctionListError('SOURCE_TEST_PROFILE_REQUIRED', 'Interactive sources require the isolated test profile')
        # Owned cookies/preferences only: never read a user's Chrome profile or
        # transfer challenge cookies between browsers, sites or work profiles.
        options = {'executable_path': executable_path}
        profile_kind = 'chromium'
        if not executable_path or Path(executable_path).name in ('headless_shell', 'headless_shell.exe'):
            # Desktop bundles only the non-visible shell. Use Playwright's
            # supported installed browser channel, never install/patch a browser.
            profile_kind = os.environ.get('SEARCHCAR_SITE_BROWSER_CHANNEL', 'msedge' if sys.platform == 'win32' else 'chrome')
            if profile_kind not in ('chrome', 'msedge'):
                raise AuctionListError('SOURCE_VISIBLE_BROWSER_UNAVAILABLE', 'Choose the installed chrome or msedge browser channel')
            options = {'channel': profile_kind}
        root = Path(settings.storage_root).parent / 'runtime' / 'source-browsers'
        site_root = root / descriptor.key
        profile = site_root / profile_kind
        for folder in (root, site_root, profile):
            if folder.is_symlink():
                raise AuctionListError('SOURCE_BROWSER_PROFILE_UNSAFE', 'Browser profile must not be a symlink')
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            folder.chmod(0o700)
        with _BROWSER_LOCKS_GUARD:
            lease = _BROWSER_LOCKS.setdefault(str(profile.resolve()), _ProfileLease())
        if not lease.acquire(interactive=interactive):
            raise AuctionListError('SOURCE_BROWSER_BUSY', 'The site browser is in use; finish choosing search filters first')
        session = AuctionBrowserSession(playwright.chromium, profile, options, interactive=interactive, lease=lease)
        return None, session, session

    def collect_search_pages(self, page, url, search_name='', *, all_pages=False, checkpoint=None, progress=None, max_pages=None, search_config=None):
        from ..database import settings
        self.descriptor  # Recheck the opt-in/isolation even for an already cached adapter.
        max_pages = settings.source_test_max_pages if max_pages is None else max_pages
        if type(max_pages) is not int or not 1 <= max_pages <= 1000:
            raise AuctionListError('SOURCE_PAGE_LIMIT_INVALID', 'Page limit must be an integer between 1 and 1000')
        rows, visited, total, expected_pages, previous = {}, 0, None, 1, None
        checkpoint = checkpoint or (lambda:None)
        denied = []
        network_failures, pending_fetch_failures, schedule_issues = [], [], []
        browser_diagnostics = dict(script_errors=0, request_failures=[], http_failures=[])
        rate_limited = False
        summary = dict(dated=0, exact_time=0, without_date=0, unrecognized_schedule=0, unsupported_market=0)
        session = page if isinstance(page, AuctionBrowserSession) else None
        browser_notice = None
        if session:
            checkpoint()
            browser_notice = session.background()
        interactive = page is getattr(self, '_interactive_page', None)
        waits_used = set()
        wait_budgets = {}
        cookie_attempts = set()
        restart_required = False
        silent_wait_used = False
        guard = None
        next_preparation_progress = 0
        loading_window_opened = False
        if search_config is not None:
            try:
                clean = self.validate_selection_snapshot(search_config)
                if clean != search_config or clean['creation_url'] != self.validate_search_url(url):
                    raise ValueError('search_config_changed')
            except (ValueError, KeyError, TypeError, InvalidSourceUrlError):
                raise AuctionListError('SEARCH_FILTERS_UNSUPPORTED', 'The saved public search conditions are invalid') from None
            factory = getattr(self, 'make_search_guard', None)
            if factory:
                guard = factory(page, clean)
        def raw_checkpoint():
            checkpoint()
            if page.is_closed():
                raise AuctionListError('SOURCE_BROWSER_CLOSED', 'The site browser was closed; search stopped')
            if rate_limited:
                raise AuctionListError('SOURCE_RATE_LIMITED', 'The site rate-limited this search; try again later')
            if guard:
                guard.check()
        def emit_progress(**extra):
            if progress:
                info = dict(current_page=visited,total_pages=expected_pages,expected_pages=expected_pages if all_pages else 1,
                              pages_visited=visited,total_results=total,ads_seen=len(rows),found_count=len(rows),complete=False,
                              max_pages=max_pages,read_summary=dict(summary),network_failures=list(network_failures),
                              schedule_issues=list(schedule_issues),interaction=None,browser_notice=browser_notice)
                info['browser_diagnostics'] = {key: list(value) if isinstance(value,list) else value
                                               for key,value in browser_diagnostics.items()}
                progress({**info, **extra})
        def preparing_checkpoint():
            nonlocal next_preparation_progress
            checked_checkpoint()
            if monotonic() >= next_preparation_progress:
                emit_progress(stage='SEARCH_WINDOW_LOADING' if loading_window_opened and session.interactive else 'PREPARING_SEARCH')
                next_preparation_progress = monotonic() + 2
        def resume_background():
            nonlocal browser_notice
            if not session or not session.interactive:
                return
            raw_checkpoint()
            if not browser_notice:
                try:
                    session.hide_verified()
                except AuctionListError as exc:
                    if exc.code != 'SOURCE_BACKGROUND_BROWSER_UNAVAILABLE':
                        raise
                    # Window presentation must not discard a verified search.
                    browser_notice = 'MANUAL_MINIMIZE'
            raw_checkpoint()
            emit_progress()
            self.prepare_page(page, checkpoint=preparing_checkpoint, verification=wait_for_user)
        def wait_for_user(code):
            nonlocal restart_required, silent_wait_used, loading_window_opened
            if code == 'SEARCH_INTERFACE_LOADING':
                # A stalled first page is not a CAPTCHA or permission to retry access denials.
                if not session or visited or loading_window_opened:
                    return
                raw_checkpoint()
                if self.search_scope(page.url) != self.search_scope(url):
                    raise AuctionListError('SOURCE_SEARCH_SCOPE_CHANGED', 'Search filters changed before opening the site window')
                loading_window_opened = True
                emit_progress(stage='SEARCH_WINDOW_LOADING')
                session.show(self.validate_search_url(url), raw_checkpoint)
                raw_checkpoint()
                if self.search_scope(page.url) != self.search_scope(url):
                    raise AuctionListError('SOURCE_SEARCH_SCOPE_CHANGED', 'Search filters changed in the site window')
                return
            if code == 'COOKIE_CONSENT':
                if self.search_scope(page.url) != self.search_scope(url):
                    raise AuctionListError('SOURCE_SEARCH_SCOPE_CHANGED', 'The browser search filters changed before the cookie choice')
                if self.reject_optional_cookies(page, checkpoint=raw_checkpoint, attempted=cookie_attempts):
                    if self.search_scope(page.url) != self.search_scope(url):
                        raise AuctionListError('SOURCE_SEARCH_SCOPE_CHANGED', 'The browser search filters changed during the cookie choice')
                    if restart_required:
                        raise AuctionListError('SEARCH_VERIFICATION_RESTART_REQUIRED', 'Site verification completed after a partial read; start the search again')
                    resume_background()
                    return
            kind = 'CONSENT' if code == 'COOKIE_CONSENT' else 'ACCESS'
            can_settle = kind == 'ACCESS' and session and not session.interactive and not silent_wait_used
            initial = self.read_dom(page) if can_settle else {}
            if can_settle and initial.get('blocked') and initial.get('manual_required'):
                silent_wait_used = True
                deadline = monotonic() + self.silent_verification_seconds
                next_progress = monotonic()
                while True:
                    raw_checkpoint()
                    state = self.read_dom(page)
                    if not state.get('blocked') and state.get('consent_required'):
                        denied.clear()
                        return wait_for_user('COOKIE_CONSENT')
                    if not state.get('blocked') and (state.get('ready') or state.get('can_prepare') or state.get('restore_ready')):
                        if self.search_scope(page.url) != self.search_scope(url):
                            raise AuctionListError('SOURCE_SEARCH_SCOPE_CHANGED', 'Search changed during background verification')
                        denied.clear()
                        pending_fetch_failures.clear()
                        return
                    if state.get('blocked') and state.get('manual_required') is False:
                        break
                    if monotonic() >= deadline:
                        break
                    if monotonic() >= next_progress:
                        emit_progress()
                        next_progress = monotonic() + 2
                    page.wait_for_timeout(250)
            if (not session and not interactive) or (kind == 'ACCESS' and kind in waits_used):
                raise AuctionListError(code, f'{self.key} denied public access; search stopped')
            state = self.read_dom(page)
            if kind == 'ACCESS' and state.get('manual_required') is False:
                raise AuctionListError('SOURCE_ACCESS_DENIED', f'{self.key} denied access without a manual verification prompt')
            if session and not session.interactive:
                if not (state.get('consent_required') or state.get('manual_required', code == 'CAPTCHA')):
                    raise AuctionListError('SOURCE_ACCESS_DENIED', f'{self.key} did not expose a manual verification prompt')
            waits_used.add(kind)
            # Some sites load a second CMP after a reload. It shares the
            # original consent budget/window, not a new 3-minute wait.
            deadline, expires_at = wait_budgets.setdefault(kind, (
                monotonic() + self.verification_timeout_seconds,
                (datetime.now(timezone.utc)+timedelta(seconds=self.verification_timeout_seconds)).isoformat(),
            ))
            interaction = dict(state='WAITING_FOR_USER',source_key=self.key,reason=code,
                               expires_at=expires_at)
            emit_progress(interaction=interaction)
            reopened = bool(session and not session.interactive)
            if reopened:
                denied.clear()
                replaced = session.show(self.validate_search_url(url), raw_checkpoint)
                restart_required = bool(visited and replaced)
            next_progress = monotonic() + 2
            while True:
                raw_checkpoint()
                state = self.read_dom(page)
                if kind == 'CONSENT' and state.get('blocked'):
                    return wait_for_user('CAPTCHA')
                if kind == 'ACCESS' and not state.get('blocked') and state.get('consent_required'):
                    return wait_for_user('COOKIE_CONSENT')
                if not state.get('blocked') and not state.get('consent_required') and (state.get('ready') or state.get('can_prepare') or state.get('restore_ready')):
                    if self.search_scope(page.url) != self.search_scope(url):
                        raise AuctionListError('SOURCE_SEARCH_SCOPE_CHANGED', 'The browser search filters changed during verification; start a new project search')
                    denied.clear()
                    pending_fetch_failures.clear()
                    emit_progress()
                    if restart_required:
                        # A fresh visible page cannot safely resume an AJAX
                        # cursor. Preserve partial rows; require a fresh scan.
                        raise AuctionListError('SEARCH_VERIFICATION_RESTART_REQUIRED', 'Site verification completed after a partial read; start the search again')
                    if session and session.interactive:
                        resume_background()
                    elif reopened and state.get('can_prepare') and not state.get('ready'):
                        self.prepare_page(page, checkpoint=preparing_checkpoint, verification=wait_for_user)
                    return
                if monotonic() >= deadline:
                    raise AuctionListError('SOURCE_VERIFICATION_TIMEOUT', 'Manual site verification did not finish in time; search stopped')
                if monotonic() >= next_progress:
                    # Commit the worker heartbeat and release SQLite's write
                    # transaction so another request can cancel this wait.
                    emit_progress(interaction=interaction)
                    next_progress = monotonic() + 2
                page.wait_for_timeout(250)
        def resource_response(response):
            nonlocal rate_limited
            parsed = urlsplit(response.url)
            resource_type = response.request.resource_type
            if response.status >= 400 and resource_type in ('document','script','stylesheet','xhr','fetch'):
                diagnostic = dict(resource_type=resource_type,status=response.status,
                                  site_resource=parsed.hostname in self.descriptor.domains)
                if diagnostic not in browser_diagnostics['http_failures']:
                    browser_diagnostics['http_failures'].append(diagnostic)
                    del browser_diagnostics['http_failures'][:-20]
            if response.status not in (401,403,429) or parsed.hostname not in self.descriptor.domains:
                return
            failure = dict(status=response.status,host=parsed.hostname,path=parsed.path,resource_type=resource_type)
            if failure not in network_failures:
                network_failures.append(failure)
                del network_failures[:-20]
            # CSS failures remain diagnostic: the public DOM still needs verification.
            if response.status == 429:
                rate_limited = True  # A challenge/reload is not a remedy for rate limits.
            elif resource_type in ('document','script'):
                denied.append(failure)
            elif resource_type in ('xhr','fetch'):
                # A denied optional fetch does not invalidate a newly verified
                # result set. If the next set never arrives, preserve the failure.
                if failure not in pending_fetch_failures:
                    pending_fetch_failures.append(failure)
                    del pending_fetch_failures[:-20]
        def checked_checkpoint():
            raw_checkpoint()
            if denied:
                wait_for_user('SOURCE_ACCESS_DENIED')
        def script_error(_error):
            browser_diagnostics['script_errors'] = min(1000, browser_diagnostics['script_errors'] + 1)
        def request_failed(request):
            if request.resource_type not in ('document','script','stylesheet','xhr','fetch'):
                return
            failure = request.failure or ''
            diagnostic = dict(resource_type=request.resource_type,
                              code=failure if re.fullmatch(r'net::ERR_[A-Z0-9_]{1,80}',failure) else 'OTHER')
            if diagnostic not in browser_diagnostics['request_failures']:
                browser_diagnostics['request_failures'].append(diagnostic)
                del browser_diagnostics['request_failures'][:-20]
        page.on('response',resource_response)
        page.on('pageerror',script_error)
        page.on('requestfailed',request_failed)
        try:
            checked_checkpoint()
            emit_progress(stage='OPENING_SEARCH')
            if session:
                session.wait_for_request(checked_checkpoint)
            response=page.goto(self.validate_search_url(url), wait_until='domcontentloaded', timeout=20000)
            if response is not None and response.status>=400:
                if response.status == 429:
                    raise AuctionListError('SOURCE_RATE_LIMITED', 'The site rate-limited this search; try again later')
                if response.status in (401,403):
                    wait_for_user('SOURCE_ACCESS_DENIED')
                else:
                    raise AuctionListError('SOURCE_ACCESS_DENIED',f'{self.key} returned HTTP {response.status}; public access was not confirmed')
            preparing_checkpoint()
            self.prepare_page(page,checkpoint=preparing_checkpoint,verification=wait_for_user)
            emit_progress(stage='READING_RESULTS')
            while True:
                deadline = monotonic() + self.read_timeout_seconds
                while True:
                    checked_checkpoint()
                    state = self.read_dom(page)
                    checked_checkpoint()
                    if state.get('blocked'):
                        wait_for_user('CAPTCHA')
                        deadline = monotonic() + self.read_timeout_seconds
                        continue
                    if state.get('consent_required'):
                        wait_for_user('COOKIE_CONSENT')
                        deadline = monotonic() + self.read_timeout_seconds
                        continue
                    if guard and (state.get('restore_ready') or state.get('ready')) and not guard.accept(state, checked_checkpoint):
                        if monotonic() >= deadline:
                            raise AuctionListError('SEARCH_RESTORE_FAILED', 'IAA did not finish applying the saved native search controls')
                        page.wait_for_timeout(250)
                        continue
                    signature = tuple(row.get('source_car_id') for row in state.get('rows') or [])
                    rows_ready = bool(signature) or (state.get('empty') and state.get('total') == 0)
                    if state.get('ready') and rows_ready and state.get('page') == visited+1 and (previous is None or signature!=previous):
                        break
                    if monotonic() >= deadline:
                        if pending_fetch_failures:
                            wait_for_user('SOURCE_ACCESS_DENIED')
                            deadline = monotonic() + self.read_timeout_seconds
                            continue
                        raise AuctionListError('SEARCH_PAGE_NOT_READY', f'{self.key} page did not expose a new verified result set')
                    page.wait_for_timeout(250)
                if loading_window_opened:
                    resume_background()
                page_rows = state.get('rows') or []
                if not page_rows and not (state.get('empty') and state.get('total')==0):
                    raise AuctionListError('SEARCH_PAGE_NOT_READY', 'Unconfirmed empty result')
                if total is not None and state.get('total')!=total:
                    raise AuctionListError('SEARCH_RESULTS_CHANGED', 'Result count changed during pagination; repeat the search')
                total = state.get('total')
                if visited==0:
                    expected_pages = max(1,(total+len(page_rows)-1)//len(page_rows)) if total and page_rows else 1
                for row in page_rows:
                    if row.get('source_car_id') in rows:
                        raise AuctionListError('SEARCH_RESULTS_CHANGED', 'Repeated listing across result pages')
                    if not self.row_is_in_scope(row):
                        # Keep raw scope accounting; never store a foreign row as USA/USD.
                        rows[row['source_car_id']]={**row,'out_of_scope':True}
                        summary['unsupported_market'] += 1
                        continue
                    detail = self.parse_list_row(row)  # Verify identity before accepting.
                    schedule = detail['schedule']
                    if schedule.get('local_date'):
                        summary['dated'] += 1
                    if schedule['precision'] == 'EXACT':
                        summary['exact_time'] += 1
                    elif row.get('date_text') in ('Upcoming lot','Future','Auction Not Assigned'):
                        summary['without_date'] += 1
                    else:
                        summary['unrecognized_schedule'] += 1
                        if len(schedule_issues)<5:
                            schedule_issues.append(dict(source_listing_id=detail['source_listing_id'],
                                raw=(schedule.get('raw') or '')[:256],issue=schedule.get('issue'),event_kind=schedule['event_kind']))
                    rows[row['source_car_id']] = row
                visited += 1
                previous = signature
                pending_fetch_failures.clear()
                emit_progress()
                if not all_pages:
                    break
                if not state.get('has_next'):
                    if total is None or len(rows)!=total:
                        raise AuctionListError('SEARCH_INCOMPLETE', 'Pagination ended before the reported result count was verified')
                    break
                if visited>=max_pages:
                    raise AuctionListError('SEARCH_PAGE_LIMIT', 'Full search reached its page safety limit; result is partial')
                checked_checkpoint()
                if self.read_dom(page).get('consent_required'):
                    wait_for_user('COOKIE_CONSENT')
                if session:
                    session.wait_for_request(checked_checkpoint)
                self.advance_page(page)
            emit_progress(complete=True)
            return SearchCollection(rows,visited,expected_pages,total)
        except Exception as exc:
            exc.partial_collection = SearchCollection(rows,visited,expected_pages,total)
            exc.network_failures = list(network_failures)
            exc.browser_diagnostics = browser_diagnostics
            try:
                emit_progress(stop_reason=getattr(exc,'code','READ_ERROR'))
            except Exception:
                pass  # Preserve the original failure/cancellation if publishing is unavailable.
            raise
        finally:
            page.remove_listener('response',resource_response)
            page.remove_listener('pageerror',script_error)
            page.remove_listener('requestfailed',request_failed)
            if guard:
                guard.close()
