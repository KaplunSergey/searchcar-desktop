"""Native IAA URI plus frozen public conditions; never fall back to /Search."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest
from app.source_search_selection import SelectionSession
from app.sources.iaa import IAASource
from test_source_search_selection import IAA
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


URL = 'https://www.iaai.com/Search?url=public%2Bcriteria%3D'


def selected(source, criteria=IAA):
    snapshot = source.capture_search_request(source.selection_entry_url, 'POST', deepcopy(criteria))
    return source.complete_selection_snapshot(snapshot, {'native_search_uri': 'public%2Bcriteria%3D'})


def test_completed_capture_uses_the_native_iaa_uri_not_the_address_bar(adapters):
    source = IAASource()
    result = selected(source)
    assert result['creation_url'] == URL
    assert result['criteria'] == IAA
    assert source.validate_selection_snapshot(result) == result
    assert result.get('captured_at')
    assert source.complete_selection_snapshot(result, {'native_search_uri': 'public%2Bcriteria%3D'}) == result
    with pytest.raises(ValueError):
        source.validate_selection_snapshot({**result, 'captured_at': 'not-a-time'})
    assert source.complete_selection_snapshot(result, {})['creation_url'] is None
    with pytest.raises(ValueError):
        source.validate_selection_snapshot({**result, 'creation_url': 'https://evil.example/Search?url=x'})


def test_scope_comparison_keeps_all_filters_but_not_pagination_or_analytics():
    source = IAASource()
    restored = deepcopy(IAA)
    restored['CurrentPage'] = 3
    restored['PageSize'] = 25
    restored['Searches'].reverse()
    for item in restored['Searches']:
        if item['FullSearch'] == '': item['FullSearch'] = None
        for facet in item['Facets'] or []: facet['ForAnalytics'] = False
    assert source.criteria_scope(restored) == source.criteria_scope(IAA)
    restored['Searches'].append({'Facets': [{'Group': 'Default', 'Value': 'True'}], 'FullSearch': None, 'LongRanges': None})
    assert source.criteria_scope(restored) == source.criteria_scope(IAA)
    for key, value in [('ShowRecommendations', True), ('miles', 25), ('ZipCode', '90210')]:
        assert source.criteria_scope({**restored, key: value}) != source.criteria_scope(IAA)
    for path in ['Searches', 'Sort', 'SaleStatusFilters', 'BidStatusFilters']:
        assert source.criteria_scope({**restored, path: []}) != source.criteria_scope(IAA)
    with pytest.raises(ValueError):
        source.criteria_scope({**restored, 'UnknownFilter': 'not silently discarded'})


def test_creation_persists_conditions_and_queue_freezes_them(adapters):
    from app.main import create_project, scan_projects
    from app.schemas import ProjectIn, ScanProjectsIn
    from app.models import Project, ScanRun, User
    from app.worker import _project_for_execution
    source = IAASource()
    snapshot = selected(source)
    draft = SelectionSession(adapters.paths['root'], adapters.user_id, 'iaa')
    draft.draft.begin('request', snapshot)
    draft.draft.finish('request', 200)
    draft.draft.settle({'ready': True})
    draft.draft.close()
    draft.persist()
    with adapters.factory() as db:
        user = db.get(User, adapters.user_id)
        result = create_project(ProjectIn(name='Saved IAA', source_key='iaa', search_url=URL,
                                search_selection_id=draft.id), current=user, db=db)
        assert result['search_config'] == snapshot
        project = db.get(Project, result['id'])
        queued = scan_projects(ScanProjectsIn(project_ids=[project.id]), current=user, db=db)
        run = db.get(ScanRun, queued['id'])
        assert run.payload['project_plans'][0]['search_config'] == snapshot
        project.search_config = {**snapshot, 'summary': ['changed after enqueue']}
        db.commit()
        assert _project_for_execution(project, run.payload).search_config == snapshot
        db.expire_all()
        assert db.get(Project, project.id).search_config['summary'] == ['changed after enqueue']


def test_search_config_has_an_additive_sqlite_migration(tmp_path):
    from app.database import create_database_engine
    from app.sqlite_migrations import migrate_sqlite, MIGRATIONS
    from sqlalchemy import inspect
    engine = create_database_engine(f'sqlite+pysqlite:///{tmp_path / "migration.sqlite3"}')
    assert migrate_sqlite(engine) == MIGRATIONS[-1].version
    assert 'search_config' in {column['name'] for column in inspect(engine).get_columns('projects')}
    assert migrate_sqlite(engine) == MIGRATIONS[-1].version
    engine.dispose()


def test_distinct_presets_can_share_a_native_uri_but_pagination_cannot_duplicate_a_preset(adapters):
    from fastapi import HTTPException
    from app.main import create_project
    from app.schemas import ProjectIn
    from app.models import User
    source = IAASource()
    def create(name, criteria, uri='public%2Bcriteria%3D'):
        snapshot = source.complete_selection_snapshot(source.capture_search_request(source.selection_entry_url, 'POST', criteria), {'native_search_uri': uri})
        draft = SelectionSession(adapters.paths['root'], adapters.user_id, 'iaa')
        draft.draft.begin('request', snapshot); draft.draft.finish('request', 200)
        draft.draft.settle({'ready': True}); draft.draft.close(); draft.persist()
        with adapters.factory() as db:
            return create_project(ProjectIn(name=name, source_key='iaa', search_url=snapshot['creation_url'], search_selection_id=draft.id), current=db.get(User, adapters.user_id), db=db)
    first = create('A', deepcopy(IAA))
    changed = deepcopy(IAA); changed['Sort'][0]['IsDescending'] = True
    second = create('B', changed)
    assert second['id'] != first['id'] and second['search_url'] == first['search_url']
    paging = deepcopy(IAA); paging['PageSize'] = 25; paging['CurrentPage'] = 3
    with pytest.raises(HTTPException) as caught:
        create('Duplicate', paging, 'aDifferentNativeCursor')
    assert caught.value.status_code == 409


def test_worker_passes_the_frozen_preset_and_never_excludes_lots_for_a_newer_config(adapters):
    from app.main import scan_projects
    from app.schemas import ScanProjectsIn
    from app.models import Project, ProjectCar, User, ScanRun
    from app.sources.registry import get_source
    from app.sources.contracts import SearchCollection
    from app.auction_storage import store_auction_row
    from app.worker import process_job
    from test_parallel_project_scan import _fake_playwright
    from test_auction_list_adapters import ROWS
    from sqlalchemy import select
    source = get_source('iaa')
    config = selected(source)
    with adapters.factory() as db:
        project = Project(owner_id=adapters.user_id, source_key='iaa', name='Frozen', name_key='frozen', search_url=URL,
                          search_config=config, search_identity=source.search_identity(config), search_page_mode='ALL_PAGES')
        db.add(project); db.flush(); project_id = project.id
        for row in ROWS['iaa'][:2]: store_auction_row(db, project, row, scan_id=1, complete=True)
        db.commit()
        run = scan_projects(ScanProjectsIn(project_ids=[project_id]), current=db.get(User, adapters.user_id), db=db)
    adapters.monkeypatch.setattr('app.worker.desktop_playwright', _fake_playwright)
    adapters.monkeypatch.setattr(source, 'open_browser', lambda *a, **k: (None, SimpleNamespace(close=lambda: None), object()))
    def collect(*args, **kwargs):
        assert kwargs['search_config'] == config
        with adapters.factory() as db:
            stored = db.get(Project, project_id)
            changed = deepcopy(config); changed['criteria']['Searches'][0]['Facets'][0]['Value'] = 'Honda'
            stored.search_config = changed
            stored.search_identity = source.search_identity(changed)
            db.commit()
        row = ROWS['iaa'][0]
        return SearchCollection({row['source_car_id']: row}, 1, 1, 1)
    adapters.monkeypatch.setattr(source, 'collect_search_pages', collect)
    process_job(run['id'])
    with adapters.factory() as db:
        assert db.get(ScanRun, run['id']).status == 'SUCCEEDED'
        assert all(item.search_status == 'FOUND' for item in db.scalars(select(ProjectCar).where(ProjectCar.project_id == project_id)))


@pytest.mark.skipif(__import__('os').environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in bundled browser fixture')
@pytest.mark.parametrize('action', ['close', 'confirm'])
def test_ordinary_selection_captures_the_rendered_native_uri_before_window_close(tmp_path, adapters, action):
    from pathlib import Path
    from app.desktop_runtime import desktop_playwright, configure_playwright_environment, configure_playwright_driver, bundled_headless_chromium
    from app.source_search_selection import SearchSelections, load_draft
    root = Path(__file__).resolve().parents[2]
    configure_playwright_environment(root / 'desktop/runtime/browsers')
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    html = '''<!doctype html><body><div>0 VEHICLES <span id="ResultFoundOnPage">No vehicles</span></div>
      <div id="ListingGrid"></div><div id="ResultHeaderContainer"><input name="RequestUri" value=""></div>
      <script>setTimeout(()=>fetch('/Search?c=fixture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(BODY)})
        .then(r=>r.text()).then(()=>document.querySelector('[name="RequestUri"]').value='public%2Bcriteria%3D'),50)</script>'''.replace('BODY', json.dumps(IAA))
    with desktop_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=str(bundled_headless_chromium(root / 'desktop/runtime/browsers')))
        page = browser.new_page()
        page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body='ok' if route.request.method == 'POST' else html))
        draft = SelectionSession(tmp_path, 1, 'iaa')
        polls = [0]
        class OwnedWindow:
            def __getattr__(self, name): return getattr(page, name)
            def wait_for_timeout(self, ms):
                page.wait_for_timeout(ms); polls[0] += 1
                if draft.draft.can_use and draft.draft.snapshot.get('creation_url'):
                    if action == 'close': page.close()
                    else: page.locator('#searchcar-selection-panel').get_by_role('button', name='Использовать выбранные фильтры', exact=True).click()
                elif polls[0] > 10: page.close()
        try:
            SearchSelections.observe(draft, IAASource(), OwnedWindow())
            draft.persist()
            result = load_draft(tmp_path, 1, 'iaa')
            assert result['status'] == 'CLOSED' and result['can_use']
            assert result['snapshot']['criteria'] == IAA
            assert result['snapshot']['creation_url'] == URL
            assert SearchSelections().current(tmp_path, 1, 'iaa') == result
        finally:
            browser.close()


@pytest.mark.skipif(__import__('os').environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in bundled browser fixture')
@pytest.mark.parametrize('case', ['success', 'uninitialized', 'cursor', 'changed', 'empty', 'empty-no-pager', 'unknown', 'http-failure', 'rate-limited', 'size-ignored', 'unsupported-sort', 'cancel'])
def test_native_restore_and_each_page_verify_the_actual_conditions(tmp_path, adapters, case):
    from pathlib import Path
    from app.desktop_runtime import desktop_playwright, configure_playwright_environment, configure_playwright_driver, bundled_headless_chromium
    from app.sources.auction_lists import AuctionListError
    root = Path(__file__).resolve().parents[2]
    configure_playwright_environment(root / 'desktop/runtime/browsers')
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    criteria = deepcopy(IAA)
    criteria['Sort'][0]['SortField'] = 'UnknownSort' if case == 'unsupported-sort' else 'AuctionDateTime'
    criteria['PageSize'] = 25
    source = IAASource()
    if case in ('uninitialized', 'cursor', 'empty-no-pager'): source.read_timeout_seconds = 1
    config = selected(source, criteria)
    html = '''<!doctype html><html><body>
      <div class="table"><div><span id="ResultFoundOnPage"></span></div><div id="ListingGrid"></div></div>
      <select id="ddlCity"><option selected>New York</option></select>
      <div id="ResultHeaderContainer"><input name="RequestUri" value="public%2Bcriteria%3D"></div>
      <script type="application/json" id="Sorts">[{"Id":"native-date-sort","Request":{"IsGeoSort":false,"SortField":"AuctionDateTime","IsDescending":false}}]</script>
      <script type="application/json" id="SortFields">[{"IsGeoSort":false,"SortField":"TenantSortOrder","IsDescending":false}]</script>
      <a id="native-date-sort" data-bind="click: $root.SortResults">Date</a>
      <a id="native-size" data-bind="click: $root.UpdatePageSize.bind($root, '25')">25</a>
      <span id="pageSizeHeader">100</span><div id="divPagination"><button id="PageNumber1" class="btn-page active">1</button><button aria-label="Next">Next</button></div>
      <script>
      let query=CRITERIA,mode=CASE;query.Sort=[{IsGeoSort:false,SortField:'TenantSortOrder',IsDescending:false}];query.PageSize=100;if(['cursor','empty-no-pager'].includes(mode))query.CurrentPage=3;
      function render(){const total=mode.startsWith('empty')?0:50;const start=(query.CurrentPage-1)*query.PageSize;
        document.querySelector('#ResultFoundOnPage').textContent=total?total+' VEHICLES':'0 VEHICLES No vehicles';
        document.querySelector('#ListingGrid').innerHTML=Array.from({length:Math.min(query.PageSize,Math.max(0,total-start))},(_,i)=>'<div class="table-row"><h4><a href="https://www.iaai.com/VehicleDetail/'+(10000+start+i)+'~US">Fixture</a></h4><span class="data-list__value--action">Fri Oct 16, 12pm EDT</span></div>').join('');
        document.querySelector('#pageSizeHeader').textContent=query.PageSize;
        const first=document.querySelector('.btn-page');if(first){first.classList.add('active');first.textContent=query.CurrentPage}
        document.querySelector('[aria-label="Next"]').disabled=query.CurrentPage*query.PageSize>=total;
        document.querySelector('#SortFields').textContent=JSON.stringify(query.Sort);
        document.querySelector('.table').classList.remove('is-loading');}
      async function search(){document.querySelector('.table').classList.add('is-loading');
        if(mode==='changed'&&query.CurrentPage===2)query.Searches[0].Facets[0].Value='Honda';
        if(mode==='unknown')query.UnknownFilter='not silently discarded';
        const response=await fetch('/Search?c=fixture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(query)});
        if(response.ok){await response.text();render();}}
      document.querySelector('#native-date-sort').onclick=()=>{query.Sort=[{IsGeoSort:false,SortField:'AuctionDateTime',IsDescending:false}];search()};
      document.querySelector('#native-size').onclick=()=>{if(mode!=='size-ignored')query.PageSize=25;search()};
      document.querySelector('#PageNumber1').onclick=()=>{query.CurrentPage=1;search()};
      document.querySelector('[aria-label="Next"]').onclick=()=>{query.CurrentPage++;search()};render();
      if(mode==='uninitialized'){Array.from(document.querySelector('#ListingGrid').children).slice(25).forEach(e=>e.remove());document.querySelector('.btn-page').classList.remove('active')}
      if(mode==='empty-no-pager')document.querySelector('#PageNumber1').remove();
      </script></body></html>'''.replace('CRITERIA', json.dumps(criteria)).replace('CASE', json.dumps(case))
    with desktop_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=str(bundled_headless_chromium(root / 'desktop/runtime/browsers')))
        page = browser.new_page()
        requests = []
        def site(route):
            if route.request.method == 'POST':
                requests.append(route.request.post_data_json)
                route.fulfill(status=500 if case == 'http-failure' else 429 if case == 'rate-limited' else 200, content_type='text/html', body='ok')
            else:
                route.fulfill(status=200, content_type='text/html', body=html)
        page.route('**/*', site)
        checks = [0]
        def checkpoint():
            checks[0] += 1
            if case == 'cancel' and requests: raise RuntimeError('cancelled by user')
        try:
            if case in ('success', 'uninitialized', 'cursor', 'empty', 'empty-no-pager'):
                collection = source.collect_search_pages(page, URL, all_pages=True, search_config=config, checkpoint=checkpoint)
                assert len(collection.rows) == (0 if case.startswith('empty') else 50)
                assert collection.pages_visited == (1 if case.startswith('empty') else 2)
                assert requests and all(source.criteria_scope(item) == source.criteria_scope(criteria) for item in requests[1:])
                assert requests[0]['CurrentPage'] == (3 if case == 'empty-no-pager' else 1)
            else:
                with pytest.raises((AuctionListError, RuntimeError)) as caught:
                    source.collect_search_pages(page, URL, all_pages=True, search_config=config, checkpoint=checkpoint)
                if case == 'changed':
                    assert caught.value.code == 'SOURCE_SEARCH_SCOPE_CHANGED'
                    assert len(caught.value.partial_collection.rows) == 25
                else:
                    assert not caught.value.partial_collection.rows
                if case == 'cancel': assert 'cancelled by user' in str(caught.value)
                if case == 'rate-limited': assert caught.value.code == 'SOURCE_RATE_LIMITED'
            assert not page.is_closed()  # Collector cleans up listeners, not another owner's browser.
        finally:
            browser.close()
