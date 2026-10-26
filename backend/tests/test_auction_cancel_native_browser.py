"""Native Chrome + real queue/worker; all site responses and storage are local."""
from contextlib import nullcontext
import os
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as BrowserError
from sqlalchemy import select

from app.browser_runtime_pool import BrowserRuntimePool
from app.desktop_runtime import configure_playwright_driver, desktop_playwright
from app.job_queue import claim_next_job
from app.main import cancel_scan, create_project, scan_projects
from app.models import Car, Project, ProjectCar, ScanRun, User
from app.schemas import CancelScanIn, ProjectIn, ScanProjectsIn
from app.sources.registry import get_source
from app.worker import process_job
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_NATIVE_WINDOW') != '1', reason='Opt-in native Chrome cancellation')
@pytest.mark.parametrize('stage', ['verification', 'pagination'])
def test_cancelled_iaa_releases_queue_window_and_preserves_saved_lots(adapters, stage):
    configure_playwright_driver(Path(__file__).resolve().parents[2] / 'desktop/runtime/playwright-driver')
    source = get_source('iaa')
    mode = {'phase': 'warm'}
    launches, sessions, cancel_threads, cancel_errors = [], [], [], []
    html = '''<span id="ResultFoundOnPage">TOTAL VEHICLES</span>
      <select id="ddlCity"><option selected>Chicago</option></select><span id="pageSizeHeader">1</span>
      <div id="ListingGrid"><div class="table-row"><h4><a id="car" href="/VehicleDetail/FIRST~US">Fixture</a></h4>
      <a href="/SalesList/100~US/10122026">Sale</a><span class="data-list__value--action">Mon Oct 12, 8:30am CDT</span></div></div>
      <div id="divPagination"><button class="btn-page active">1</button><button id="next" aria-label="Next" DISABLED>Next</button></div>
      <script>document.querySelector('#next').onclick=()=>{
        document.querySelector('#car').href='/VehicleDetail/10002~US';
        document.querySelector('.btn-page').textContent='2';document.querySelector('#next').disabled=true;};</script>'''
    with adapters.factory() as db:
        owner = db.get(User, adapters.user_id)
        projects = {name: create_project(ProjectIn(name=name, source_key='iaa',
            search_url='https://www.iaai.com/Search?url=' + name,
            search_page_mode='ALL_PAGES'), owner, db)['id'] for name in ('A', 'B')}

    def enqueue(name):
        with adapters.factory() as db:
            return scan_projects(ScanProjectsIn(project_ids=[projects[name]]), db.get(User, adapters.user_id), db)['id']

    def stored(name):
        with adapters.factory() as db:
            return {car.source_listing_id: relation.search_status for car, relation in db.execute(
                select(Car, ProjectCar).join(ProjectCar, ProjectCar.car_id == Car.id)
                .where(ProjectCar.project_id == projects[name], ProjectCar.tracking_enabled.is_(True)))}

    def cancel(job_id):
        try:
            with adapters.factory() as db:
                cancel_scan(job_id, CancelScanIn(all_projects=True), db.get(User, adapters.user_id), db)
        except Exception as exc:
            cancel_errors.append(exc)

    def published(_owner, job_id):
        if mode['phase'] != 'cancel' or cancel_threads:
            return
        with adapters.factory() as db:
            info = (db.get(ScanRun, job_id).payload.get('pagination') or {}).get(str(projects['A']), {})
        ready = (info.get('interaction') or {}).get('state') == 'WAITING_FOR_USER' and sessions[-1].has_window
        if (stage == 'verification' and ready) or (stage == 'pagination' and info.get('pages_visited') == 1):
            thread = Thread(target=cancel, args=(job_id,))
            cancel_threads.append(thread)
            thread.start()

    adapters.monkeypatch.setattr('app.worker.publish_scan_update', published)
    adapters.monkeypatch.setattr('app.desktop_license.require_search_entitlement', lambda *_: None)
    adapters.monkeypatch.setattr(source, 'silent_verification_seconds', 0)
    adapters.monkeypatch.setattr(source, 'verification_timeout_seconds', 10)
    adapters.monkeypatch.delenv('SEARCHCAR_CHROMIUM_EXECUTABLE', raising=False)
    original_collect = source.collect_search_pages
    def collect(*args, **kwargs):
        checkpoint = kwargs['checkpoint']
        def after_cancel_command():
            # Complete the concurrent API command outside the worker's command lock.
            for thread in cancel_threads:
                thread.join(timeout=5)
                assert not thread.is_alive() and not cancel_errors
            checkpoint()
        return original_collect(*args, **{**kwargs, 'checkpoint': after_cancel_command})
    adapters.monkeypatch.setattr(source, 'collect_search_pages', collect)
    with desktop_playwright() as pw:
        def launch(profile, **options):
            launches.append(options['headless'])
            context = pw.chromium.launch_persistent_context(profile, **options)
            def site(route):
                is_b = route.request.url.endswith('=B')
                if mode['phase'] == 'cancel' and stage == 'verification' and not is_b:
                    body = '<p>Verify you are human</p>'
                else:
                    total = 2 if mode['phase'] != 'warm' and not is_b else 1
                    first = '20001' if is_b else '10003' if mode['phase'] == 'warm' else '10001'
                    body = html.replace('TOTAL', str(total)).replace('FIRST', first).replace('DISABLED', 'disabled' if total == 1 else '')
                route.fulfill(content_type='text/html', body=body)
            context.route('**/*', site)
            return context
        driver = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        original_open = source.open_browser
        def open_browser(*args, **kwargs):
            # Begin with a verified visible fixture session; no real CAPTCHA is solved.
            handles = original_open(*args, **kwargs, interactive=True)
            handles[1].request_interval_seconds = 0
            sessions.append(handles[1])
            return handles
        adapters.monkeypatch.setattr(source, 'open_browser', open_browser)
        engine = adapters.factory.kw['bind']
        try:
            with BrowserRuntimePool(lambda: nullcontext(driver)) as pool:
                warm = enqueue('A')
                assert claim_next_job(engine, 'fixture') == warm
                process_job(warm, runtime_pool=pool)
                with adapters.factory() as db:
                    assert db.get(ScanRun, warm).status == 'SUCCEEDED'
                assert stored('A') == {'10003~US': 'FOUND'}
                session = sessions[0]
                original_context = session.context
                original_browser = original_context.browser
                assert not session.has_window
                mode['phase'] = 'cancel'
                cancelled, following = enqueue('A'), enqueue('B')
                assert claim_next_job(engine, 'fixture') == cancelled
                assert claim_next_job(engine, 'other-fixture') is None
                process_job(cancelled, runtime_pool=pool)
                assert cancel_threads, 'The intended cancellation boundary was never reached'
                cancel_threads[0].join(timeout=5)
                assert not cancel_threads[0].is_alive() and not cancel_errors
                with adapters.factory() as db:
                    run = db.get(ScanRun, cancelled)
                    assert run.status == 'CANCELLED' and run.finished_at is not None
                    assert run.payload['project_statuses'][str(projects['A'])] == 'CANCELLED'
                    assert not run.payload['pagination'][str(projects['A'])]['complete']
                    assert db.get(ScanRun, following).status == 'QUEUED'
                    assert db.get(Project, projects['A']).search_url.endswith('=A')
                    assert db.get(Project, projects['B']).search_url.endswith('=B')
                assert stored('A') == ({'10003~US': 'FOUND', '10001~US': 'FOUND'} if stage == 'pagination' else {'10003~US': 'FOUND'})
                assert stored('B') == {}
                assert session.context is original_context and not session.has_window
                assert len(original_context.pages) == 1
                cdp = original_context.new_cdp_session(session.page)
                with pytest.raises(BrowserError, match='window.*not found'):
                    cdp.send('Browser.getWindowForTarget')
                cdp.detach()
                mode['phase'] = 'resume'
                assert claim_next_job(engine, 'fixture') == following
                process_job(following, runtime_pool=pool)
                assert stored('B') == {'20001~US': 'FOUND'}
                again = enqueue('A')
                assert claim_next_job(engine, 'fixture') == again
                process_job(again, runtime_pool=pool)
                with adapters.factory() as db:
                    assert db.get(ScanRun, following).status == db.get(ScanRun, again).status == 'SUCCEEDED'
                    assert not db.scalars(select(ScanRun).where(ScanRun.status.in_(['QUEUED', 'RUNNING', 'CANCEL_REQUESTED']))).all()
                assert stored('A') == {'10003~US': 'NOT_FOUND_IN_SEARCH', '10001~US': 'FOUND', '10002~US': 'FOUND'}
                assert stored('B') == {'20001~US': 'FOUND'}
                assert len(sessions) == 1 and launches == [False]
            assert session.context is None and not original_browser.is_connected()
        finally:
            for thread in cancel_threads:
                thread.join(timeout=5)
