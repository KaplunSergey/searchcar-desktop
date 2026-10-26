"""Actual macOS Chrome windows, but no live site or personal browser profile."""
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.desktop_runtime import desktop_playwright, configure_playwright_driver
from app.browser_runtime_pool import BrowserRuntimePool
from app.sources.registry import get_source
from app.models import Project, ProjectCar
from app.auction_storage import store_auction_row
from sqlalchemy import select
from playwright.sync_api import Error as BrowserError
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


@pytest.mark.skipif(os.environ.get('RUN_SOURCE_NATIVE_WINDOW') != '1', reason='Opt-in visible native browser')
def test_actual_native_verification_fallback_reads_two_pages_and_two_cars(adapters):
    root = Path(__file__).resolve().parents[2]
    configure_playwright_driver(root / 'desktop/runtime/playwright-driver')
    source = get_source('iaa')
    html = '''<div>2 VEHICLES <span id="ResultFoundOnPage">1</span></div>
      <select id="ddlCity"><option selected>Chicago</option></select><span id="pageSizeHeader">1</span>
      <div id="ListingGrid"><div class="table-row"><h4><a id="car" href="/VehicleDetail/10001~US">First fixture car</a></h4>
        <a href="/SalesList/100~US/10122026">Sale</a><span class="data-list__value--action">Mon Oct 12, 8:30am CDT</span>
      </div></div>
      <div id="divPagination"><button class="btn-page active">1</button><button id="next" aria-label="Next">Next</button></div>
      <script>document.querySelector('#next').onclick=()=>{
        const a=document.querySelector('#car');a.href='/VehicleDetail/10002~US';a.textContent='Second fixture car';
        document.querySelector('.btn-page').textContent='2';document.querySelector('#next').disabled=true;
      };</script>'''
    launches = []
    with desktop_playwright() as pw:
        def launch(profile, **options):
            launches.append(options['headless'])
            context = pw.chromium.launch_persistent_context(profile, **options)
            body = '<p>Verify you are human</p>' if options['headless'] else html
            context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=body))
            return context
        _, session, _ = source.open_browser(SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch)),
                                            headless=True, executable_path='/bundled/headless_shell')
        session.request_interval_seconds = 0
        progress = []
        try:
            result = source.collect_search_pages(session, 'https://www.iaai.com/Search?url=fixture', all_pages=True, progress=progress.append)
            assert result.pages_visited == 2 and result.total_results == 2
            assert set(result.rows) == {'10001~US', '10002~US'}
            assert {source.parse_list_row(row)['vehicle_title'] for row in result.rows.values()} == {'First fixture car', 'Second fixture car'}
            assert launches == [True, False]
            assert progress[-1]['complete']
            if session.interactive:
                assert progress[-1]['browser_notice'] == 'MANUAL_MINIMIZE'
            # A subsequent update must retain verification, even when native
            # macOS minimization is unavailable. No third launch/activation.
            session.park()
            again = source.collect_search_pages(session, 'https://www.iaai.com/Search?url=fixture', all_pages=True)
            assert set(again.rows) == set(result.rows) and again.pages_visited == 2
            assert launches == [True, False]
            with adapters.factory() as db:
                project = Project(owner_id=adapters.user_id, source_key='iaa', name='Native fixture', name_key='native fixture',
                                  search_url='https://www.iaai.com/Search?url=fixture')
                db.add(project)
                db.flush()
                for row in result.rows.values():
                    assert store_auction_row(db, project, row, scan_id=None, complete=True) is not None
                db.commit()
                assert len(db.scalars(select(ProjectCar).where(ProjectCar.project_id == project.id)).all()) == 2
            actual_page = session.page
            native_browser = session.context.browser
            adapters.monkeypatch.setattr(source, 'open_browser', lambda *_args, **_kwargs: (None, session, session))
            with BrowserRuntimePool(lambda: nullcontext(pw)) as pool:
                assert pool.acquire(adapters.user_id, source)[1] is session
                pool.release(session)
                assert actual_page.is_closed(), 'The native IAA window must not remain on about:blank'
                assert session.context is not None and not session.has_window
                cdp = session.context.new_cdp_session(session.page)
                with pytest.raises(BrowserError, match='window.*not found'):
                    cdp.send('Browser.getWindowForTarget')
                cdp.detach()
                if native_browser is not None:
                    assert native_browser.is_connected(), 'Keep verification without keeping a visible window'
            assert session.context is None
            if native_browser is not None:
                assert not native_browser.is_connected(), 'Final shutdown must close the retained browser'
        finally:
            session.close()
