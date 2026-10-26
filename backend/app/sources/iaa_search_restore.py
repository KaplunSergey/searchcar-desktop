"""Restore through IAA's native URI/controls and verify ordinary search traffic."""
import json
from urllib.parse import urlsplit

from .auction_lists import AuctionBrowserSession, AuctionListError


class IAASearchGuard:
    def __init__(self, source, page, config):
        self.source, self.page = source, page
        self.criteria = config['criteria']
        self.expected = source.criteria_scope(self.criteria)
        self.reset()
        self.listeners = [('request', self.started), ('requestfinished', self.finished),
                          ('requestfailed', self.failed)]
        for event, callback in self.listeners:
            page.on(event, callback)

    def reset(self):
        self.phase = None
        self.latest = self.actual = self.cursor = self.size = self.error = None
        self.completed = False
        self.first_verified = False

    def started(self, request):
        if request.frame != self.page.main_frame:
            return
        if request.resource_type == 'document' and urlsplit(request.url).hostname == 'www.iaai.com':
            self.reset()
            return
        if not self.source.is_selection_request(request.url, request.method):
            return
        self.latest, self.completed, self.actual = request, False, None
        try:
            raw = request.post_data or ''
            if len(raw.encode()) > 65536:
                raise ValueError('search_conditions_too_large')
            criteria = json.loads(raw)
            self.actual = self.source.criteria_scope(criteria)
            self.cursor = criteria['CurrentPage']
            self.size = criteria['PageSize']
        except (ValueError, TypeError, KeyError):
            self.error = 'SEARCH_FILTERS_UNSUPPORTED'

    def finished(self, request):
        if request.frame != self.page.main_frame:
            return
        if request != self.latest:
            # An older result can repaint the grid after the current request.
            if self.source.is_selection_request(request.url, request.method):
                self.error = 'SOURCE_SEARCH_SCOPE_CHANGED'
            return
        response = request.response()
        if response is None or response.status >= 400:
            if response is None or response.status not in (401, 403, 429):
                self.error = 'SEARCH_RESTORE_FAILED'
            return  # Shared access/consent/rate-limit handling retains priority.
        self.completed = True
        if (self.phase == 'PAGE_SIZE' or self.first_verified) and self.size != self.criteria['PageSize']:
            self.error = 'SEARCH_RESTORE_FAILED'
            return
        actual, expected = self.actual, self.expected
        if not self.first_verified and self.phase != 'PAGE_SIZE' and actual is not None:
            actual = {key: value for key, value in actual.items() if key != 'Sort'}
            expected = {key: value for key, value in expected.items() if key != 'Sort'}
        if actual != expected:
            self.error = 'SOURCE_SEARCH_SCOPE_CHANGED'

    def failed(self, request):
        if request == self.latest:
            self.error = 'SEARCH_RESTORE_FAILED'

    def check(self):
        if self.error:
            raise AuctionListError(self.error, 'IAA could not restore and verify the saved search conditions; no general search was used')

    def accept(self, state, checkpoint):
        self.check()
        if self.phase is None:
            checkpoint()
            if isinstance(self.page, AuctionBrowserSession):
                self.page.wait_for_request(checkpoint)
            # Native URIs can include page 3 while the restored page size is 100.
            # Rewind before changing sort/size, otherwise IAA can render no rows.
            if self.page.evaluate(REWIND_CONTROL):
                self.phase = 'FIRST'
            elif state.get('empty') and state.get('total') == 0:
                # An empty search has no page-one control. Native sort/size
                # still emits its complete query, which must match before use.
                self.restore_controls()
            return False
        if self.phase in ('FIRST', 'SORT') and self.completed:
            checkpoint()
            if isinstance(self.page, AuctionBrowserSession):
                self.page.wait_for_request(checkpoint)
            self.restore_controls()
            return False
        verified_empty = bool(state.get('empty') and state.get('total') == 0)
        accepted = bool(self.completed and self.actual == self.expected and self.size == self.criteria['PageSize']
                        and (self.cursor == state.get('page') or verified_empty))
        if accepted and not self.first_verified:
            accepted = self.cursor == 1 or verified_empty
            self.first_verified = accepted
        return accepted

    def restore_controls(self):
        action = self.page.evaluate(RESTORE_CONTROLS, {'sort': self.criteria['Sort'], 'size': self.criteria['PageSize']})
        if action not in ('SORT', 'PAGE_SIZE'):
            raise AuctionListError('SEARCH_RESTORE_UNSUPPORTED', 'IAA no longer exposes a supported native sort/page-size control for this search')
        self.phase = action

    def close(self):
        for event, callback in self.listeners:
            self.page.remove_listener(event, callback)


# These are native control handlers, not an API client, saved JS or model injection.
REWIND_CONTROL = r"""() => {
  if(location.origin!=='https://www.iaai.com')return false;
  const control=document.querySelector('#divPagination #PageNumber1');
  if(!control)return false;
  control.click();return true;
}"""

RESTORE_CONTROLS = r"""({sort,size}) => {
  if(location.origin!=='https://www.iaai.com'||!document.querySelector('#ListingGrid'))return null;
  const same=(a,b)=>JSON.stringify(a.map(s=>[s.IsGeoSort,s.SortField,s.IsDescending]))===JSON.stringify(b.map(s=>[s.IsGeoSort,s.SortField,s.IsDescending]));
  let current,available;
  try{current=JSON.parse(document.querySelector('#SortFields')?.textContent||'null');
      available=JSON.parse(document.querySelector('#Sorts')?.textContent||'null');}catch{return null}
  if(!Array.isArray(current)||!Array.isArray(available))return null;
  if(!same(current,sort)){
    if(sort.length!==1||sort[0].IsGeoSort)return null;
    const option=available.find(s=>s.Request&&same([s.Request],sort));
    const control=option&&document.getElementById(option.Id);
    if(!control||!control.matches('a[data-bind*="SortResults"]'))return null;
    control.click();return 'SORT';
  }
  const control=Array.from(document.querySelectorAll('a[data-bind*="UpdatePageSize"]')).find(e=>e.textContent.trim()===String(size));
  if(!control)return null;
  control.click();return 'PAGE_SIZE';
}"""
