"""Copart USA Classic public DOM, opt-in test profile only."""
import re
import json
from time import monotonic
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from playwright.sync_api import Error as BrowserError
from .auction_lists import AuctionBrowserSession, AuctionListError, AuctionListSource, copart_schedule
from .contracts import InvalidSourceUrlError
from .search_presets import CopartCriteria, preset


class CopartSource(AuctionListSource):
    key = 'copart'
    search_path = r'/(?:ru/)?lotSearchResults/?'
    listing_path = r'/(?:ru/)?lot/(\d+)(?:/[^?#]*)?'
    listing_prefix = '/lot/'
    search_filter_key = 'searchCriteria'
    default_mileage_unit = 'mi'
    next_selector = '#serverSideDataTable_next a'
    selection_entry_url = 'https://www.copart.com/vehicleFinder'
    # Copart bootstraps its newer UI before exposing the Classic switch.
    prepare_timeout_seconds = 90

    def read_selection_dom(self, page):
        state = self.read_dom(page)
        # Selecting a public URL does not depend on Classic's table parser.
        # Actual scans still require fully verified rows and pagination.
        state['ready'] = state.get('document_loaded', False)
        return state

    def validate_selection_snapshot(self, snapshot):
        return self.capture_search_url(snapshot['entry_url'])

    def capture_search_url(self, url):
        if len(url) > 65536:
            raise ValueError('search_conditions_too_large')
        value = self.validate_search_url(url)
        criteria = json.loads(parse_qs(urlsplit(value).query)['searchCriteria'][0])
        clean = CopartCriteria.model_validate(criteria).model_dump(exclude_unset=True)
        summary = list(clean.get('query', []))
        for group, values in clean.get('filter', {}).items():
            summary.extend(f'{group}: {item}' for item in values)
        summary.extend(f'Exclude: {item}' for item in clean.get('exclusions', []))
        # Keep only the public search, not tracking/session-looking URL fields.
        query = parse_qs(urlsplit(value).query)
        public_query = {'searchCriteria': json.dumps(clean, separators=(',', ':'))}
        if 'free' in query:
            if query['free'] not in (['true'], ['false']):
                raise ValueError('unsupported_free_filter')
            public_query['free'] = query['free'][0]
        entry = urlunsplit(urlsplit(value)._replace(query=urlencode(public_query), fragment=''))
        return preset(self.key, entry, clean, summary, creation_url=entry)

    def validate_search_url(self,url):
        value=super().validate_search_url(url)
        parsed=urlsplit(value)
        try:
            if not isinstance(json.loads(parse_qs(parsed.query)['searchCriteria'][0]),dict):
                raise ValueError()
        except ValueError:
            raise InvalidSourceUrlError('Invalid Copart searchCriteria JSON') from None
        return urlunsplit(parsed._replace(path=parsed.path.removeprefix('/ru'))) if parsed.path.startswith('/ru/') else value

    @staticmethod
    def parse_schedule(row):
        return copart_schedule(row.get('date_text'))

    def search_scope(self, url):
        return json.loads(super().search_scope(url))

    def advance_page(self, page):
        if not page.evaluate(self.pagination_script):
            raise AuctionListError('SEARCH_PAGE_NOT_READY', 'Copart pagination could not preserve the search filters')
        super().advance_page(page)

    # Classic rebuilds filters on Next and can drop the URL's MAKE/MODL/VEHT.
    # Keep the actual first request unchanged apart from the native page cursor.
    pagination_script = r"""() => {
      if(typeof jQuery==='undefined'||!jQuery.fn?.dataTable?.isDataTable('#serverSideDataTable')) return false;
      const table=jQuery('#serverSideDataTable');
      const params=table.DataTable().ajax.params();
      if(!params||typeof params!=='object') return false;
      const paging=new Set(['draw','start','length','page','size']);
      const criteria=JSON.parse(JSON.stringify(Object.fromEntries(Object.entries(params).filter(([key])=>!paging.has(key)))));
      table.off('preXhr.dt.searchcar').one('preXhr.dt.searchcar',(_event,_settings,data)=>{
        for(const key of Object.keys(data)) if(!paging.has(key)) delete data[key];
        Object.assign(data,JSON.parse(JSON.stringify(criteria)));
      });
      return true;
    }"""

    @staticmethod
    def row_is_in_scope(row):
        state=re.match(r'^([A-Z]{2})\s*-',row.get('location') or '')
        return bool(state and state[1] in 'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split())

    def prepare_page(self, page, *, checkpoint, verification):
        started=monotonic()
        deadline=started+self.prepare_timeout_seconds
        window_requested=False
        while not page.locator('#serverSideDataTable').count():
            checkpoint()
            if page.locator('#serverSideDataTable').count():
                return
            state=self.read_dom(page)
            if state.get('blocked') or state.get('consent_required'):
                verification('CAPTCHA' if state.get('blocked') else 'COOKIE_CONSENT')
                deadline=monotonic()+self.prepare_timeout_seconds
                continue
            classic=page.get_by_role('button',name=re.compile('Classic view',re.I))
            if classic.count():
                if isinstance(page, AuctionBrowserSession):
                    page.wait_for_request(checkpoint)
                try:
                    classic.click(timeout=5000)
                except BrowserError:
                    # A consent banner can arrive between the DOM check and
                    # click. The shared handler may reject optional cookies;
                    # never force-click through an overlay.
                    if not self.read_dom(page).get('consent_required'):
                        raise
                    verification('COOKIE_CONSENT')
                    deadline=monotonic()+self.prepare_timeout_seconds
                    continue
                return
            if isinstance(page, AuctionBrowserSession) and not page.interactive and not window_requested and monotonic()-started >= 15:
                window_requested=True
                verification('SEARCH_INTERFACE_LOADING')
                continue  # Keep the original deadline; no automatic refresh loop.
            if monotonic()>=deadline:
                interface=state.get('interface') or {}
                raise AuctionListError('SEARCH_PAGE_NOT_READY',
                    f'Copart search interface did not finish loading after {self.prepare_timeout_seconds}s '
                    f'(document_loaded={bool(state.get("document_loaded"))}; Classic view/table unavailable; '
                    f'modern_table={bool(interface.get("modern_table"))}; '
                    f'unrendered_template={bool(interface.get("unrendered_template"))}; '
                    f'visible_frames={interface.get("visible_frames",0)})')
            page.wait_for_timeout(250)

    dom_script = r"""() => {
      const table=document.querySelector('#serverSideDataTable');
      const text=(e)=>e?.innerText?.trim()||null;
      const info=text(document.querySelector('#serverSideDataTable_info'))||'';
      const counts=info.match(/Showing\s+([\d,]+)\s+to\s+([\d,]+)\s+of\s+([\d,]+)\s+entries/i);
      const total=counts?Number(counts[3].replaceAll(',','')):null;
      const number=text(document.querySelector('#serverSideDataTable_paginate li.active a'));
      const body=(document.body?.innerText||'').slice(0,4000);
      const consent=Array.from(document.querySelectorAll('.fc-consent-root .fc-dialog-overlay, #onetrust-banner-sdk, #onetrust-pc-sdk'))
        .some(e=>e.getClientRects().length&&getComputedStyle(e).visibility==='visible');
      const rows=Array.from(table?.querySelectorAll('tbody tr')||[]).filter(r=>r.querySelector('[data-uname="lotsearchLotnumber"]')).map(r=>{
        const field=(name)=>r.querySelector('[data-uname="'+name+'"]');
        const image=r.querySelector('[data-uname="lotsearchLotimage"]');
        const icons=Array.from(r.querySelectorAll('img')).map(i=>i.alt||'');
        const engine=icons.some(a=>/CERT-D|Run and Drive/i.test(a))?'Run & Drive':icons.some(a=>/CERT-R|Engine Start/i.test(a))?'Engine Start':icons.some(a=>/CERT-E|Enhanced/i.test(a))?'Enhanced':null;
        const link=field('lotsearchLotnumber');
        return {source_car_id:text(link),url:link?.href,title:[text(field('lotsearchLotcenturyyear')),text(field('lotsearchLotmake')),text(field('lotsearchLotmodel'))].filter(Boolean).join(' '),
          date_text:text(field('lotsearchLotauctiondate')),odometer:text(field('lotsearchLotodometerreading')),accuracy:field('lotsearchLotodometerreading')?.getAttribute('title')||null,
          damage:text(field('lotsearchLotdamagedescription')),document:field('lotsearchSaletitletype')?.getAttribute('title')||null,
          location:text(field('lotsearchLotyardname')),engine_status:engine,retail:text(field('lotsearchLotestimatedretailvalue')),
          bid:text(field('lotsearchLothighbid')),buy_now:text(field('lotsearchLotbuynowprice')),image:image?.getAttribute('src')||image?.getAttribute('lazy-src')||null};
      });
      return {rows,total,page:Number(number)||1,has_next:!!document.querySelector('#serverSideDataTable_next:not(.disabled)'),
        document_loaded:['interactive','complete'].includes(document.readyState),
        can_prepare:Array.from(document.querySelectorAll('button')).some(e=>/Classic view/i.test(text(e)||'')),
        interface:{modern_table:!!document.querySelector('p-table table'),unrendered_template:body.includes('{{'),
          visible_frames:Array.from(document.querySelectorAll('iframe')).filter(e=>e.getClientRects().length&&e.clientWidth>0&&e.clientHeight>0&&getComputedStyle(e).visibility==='visible').length},
        consent_required:consent,
        ready:!!table&&!!counts,empty:total===0&&/No (matching records|data available)/i.test(text(table)||''),
        manual_required:/verify you are human|confirm you are human|security check|captcha/i.test(body)||(!table&&!!document.querySelector('iframe[src*="captcha"],iframe[src*="_Incapsula_Resource"]')),
        blocked:/Access Denied|verify you are human|confirm you are human|security check|captcha|Pardon Our Interruption|Request unsuccessful/i.test(body)||(!table&&!!document.querySelector('iframe[src*="captcha"],iframe[src*="_Incapsula_Resource"]'))};
    }"""
