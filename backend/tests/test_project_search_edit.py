"""Editing captured conditions keeps the project and its local history."""
from copy import deepcopy
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import require_csrf, require_user
from app.database import get_db
from app.main import app, create_project, scan_projects
from app.models import Car, Project, ProjectCar, ScanRun, User
from app.schemas import ProjectIn, ScanProjectsIn
from app.source_search_selection import SelectionSession, SearchSelections, save_draft
from app.sources.registry import get_source
from test_auction_list_adapters import adapters
from test_source_search_selection import IAA
from test_source_test_profile import isolated_environment, profile_queue


def snapshot(key, make):
    source = get_source(key)
    if key == 'copart':
        return source.capture_search_url('https://www.copart.com/lotSearchResults?' + urlencode({'searchCriteria': json.dumps({'query': [make]})}))
    criteria = deepcopy(IAA)
    criteria['Searches'] = [{'Facets': [{'Group': 'Make', 'Value': make}], 'FullSearch': None, 'LongRanges': None}]
    return source.complete_selection_snapshot(source.capture_search_request(source.selection_entry_url, 'POST', criteria), {'native_search_uri': make})


def draft(adapters, key, make, token, project_id=None):
    session = SelectionSession(adapters.paths['root'], adapters.user_id, key, draft_id=token * 32)
    session.draft.begin('fixture', snapshot(key, make)); session.draft.finish('fixture', 200)
    session.draft.settle({'ready': True}); session.draft.close()
    value = session.public()
    if project_id is not None:
        value['project_id'] = project_id
    save_draft(session.root, session.owner, session.source, value)
    return session


@pytest.fixture
def editor(adapters):
    def database():
        with adapters.factory() as db: yield db
    def user():
        with adapters.factory() as db: return db.get(User, adapters.user_id)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides.update({get_db: database, require_user: user, require_csrf: user})
    adapters.monkeypatch.setattr('app.main.search_selections', SearchSelections())
    adapters.monkeypatch.setattr('app.desktop_license.require_search_entitlement', lambda *_: None)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(previous)


def project(adapters, key, make='Audi', token='a'):
    selected = draft(adapters, key, make, token)
    with adapters.factory() as db:
        return create_project(ProjectIn(name=make, source_key=key, search_url=selected.draft.snapshot['creation_url'],
            search_selection_id=selected.id, search_draft_id=token * 32), db.get(User, adapters.user_id), db)


@pytest.mark.parametrize('key', ['copart', 'iaa'])
def test_edit_saves_only_confirmed_project_draft_and_next_job_freezes_new_filters(adapters, editor, key):
    original = project(adapters, key)
    other = project(adapters, key, 'Honda', 'b')
    with adapters.factory() as db:
        car = Car(source_key=key, source_listing_id='123~US' if key == 'iaa' else '123', url='https://fixture.test/123', title='Kept history')
        db.add(car); db.flush()
        db.add(ProjectCar(project_id=original['id'], car_id=car.id, favorite=True, tracking_enabled=False, search_status='REMOVED_FROM_PROJECT'))
        db.commit()
    selected = draft(adapters, key, 'BMW', 'c', original['id'])
    response = editor.post(f"/api/projects/{original['id']}/search-selection", json={
        'selection_id': selected.id, 'draft_id': 'c' * 32, 'expected_revision': original['search_revision']})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['id'] == original['id'] and value['name'] == original['name']
    assert value['search_config'] == selected.draft.snapshot
    assert value['search_revision'] == original['search_revision'] + 1 and value['last_refreshed_at'] is None
    with adapters.factory() as db:
        assert db.get(Project, other['id']).search_config == other['search_config']
        relation = db.scalar(select(ProjectCar).where(ProjectCar.project_id == original['id']))
        assert relation.favorite and not relation.tracking_enabled and relation.search_status == 'REMOVED_FROM_PROJECT'
        assert db.get(Car, relation.car_id).title == 'Kept history'
        assert not db.scalars(select(ScanRun)).all(), 'Saving conditions must not silently enqueue a duplicate job'
        run = scan_projects(ScanProjectsIn(project_ids=[original['id']]), db.get(User, adapters.user_id), db)
        assert db.get(ScanRun, run['id']).payload['project_plans'][0]['search_config'] == value['search_config']


@pytest.mark.parametrize('key', ['copart', 'iaa'])
@pytest.mark.parametrize('failure', ['stale', 'other_project', 'unconfirmed', 'active', 'duplicate', 'foreign'])
def test_rejected_edit_does_not_change_project_or_queued_conditions(adapters, editor, key, failure):
    original = project(adapters, key)
    other = project(adapters, key, 'Honda', 'b')
    selected = draft(adapters, key, 'Honda' if failure == 'duplicate' else 'BMW', 'c', other['id'] if failure == 'other_project' else original['id'])
    if failure == 'unconfirmed':
        value = selected.public(); value.update(project_id=original['id'], can_use=False, issue='LAST_CHANGE_UNCONFIRMED')
        save_draft(selected.root, selected.owner, key, value)
    with adapters.factory() as db:
        if failure == 'active':
            queued = scan_projects(ScanProjectsIn(project_ids=[original['id']]), db.get(User, adapters.user_id), db)
        if failure == 'foreign':
            user = User(username='foreign', username_key='foreign', password_hash='test'); db.add(user); db.flush()
            db.get(Project, original['id']).owner_id = user.id; db.commit()
    response = editor.post(f"/api/projects/{original['id']}/search-selection", json={
        'selection_id': selected.id, 'draft_id': 'c' * 32, 'expected_revision': 99 if failure == 'stale' else original['search_revision']})
    assert response.status_code == (404 if failure == 'foreign' else 409), response.text
    assert response.json()['detail'] == {
        'foreign': 'project_not_found', 'active': 'project_search_active', 'stale': 'project_search_changed',
        'other_project': 'search_selection_changed', 'unconfirmed': 'search_selection_changed',
        'duplicate': 'project_name_or_url_exists',
    }[failure]
    with adapters.factory() as db:
        assert db.get(Project, original['id']).search_config == original['search_config']
        assert db.get(Project, original['id']).price_filter_revision == original['search_revision']
        if failure == 'active':
            assert db.get(ScanRun, queued['id']).payload['project_plans'][0]['search_config'] == original['search_config']


@pytest.mark.parametrize('key', ['copart', 'iaa'])
def test_open_edit_starts_from_owned_project_not_previous_site_filters(adapters, editor, key):
    original = project(adapters, key)
    adapters.monkeypatch.setattr('app.source_search_selection.Thread', lambda **_: SimpleNamespace(start=lambda: None, is_alive=lambda: True))
    response = editor.post(f'/api/source-search/{key}/selection', json={'action': 'OPEN', 'draft_id': 'd' * 32, 'project_id': original['id']})
    assert response.status_code == 200, response.text
    selection = response.json()['selection']
    assert selection['project_id'] == original['id']
    assert selection['snapshot'] == original['search_config']
    assert not selection['can_use'], 'Opening a saved search is not a new successful confirmation'


def test_edit_requires_real_authentication_and_csrf_before_mutation(adapters, editor):
    from starlette.requests import Request
    from app.auth import create_session, settings as auth_settings
    original = project(adapters, 'copart')
    selected = draft(adapters, 'copart', 'BMW', 'c', original['id'])
    body = {'selection_id': selected.id, 'draft_id': 'c' * 32, 'expected_revision': original['search_revision']}
    path = f"/api/projects/{original['id']}/search-selection"
    overridden = app.dependency_overrides.pop(require_csrf)
    try:
        assert editor.post(path, json=body).status_code == 401
        with adapters.factory() as db:
            token, csrf, _ = create_session(db, db.get(User, adapters.user_id), Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)}))
            db.commit()
        editor.cookies.set(auth_settings.auth_cookie_name, token)
        assert editor.post(path, json=body).status_code == 403
        assert editor.post(path, json=body, headers={'x-csrf-token': csrf, 'origin': 'https://evil.test'}).status_code == 403
        response = editor.post(path, json=body, headers={'x-csrf-token': csrf})
        assert response.status_code == 200, response.text
    finally:
        app.dependency_overrides[require_csrf] = overridden


def test_cancel_selection_does_not_need_search_entitlement_or_modify_the_project(adapters, editor):
    original = project(adapters, 'iaa')
    adapters.monkeypatch.setattr('app.source_search_selection.Thread', lambda **_: SimpleNamespace(start=lambda: None, is_alive=lambda: True))
    selected = editor.post('/api/source-search/iaa/selection', json={'action': 'OPEN', 'draft_id': 'e' * 32, 'project_id': original['id']}).json()['selection']
    from app.desktop_license import SearchEntitlementError
    def denied(*_):
        raise SearchEntitlementError('LICENSE_SOURCE_NOT_ALLOWED')
    adapters.monkeypatch.setattr('app.desktop_license.require_search_entitlement', denied)
    response = editor.post('/api/source-search/iaa/selection', json={
        'action': 'CANCEL', 'draft_id': 'e' * 32, 'project_id': original['id'], 'selection_id': selected['id']})
    assert response.status_code == 200, response.text
    from app.main import search_selections
    session = next(iter(search_selections.sessions.values()))
    assert session.stop.is_set()
    with adapters.factory() as db:
        assert db.get(Project, original['id']).search_config == original['search_config']
        assert not db.scalars(select(ScanRun)).all()


def test_edit_rejects_revoked_search_access_without_changing_saved_conditions(adapters, editor):
    from app.desktop_license import SearchEntitlementError
    original = project(adapters, 'copart')
    selected = draft(adapters, 'copart', 'BMW', 'c', original['id'])
    def denied(*_):
        raise SearchEntitlementError('LICENSE_SOURCE_NOT_ALLOWED')
    adapters.monkeypatch.setattr('app.desktop_license.require_search_entitlement', denied)
    response = editor.post(f"/api/projects/{original['id']}/search-selection", json={
        'selection_id': selected.id, 'draft_id': 'c' * 32, 'expected_revision': original['search_revision']})
    assert response.status_code == 402
    with adapters.factory() as db:
        assert db.get(Project, original['id']).search_config == original['search_config']
