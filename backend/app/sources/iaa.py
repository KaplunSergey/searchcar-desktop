"""IAA USA public list DOM; opaque filters and VehicleDetail identity retained."""
from .auction_lists import AuctionListSource, AuctionListError, iaa_schedule
import json
import hashlib
import re
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlencode, unquote, parse_qs
from .contracts import InvalidSourceUrlError
from .search_presets import IAACriteria, preset


class IAASource(AuctionListSource):
    key = 'iaa'
    reuse_windowed_browser_session = False
    reuse_hidden_browser_session = True
    search_path = r'/Search/?'
    listing_path = r'/VehicleDetail/(\d+~US)/?'
    listing_prefix = '/VehicleDetail/'
    search_filter_key = 'url'
    default_mileage_unit = None
    next_selector = '#divPagination button[aria-label="Next"]'
    selection_entry_url = 'https://www.iaai.com/Search'

    @staticmethod
    def prepare_fresh_selection(page, *, invalidate):
        # Native reset clears the site's remembered search, not cookies/login.
        clear = page.get_by_text('Clear All Filters', exact=True)
        for index in range(clear.count()):
            button = clear.nth(index)
            if button.is_visible():
                invalidate()
                button.click(timeout=2000)
                return True
        if clear.count():
            raise AuctionListError('SOURCE_SELECTION_RESET_UNAVAILABLE', 'Expand IAA filters to clear the previous search before choosing a new one')
        return False

    def validate_selection_snapshot(self, snapshot):
        result = self.capture_search_request(snapshot['entry_url'], 'POST', snapshot['criteria'])
        if result is None:
            raise ValueError('invalid_iaa_preset')
        if snapshot.get('creation_url'):
            try:
                value = self.validate_search_url(snapshot['creation_url'])
                if set(parse_qs(urlsplit(value).query)) - {'url', 'zipcode'}:
                    raise ValueError('invalid_native_search_url')
            except InvalidSourceUrlError:
                raise ValueError('invalid_native_search_url') from None
            result['creation_url'] = value
        if 'captured_at' in snapshot:
            timestamp = snapshot['captured_at']
            if not isinstance(timestamp, str) or len(timestamp) > 64 or datetime.fromisoformat(timestamp).tzinfo is None:
                raise ValueError('invalid_capture_time')
            result['captured_at'] = timestamp
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 65536:
            raise ValueError('search_conditions_too_large')
        return result

    def complete_selection_snapshot(self, snapshot, state):
        result = deepcopy(snapshot)
        result['creation_url'] = None
        uri = state.get('native_search_uri')
        if isinstance(uri, str) and 0 < len(uri) <= 4096 and re.fullmatch(r'[A-Za-z0-9%+/=]+', uri):
            query = {'url': unquote(uri)}
            if snapshot['criteria']['ZipCode']:
                query['zipcode'] = snapshot['criteria']['ZipCode']
            result['creation_url'] = self.validate_search_url(self.selection_entry_url + '?' + urlencode(query))
            result['captured_at'] = (snapshot.get('captured_at') if snapshot.get('creation_url') == result['creation_url']
                                     else None) or datetime.now(timezone.utc).isoformat()
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 65536:
            raise ValueError('search_conditions_too_large')
        return result

    @staticmethod
    def criteria_scope(criteria):
        clean = IAACriteria.model_validate(criteria).model_dump()
        # Native URI reconstruction reorders groups and adds analytics metadata;
        # retain grouping, every value/range and nonempty full-text condition.
        searches = []
        for search in clean['Searches']:
            facets = sorted((f['Group'], f['Value']) for f in search['Facets'] or [])
            ranges = sorted(((r['Name'], r['From'], r['To']) for r in search['LongRanges'] or []), key=repr)
            if facets == [('Default', 'True')] and not search['FullSearch'] and not ranges:
                continue  # IAA adds this inert baseline when opening its own URI.
            searches.append((facets, search['FullSearch'] or None, ranges))
        clean['Searches'] = sorted(searches, key=repr)
        del clean['CurrentPage'], clean['PageSize']
        for field in ('SaleStatusFilters', 'BidStatusFilters'):
            clean[field] = sorted(clean[field], key=lambda item: json.dumps(item, sort_keys=True))
        return clean

    def search_identity(self, config):
        value = json.dumps(self.criteria_scope(config['criteria']), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(b'iaa-criteria-v1\0' + value.encode('utf-8')).hexdigest()

    @staticmethod
    def is_selection_request(url, method):
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname == 'www.iaai.com'
                and parsed.path.rstrip('/') == '/Search' and method == 'POST'
                and not parsed.username and not parsed.password and parsed.port in (None, 443))

    def capture_search_request(self, url, method, body):
        if not self.is_selection_request(url, method):
            return None
        criteria = IAACriteria.model_validate(body).model_dump(exclude_unset=True)
        criteria['CurrentPage'] = 1
        summary = []
        for search in criteria['Searches']:
            summary.extend(f"{item['Group']}: {item['Value']}" for item in search['Facets'] or [])
            summary.extend(f"{item['Name']}: {item['From']}–{item['To']}" for item in search['LongRanges'] or [])
            if search['FullSearch']:
                summary.append(f"Search: {search['FullSearch']}")
        for field, name in [('SaleStatusFilters', 'SaleStatus'), ('BidStatusFilters', 'BidStatus')]:
            summary.extend(f"{name}: {item[name]}" for item in criteria[field] if item['IsSelected'])
        if criteria['ZipCode'] or criteria['miles']:
            summary.append(f"ZIP: {criteria['ZipCode']} · miles: {criteria['miles']}")
        # The native URI is attached only after its successful result renders.
        return preset(self.key, self.selection_entry_url, criteria, summary)

    @staticmethod
    def parse_schedule(row):
        return iaa_schedule(row.get('date_text'),row.get('sale_list_url'),row.get('site_city'),
                            event_kind=row.get('schedule_event_kind','START'))

    @staticmethod
    def row_is_in_scope(row):
        return str(row.get('source_car_id','')).endswith('~US')

    def prepare_page(self, page, *, checkpoint, verification):
        pass  # The public default grid already contains the required fields.

    def make_search_guard(self, page, config):
        from .iaa_search_restore import IAASearchGuard
        return IAASearchGuard(self, page, config)

    def read_selection_dom(self, page):
        state = self.read_dom(page)
        # Choosing conditions does not require the city/timezone and full-page
        # pagination proof used by the subsequent list collector.
        return {**state, 'ready': state.get('selection_ready', False)}

    dom_script = r"""() => {
      const grid=document.querySelector('#ListingGrid'), text=(e)=>e?.innerText?.trim()||null;
      const header=text(document.querySelector('#ResultFoundOnPage')?.parentElement)||'';
      const count=header.match(/([\d,]+)\s+VEHICLES/i),total=count?Number(count[1].replaceAll(',','')):null;
      const city=document.querySelector('#ddlCity option:checked')?.textContent?.trim()||null;
      const rows=Array.from(grid?.querySelectorAll('.table-row')||[]).filter(r=>r.querySelector('h4 a[href*="VehicleDetail/"]')).map(r=>{
        const link=r.querySelector('h4 a[href*="VehicleDetail/"]');
        const field=(prefix)=>Array.from(r.querySelectorAll('[title]')).find(e=>e.getAttribute('title').startsWith(prefix+':'));
        const value=(prefix)=>text(field(prefix));
        const sale=r.querySelector('a[href*="/SalesList/"]');
        const picture=r.querySelector('a[aria-label="image"] img')||r.querySelector('img');
        const condition=r.querySelector('[aria-label="Vehicle Condition"]');
        const timed=!!r.querySelector('[id^="timedauctionCountdown"]');
        return {source_car_id:link.href.split('/VehicleDetail/')[1]?.split('?')[0],url:link.href,title:text(link),stock_number:value('Stock #'),
          odometer:value('Odometer'),damage:value('Primary Damage'),secondary_damage:value('Secondary Damage'),document:value('Title/Sale Doc'),
          keys:value('Key '),engine_status:text(condition?.querySelector('.badge')),
          location:value('Branch'),retail:value('ACV'),
          date_text:text(r.querySelector('.data-list__value--action'))||(/Auction Not Assigned/.test(text(r)||'')?'Auction Not Assigned':null),
          sale_list_url:sale?.href||null,site_city:city,schedule_event_kind:timed?'UNKNOWN':'START',
          source_action:text(r.querySelector('.btn--pre-bid'))||text(Array.from(r.querySelectorAll('.data-list--action a')).find(e=>/^(BID NOW|JOIN AUCTION)$/i.test(text(e)||''))),
          buy_now:text(Array.from(r.querySelectorAll('a')).find(e=>/^Buy Now\s*\$/i.test(text(e)||''))),
          image:picture?.getAttribute('src')||picture?.getAttribute('data-src')||null};
      });
      const next=document.querySelector('#divPagination button[aria-label="Next"]');
      const active=document.querySelector('#divPagination button.btn-page.active');
      const pageNumber=Number(text(active)), pageSize=Number(text(document.querySelector('#pageSizeHeader')));
      const empty=total===0&&/No (vehicles|results|records)/i.test(header);
      const expectedRows=total!==null&&pageNumber>0&&pageSize>0?Math.min(pageSize,Math.max(0,total-(pageNumber-1)*pageSize)):null;
      const body=(document.body?.innerText||'').slice(0,4000);
      // A background service iframe is not a request for human interaction.
      const visibleChallenge=Array.from(document.querySelectorAll('iframe[src*="captcha" i],iframe[src*="_Incapsula_Resource"]')).some(e=>{
        const rect=e.getBoundingClientRect();
        if(e.clientWidth<=1||e.clientHeight<=1||rect.width<=1||rect.height<=1||rect.bottom<=0||rect.right<=0||rect.top>=innerHeight||rect.left>=innerWidth)return false;
        for(let node=e;node;node=node.parentElement){
          const style=getComputedStyle(node);
          if(style.display==='none'||style.visibility!=='visible'||Number(style.opacity)===0)return false;
        }
        return true;
      });
      const manual=visibleChallenge||/\b(?:verify|confirm)\s+(?:that\s+)?you\s+are\s+(?:a\s+)?human\b|\b(?:complete|solve|enter)\s+(?:(?:the|this)\s+)?(?:captcha|security check)\b/i.test(body);
      const consent=Array.from(document.querySelectorAll('.fc-consent-root .fc-dialog-overlay, #onetrust-banner-sdk, #onetrust-pc-sdk'))
        .some(e=>e.getClientRects().length&&getComputedStyle(e).visibility==='visible');
      const loading=!!document.querySelector('.table.is-loading');
      return {rows,total,page:pageNumber||1,has_next:!!next&&!next.disabled,
        native_search_uri:document.querySelector('#ResultHeaderContainer [name="RequestUri"]')?.value||null,
        restore_ready:!loading&&!!grid&&total!==null,
        consent_required:consent,
        selection_ready:!loading&&!!grid&&total!==null&&(empty||rows.length>0)&&grid.getAttribute('aria-busy')!=='true',
        ready:!loading&&!!grid&&(empty||(!!active&&!!next&&!!city&&expectedRows!==null&&rows.length===expectedRows)),empty,
        manual_required:manual,
        blocked:manual||/Access Denied|security check|Pardon Our Interruption|Request unsuccessful/i.test(body)};
    }"""
