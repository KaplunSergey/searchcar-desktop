"""Public search conditions captured from ordinary site actions, never credentials."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from app.sources.iaa import IAASource
from app.sources.copart import CopartSource
from app.source_search_selection import SelectionDraft, SelectionSession, SearchSelections, load_draft, save_draft
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


IAA = {
    'Searches': [
        {'Facets': [{'Group': 'Make', 'Value': 'Cadillac'}], 'FullSearch': '', 'LongRanges': None},
        {'Facets': [{'Group': 'InventoryTypes', 'Value': 'SUVs', 'ForAnalytics': False}], 'FullSearch': None, 'LongRanges': None},
        {'Facets': [{'Group': 'Model', 'Value': 'ESCALADE'}], 'FullSearch': None, 'LongRanges': None},
        {'Facets': [{'Group': 'Series', 'Value': 'PLATINUM'}], 'FullSearch': None, 'LongRanges': None},
        {'Facets': None, 'FullSearch': None, 'LongRanges': [{'From': 2017, 'Name': 'Year', 'To': 2027}]},
    ],
    'ZipCode': '', 'miles': 0, 'PageSize': 100, 'CurrentPage': 1,
    'Sort': [{'IsGeoSort': False, 'SortField': 'TenantSortOrder', 'IsDescending': False}],
    'ShowRecommendations': False,
    'SaleStatusFilters': [{'SaleStatus': 1, 'IsSelected': True}],
    'BidStatusFilters': [{'BidStatus': 6, 'IsSelected': True}],
}


def test_iaa_can_capture_public_filters_separately_from_the_address_bar():
    source = IAASource()
    capture = getattr(source, 'capture_search_request', None)
    assert callable(capture), 'IAA has no way to capture filters absent from the URL'
    result = capture('https://www.iaai.com/Search?c=123', 'POST', deepcopy(IAA))
    assert result['criteria'] == IAA
    assert result['entry_url'] == 'https://www.iaai.com/Search'
    assert result['creation_url'] is None  # Replay has not passed its live gate.
    assert any('2017' in label and '2027' in label for label in result['summary'])
    assert any('PLATINUM' in label for label in result['summary'])
    assert 'BidStatus: 6' in result['summary']  # No guessed human-readable enum.


def test_copart_capture_is_owned_by_its_adapter_not_iaa_shape():
    source = CopartSource()
    capture = getattr(source, 'capture_search_url', None)
    assert callable(capture), 'Copart has no validated selection capture'
    # No registry/isolation access in this public-format unit test.
    source._validated_url = lambda url: (url, __import__('urllib.parse', fromlist=['urlsplit']).urlsplit(url))
    result = capture('https://www.copart.com/lotSearchResults?searchCriteria=%7B%22query%22%3A%5B%22Audi%22%5D%7D&qId=discard')
    assert result['criteria'] == {'query': ['Audi']}
    assert 'qId' not in result['entry_url']
    assert result['creation_url'] == result['entry_url']


def captured(make='Cadillac'):
    body = deepcopy(IAA)
    body['Searches'][0]['Facets'][0]['Value'] = make
    return IAASource().capture_search_request('https://www.iaai.com/Search', 'POST', body)


def test_close_keeps_last_successful_filters_and_marks_an_unfinished_change():
    draft = SelectionDraft()
    draft.begin('first', captured())
    draft.finish('first', 200)
    draft.settle({'ready': True, 'total': 0, 'empty': True})
    assert draft.snapshot['criteria'] == IAA  # A valid empty result is selectable.
    draft.begin('unfinished', captured('Honda'))
    draft.close()
    assert draft.snapshot['criteria'] == IAA
    assert draft.status == 'CLOSED'
    assert draft.issue == 'LAST_CHANGE_UNCONFIRMED'
    assert not draft.can_use


def test_late_old_response_cannot_replace_the_latest_filter_change():
    draft = SelectionDraft()
    draft.begin('old', captured('Honda'))
    draft.begin('new', captured('Cadillac'))
    draft.finish('new', 200)
    draft.settle({'ready': True})
    draft.finish('old', 200)
    draft.settle({'ready': True})
    draft.close()
    assert draft.snapshot['criteria'] == IAA
    # The old callback might also repaint the site's DOM. Never claim that
    # the visible grid still confirms the newer request in that situation.
    assert not draft.can_use and draft.issue == 'LAST_CHANGE_UNCONFIRMED'


@pytest.mark.parametrize('dom', [{'ready': False}, {'ready': True, 'blocked': True}, {'ready': True, 'consent_required': True}])
def test_http_200_without_verified_results_does_not_save_a_selection(dom):
    draft = SelectionDraft()
    draft.begin('request', captured())
    draft.finish('request', 200)
    draft.settle(dom)
    draft.close()
    assert draft.snapshot is None and not draft.can_use


@pytest.mark.parametrize('dom,issue', [
    ({'ready': False}, 'SEARCH_RESULTS_NOT_READY'),
    ({'ready': True, 'blocked': True}, 'SITE_ACCESS_BLOCKED'),
    ({'ready': True, 'blocked': True, 'manual_required': True}, 'SITE_VERIFICATION_REQUIRED'),
    ({'ready': True, 'consent_required': True}, 'SITE_CONSENT_REQUIRED'),
])
def test_unavailable_confirmation_explains_which_boundary_is_waiting(dom, issue):
    draft = SelectionDraft()
    draft.begin('request', captured())
    draft.finish('request', 200)
    draft.settle(dom)
    assert not draft.can_use and draft.issue == issue
    draft.settle({'ready': True})
    assert draft.can_use and draft.issue is None


def test_no_request_is_not_reported_as_loading_forever():
    draft = SelectionDraft()
    draft.settle({'ready': True})
    assert not draft.can_use and draft.issue == 'NO_SEARCH_CAPTURED'


@pytest.mark.parametrize('status', [403, 429, 500, None])
def test_failed_search_never_promotes_a_candidate(status):
    draft = SelectionDraft()
    draft.begin('request', captured())
    draft.finish('request', status)
    draft.settle({'ready': True})
    draft.close()
    assert draft.snapshot is None and not draft.can_use


def test_confirm_requires_latest_completed_selection_not_just_any_previous_snapshot():
    draft = SelectionDraft()
    draft.begin('first', captured())
    draft.finish('first', 200)
    draft.settle({'ready': True})
    assert draft.can_use
    draft.begin('unknown', None)
    assert not draft.can_use
    draft.close()
    assert draft.snapshot is not None and draft.issue == 'LAST_CHANGE_UNCONFIRMED'


@pytest.mark.parametrize('extra', ['Authorization', 'csrf', 'UnknownFilter'])
def test_unknown_top_level_fields_are_rejected_not_saved_or_silently_dropped(extra):
    body = {**deepcopy(IAA), extra: 'do-not-store'}
    with pytest.raises(ValueError):
        IAASource().capture_search_request('https://www.iaai.com/Search', 'POST', body)


def test_unknown_nested_fields_and_invalid_ranges_are_rejected():
    body = deepcopy(IAA)
    body['Searches'][0]['Facets'][0]['accessToken'] = 'do-not-store'
    with pytest.raises(ValueError):
        IAASource().capture_search_request('https://www.iaai.com/Search', 'POST', body)
    body = deepcopy(IAA)
    body['Searches'][-1]['LongRanges'][0]['From'] = 2030
    with pytest.raises(ValueError):
        IAASource().capture_search_request('https://www.iaai.com/Search', 'POST', body)


def test_other_origin_and_method_cannot_be_captured_as_iaa_search():
    source = IAASource()
    assert source.capture_search_request('https://evil.example/Search', 'POST', deepcopy(IAA)) is None
    assert source.capture_search_request('https://www.iaai.com/Search', 'GET', deepcopy(IAA)) is None


def test_drafts_survive_browser_close_without_becoming_projects_and_are_owner_scoped(tmp_path, adapters):
    session = SelectionSession(tmp_path, 1, 'iaa')
    session.draft.begin('request', captured())
    session.draft.finish('request', 200)
    session.draft.settle({'ready': True})
    session.draft.close()
    session.persist()
    restored = load_draft(tmp_path, 1, 'iaa')
    assert restored['snapshot']['criteria'] == IAA
    assert restored['status'] == 'CLOSED' and restored['can_use']
    assert load_draft(tmp_path, 2, 'iaa') is None
    assert load_draft(tmp_path, 1, 'copart') is None
    manager = SearchSelections()
    assert manager.current(tmp_path, 1, 'iaa') == restored
    with pytest.raises(ValueError, match='selection_not_found'):
        manager.confirm(tmp_path, 2, 'iaa', session.id)


def test_process_restart_does_not_claim_an_open_selection_was_confirmed(tmp_path):
    session = SelectionSession(tmp_path, 1, 'iaa')
    session.draft.begin('request', captured())
    session.draft.finish('request', 200)
    session.draft.settle({'ready': True})
    session.persist()
    restored = load_draft(tmp_path, 1, 'iaa')
    assert restored['snapshot']['criteria'] == IAA
    assert not restored['can_use'] and restored['issue'] == 'SELECTION_INTERRUPTED'


def test_unrecognized_payload_and_credentials_are_not_written_even_during_an_edit(tmp_path):
    session = SelectionSession(tmp_path, 1, 'iaa')
    session.draft.begin('rejected', None)
    session.persist()
    text = (tmp_path / 'runtime/search-selections/1-iaa.json').read_text()
    assert json.loads(text)['snapshot'] is None
    assert 'Authorization' not in text and 'csrf' not in text


def test_symlink_draft_store_is_rejected(tmp_path):
    (tmp_path / 'runtime').mkdir()
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (tmp_path / 'runtime/search-selections').symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match='selection_path_unsafe'):
        save_draft(tmp_path, 1, 'iaa', {})
    assert not list(elsewhere.iterdir())


def test_reopening_selection_preserves_previous_filters_until_a_new_success(tmp_path, adapters):
    previous = SelectionSession(tmp_path, 1, 'iaa')
    previous.draft.begin('first', captured())
    previous.draft.finish('first', 200)
    previous.draft.settle({'ready': True})
    previous.draft.close()
    previous.persist()
    # No real browser in this coordinator test.
    adapters.monkeypatch.setattr('app.source_search_selection.Thread', lambda **_: SimpleNamespace(
        start=lambda: None, is_alive=lambda: True))
    manager = SearchSelections()
    result = manager.start(tmp_path, 1, 'iaa')
    assert result['snapshot']['criteria'] == IAA
    assert not result['can_use'] and result['status'] == 'SELECTING'
    active = manager.sessions[(str(tmp_path), 1, 'iaa')]
    active.draft.close()
    active.persist()
    result = load_draft(tmp_path, 1, 'iaa')
    assert result['snapshot']['criteria'] == IAA
    assert result['issue'] == 'LAST_CHANGE_UNCONFIRMED'


def test_browser_profile_is_reserved_across_visible_background_transitions(adapters):
    from app.sources.registry import get_source
    from app.sources.auction_lists import AuctionListError
    calls = []
    def launch(*_args, **options):
        calls.append(options)
        return SimpleNamespace(pages=[SimpleNamespace()], close=lambda: None)
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    source = get_source('iaa')
    _, session, _ = source.open_browser(playwright, headless=False, executable_path='/fixture/chromium', interactive=True)
    try:
        assert calls[0]['headless'] is False
        states = []
        session._window_state = states.append
        session.background()
        assert states == ['minimized']
        assert len(calls) == 1, 'Visibility must not relaunch the verified context'
        with pytest.raises(AuctionListError, match='browser is in use') as error:
            source.open_browser(playwright, headless=True, executable_path='/fixture/chromium')
        assert error.value.code == 'SOURCE_BROWSER_BUSY'
    finally:
        session.close()
    _, session, _ = source.open_browser(playwright, headless=True, executable_path='/fixture/chromium')
    session.close()
    session.close()  # Idempotent cleanup must not release a different session's lease.


def test_selection_requests_idle_profile_release_without_touching_active_browser(adapters):
    from app.sources.registry import get_source
    from app.sources.auction_lists import AuctionListError
    from test_auction_background_browser import runtime, snapshot, ROWS
    source, (_, session, _), _, calls, _, clock = runtime(adapters, 'iaa', [[snapshot('iaa', [ROWS['iaa'][0]], total=1)]])
    playwright = SimpleNamespace(chromium=session.chromium)
    try:
        assert session.reuse_requested()
        with pytest.raises(AuctionListError) as caught:
            source.open_browser(playwright, headless=False, executable_path='/test/chromium', interactive=True)
        assert caught.value.code == 'SOURCE_BROWSER_BUSY'
        assert not session.reuse_requested()
        assert len(calls) == 1, 'Only the worker may close its own Playwright context'
    finally:
        session.close()
    with pytest.raises(AuctionListError) as caught:
        source.open_browser(playwright, headless=True, executable_path='/test/chromium')
    assert caught.value.code == 'SOURCE_BROWSER_BUSY', 'Give the waiting selection first use of the profile'
    clock[0] += 2


def test_selection_waits_for_worker_to_release_idle_profile(tmp_path, adapters):
    from contextlib import contextmanager
    from app.sources.auction_lists import AuctionListError
    from app.sources.registry import get_source
    source = get_source('iaa')
    attempts, closed = [], []
    context = SimpleNamespace(close=lambda: closed.append(True))
    page = SimpleNamespace(is_closed=lambda: False)
    def open_browser(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise AuctionListError('SOURCE_BROWSER_BUSY', 'Idle worker context is yielding')
        return None, context, page
    @contextmanager
    def driver():
        yield object()
    manager = SearchSelections()
    session = SelectionSession(tmp_path, 1, 'iaa')
    adapters.monkeypatch.setattr(source, 'open_browser', open_browser)
    adapters.monkeypatch.setattr('app.desktop_runtime.desktop_playwright', driver)
    adapters.monkeypatch.setattr(manager, 'observe', lambda *_: session.draft.close())
    manager._run(session)
    assert len(attempts) == 2 and closed == [True]
    assert session.public()['status'] == 'CLOSED'


def test_capture_runtime_failure_is_reported_even_after_its_context_has_closed(tmp_path, adapters):
    from contextlib import contextmanager
    @contextmanager
    def playwright():
        yield object()
    actual = SimpleNamespace(is_closed=lambda: False)
    wrapper = SimpleNamespace(page=actual)
    def cleanup():
        wrapper.page = None
    source = SimpleNamespace(open_browser=lambda *_args, **_kwargs: (None, SimpleNamespace(close=cleanup), wrapper))
    adapters.monkeypatch.setattr('app.sources.registry.get_source', lambda _: source)
    adapters.monkeypatch.setattr('app.desktop_runtime.desktop_playwright', playwright)
    manager = SearchSelections()
    def fail(*_args):
        raise RuntimeError('private error text must not be saved')
    adapters.monkeypatch.setattr(manager, 'observe', fail)
    session = SelectionSession(tmp_path, 1, 'iaa')
    manager._run(session)
    assert wrapper.page is None
    result = load_draft(tmp_path, 1, 'iaa')
    assert result['status'] == 'FAILED' and result['issue'] == 'SOURCE_SELECTION_FAILED'
    assert 'private error' not in json.dumps(result)


def test_selection_api_is_owner_scoped_and_does_not_create_a_project(adapters):
    from fastapi.testclient import TestClient
    from app.auth import require_user, require_csrf
    from app.database import get_db
    from app.main import app
    from app.models import Project, ScanRun, User
    from sqlalchemy import select, func
    user_id = [adapters.user_id]
    def db_dependency():
        with adapters.factory() as db:
            yield db
    def user():
        with adapters.factory() as db:
            return db.get(User, user_id[0])
    previous = dict(app.dependency_overrides)
    app.dependency_overrides.update({get_db: db_dependency, require_user: user, require_csrf: user})
    try:
        with TestClient(app) as client:
            response = client.get('/api/source-search/iaa/selection')
            assert response.status_code == 200, response.text
            assert response.json() == {'selection': None}
            with adapters.factory() as db:
                assert db.scalar(select(func.count()).select_from(Project)) == 1
                assert db.scalar(select(func.count()).select_from(ScanRun)) == 0
            rejected = client.post('/api/projects', json={
                'name': 'Unconfirmed', 'source_key': 'copart',
                'search_url': 'https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D',
                'search_selection_id': 'f' * 32,
            })
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()['detail'] == 'search_selection_changed'
            from app.sources.registry import get_source
            picked = SelectionSession(adapters.paths['root'], user_id[0], 'copart')
            url = 'https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D'
            picked.draft.begin('url', get_source('copart').capture_search_url(url))
            picked.draft.finish('url', 200)
            picked.draft.settle({'ready': True})
            picked.draft.close()
            picked.persist()
            accepted = client.post('/api/projects', json={
                'name': 'Confirmed', 'source_key': 'copart',
                'search_url': picked.draft.snapshot['creation_url'], 'search_selection_id': picked.id,
            })
            assert accepted.status_code == 201, accepted.text
            with adapters.factory() as db:
                assert db.scalar(select(func.count()).select_from(ScanRun)) == 0
            changed = client.post('/api/projects', json={
                'name': 'Wrong criteria', 'source_key': 'copart',
                'search_url': 'https://www.copart.com/lotSearchResults?searchCriteria=%7B%22query%22%3A%5B%22Honda%22%5D%7D',
                'search_selection_id': picked.id,
            })
            assert changed.status_code == 409 and changed.json()['detail'] == 'search_selection_changed'
            adapters.settings.source_test_mode = False
            assert client.get('/api/source-search/iaa/selection').status_code == 409
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
