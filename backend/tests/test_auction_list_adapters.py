import copy
import json
import os
import time
from threading import Thread
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from playwright.sync_api import Error as BrowserError

from app.sources.registry import get_source, list_sources
from app.sources.contracts import ScanError
from app.schemas import ProjectIn
from app.main import create_project, scan_projects, cancel_scan
from app.schemas import ScanProjectsIn, CancelScanIn
from app.models import Car, CarSnapshot, Project, ProjectCar, ScanRun, User
from app.worker import process_job
from app.auction_read import project_auction_view
from app.auction_storage import store_auction_row, finish_auction_search
from app.desktop_runtime import desktop_playwright, configure_playwright_environment, configure_playwright_driver
from test_source_test_profile import isolated_environment, profile_queue
from test_parallel_project_scan import _fake_playwright


ROWS = json.loads((Path(__file__).parent / "fixtures/auction_list_rows.json").read_text())


@pytest.fixture
def adapters(profile_queue):
    profile_queue.monkeypatch.setattr("app.database.settings", profile_queue.settings)
    return profile_queue


@pytest.mark.parametrize("key", ["copart", "iaa"])
def test_public_list_adapters_exist_only_in_isolated_profile(adapters, key):
    source = get_source(key)
    assert {"SEARCH", "LIST_ONLY"} <= source.descriptor.capabilities
    assert key in {item.key for item in list_sources()}
    adapters.settings.source_test_mode = False
    with pytest.raises(ValueError, match="Unsupported listing source"):
        get_source(key)  # A previously cached test adapter is not a production adapter.


def test_copart_normalizes_real_row_without_turning_bid_into_sale_price(adapters):
    value = get_source("copart").parse_list_row(ROWS["copart"][0])
    assert value["source_listing_id"] == "66091506"
    assert value["mileage"] == {"value": 130543, "unit": "mi", "accuracy": "ACTUAL"}
    assert value["schedule"]["utc_at"] == "2026-10-14T14:00:00+00:00"
    assert {v["meaning"]: v["amount_minor"] for v in value["money_values"]} == {
        "CURRENT_BID": 0, "ESTIMATED_RETAIL": 1724600}
    assert "price_krw" not in value
    assert value["keys"] is None


def test_iaa_uses_vehicle_url_identity_sales_list_year_and_verified_city(adapters):
    source = get_source("iaa")
    value = source.parse_list_row(ROWS["iaa"][0])
    assert value["source_listing_id"] == "46075606~US"  # Stock number is not the URL key.
    assert value["schedule"]["source_timezone"] == "America/Chicago"
    assert value["schedule"]["utc_at"] == "2026-10-12T13:30:00+00:00"
    assert value["mileage"]["accuracy"] is None  # Unlabelled miles aren't claimed Actual.
    assert not any(v["meaning"] == "CURRENT_BID" for v in value["money_values"])
    assert value["keys"] is True
    for fields in ({"sale_list_url":None}, {"site_city":None}, {"date_text":"Mon Oct 12, 8:30am CST"}):
        assert source.parse_list_row({**ROWS["iaa"][0], **fields})["schedule"]["precision"] == "UNKNOWN"


def test_iaa_timed_date_conflict_is_reported_not_claimed_as_a_live_start(adapters):
    # Real list observation 2026-10-13: timed action differs from View Sale List.
    row={**ROWS['iaa'][0],'date_text':'Tue Oct 13, 8:09pm CDT',
         'sale_list_url':'https://www.iaai.com/SalesList/622~US/10162026',
         'schedule_event_kind':'UNKNOWN','source_action':'BID NOW'}
    value=get_source('iaa').parse_list_row(row)
    assert value['schedule']['precision']=='UNKNOWN'
    assert value['schedule']['utc_at'] is None
    assert value['schedule']['event_kind']=='UNKNOWN'
    assert value['schedule']['issue']=='DATE_CONFLICT'


@pytest.mark.parametrize("key", ["copart", "iaa"])
def test_url_validation_retains_encoded_filters_and_rejects_cross_source(adapters, key):
    source = get_source(key)
    url = "https://www.copart.com/ru/lotSearchResults?searchCriteria=%7B%22query%22%3A%5B%22A5%2B%22%5D%7D" if key == "copart" else "https://www.iaai.com/Search?url=a%2Bb%2Fc%3D"
    result = source.validate_search_url(url)
    assert result.split("?",1)[1] == url.split("?",1)[1]
    if key == "copart":
        assert "/ru/" not in result
    for bad in ("https://evil.example/Search", "https://u:p@www.iaai.com/Search?url=a", "https://www.copart.com:9999/lotSearchResults", "javascript:alert(1)"):
        with pytest.raises(ScanError):
            source.validate_search_url(bad)


class Page:
    def __init__(self, snapshots):
        self.snapshots, self.index, self.navigations, self.listeners = snapshots, 0, [], {}
    def on(self, event, callback):
        self.listeners[event]=callback
    def remove_listener(self,event,callback):
        self.listeners.pop(event,None)
    def goto(self, url, **kwargs):
        self.navigations.append(url)
        self.url=url
    def evaluate(self, script):
        return copy.deepcopy(self.snapshots[self.index])
    def locator(self, selector):
        page = self
        class Locator:
            def count(self): return 0 if 'consent-root' in selector or 'onetrust' in selector else 1
            def click(self, **kwargs): page.index += 1
        return Locator()
    def wait_for_timeout(self, *_): pass
    def is_closed(self): return False
    url=''


def snapshot(key, rows, *, page=1, total=2, has_next=False):
    return {"rows":rows, "page":page, "total":total, "has_next":has_next,
            "ready":True, "blocked":False, "empty":not rows, "site_city":"Chicago"}


def test_copart_keeps_initial_filters_when_the_site_rebuilds_next_page_criteria(adapters):
    # Live 15 Oct: Next drops MAKE/MODL/VEHT while the URL stays unchanged.
    source = get_source('copart')
    states = [snapshot('copart', [ROWS['copart'][0]], total=2, has_next=True),
              snapshot('copart', [ROWS['copart'][1]], page=2, total=2)]
    class FilterLosingPage(Page):
        pinned = False
        def evaluate(self, script):
            if script == source.dom_script:
                state = super().evaluate(script)
                if self.index and not self.pinned:
                    state['total'] = 1000
                return state
            self.pinned = True
            return True
    page = FilterLosingPage(states)
    result = source.collect_search_pages(page,
        'https://www.copart.com/lotSearchResults?searchCriteria=%7B%22filter%22%3A%7B%22MAKE%22%3A%5B%22%23MakeCode%3AAUDI%22%5D%7D%7D',
        all_pages=True)
    assert result.pages_visited == 2 and len(result.rows) == 2
    assert page.pinned
    assert len(page.navigations) == 1  # Normal Next, not a reload or separate API request.


def test_copart_stops_partial_if_pagination_cannot_preserve_filters(adapters):
    source = get_source('copart')
    page = Page([snapshot('copart', [ROWS['copart'][0]], total=2, has_next=True),
                 snapshot('copart', [ROWS['copart'][1]], page=2, total=2)])
    original = page.evaluate
    page.evaluate = lambda script: original(script) if script == source.dom_script else False
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page, 'https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D', all_pages=True)
    assert caught.value.code == 'SEARCH_PAGE_NOT_READY'
    assert len(caught.value.partial_collection.rows) == 1
    assert caught.value.partial_collection.pages_visited == 1
    assert page.index == 0


@pytest.mark.parametrize('key',['copart','iaa'])
def test_browser_starts_in_background_with_an_owned_persistent_profile(adapters,key):
    calls=[]
    page=Page([])
    browser=SimpleNamespace(close=lambda:None)
    context=SimpleNamespace(browser=browser,pages=[page],new_page=lambda:page,close=lambda:None)
    browser.new_context=lambda **kwargs:context
    chromium=SimpleNamespace(
        launch=lambda **kwargs:(calls.append(('ephemeral',kwargs)) or browser),
        launch_persistent_context=lambda path,**kwargs:(calls.append((path,kwargs)) or context),
    )
    source=get_source(key)
    opened=source.open_browser(SimpleNamespace(chromium=chromium),headless=True,executable_path='/test/chromium')
    assert opened[0] is None  # Persistent context/session owns browser cleanup.
    assert opened[1] is opened[2]
    assert opened[2].page is page
    path,options=calls[0]
    assert path!='ephemeral'
    assert Path(path).is_relative_to(Path(adapters.settings.storage_root).parent/'runtime')
    assert Path(path).parent.name==key
    assert options['headless'] is True
    assert options['executable_path']=='/test/chromium'
    assert options.get('viewport') == {'width':1280,'height':800}
    assert options.get('no_viewport') is not True  # Fixed desktop DOM only in background mode.


def test_installed_full_browser_channel_supports_both_background_and_manual_mode(adapters):
    page=Page([])
    calls=[]
    context=SimpleNamespace(browser=SimpleNamespace(close=lambda:None),pages=[page],close=lambda:None)
    chromium=SimpleNamespace(launch_persistent_context=lambda path,**kwargs:(calls.append((path,kwargs)) or context))
    adapters.monkeypatch.setenv('SEARCHCAR_SITE_BROWSER_CHANNEL','chrome')
    get_source('copart').open_browser(SimpleNamespace(chromium=chromium),headless=True,executable_path='/bundle/headless_shell')
    path,options=calls[0]
    assert options.get('channel')=='chrome'
    assert not options.get('executable_path')
    assert Path(path).name=='chrome'


@pytest.mark.parametrize('key',['copart','iaa'])
def test_interactive_verification_waits_for_the_user_not_an_automatic_reload(adapters,key):
    source=get_source(key)
    states=[{**snapshot(key,[],total=0),'ready':False,'blocked':True},snapshot(key,[ROWS[key][0]],total=1)]
    page=Page(states)
    page.wait_for_timeout=lambda *_:setattr(page,'index',1)  # The user completes the site's own challenge.
    adapters.monkeypatch.setattr(source,'_interactive_page',page,raising=False)
    progress=[]
    url='https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D' if key=='copart' else 'https://www.iaai.com/Search?url=x'
    result=source.collect_search_pages(page,url,progress=progress.append)
    assert len(result.rows)==1
    assert page.navigations==[url]  # No automatic reload or CAPTCHA interaction.
    assert progress[0]['interaction']['state']=='WAITING_FOR_USER'
    assert progress[-1].get('interaction') is None


@pytest.mark.parametrize('key',['copart','iaa'])
def test_interactive_wait_can_cancel_and_times_out_without_empty_success(adapters,key):
    source=get_source(key)
    page=Page([{**snapshot(key,[],total=0),'ready':False,'blocked':True}])
    adapters.monkeypatch.setattr(source,'_interactive_page',page,raising=False)
    url='https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D' if key=='copart' else 'https://www.iaai.com/Search?url=x'
    checks=[]
    def checkpoint():
        checks.append(True)
        if len(checks)>2:raise RuntimeError('user cancelled')
    with pytest.raises(RuntimeError,match='user cancelled'):
        source.collect_search_pages(page,url,checkpoint=checkpoint)
    adapters.monkeypatch.setattr(source,'verification_timeout_seconds',0.001,raising=False)
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page,url)
    assert caught.value.code=='SOURCE_VERIFICATION_TIMEOUT'
    assert not caught.value.partial_collection.rows


def test_manual_verification_cannot_change_the_project_filters(adapters):
    source=get_source('copart')
    page=Page([{**snapshot('copart',[],total=0),'ready':False,'blocked':True},snapshot('copart',[ROWS['copart'][0]],total=1)])
    def user_changes_search(*_):
        page.index=1
        page.url='https://www.copart.com/lotSearchResults?searchCriteria=%7B%22query%22%3A%5B%22BMW%22%5D%7D'
    page.wait_for_timeout=user_changes_search
    adapters.monkeypatch.setattr(source,'_interactive_page',page,raising=False)
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page,'https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D')
    assert caught.value.code=='SOURCE_SEARCH_SCOPE_CHANGED'
    assert not caught.value.partial_collection.rows


def test_a_second_denial_is_not_another_manual_wait(adapters):
    source=get_source('iaa')
    page=Page([{**snapshot('iaa',[],total=0),'ready':False,'blocked':True},snapshot('iaa',[ROWS['iaa'][0]],total=1)])
    page.wait_for_timeout=lambda *_:setattr(page,'index',1)
    original=page.evaluate
    reads=[]
    def read(script):
        reads.append(True)
        if len(reads)>=4:
            page.listeners['response'](SimpleNamespace(status=403,url='https://www.iaai.com/runtime.js',request=SimpleNamespace(resource_type='script')))
        return original(script)
    page.evaluate=read
    adapters.monkeypatch.setattr(source,'_interactive_page',page,raising=False)
    progress=[]
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page,'https://www.iaai.com/Search?url=x',progress=progress.append)
    assert caught.value.code=='SOURCE_ACCESS_DENIED'
    assert len([p for p in progress if p.get('interaction')])==1
    assert not caught.value.partial_collection.rows


def test_closed_site_window_stops_search(adapters):
    page=Page([])
    page.is_closed=lambda:True
    with pytest.raises(ScanError) as caught:
        get_source('iaa').collect_search_pages(page,'https://www.iaai.com/Search?url=x')
    assert caught.value.code=='SOURCE_BROWSER_CLOSED'
    assert not page.navigations


def test_site_navigation_during_dom_read_is_loading_but_other_errors_are_not_hidden(adapters):
    source=get_source('iaa')
    page=Page([snapshot('iaa',[ROWS['iaa'][0]],total=1)])
    original=page.evaluate
    reads=[]
    def navigating(script):
        reads.append(True)
        if len(reads)==1:raise BrowserError('Execution context was destroyed, most likely because of a navigation')
        return original(script)
    page.evaluate=navigating
    assert len(source.collect_search_pages(page,'https://www.iaai.com/Search?url=x').rows)==1
    def broken(script):raise BrowserError('Unexpected DOM script error')
    page.evaluate=broken
    with pytest.raises(BrowserError,match='Unexpected DOM script error'):
        source.collect_search_pages(page,'https://www.iaai.com/Search?url=x')


@pytest.mark.parametrize('key',['copart','iaa'])
def test_result_header_before_async_rows_is_still_loading(adapters,key):
    source=get_source(key)
    page=Page([snapshot(key,[],total=1),snapshot(key,[ROWS[key][0]],total=1)])
    page.wait_for_timeout=lambda *_:setattr(page,'index',1)
    url='https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D' if key=='copart' else 'https://www.iaai.com/Search?url=x'
    assert len(source.collect_search_pages(page,url).rows)==1


def test_consent_overlay_must_be_closed_before_pagination(adapters):
    source=get_source('copart')
    page=Page([{**snapshot('copart',[ROWS['copart'][0]],total=1),'consent_required':True},snapshot('copart',[ROWS['copart'][0]],total=1)])
    page.wait_for_timeout=lambda *_:setattr(page,'index',1)
    adapters.monkeypatch.setattr(source,'_interactive_page',page,raising=False)
    progress=[]
    result=source.collect_search_pages(page,'https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D',all_pages=True,progress=progress.append)
    assert len(result.rows)==1
    assert progress[0]['interaction']['reason']=='COOKIE_CONSENT'


@pytest.mark.parametrize('first,second', [('COOKIE_CONSENT','CAPTCHA'),('CAPTCHA','COOKIE_CONSENT')])
def test_cookie_choice_and_access_verification_have_separate_waits(adapters, first, second):
    source=get_source('copart')
    ready=snapshot('copart',[ROWS['copart'][0]],total=1)
    states=[{**ready,'consent_required':first=='COOKIE_CONSENT','blocked':first=='CAPTCHA'},
            {**ready,'consent_required':second=='COOKIE_CONSENT','blocked':second=='CAPTCHA'},ready]
    page=Page(states)
    page.wait_for_timeout=lambda *_:setattr(page,'index',min(page.index+1,2))
    adapters.monkeypatch.setattr(source,'_interactive_page',page,raising=False)
    progress=[]
    result=source.collect_search_pages(page,'https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D',progress=progress.append)
    assert len(result.rows)==1
    assert [p['interaction']['reason'] for p in progress if p.get('interaction')]==[first,second]
    assert len(page.navigations)==1


@pytest.mark.parametrize('key',['copart','iaa'])
def test_optional_fetch_failure_does_not_override_verified_rows(adapters,key):
    source=get_source(key)
    page=Page([snapshot(key,[ROWS[key][0]],total=1)])
    original=page.evaluate
    def read(script):
        page.listeners['response'](SimpleNamespace(status=403,url=f'https://{source.descriptor.domains[0]}/optional?token=secret#fragment',request=SimpleNamespace(resource_type='fetch')))
        return original(script)
    page.evaluate=read
    progress=[]
    url='https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D' if key=='copart' else 'https://www.iaai.com/Search?url=x'
    assert len(source.collect_search_pages(page,url,progress=progress.append).rows)==1
    assert progress[-1]['network_failures']==[dict(status=403,host=source.descriptor.domains[0],path='/optional',resource_type='fetch')]
    assert 'secret' not in json.dumps(progress)


def test_failed_fetch_without_new_page_is_access_failure_not_empty(adapters):
    source=get_source('iaa')
    adapters.monkeypatch.setattr(source,'read_timeout_seconds',0.001)
    page=Page([snapshot('iaa',[ROWS['iaa'][0]],total=2,has_next=True),snapshot('iaa',[],page=2,total=2)])
    original=page.evaluate
    def read(script):
        if page.index==1:
            page.listeners['response'](SimpleNamespace(status=403,url='https://www.iaai.com/search-data?secret=private',request=SimpleNamespace(resource_type='xhr')))
        return original(script)
    page.evaluate=read
    progress=[]
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page,'https://www.iaai.com/Search?url=x',all_pages=True,progress=progress.append)
    assert caught.value.code=='SOURCE_ACCESS_DENIED'
    assert len(caught.value.partial_collection.rows)==1
    assert caught.value.network_failures[0]['path']=='/search-data'
    assert progress[-1]['stop_reason']=='SOURCE_ACCESS_DENIED'
    assert progress[-1]['complete'] is False
    assert 'private' not in json.dumps(progress)


def test_rate_limit_stops_without_reload_or_manual_challenge(adapters):
    source=get_source('iaa')
    page=Page([snapshot('iaa',[ROWS['iaa'][0]],total=1)])
    original=page.evaluate
    def read(script):
        page.listeners['response'](SimpleNamespace(status=429,url='https://www.iaai.com/search-data',request=SimpleNamespace(resource_type='xhr')))
        return original(script)
    page.evaluate=read
    adapters.monkeypatch.setattr(source,'_interactive_page',page,raising=False)
    progress=[]
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page,'https://www.iaai.com/Search?url=x',progress=progress.append)
    assert caught.value.code=='SOURCE_RATE_LIMITED'
    assert not any(p.get('interaction') for p in progress)
    assert len(page.navigations)==1


def test_configurable_page_budget_and_read_summary(adapters):
    source=get_source('copart')
    adapters.monkeypatch.setattr(adapters.settings,'source_test_max_pages',60,raising=False)
    rows=[{**ROWS['copart'][0],'source_car_id':str(80000000+i),'url':f'https://www.copart.com/lot/{80000000+i}'} for i in range(51)]
    pages=[snapshot('copart',[row],page=i+1,total=51,has_next=i<50) for i,row in enumerate(rows)]
    progress=[]
    result=source.collect_search_pages(Page(pages),'https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D',all_pages=True,progress=progress.append)
    assert result.pages_visited==51
    assert progress[-1]['max_pages']==60
    undated={**ROWS['copart'][1],'date_text':'Upcoming lot'}
    unknown={**ROWS['copart'][0],'source_car_id':'80000999','url':'https://www.copart.com/lot/80000999','date_text':'New unrecognised date'}
    foreign={**unknown,'source_car_id':'80000888','url':'https://www.copart.com/lot/80000888','location':'ON - TORONTO'}
    source.collect_search_pages(Page([snapshot('copart',[ROWS['copart'][0],undated,unknown,foreign],total=4)]),
        'https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D',progress=progress.append)
    assert progress[-1]['read_summary']==dict(dated=1,exact_time=1,without_date=1,unrecognized_schedule=1,unsupported_market=1)


@pytest.mark.parametrize('problem', ['limit','changed','duplicate'])
def test_partial_auction_search_does_not_exclude_previously_saved_lots(adapters,problem):
    source=get_source('iaa')
    one,two=ROWS['iaa'][:2]
    pages=[snapshot('iaa',[one,two],total=2)]
    adapters.monkeypatch.setattr(source,'open_browser',lambda *a,**k:(SimpleNamespace(close=lambda:None),SimpleNamespace(close=lambda:None),Page(pages)))
    adapters.monkeypatch.setattr('app.worker.desktop_playwright',_fake_playwright)
    with adapters.factory() as db:
        owner=db.get(User,adapters.user_id)
        project=create_project(ProjectIn(name='Coverage',source_key='iaa',search_url='https://www.iaai.com/Search?url=x',search_page_mode='ALL_PAGES'),owner,db)
        job=scan_projects(ScanProjectsIn(project_ids=[project['id']]),owner,db)
    process_job(job['id'])
    pages[:]=[snapshot('iaa',[one],total=2,has_next=True),snapshot('iaa',[one,two] if problem=='duplicate' else [two],page=2,total=3 if problem=='changed' else 2)]
    if problem=='limit':adapters.monkeypatch.setattr(adapters.settings,'source_test_max_pages',1,raising=False)
    with adapters.factory() as db:
        again=scan_projects(ScanProjectsIn(project_ids=[project['id']]),db.get(User,adapters.user_id),db)
    process_job(again['id'])
    with adapters.factory() as db:
        run=db.get(ScanRun,again['id'])
        assert run.status!='SUCCEEDED'
        assert run.payload['pagination'][str(project['id'])]['complete'] is False
        assert run.payload['pagination'][str(project['id'])]['stop_reason'] in ('SEARCH_PAGE_LIMIT','SEARCH_RESULTS_CHANGED')
        assert all(r.search_status=='FOUND' for r in db.scalars(select(ProjectCar).where(ProjectCar.project_id==project['id'])))


def test_worker_manual_wait_can_be_cancelled_from_another_connection_and_restarted(adapters):
    source=get_source('iaa')
    closed=[]
    attempts=[]
    cancellation=[]
    cancel_errors=[]
    def open_browser(*args,**kwargs):
        attempts.append(True)
        state={**snapshot('iaa',[],total=0),'ready':False,'blocked':True} if len(attempts)==1 else snapshot('iaa',[ROWS['iaa'][0]],total=1)
        page=Page([state])
        page.wait_for_timeout=lambda *_:time.sleep(0.02)
        source._interactive_page=page
        return SimpleNamespace(close=lambda:closed.append('browser')),SimpleNamespace(close=lambda:closed.append('context')),page
    adapters.monkeypatch.setattr(source,'open_browser',open_browser)
    adapters.monkeypatch.setattr(source,'verification_timeout_seconds',8)
    adapters.monkeypatch.setattr('app.worker.desktop_playwright',_fake_playwright)
    with adapters.factory() as db:
        owner=db.get(User,adapters.user_id)
        project=create_project(ProjectIn(name='Interactive',source_key='iaa',search_url='https://www.iaai.com/Search?url=x'),owner,db)
        job=scan_projects(ScanProjectsIn(project_ids=[project['id']]),owner,db)
    def cancel():
        try:
            with adapters.factory() as db:
                cancel_scan(job['id'],CancelScanIn(all_projects=True),db.get(User,adapters.user_id),db)
        except Exception as exc:cancel_errors.append(exc)
    def published(*_):
        if cancellation:return
        with adapters.factory() as db:
            info=(db.get(ScanRun,job['id']).payload.get('pagination') or {}).get(str(project['id']),{})
            if info.get('interaction',{}).get('state')=='WAITING_FOR_USER':
                thread=Thread(target=cancel)
                cancellation.append(thread)
                thread.start()
    adapters.monkeypatch.setattr('app.worker.publish_scan_update',published)
    process_job(job['id'])
    assert cancellation
    cancellation[0].join(timeout=10)
    assert not cancellation[0].is_alive() and not cancel_errors
    with adapters.factory() as db:
        assert db.get(ScanRun,job['id']).status=='CANCELLED'
        assert not list(db.scalars(select(Car)))
        again=scan_projects(ScanProjectsIn(project_ids=[project['id']]),db.get(User,adapters.user_id),db)
    process_job(again['id'])
    with adapters.factory() as db:
        assert db.get(ScanRun,again['id']).status=='SUCCEEDED'
    assert closed==['context','browser','context','browser']


@pytest.mark.parametrize("key", ["copart", "iaa"])
def test_pagination_is_verified_and_first_page_is_deliberate_coverage(adapters, key):
    source = get_source(key)
    rows = ROWS[key]
    url = "https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D" if key == "copart" else "https://www.iaai.com/Search?url=fixture"
    pages = [snapshot(key, [rows[0]], total=2, has_next=True), snapshot(key, [rows[1]], page=2,total=2)]
    first = source.collect_search_pages(Page(pages), url, all_pages=False)
    assert first.pages_visited == 1 and len(first.rows) == 1
    full = source.collect_search_pages(Page(pages), url, all_pages=True)
    assert full.pages_visited == 2 and len(full.rows) == 2
    assert len({row["url"] for row in full.rows.values()}) == 2
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(Page(pages), url, all_pages=True, max_pages=1)
    assert len(caught.value.partial_collection.rows) == 1


@pytest.mark.parametrize("key", ["copart", "iaa"])
def test_block_or_loading_is_not_empty_and_partial_results_survive(adapters, key):
    source = get_source(key)
    adapters.monkeypatch.setattr(source, "read_timeout_seconds", 0.005)
    url = "https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D" if key == "copart" else "https://www.iaai.com/Search?url=x"
    empty = source.collect_search_pages(Page([snapshot(key, [], total=0)]), url, all_pages=True)
    assert empty.total_results == 0 and not empty.rows
    for state in ({"blocked":True}, {"ready":False}):
        with pytest.raises(ScanError):
            source.collect_search_pages(Page([{**snapshot(key,[],total=0), **state}]), url)
    pages = [snapshot(key,[ROWS[key][0]],total=2,has_next=True), {**snapshot(key,[],page=2,total=2), "blocked":True}]
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(Page(pages), url, all_pages=True)
    assert len(caught.value.partial_collection.rows) == 1


@pytest.mark.parametrize('key',['copart','iaa'])
def test_denied_required_scripts_stop_even_when_document_is_http_200(adapters,key):
    source=get_source(key)
    page=Page([snapshot(key,[ROWS[key][0]],total=1)])
    original_evaluate=page.evaluate
    def blocked_script(script):
        callback=page.listeners.get('response')
        if callback:
            callback(SimpleNamespace(status=403,url=f'https://{source.descriptor.domains[0]}/runtime.js',request=SimpleNamespace(resource_type='script')))
        return original_evaluate(script)
    page.evaluate=blocked_script
    url='https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D' if key=='copart' else 'https://www.iaai.com/Search?url=x'
    with pytest.raises(ScanError) as caught:
        source.collect_search_pages(page,url)
    assert caught.value.code=='SOURCE_ACCESS_DENIED'
    assert not caught.value.partial_collection.rows
    assert page.listeners=={}


@pytest.mark.parametrize("key", ["copart", "iaa"])
def test_manual_project_worker_and_auction_api_work_without_detail_pages(adapters, key):
    source = get_source(key)
    pages = []
    def open_browser(*args, **kwargs):
        page = Page([snapshot(key, ROWS[key], total=len(ROWS[key]))])
        pages.append(page)
        return SimpleNamespace(close=lambda:None), SimpleNamespace(close=lambda:None), page
    adapters.monkeypatch.setattr(source, "open_browser", open_browser)
    adapters.monkeypatch.setattr("app.worker.desktop_playwright", _fake_playwright)
    url = "https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D" if key == "copart" else "https://www.iaai.com/Search?url=fixture"
    with adapters.factory() as db:
        owner = db.get(User, adapters.user_id)
        project = create_project(ProjectIn(name="Auction", source_key=key,search_url=url,auto_update=False),owner,db)
        result = scan_projects(ScanProjectsIn(project_ids=[project["id"]]),owner,db)
    process_job(result["id"])
    with adapters.factory() as db:
        run = db.get(ScanRun,result["id"])
        assert run.status == "SUCCEEDED", run.error
        assert run.payload['pagination'][str(project['id'])]['initial_full_scan'] is False
        cars = list(db.scalars(select(Car).where(Car.source_key==key)))
        assert len(cars) == (1 if key=="copart" else 2)  # Undated new lots are not inserted.
        assert all(c.canonical_encar_id is None and c.current_price is None for c in cars)
        view = project_auction_view(db,db.get(Project,project["id"]),
            now=datetime(2026,10,12,10,tzinfo=timezone.utc),computer_timezone="Europe/Warsaw")
        assert len(view["lots"]) == len(cars)
        assert view["lots"][0]["url"].startswith("https://www.")
        assert all("VehicleDetail/" not in url and "/lot/" not in url for page in pages for url in page.navigations)
        assert db.scalar(select(CarSnapshot)) is not None
        owner=db.get(User,adapters.user_id)
        again=scan_projects(ScanProjectsIn(project_ids=[project["id"]]),owner,db)
    process_job(again["id"])
    with adapters.factory() as db:
        assert len(list(db.scalars(select(Car).where(Car.source_key==key))))==len(cars)


def test_auction_date_loss_absence_and_partial_reads_preserve_history(adapters):
    with adapters.factory() as db:
        project=Project(owner_id=adapters.user_id,source_key='iaa',name='Lifecycle',name_key='lifecycle',search_url='https://www.iaai.com/Search?url=x')
        other=Project(owner_id=adapters.user_id,source_key='iaa',name='Other',name_key='other',search_url='https://www.iaai.com/Search?url=y')
        db.add_all([project,other]);db.flush()
        row=ROWS['iaa'][0]
        store_auction_row(db,project,row,scan_id=100,complete=True)
        store_auction_row(db,other,row,scan_id=100,complete=True)
        car=db.scalar(select(Car).where(Car.source_key=='iaa'))
        relation=db.get(ProjectCar,dict(project_id=project.id,car_id=car.id))
        relation.favorite=True
        without_date={**row,'date_text':'Auction Not Assigned','sale_list_url':None,'odometer':None}
        store_auction_row(db,project,without_date,scan_id=101,complete=False)
        assert car.details['mileage']['value']==78834
        before=project_auction_view(db,project,now=datetime(2026,10,12,10,tzinfo=timezone.utc),computer_timezone='Europe/Warsaw',today_only=True)
        assert len(before['lots'])==1
        store_auction_row(db,project,without_date,scan_id=102,complete=True)
        assert project_auction_view(db,project,now=datetime(2026,10,12,10,tzinfo=timezone.utc),computer_timezone='Europe/Warsaw',today_only=True)['lots']==[]
        assert len(list(db.scalars(select(CarSnapshot))))>=2
        finish_auction_search(db,project,set(),all_pages=False)
        assert relation.search_status=='FOUND'
        finish_auction_search(db,project,set(),all_pages=True)
        assert relation.search_status=='NOT_FOUND_IN_SEARCH'
        assert relation.favorite is True and relation.tracking_enabled is True
        assert db.get(ProjectCar,dict(project_id=other.id,car_id=car.id)).search_status=='FOUND'
        assert car.status!='SOLD'


@pytest.mark.parametrize('key', ['copart', 'iaa'])
def test_removing_an_auction_lot_is_project_scoped_and_survives_refresh_and_backup(adapters, tmp_path, key):
    from fastapi.testclient import TestClient
    from app.auth import require_csrf, require_user
    from app.database import get_db, create_database_engine
    from app.desktop_backup import export_backup, restore_backup
    from app.main import app
    from app.models import CarEvent
    from sqlalchemy.orm import sessionmaker

    with adapters.factory() as db:
        owner = db.get(User, adapters.user_id)
        foreign = User(username='other-auction-owner', username_key='other-auction-owner', password_hash='!fixture')
        db.add(foreign); db.flush()
        projects = [Project(owner_id=who, source_key=key, name=f'Removal {i}', name_key=f'removal {i}',
                            search_url=f'https://example.invalid/search/{key}/{i}')
                    for i, who in enumerate([owner.id, owner.id, foreign.id])]
        db.add_all(projects); db.flush()
        for project in projects:
            store_auction_row(db, project, ROWS[key][0], scan_id=100, complete=True)
        db.commit()
        ids = [p.id for p in projects]
        car = db.scalar(select(Car).where(Car.source_key == key))
        car_id = car.id
        database_path = Path(db.get_bind().url.database)

    previous = dict(app.dependency_overrides)
    def database():
        with adapters.factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_user] = lambda: owner
    try:
        with TestClient(app) as client:
            # No override for the mutation's auth boundary yet.
            assert client.delete(f'/api/projects/{ids[0]}/cars/{car_id}').status_code == 401
            app.dependency_overrides[require_csrf] = lambda: owner
            assert client.delete(f'/api/projects/{ids[2]}/cars/{car_id}').status_code == 404
            assert client.delete(f'/api/projects/{ids[0]}/cars/{car_id}').status_code == 204
            assert client.get(f'/api/projects/{ids[0]}/auction-lots?computer_timezone=UTC').json()['lots'] == []
            assert len(client.get(f'/api/projects/{ids[1]}/auction-lots?computer_timezone=UTC').json()['lots']) == 1
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(previous)

    # New sessions simulate restart; neither partial nor full updates revive it.
    for scan_id, complete in [(101, False), (102, True)]:
        with adapters.factory() as db:
            project = db.get(Project, ids[0])
            assert store_auction_row(db, project, ROWS[key][0], scan_id=scan_id, complete=complete) is None
            finish_auction_search(db, project, set(), all_pages=complete)
            db.commit()
            removed = db.get(ProjectCar, (ids[0], car_id))
            assert removed.tracking_enabled is False and removed.search_status == 'REMOVED_FROM_PROJECT'
            assert db.get(Car, car_id).excluded is False
            assert all(db.get(ProjectCar, (pid, car_id)).tracking_enabled for pid in ids[1:])
            assert len(list(db.scalars(select(CarEvent).where(CarEvent.car_id == car_id, CarEvent.kind == 'REMOVED_FROM_PROJECT')))) == 1

    archive = tmp_path / 'removed-lot.searchcar-backup'
    export_backup(database_path, tmp_path / 'storage', archive)
    restored = tmp_path / 'restored' / 'database.sqlite3'
    restore_backup(archive, restored, tmp_path / 'restored-storage', tmp_path / 'rollback')
    engine = create_database_engine(f'sqlite+pysqlite:///{restored}')
    try:
        with sessionmaker(engine)() as db:
            project = db.get(Project, ids[0])
            assert store_auction_row(db, project, ROWS[key][0], scan_id=103, complete=True) is None
            assert db.get(ProjectCar, (ids[0], car_id)).tracking_enabled is False
            assert all(db.get(ProjectCar, (pid, car_id)).tracking_enabled for pid in ids[1:])
    finally:
        engine.dispose()


@pytest.mark.parametrize('key',['copart','iaa'])
def test_foreign_rows_count_toward_coverage_but_are_not_usa_prices(adapters,key):
    source=get_source(key)
    foreign={**ROWS[key][0], 'source_car_id':'99999999~CA' if key=='iaa' else '99999999',
             'location':'ON - TORONTO', 'url':'https://www.iaai.com/VehicleDetail/99999999~CA' if key=='iaa' else 'https://www.copart.com/lot/99999999'}
    url='https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D' if key=='copart' else 'https://www.iaai.com/Search?url=x'
    collection=source.collect_search_pages(Page([snapshot(key,[ROWS[key][0],foreign],total=2)]),url,all_pages=True)
    assert collection.total_results==len(collection.rows)==2
    assert collection.rows[foreign['source_car_id']]['out_of_scope'] is True
    with pytest.raises(ScanError,match='USA'):
        source.parse_list_row(foreign)


@pytest.mark.parametrize('key',['copart','iaa'])
def test_test_projects_reject_krw_override_and_telegram(adapters,key):
    url='https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D' if key=='copart' else 'https://www.iaai.com/Search?url=x'
    for extra in ({'price_filter_mode':'CUSTOM','price_max_krw':26000000},{'telegram_url':'https://t.me/example'}):
        with pytest.raises(ValueError,match='source_test_link_filters_only_no_telegram'):
            ProjectIn(name='Test',source_key=key,search_url=url,**extra)
    assert ProjectIn(name='Test',source_key=key,search_url=url,auto_update=True).auto_update is False


@pytest.mark.parametrize('status,code', [(403,'SOURCE_ACCESS_DENIED'),(429,'SOURCE_RATE_LIMITED')])
def test_denied_source_stops_its_remaining_projects_in_the_same_batch(adapters,status,code):
    source=get_source('copart')
    calls=[]
    def open_browser(*args,**kwargs):
        calls.append(True)
        page=Page([])
        page.goto=lambda *args,**kwargs:SimpleNamespace(status=status)
        return SimpleNamespace(close=lambda:None),SimpleNamespace(close=lambda:None),page
    adapters.monkeypatch.setattr(source,'open_browser',open_browser)
    adapters.monkeypatch.setattr('app.worker.desktop_playwright',_fake_playwright)
    with adapters.factory() as db:
        owner=db.get(User,adapters.user_id)
        projects=[create_project(ProjectIn(name=f'Denied {i}',source_key='copart',search_url=f'https://www.copart.com/lotSearchResults?searchCriteria=%7B%22query%22%3A%5B%22{i}%22%5D%7D'),owner,db) for i in range(2)]
        job=scan_projects(ScanProjectsIn(project_ids=[p['id'] for p in projects]),owner,db)
    process_job(job['id'])
    assert len(calls)==1
    with adapters.factory() as db:
        run=db.get(ScanRun,job['id'])
        assert run.status!='SUCCEEDED'
        assert len(run.payload['failures'])==2
        assert all(f['code']==code for f in run.payload['failures'])
        assert not list(db.scalars(select(Car)))


@pytest.mark.parametrize('key',['copart','iaa'])
def test_cancelled_collection_keeps_checked_rows_and_can_start_fresh(adapters,key):
    source=get_source(key)
    url='https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D' if key=='copart' else 'https://www.iaai.com/Search?url=x'
    pages=[snapshot(key,[ROWS[key][0]],total=2,has_next=True),snapshot(key,[ROWS[key][1]],page=2,total=2)]
    cancel={'ready':False}
    def checkpoint():
        if cancel['ready']:raise RuntimeError('cancelled by user')
    def progress(info):cancel['ready']=True
    with pytest.raises(RuntimeError,match='cancelled by user') as caught:
        source.collect_search_pages(Page(pages),url,all_pages=True,checkpoint=checkpoint,progress=progress)
    assert len(caught.value.partial_collection.rows)==1
    assert source.collect_search_pages(Page(pages),url,all_pages=False).pages_visited==1


@pytest.mark.skipif(os.environ.get('SEARCHCAR_AUCTION_LIVE_SMOKE')!='1',reason='opt-in public auction network smoke')
@pytest.mark.parametrize('key',['copart','iaa'])
def test_real_public_lists_in_background_with_manual_fallback(adapters,key):
    root=Path(__file__).resolve().parents[2]
    inputs=json.loads((root/'docs/fixtures/msa01/public-observations.json').read_text())
    configure_playwright_environment(root/'desktop/runtime/browsers')
    configure_playwright_driver(root/'desktop/runtime/playwright-driver')
    source=get_source(key)
    live_page_size=os.environ.get('SEARCHCAR_AUCTION_LIVE_PAGE_SIZE')
    if key=='iaa' and live_page_size:
        assert live_page_size in ('25','50','100')
        def prepare_pagination(page,*,checkpoint,verification):
            deadline=time.monotonic()+source.read_timeout_seconds
            while True:
                checkpoint()
                state=source.read_dom(page)
                # A header alone predates the completed list navigation. Do
                # not click an old/unbound page-size control during that phase.
                if state.get('ready') and state.get('rows'):break
                if state.get('blocked'):verification('CAPTCHA')
                assert time.monotonic()<deadline,'IAA did not load its page-size control'
                page.wait_for_timeout(250)
            page.locator('#pageSizeHeader').click(timeout=10000)
            print(json.dumps({'source':key,'page_size_menu':page.locator('#pageSizeHeader').evaluate('e => e.parentElement.outerHTML')},ensure_ascii=False),flush=True)
            page.get_by_role('link',name=live_page_size,exact=True).click(timeout=10000)
            deadline=time.monotonic()+source.read_timeout_seconds
            while True:
                checkpoint()
                state=source.read_dom(page)
                if state.get('ready') and 0<len(state.get('rows',[]))<=int(live_page_size):break
                assert time.monotonic()<deadline,'IAA page-size change did not expose new rows'
                page.wait_for_timeout(250)
        adapters.monkeypatch.setattr(source,'prepare_page',prepare_pagination)
    with desktop_playwright() as playwright:
        browser,context,page=source.open_browser(playwright,headless=True,executable_path=os.environ['SEARCHCAR_CHROMIUM_EXECUTABLE'])
        network_errors=[]
        page.on('requestfailed',lambda request:network_errors.append({'path':request.url.split('?')[0],'error':request.failure}))
        page.on('response',lambda response:network_errors.append({'path':response.url.split('?')[0],'status':response.status}) if response.status>=400 else None)
        page.on('pageerror',lambda error:network_errors.append({'script_error':str(error)[:300]}))
        last_progress=[]
        def progress(info):
            marker=(info.get('current_page'),info.get('complete'),info.get('stop_reason'),(info.get('interaction') or {}).get('state'),(info.get('interaction') or {}).get('reason'))
            if last_progress==[marker]:return
            last_progress[:]=[marker]
            print(json.dumps({'source':key,'progress':info}),flush=True)
        try:
            collection=source.collect_search_pages(page,inputs[key]['input_url'],all_pages=True,max_pages=10,
                progress=progress)
            dated=[source.parse_list_row(row) for row in collection.rows.values() if not row.get('out_of_scope')]
            dated=[lot for lot in dated if lot['schedule'].get('local_date')]
            assert collection.total_results==len(collection.rows)
            assert dated, 'No dated public rows were confirmed in this query'
            # Characterization of real rows through the existing persistence
            # boundary, using only this disposable fixture's owner/database.
            with adapters.factory() as db:
                owner=db.get(User,adapters.user_id)
                project=create_project(ProjectIn(name=f'Live {key}',source_key=key,
                    search_url=inputs[key]['input_url'],search_page_mode='ALL_PAGES'),owner,db)
                job=scan_projects(ScanProjectsIn(project_ids=[project['id']]),owner,db)
                stored_project=db.get(Project,project['id'])
                for row in collection.rows.values():
                    if not row.get('out_of_scope'):
                        store_auction_row(db,stored_project,row,scan_id=job['id'],complete=True)
                finish_auction_search(db,stored_project,set(collection.rows),all_pages=True)
                db.commit()
                view=project_auction_view(db,stored_project,now=datetime.now(timezone.utc),computer_timezone='Europe/Warsaw')
                assert len(view['lots'])==len(dated)
                expected={lot['source_listing_id']:lot['schedule'] for lot in dated}
                assert {lot['source_listing_id'] for lot in view['lots']}==set(expected)
                for lot in view['lots']:
                    assert lot['source_key']==key
                    assert lot['schedule']['utc_at']==expected[lot['source_listing_id']]['utc_at']
                    assert lot['schedule']['precision']==expected[lot['source_listing_id']]['precision']
                print(json.dumps(dict(source=key,stored_dated=len(view['lots']),
                    computer_timezone=view['computer_timezone'],storage='DISPOSABLE_TEST_DATABASE')))
            print(json.dumps(dict(source=key,pages=collection.pages_visited,total=collection.total_results,
                dated=len(dated),exact=sum(lot['schedule']['precision']=='EXACT' for lot in dated),
                photos=sum(bool(lot['main_image_source_url']) for lot in dated),
                sample=[dict(id=lot['source_listing_id'],schedule=lot['schedule']) for lot in dated[:2]])))
            uncertain=[row for row in collection.rows.values() if not row.get('out_of_scope')
                and row.get('date_text') not in ('Upcoming lot','Future','Auction Not Assigned')
                and source.parse_list_row(row)['schedule']['precision']!='EXACT']
            print(json.dumps({'source':key,'unrecognized_schedules':[
                {field:row.get(field) for field in ('source_car_id','date_text','sale_list_url','site_city')}
                for row in uncertain[:5]],'schedule_dom':page.evaluate("""ids => Array.from(document.querySelectorAll('#ListingGrid .table-row')).filter(r => ids.some(id=>(r.querySelector('h4 a')?.href||'').includes('/VehicleDetail/'+id))).map(r=>({action:r.querySelector('.data-list__value--action')?.closest('.table-cell')?.outerHTML?.slice(0,2500)}))""",[row['source_car_id'] for row in uncertain[:5]]),
                'pagination_dom':page.evaluate("() => ({pagination:document.querySelector('#divPagination')?.outerHTML?.slice(0,3000),sizes:Array.from(document.querySelectorAll('button,a')).filter(e=>/^(25|50|100)$/.test(e.innerText?.trim()||'')).map(e=>e.outerHTML)})")},ensure_ascii=False))
        except Exception as exc:
            try:
                diagnostic=page.evaluate("() => ({title:document.title,width:innerWidth,height:innerHeight,body:(document.body?.innerText||'').slice(0,1500),tables:document.querySelectorAll('table').length,grid:!!document.querySelector('#ListingGrid'),frames:Array.from(document.querySelectorAll('iframe')).map(e=>e.src.split('?')[0])})")
                diagnostic['controls']=page.evaluate("() => ({pager:document.querySelector('#divPagination')?.outerHTML?.slice(0,3500),size:document.querySelector('#pageSizeHeader')?.parentElement?.outerHTML?.slice(0,2500),city:document.querySelector('#ddlCity')?.outerHTML?.slice(0,500)})")
                diagnostic['state']={k:v for k,v in source.read_dom(page).items() if k!='rows'}
                diagnostic['source_frames']=[]
                from urllib.parse import urlsplit
                for frame in page.frames:
                    if frame==page.main_frame or urlsplit(frame.url).hostname not in source.descriptor.domains:continue
                    try:
                        state=frame.evaluate(source.dom_script)
                        diagnostic['source_frames'].append(dict(path=urlsplit(frame.url).path,
                            same_scope=source.search_scope(frame.url)==source.search_scope(inputs[key]['input_url']),
                            rows=len(state.get('rows',[])),state={k:v for k,v in state.items() if k!='rows'}))
                    except Exception as frame_error:
                        diagnostic['source_frames'].append(dict(path=urlsplit(frame.url).path,error=type(frame_error).__name__))
            except Exception as diagnostic_error:
                diagnostic={'error':type(diagnostic_error).__name__}
            print(json.dumps(dict(source=key,original_error=str(exc)[:700],network_errors=network_errors[:15],diagnostic=diagnostic)))
            raise
        finally:
            context.close()
            if browser is not None:
                browser.close()
