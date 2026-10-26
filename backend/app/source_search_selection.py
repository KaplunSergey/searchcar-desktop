"""Owned-browser selection drafts; never create or enqueue a project implicitly."""
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from threading import Event, RLock, Thread
from time import monotonic
from uuid import uuid4
from urllib.parse import urlsplit

from playwright.sync_api import Error as BrowserError
from .sources.contracts import InvalidSourceUrlError, SourceUnavailableError, ScanError
from .source_selection_panel import PANEL_SCRIPT, panel_content


@dataclass
class SelectionDraft:
    status: str = 'SELECTING'
    snapshot: dict | None = None
    issue: str | None = None
    _latest: object = None
    _candidate: dict | None = None
    _completed: bool = False

    @property
    def can_use(self):
        return self.snapshot is not None and self.issue is None and self.status in ('READY', 'CLOSED')

    def dirty(self):
        if self.status in ('CLOSED', 'FAILED'):
            return
        self._latest = self._candidate = None
        self._completed = False
        self.status = 'SELECTING'
        self.issue = 'LAST_CHANGE_UNCONFIRMED'

    def begin(self, token, candidate):
        self.dirty()
        self._latest, self._candidate = token, candidate
        if candidate is None:
            self.issue = 'SEARCH_FILTERS_UNSUPPORTED'

    def finish(self, token, status):
        if self.status in ('CLOSED', 'FAILED'):
            return
        if token != self._latest:
            if self.status == 'READY':
                self.dirty()
            return
        self._completed = status is not None and 200 <= status < 300 and self._candidate is not None
        if not self._completed:
            self.issue = 'SEARCH_FILTERS_UNSUPPORTED' if self._candidate is None else 'SEARCH_REQUEST_FAILED'

    def settle(self, dom):
        if self.status in ('CLOSED', 'FAILED'):
            return
        if not dom.get('ready') or dom.get('blocked') or dom.get('manual_required') or dom.get('consent_required'):
            self.status = 'SELECTING'
            if dom.get('manual_required'):
                self.issue = 'SITE_VERIFICATION_REQUIRED'
            elif dom.get('blocked'):
                self.issue = 'SITE_ACCESS_BLOCKED'
            elif dom.get('consent_required'):
                self.issue = 'SITE_CONSENT_REQUIRED'
            elif self._completed:
                self.issue = 'SEARCH_RESULTS_NOT_READY'
            elif self._latest is None and self.snapshot is None and self.issue is None:
                self.issue = 'NO_SEARCH_CAPTURED'
            return
        if self._completed:
            self.snapshot = deepcopy(self._candidate)
            self.status, self.issue = 'READY', None
        elif self._latest is None and self.snapshot is None and self.issue is None:
            self.issue = 'NO_SEARCH_CAPTURED'

    def close(self):
        ready = self.can_use
        self.status = 'CLOSED'
        if not ready:
            self.issue = 'LAST_CHANGE_UNCONFIRMED' if self.snapshot else (self.issue or 'NO_SEARCH_CAPTURED')

    def public(self):
        return deepcopy(dict(status=self.status, snapshot=self.snapshot, issue=self.issue, can_use=self.can_use))


def _draft_path(root, owner, source, draft_id=None):
    if type(owner) is not int or owner < 1 or not source.isascii() or not source.replace('-', '').isalnum():
        raise ValueError('selection_identity_invalid')
    if draft_id is not None and (not isinstance(draft_id, str) or len(draft_id) != 32
                                 or any(c not in '0123456789abcdef' for c in draft_id)):
        raise ValueError('selection_identity_invalid')
    suffix = f'-{draft_id}' if draft_id is not None else ''
    path = Path(root) / 'runtime' / 'search-selections' / f'{owner}-{source}{suffix}.json'
    for folder in (Path(root), path.parent.parent, path.parent):
        if folder.is_symlink():
            raise ValueError('selection_path_unsafe')
    if path.is_symlink():
        raise ValueError('selection_path_unsafe')
    return path


def save_draft(root, owner, source, value):
    path = _draft_path(root, owner, source, value.get('draft_id'))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def load_draft(root, owner, source, *, draft_id=None):
    path = _draft_path(root, owner, source, draft_id)
    try:
        if path.stat().st_size > 131072:
            raise ValueError('selection_file_invalid')
        value = json.loads(path.read_text(encoding='utf-8'))
        expected = {'version', 'id', 'owner_id', 'source_key', 'status', 'snapshot', 'issue', 'can_use'}
        if draft_id is not None:
            expected.add('draft_id')
        if 'project_id' in value:
            expected.add('project_id')
            if type(value['project_id']) is not int or value['project_id'] < 1:
                raise ValueError('selection_file_invalid')
        if (not isinstance(value, dict) or set(value) != expected
                or value.get('draft_id') != draft_id
                or type(value['owner_id']) is not int or value['owner_id'] != owner
                or value['source_key'] != source or value['version'] != 1
                or value['status'] not in ('SELECTING', 'READY', 'CLOSED', 'FAILED')
                or type(value['can_use']) is not bool
                or not isinstance(value['id'], str) or len(value['id']) != 32):
            raise ValueError('selection_file_invalid')
        snapshot = value.get('snapshot')
        if value['can_use'] and snapshot is None:
            raise ValueError('selection_file_invalid')
        if snapshot is not None:
            if (not isinstance(snapshot, dict) or snapshot.get('source_key') != source
                    or snapshot.get('schema_version') != 1):
                raise ValueError('selection_file_invalid')
        # A process restart cannot prove that the last in-flight edit finished.
        if value['status'] not in ('CLOSED', 'FAILED'):
            value.update(status='CLOSED', issue='SELECTION_INTERRUPTED', can_use=False)
        return value
    except FileNotFoundError:
        return None


class SelectionSession:
    def __init__(self, root, owner, source, *, locale='ru', draft_id=None, project_id=None, entry_url=None):
        _draft_path(root, owner, source, draft_id)
        self.root, self.owner, self.source = root, owner, source
        self.draft_id = draft_id
        self.project_id, self.entry_url = project_id, entry_url
        self.id = uuid4().hex
        self.draft = SelectionDraft()
        self.stop = Event()
        self.lock = RLock()
        self.last_saved = None
        self.thread = None
        self.locale = locale

    def public(self):
        with self.lock:
            return dict(version=1, id=self.id, owner_id=self.owner, source_key=self.source,
                        **({'draft_id': self.draft_id} if self.draft_id is not None else {}),
                        **({'project_id': self.project_id} if self.project_id is not None else {}),
                        **self.draft.public())

    def persist(self):
        value = self.public()
        if value != self.last_saved:
            save_draft(self.root, self.owner, self.source, value)
            self.last_saved = value


class SearchSelections:
    """Single-process desktop coordinator. Each browser stays on its own thread."""
    def __init__(self):
        self.lock = RLock()
        self.sessions = {}

    @staticmethod
    def _key(root, owner, source, draft_id):
        return (str(root), owner, source) + ((draft_id,) if draft_id is not None else ())

    def current(self, root, owner, source, *, draft_id=None):
        _draft_path(root, owner, source, draft_id)
        with self.lock:
            session = self.sessions.get(self._key(root, owner, source, draft_id))
        if session:
            return session.public()
        value = load_draft(root, owner, source, draft_id=draft_id)
        if value and value.get('snapshot'):
            from .sources.registry import get_source
            # Validation belongs to the source, not a fixed list in the coordinator.
            try:
                clean = get_source(source).validate_selection_snapshot(value['snapshot'])
            except (InvalidSourceUrlError, SourceUnavailableError):
                raise ValueError('selection_file_invalid') from None
            if clean != value['snapshot']:
                raise ValueError('selection_file_invalid')
        return value

    def start(self, root, owner, source, *, locale='ru', draft_id=None, project_id=None, initial_snapshot=None, entry_url=None):
        if entry_url is not None:
            from .sources.registry import get_source
            entry_url = get_source(source).validate_search_url(entry_url)
        key = self._key(root, owner, source, draft_id)
        with self.lock:
            previous = self.sessions.get(key)
            if previous and previous.project_id != project_id:
                raise ValueError('selection_project_changed')
            if previous and previous.thread.is_alive():
                return previous.public()
            saved = self.current(root, owner, source, draft_id=draft_id)
            if saved and saved.get('project_id') != project_id:
                raise ValueError('selection_project_changed')
            session = SelectionSession(root, owner, source, locale=locale, draft_id=draft_id, project_id=project_id, entry_url=entry_url)
            if saved and saved.get('snapshot'):
                session.draft.snapshot = deepcopy(saved['snapshot'])
                session.draft.issue = 'LAST_CHANGE_UNCONFIRMED'
            elif initial_snapshot:
                from .sources.registry import get_source
                session.draft.snapshot = get_source(source).validate_selection_snapshot(initial_snapshot)
                session.draft.issue = 'LAST_CHANGE_UNCONFIRMED'
            session.persist()
            session.thread = Thread(target=self._run, args=(session,), daemon=True, name=f'search-selection-{source}')
            self.sessions[key] = session
            session.thread.start()
            return session.public()

    def confirm(self, root, owner, source, selection_id, *, draft_id=None):
        key = self._key(root, owner, source, draft_id)
        with self.lock:
            session = self.sessions.get(key)
        if session is None or session.id != selection_id:
            raise ValueError('selection_not_found')
        with session.lock:
            if not session.draft.can_use:
                raise ValueError('selection_not_ready')
            session.stop.set()
        return session.public()

    def cancel(self, root, owner, source, selection_id, *, draft_id=None):
        with self.lock:
            session = self.sessions.get(self._key(root, owner, source, draft_id))
        if session is None or session.id != selection_id:
            raise ValueError('selection_not_found')
        session.stop.set()
        return session.public()

    def shutdown(self):
        with self.lock:
            sessions = list(self.sessions.values())
        for session in sessions:
            session.stop.set()
        deadline = monotonic() + 3
        for session in sessions:
            session.thread.join(timeout=max(0, deadline - monotonic()))

    def _run(self, session):
        from .desktop_runtime import desktop_playwright
        from .sources.registry import get_source
        page = context = None
        try:
            source = get_source(session.source)
            with desktop_playwright() as pw:
                deadline = monotonic() + 30
                while not session.stop.is_set():
                    try:
                        _, context, page = source.open_browser(pw, headless=False,
                            executable_path=os.environ.get('SEARCHCAR_CHROMIUM_EXECUTABLE'), interactive=True)
                        break
                    except ScanError as exc:
                        if getattr(exc, 'code', None) != 'SOURCE_BROWSER_BUSY' or monotonic() >= deadline:
                            raise
                        session.stop.wait(0.25)
                if context is None:
                    with session.lock:
                        session.draft.close()
                    return
                actual_page = getattr(page, 'page', page)
                try:
                    self.observe(session, source, page)
                except Exception:
                    if actual_page.is_closed():
                        with session.lock:
                            session.draft.close()
                    else:
                        raise
                finally:
                    closing = context
                    context = None
                    closing.close()
        except Exception as exc:
            with session.lock:
                session.draft.status = 'FAILED'
                session.draft.issue = getattr(exc, 'code', 'SOURCE_SELECTION_FAILED')
        finally:
            try:
                if context:
                    context.close()
            finally:
                session.persist()

    @staticmethod
    def observe(session, source, page):
        pending = {}
        url_requests = set()
        url_request_failed = False
        capture_request = getattr(source, 'capture_search_request', None)
        capture_url = getattr(source, 'capture_search_url', None)
        prepare_fresh = getattr(source, 'prepare_fresh_selection', None) if session.draft.snapshot is None and session.project_id is None else None

        def invalidate():
            with session.lock:
                session.draft.dirty()

        def owns_page():
            try:
                parsed = urlsplit(page.url)
                return (parsed.scheme == 'https' and parsed.hostname == urlsplit(source.selection_entry_url).hostname
                        and not parsed.username and not parsed.password and parsed.port in (None, 443))
            except ValueError:
                return False

        def started(request):
            nonlocal url_request_failed
            if request.frame != page.main_frame:
                return
            # Link-only sources need the loaded search document, not unrelated
            # analytics/fetches or the request-body completion rules used by IAA.
            if capture_url and request.resource_type == 'document':
                try:
                    source.validate_search_url(request.url)
                    url_requests.add(request)
                    url_request_failed = False
                    with session.lock:
                        session.draft.dirty()
                except (ValueError, InvalidSourceUrlError):
                    pass
            if not capture_request or not source.is_selection_request(request.url, request.method):
                return
            try:
                raw = request.post_data or ''
                candidate = capture_request(request.url, request.method, json.loads(raw)) if len(raw) <= 65536 else None
            except (ValueError, TypeError):
                candidate = None
            with session.lock:
                session.draft.begin(request, candidate)
                pending[request] = candidate

        def finished(request, *, failed=False):
            nonlocal url_request_failed
            if request in url_requests:
                response = None if failed else request.response()
                if response is None or response.status >= 400:
                    url_request_failed = True
            url_requests.discard(request)
            if request not in pending:
                return
            pending.pop(request)
            response = None if failed else request.response()
            with session.lock:
                session.draft.finish(request, response.status if response else None)

        def dirty(binding_source):
            if binding_source['frame'] != page.main_frame or capture_url:
                return
            with session.lock:
                session.draft.dirty()

        def confirm(binding_source):
            if binding_source['frame'] != page.main_frame or not owns_page():
                return False
            with session.lock:
                if session.draft.can_use:
                    session.stop.set()
                    return True
            return False

        page.on('request', started)
        page.on('requestfinished', finished)
        page.on('requestfailed', lambda request: finished(request, failed=True))
        # One checkbox activation emits click/input/change; observe its new
        # value before the site handler, without invalidating that request again.
        page.expose_binding('__searchcarSelectionEdited', dirty)
        page.expose_binding('__searchcarSelectionConfirm', confirm)
        page.add_init_script("""(()=>{let lastControl=null,lastValue;
        const edited=e=>{
          if(e.composedPath().some(n=>n.id==='searchcar-selection-panel'))return;
          if(!(e.target instanceof Element))return;
          const control=e.target.closest('input,select,textarea');
          if(control){
            const toggle=control.matches('input[type="checkbox"],input[type="radio"]');
            if(e.type==='click'&&!toggle)return;
            const value=toggle?control.checked:control.matches('select')
              ?JSON.stringify(Array.from(control.selectedOptions,o=>o.value)):control.value;
            if(control===lastControl&&value===lastValue)return;
            lastControl=control;lastValue=value;
          }else{
            if(e.type!=='click'||!e.target.closest('button,a'))return;
            lastControl=null;
          }
          window.__searchcarSelectionEdited();
        }; for(const event of ['input','change','click'])document.addEventListener(event,edited,true);})();""")
        try:
            entry = (session.draft.snapshot.get('creation_url') or session.draft.snapshot['entry_url']) if session.draft.snapshot else session.entry_url or source.selection_entry_url
            page.goto(entry, wait_until='domcontentloaded', timeout=20000)
        except BrowserError:
            if page.is_closed():
                with session.lock:
                    session.draft.close()
                return
            # A timed-out load may still finish while the user operates the page.
        deadline = monotonic() + 15 * 60
        previous_url = None
        while not page.is_closed():
            if monotonic() >= deadline:
                with session.lock:
                    session.draft.issue = 'SELECTION_INTERRUPTED'
                break
            if not owns_page():
                with session.lock:
                    session.draft.dirty()
                state = {'ready': False}
            else:
                state = getattr(source, 'read_selection_dom', source.read_dom)(page)
            if prepare_fresh:
                if (state.get('ready') and not any(state.get(k) for k in ('blocked', 'manual_required', 'consent_required'))
                        and not pending):
                    reset = prepare_fresh(page, invalidate=invalidate)
                    prepare_fresh = None
                    if reset:
                        state = {**state, 'ready': False}
                else:
                    state = {**state, 'ready': False}
            if capture_url:
                capture_issue = None
                try:
                    current = capture_url(page.url)
                except InvalidSourceUrlError:
                    current = None
                except (ValueError, TypeError):
                    current = None
                    capture_issue = 'SEARCH_FILTERS_UNSUPPORTED'
                if current and state.get('ready') and not url_requests and not url_request_failed:
                    with session.lock:
                        if current != previous_url or session.draft.status != 'READY':
                            session.draft.begin('url', current)
                            session.draft.finish('url', 200)
                            previous_url = current
                if url_requests or url_request_failed or not current:
                    with session.lock:
                        session.draft.dirty()
                        if url_request_failed:
                            session.draft.issue = 'SEARCH_REQUEST_FAILED'
                        elif capture_issue:
                            session.draft.issue = capture_issue
            with session.lock:
                complete_snapshot = getattr(source, 'complete_selection_snapshot', None)
                if complete_snapshot and session.draft._completed and state.get('ready') and session.draft._candidate:
                    session.draft._candidate = complete_snapshot(session.draft._candidate, state)
                session.draft.settle(state)
                if session.stop.is_set():
                    if not session.draft.can_use:
                        session.draft.issue = 'SELECTION_INTERRUPTED'
                    break
                content = panel_content(session.draft, session.locale)
            # Only our source's top-level page receives the panel, never a
            # challenge iframe or an unrelated site reached through a link.
            if owns_page():
                try:
                    page.evaluate(PANEL_SCRIPT, content)
                except BrowserError as exc:
                    if page.is_closed() or not any(text in str(exc) for text in ('Execution context was destroyed', 'Cannot find context with specified id')):
                        raise
            session.persist()
            try:
                page.wait_for_timeout(250)
            except BrowserError:
                if not page.is_closed():
                    raise
        with session.lock:
            if pending or url_requests:
                session.draft.dirty()
            session.draft.close()


search_selections = SearchSelections()
