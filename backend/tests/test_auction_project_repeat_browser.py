"""Saved draft -> project -> queued worker -> browser -> DB, with local responses.

These are repeat/isolation regressions, not evidence of live-site access.
"""
from copy import deepcopy
from contextlib import contextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from sqlalchemy import select

from app.browser_runtime_pool import BrowserRuntimePool
from app.desktop_runtime import desktop_playwright, configure_playwright_driver
from app.main import create_project, scan_projects
from app.models import Car, Project, ProjectCar, ScanRun, User
from app.schemas import ProjectIn, ScanProjectsIn
from app.source_search_selection import SelectionSession
from app.sources.registry import get_source
from app.worker import process_job
from test_auction_list_adapters import adapters
from test_source_search_selection import IAA
from test_source_test_profile import isolated_environment, profile_queue


def iaa_page(criteria, make):
    return '''<div><span id="ResultFoundOnPage">26 VEHICLES</span></div>
      <select id="ddlCity"><option selected>Chicago</option></select>
      <div id="ListingGrid"></div><span id="pageSizeHeader">25</span>
      <div id="divPagination"><button id="PageNumber1" class="btn-page active">1</button><button id="next" aria-label="Next">Next</button></div>
      <script type="application/json" id="SortFields">SORT</script><script type="application/json" id="Sorts">[]</script>
      <a id="size" data-bind="click: $root.UpdatePageSize">25</a>
      <script>
      let query=CRITERIA,make=MAKE;window.previousScope=localStorage.getItem('scope');localStorage.setItem('scope',make);
      function render(){const start=(query.CurrentPage-1)*25,base=make==='Audi'?10000:20000;
        document.querySelector('#ListingGrid').innerHTML=Array.from({length:Math.min(25,26-start)},(_,i)=>
          '<div class="table-row"><h4><a href="/VehicleDetail/'+(base+start+i+1)+'~US">'+make+' fixture</a></h4><a href="/SalesList/100~US/10122026">Sale</a><span class="data-list__value--action">Mon Oct 12, 8:30am CDT</span></div>').join('');
        document.querySelector('.btn-page').textContent=query.CurrentPage;document.querySelector('#next').disabled=query.CurrentPage===2;}
      async function search(){document.querySelector('#ListingGrid').setAttribute('aria-busy','true');
        await fetch('/Search?c=fixture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(query)});
        render();document.querySelector('#ListingGrid').removeAttribute('aria-busy');}
      document.querySelector('#PageNumber1').onclick=()=>{query.CurrentPage=1;search()};
      document.querySelector('#size').onclick=search;
      document.querySelector('#next').onclick=()=>{query.CurrentPage++;search()};render();
      </script>'''.replace('CRITERIA', json.dumps(criteria)).replace('SORT', json.dumps(criteria['Sort'])).replace('MAKE', json.dumps(make))


def copart_page(criteria, make):
    return '''<table id="serverSideDataTable"><tbody></tbody></table>
      <div id="serverSideDataTable_info"></div><div id="serverSideDataTable_paginate"><li class="active"><a>1</a></li></div>
      <li id="serverSideDataTable_next"><a href="#">Next</a></li>
      <script>
      let params=CRITERIA,make=MAKE,page=1,pinned=null;
      window.previousScope=localStorage.getItem('scope');localStorage.setItem('scope',make);
      function render(){const current=params.query[0],base=current==='Audi'?10000:current==='BMW'?20000:90000,start=(page-1)*25;
        document.querySelector('tbody').innerHTML=Array.from({length:Math.min(25,26-start)},(_,i)=>'<tr><td><a data-uname="lotsearchLotnumber" href="/lot/'+(base+start+i+1)+'">'+(base+start+i+1)+'</a></td><td data-uname="lotsearchLotcenturyyear">2024</td><td data-uname="lotsearchLotmake">'+current+'</td><td data-uname="lotsearchLotmodel">fixture</td><td data-uname="lotsearchLotauctiondate">10/20/2026 02:00 pm UTC</td><td data-uname="lotsearchLotyardname">CA - LOS ANGELES</td></tr>').join('');
        document.querySelector('#serverSideDataTable_info').textContent='Showing '+(start+1)+' to '+Math.min(start+25,26)+' of 26 entries';
        document.querySelector('.active a').textContent=page;document.querySelector('#serverSideDataTable_next').classList.toggle('disabled',page===2);}
      // Model the site's DataTables preXhr event and its known filter-loss bug.
      const table={DataTable:()=>({ajax:{params:()=>params}}),off:()=>table,one:(_name,fn)=>{pinned=fn;return table}};
      window.jQuery=()=>table;jQuery.fn={dataTable:{isDataTable:()=>true}};
      document.querySelector('#serverSideDataTable_next a').onclick=async e=>{e.preventDefault();
        let next={query:['WRONG_SCOPE'],start:25,length:25};if(pinned){pinned(null,null,next);pinned=null}
        await fetch('/fixture-list',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(next)});
        params=next;page++;render();};render();
      </script>'''.replace('CRITERIA', json.dumps(criteria)).replace('MAKE', json.dumps(make))


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_SELECTION_BROWSER') != '1', reason='Opt-in installed Chrome fixtures')
@pytest.mark.parametrize('key', ['iaa', 'copart'])
def test_saved_projects_keep_their_conditions_across_a_b_a_and_browser_restart(adapters, key):
    configure_playwright_driver(Path(__file__).resolve().parents[2] / 'desktop/runtime/playwright-driver')
    source = get_source(key)
    configs, projects = {}, {}
    for make, draft_id in [('Audi', 'a' * 32), ('BMW', 'b' * 32)]:
        if key == 'iaa':
            criteria = deepcopy(IAA)
            criteria['Searches'] = [{'Facets': [{'Group': 'Make', 'Value': make}], 'FullSearch': None, 'LongRanges': None}]
            criteria['PageSize'] = 25
            config = source.complete_selection_snapshot(source.capture_search_request(source.selection_entry_url, 'POST', criteria), {'native_search_uri': make})
        else:
            config = source.capture_search_url('https://www.copart.com/lotSearchResults?' + urlencode({'searchCriteria': json.dumps({'query': [make]})}))
        configs[make] = config
        selection = SelectionSession(adapters.paths['root'], adapters.user_id, key, draft_id=draft_id)
        selection.draft.begin('fixture', config); selection.draft.finish('fixture', 200)
        selection.draft.settle({'ready': True}); selection.draft.close(); selection.persist()
        with adapters.factory() as db:
            projects[make] = create_project(ProjectIn(name=make, source_key=key, search_url=config['creation_url'],
                search_page_mode='ALL_PAGES', search_selection_id=selection.id, search_draft_id=draft_id), db.get(User, adapters.user_id), db)['id']

    launches, documents, requests, browser_scopes, sessions = [], [], [], [], []

    @contextmanager
    def driver():
        with desktop_playwright() as pw:
            def launch(profile, **options):
                launches.append((profile, options['headless']))
                assert options['headless'], 'Ordinary repeated searches must not show a window'
                context = pw.chromium.launch_persistent_context(profile, **options)
                def site(route):
                    request = route.request
                    if request.method == 'POST':
                        requests.append(request.post_data_json)
                        route.fulfill(content_type='text/html', body='ok')
                        return
                    query = parse_qs(urlsplit(request.url).query)
                    make = query['url'][0] if key == 'iaa' else json.loads(query['searchCriteria'][0])['query'][0]
                    documents.append(make)
                    criteria = configs[make]['criteria']
                    route.fulfill(content_type='text/html', body=iaa_page(criteria, make) if key == 'iaa' else copart_page(criteria, make))
                context.route('**/*', site)
                return context
            yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

    original_open = source.open_browser
    def open_browser(*args, **kwargs):
        handles = original_open(*args, **kwargs)
        sessions.append(handles[1])
        handles[1].request_interval_seconds = 0  # Pacing has its own controlled-clock regression.
        return handles
    adapters.monkeypatch.setattr(source, 'open_browser', open_browser)
    original_collect = source.collect_search_pages
    def collect(page, *args, **kwargs):
        result = original_collect(page, *args, **kwargs)
        browser_scopes.append(page.evaluate('() => [window.previousScope,localStorage.getItem("scope")]'))
        return result
    adapters.monkeypatch.setattr(source, 'collect_search_pages', collect)
    adapters.monkeypatch.delenv('SEARCHCAR_CHROMIUM_EXECUTABLE', raising=False)
    adapters.monkeypatch.setattr('app.desktop_license.require_search_entitlement', lambda *_: None)

    def scan(pool, make):
        with adapters.factory() as db:
            run = scan_projects(ScanProjectsIn(project_ids=[projects[make]]), db.get(User, adapters.user_id), db)
            assert db.get(ScanRun, run['id']).payload['project_plans'][0]['search_config'] == configs[make]
        before = len(requests)
        process_job(run['id'], runtime_pool=pool)
        with adapters.factory() as db:
            finished = db.get(ScanRun, run['id'])
            assert finished.status == 'SUCCEEDED', (finished.error, finished.payload.get('failures'))
            assert finished.payload['pagination'][str(projects[make])]['pages_visited'] == 2
            for label, project_id in projects.items():
                assert db.get(Project, project_id).search_config == configs[label]
                cars = db.scalars(select(Car).join(ProjectCar, ProjectCar.car_id == Car.id).where(ProjectCar.project_id == project_id)).all()
                relations = db.scalars(select(ProjectCar).where(ProjectCar.project_id == project_id)).all()
                assert all(relation.search_status == 'FOUND' and relation.tracking_enabled for relation in relations)
                assert all(car.status == 'AVAILABLE' for car in cars)
                if label == make or cars:
                    base = 10000 if label == 'Audi' else 20000
                    assert {car.source_listing_id for car in cars} == {str(base + i) + ('~US' if key == 'iaa' else '') for i in range(1, 27)}
        for body in requests[before:]:
            if key == 'iaa':
                assert source.criteria_scope(body) == source.criteria_scope(configs[make]['criteria'])
            else:
                assert body['query'] == [make]
        assert len(requests) > before
        assert sessions[-1].url == 'about:blank'

    with BrowserRuntimePool(driver) as pool:
        for make in ('Audi', 'BMW', 'Audi'):
            scan(pool, make)
        assert len(launches) == 1
    with BrowserRuntimePool(driver) as pool:
        scan(pool, 'Audi')
    assert documents == ['Audi', 'BMW', 'Audi', 'Audi']
    assert browser_scopes == [[None, 'Audi'], ['Audi', 'BMW'], ['BMW', 'Audi'], ['Audi', 'Audi']]
    assert len(launches) == 2 and launches[0][0] == launches[1][0]
    assert all(session.context is None for session in sessions)
