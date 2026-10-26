"""A creation draft belongs to one prospective project, not the whole site."""
from copy import deepcopy
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

from app.source_search_selection import SelectionSession, SearchSelections, load_draft
from app.sources.registry import get_source
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


def picked(root, source, make, draft_id):
    session = SelectionSession(root, 1, source.key, draft_id=draft_id)
    url = source.selection_entry_url.replace('/vehicleFinder', '/lotSearchResults') + '?' + urlencode({
        'searchCriteria': json.dumps({'query': [make]})})
    session.draft.begin('url', source.capture_search_url(url))
    session.draft.finish('url', 200)
    session.draft.settle({'ready': True})
    session.draft.close()
    session.persist()
    return session


def test_two_project_drafts_never_read_or_confirm_each_other(tmp_path, adapters):
    source = get_source('copart')
    first = picked(tmp_path, source, 'Dodge', 'a' * 32)
    manager = SearchSelections()
    assert manager.current(tmp_path, 1, 'copart', draft_id='b' * 32) is None
    assert manager.current(tmp_path, 1, 'copart') is None
    second = picked(tmp_path, source, 'Nissan', 'b' * 32)
    assert manager.current(tmp_path, 1, 'copart', draft_id='a' * 32)['snapshot'] == first.draft.snapshot
    assert load_draft(tmp_path, 1, 'copart', draft_id='b' * 32)['snapshot'] == second.draft.snapshot
    with pytest.raises(ValueError, match='selection_not_found'):
        manager.confirm(tmp_path, 1, 'copart', first.id, draft_id='b' * 32)


def test_fresh_draft_does_not_inherit_legacy_site_snapshot(tmp_path, adapters):
    source = get_source('copart')
    picked(tmp_path, source, 'Dodge', None)
    adapters.monkeypatch.setattr('app.source_search_selection.Thread', lambda **_: SimpleNamespace(
        start=lambda: None, is_alive=lambda: True))
    manager = SearchSelections()
    fresh = manager.start(tmp_path, 1, 'copart', draft_id='c' * 32)
    assert fresh['snapshot'] is None and not fresh['can_use']
    assert fresh['draft_id'] == 'c' * 32
    assert manager.current(tmp_path, 1, 'copart')['snapshot']['criteria']['query'] == ['Dodge']


def test_project_copies_only_its_own_draft_and_keeps_it_when_another_is_created(adapters):
    from app.main import create_project
    from app.models import Project, User
    from app.schemas import ProjectIn
    source = get_source('copart')
    first = picked(adapters.paths['root'], source, 'Dodge', 'a' * 32)
    second = picked(adapters.paths['root'], source, 'Nissan', 'b' * 32)
    def body(name, session, draft_id):
        return ProjectIn(name=name, source_key='copart', search_url=session.draft.snapshot['creation_url'],
                         search_selection_id=session.id, search_draft_id=draft_id)
    with adapters.factory() as db:
        user = db.get(User, adapters.user_id)
        a = create_project(body('Dodge', first, 'a' * 32), current=user, db=db)
        frozen = deepcopy(a['search_config'])
        with pytest.raises(HTTPException) as caught:
            create_project(body('Wrong draft', first, 'b' * 32), current=user, db=db)
        assert caught.value.detail == 'search_selection_changed'
        b = create_project(body('Nissan', second, 'b' * 32), current=user, db=db)
        assert b['search_config']['criteria']['query'] == ['Nissan']
        db.expire_all()
        assert db.get(Project, a['id']).search_config == frozen
        assert frozen['criteria']['query'] == ['Dodge']


@pytest.mark.parametrize('draft_id', ['../escape', 'A' * 32, 'a' * 31, ''])
def test_draft_scope_is_validated_before_filesystem_access(tmp_path, draft_id):
    with pytest.raises(ValueError, match='selection_identity_invalid'):
        load_draft(tmp_path, 1, 'copart', draft_id=draft_id)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('flag', [False, True])
def test_copart_native_frontier_link_keeps_the_public_vanity_keyword_flag(adapters, flag):
    criteria = {'query': ['*'], 'filter': {'MAKE': ['search_lot_make_desc:"NISSAN"'],
        'MODL': ['lot_model_desc:"FRONTIER"'], 'YEAR': ['lot_year:[2026 TO 2027]']},
        'watchListOnly': False, 'searchName': '', 'freeFormSearch': False, 'vanityKeyword': flag}
    url = 'https://www.copart.com/lotSearchResults?' + urlencode({'free': 'false', 'searchCriteria': json.dumps(criteria)})
    source = get_source('copart')
    snapshot = source.capture_search_url(url)
    assert snapshot['criteria'] == criteria
    assert source.capture_search_url(snapshot['creation_url']) == snapshot


def test_user_frontier_url_encoding_keeps_filters_but_not_tracking(adapters):
    url = 'https://www.copart.com/lotSearchResults?searchCriteria=%7B%22query%22:%5B%22*%22%5D,%22filter%22:%7B%22MAKE%22:%5B%22search_lot_make_desc:%5C%22NISSAN%5C%22%22%5D,%22MODL%22:%5B%22lot_model_desc:%5C%22FRONTIER%5C%22%22%5D,%22YEAR%22:%5B%22lot_year:%5B2026%20TO%202027%5D%22%5D%7D,%22watchListOnly%22:false,%22searchName%22:%22%22,%22freeFormSearch%22:false,%22vanityKeyword%22:false%7D&free=false&qId=fixture-tracking'
    source = get_source('copart')
    snapshot = source.capture_search_url(url)
    assert snapshot['criteria']['vanityKeyword'] is False
    assert snapshot['criteria']['filter'] == {'MAKE': ['search_lot_make_desc:"NISSAN"'],
        'MODL': ['lot_model_desc:"FRONTIER"'], 'YEAR': ['lot_year:[2026 TO 2027]']}
    assert 'qId' not in snapshot['creation_url']
    assert 'free=false' in snapshot['creation_url']
    assert source.capture_search_url(snapshot['creation_url']) == snapshot


def test_selection_http_api_requires_the_requested_creation_scope(adapters):
    from fastapi.testclient import TestClient
    from app.auth import require_user, require_csrf
    from app.database import get_db
    from app.main import app
    from app.models import User
    source = get_source('copart')
    first = picked(adapters.paths['root'], source, 'Dodge', 'a' * 32)
    def database():
        with adapters.factory() as db: yield db
    def user():
        with adapters.factory() as db: return db.get(User, adapters.user_id)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides.update({get_db: database, require_user: user, require_csrf: user})
    adapters.monkeypatch.setattr('app.main.search_selections', SearchSelections())
    adapters.monkeypatch.setattr('app.source_search_selection.Thread', lambda **_: SimpleNamespace(
        start=lambda: None, is_alive=lambda: True))
    try:
        with TestClient(app) as client:
            path = '/api/source-search/copart/selection'
            assert client.get(path).json() == {'selection': None}
            assert client.get(path, params={'draft_id': 'a' * 32}).json()['selection']['id'] == first.id
            assert client.get(path, params={'draft_id': 'b' * 32}).json() == {'selection': None}
            assert client.get(path, params={'draft_id': '../escape'}).status_code == 422
            opened = client.post(path, json={'action': 'OPEN', 'draft_id': 'b' * 32})
            assert opened.status_code == 200, opened.text
            assert opened.json()['selection']['snapshot'] is None
            assert opened.json()['selection']['draft_id'] == 'b' * 32
            rejected = client.post(path, json={'action': 'CONFIRM', 'draft_id': 'b' * 32, 'selection_id': first.id})
            assert rejected.status_code == 409
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
