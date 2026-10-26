"""Local calendar-page prototype checks; no external/calendar writes or user data."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, parse_qs

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url',default='http://127.0.0.1:1421/')
parser.add_argument('--report-dir',type=Path,default=root/'work/calendar-page/ui')
parser.add_argument('--check-headers',action='store_true',help='Verify production headers through local wrangler pages dev')
args=parser.parse_args()
if urlsplit(args.base_url).hostname not in ('127.0.0.1','localhost'):
    parser.error('Use an isolated local server only')
report=args.report_dir.resolve();report.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(root/'backend'))
from app.desktop_runtime import desktop_playwright, configure_playwright_environment, configure_playwright_driver, bundled_headless_chromium
configure_playwright_environment(root/'desktop/runtime/browsers')
configure_playwright_driver(root/'desktop/runtime/playwright-driver')
payload=dict(v=1,source='copart',id='65507986',title='Calendar test Audi A5 🚙',url='https://www.copart.com/lot/65507986',start='2099-10-21T13:30:00.000Z',round='round-1',locale='ru')
results=[]
with desktop_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path=str(bundled_headless_chromium(root/'desktop/runtime/browsers')))
    context=browser.new_context(viewport={'width':1280,'height':900},locale='ru-RU',timezone_id='Europe/Warsaw')
    page=context.new_page();errors=[];requests=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('request',lambda request:requests.append(request.url))
    page.clock.install(time=datetime(2099,10,21,10,tzinfo=timezone.utc))
    def visit(data):
        page.goto(args.base_url+'#'+quote(json.dumps(data,ensure_ascii=False),safe=''))
    def check(name,fn):
        try: fn();results.append(dict(name=name,status='passed'))
        except Exception:
            page.screenshot(path=str(report/'failure.png'),full_page=True)
            results.append(dict(name=name,status='failed'))
            (report/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
            raise
    if args.check_headers:
        def security_headers():
            response=context.request.get(args.base_url)
            assert response.status==200
            headers=response.headers
            assert headers.get('referrer-policy')=='no-referrer','Event-page navigation must not leak referrer data'
            assert headers.get('x-frame-options')=='DENY','The hosted event page must prevent framing'
            assert headers.get('x-content-type-options')=='nosniff'
            assert headers.get('x-robots-tag')=='noindex, nofollow'
            csp=headers.get('content-security-policy','')
            for directive in ["default-src 'none'", "script-src 'self'", "style-src 'self'", "connect-src 'none'", "frame-ancestors 'none'"]:
                assert directive in csp,directive
            assert 'unsafe-inline' not in csp and 'unsafe-eval' not in csp
        check('Pages HTTP headers prevent framing, sniffing, indexing, referrer leakage and external scripts/connections',security_headers)
    def event_actions():
        for source,locale in [('copart','ru'),('iaa','uk')]:
            data={**payload,'source':source,'locale':locale,'url':payload['url'] if source=='copart' else 'https://www.iaai.com/VehicleDetail/12345678~US'}
            visit(data)
            page.get_by_role('heading',name='Calendar test Audi A5 🚙',exact=False).wait_for()
            google=page.get_by_role('link',name='Google Calendar',exact=False)
            url=google.get_attribute('href');query=parse_qs(urlsplit(url).query)
            assert urlsplit(url).netloc=='calendar.google.com'
            assert query['dates']==['20991021T133000Z/20991021T133000Z']
            assert data['url'] in query['details'][0]
            assert page.locator('time').get_attribute('datetime')==payload['start']
            assert '15:30' in page.locator('time').inner_text()
            assert 'Europe/Warsaw' in page.inner_text('main')
            page.get_by_role('link',name='↗',exact=False).focus();page.keyboard.press('Tab')
            assert google.evaluate('(e)=>document.activeElement===e')
            assert google.evaluate('(e)=>getComputedStyle(e).outlineStyle')=='solid'
            outlook=page.get_by_role('link',name='Outlook · Windows',exact=False)
            query=parse_qs(urlsplit(outlook.get_attribute('href')).query)
            assert urlsplit(outlook.get_attribute('href')).netloc=='outlook.live.com'
            assert query['startdt']==[payload['start']] and 'enddt' not in query
            assert data['url'] in query['body'][0]
            reminder=page.locator('a[href^="shortcuts://"]')
            reminder_href=reminder.get_attribute('href')
            assert 'name=SearchCar%20Reminder' in reminder_href and '+' not in reminder_href,'Native Shortcuts does not form-decode + spaces'
            query=parse_qs(urlsplit(reminder_href).query)
            assert query['name']==['SearchCar Reminder'] and query['input']==['text']
            reminder_data=json.loads(query['text'][0])
            assert reminder_data['title'].startswith(data['title'])
            assert reminder_data['alert_utc']=='2099-10-21T13:15:00.000Z'
            assert reminder_data['url']==data['url']
            page.locator('.apple-reminder-setup summary').click()
            install=page.locator('.apple-reminder-setup a')
            response=context.request.get(args.base_url.rstrip('/')+install.get_attribute('href'))
            assert response.status==200 and response.body().startswith(b'AEA1')
            if args.check_headers:
                assert response.headers['content-type']=='application/x-apple.shortcut'
                assert response.headers['cache-control']=='no-store'
            page.locator('.apple-reminder-setup summary').click()
            apple=page.get_by_role('link',name='Apple Calendar / .ics',exact=False)
            href=apple.get_attribute('href')
            assert href and not href.startswith(('blob:','data:')),'Apple Calendar needs a real HTTP calendar response, not a forced local download'
            assert apple.get_attribute('download') is None
            endpoint=urlsplit(href)
            assert endpoint.path.endswith('/event.ics') and endpoint.netloc in ('',urlsplit(args.base_url).netloc)
            response=context.request.get(args.base_url.rstrip('/')+'/event.ics?'+endpoint.query)
            assert response.status==200
            assert response.headers['content-type'].startswith('text/calendar')
            assert response.headers['content-disposition']==f'inline; filename="SearchCar-{source}-65507986.ics"'
            assert response.headers['cache-control']=='no-store'
            ics=response.text().replace('\r\n ','')
            assert 'TRIGGER:-PT15M' in ics and 'DTSTART:20991021T133000Z' in ics and data['url'] in ics
            assert 'DTEND:' not in ics and 'ATTENDEE:' not in ics
            for width in [1280,800,360]:
                page.set_viewport_size({'width':width,'height':900})
                assert page.locator('html').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                page.screenshot(path=str(report/f'{source}-{locale}-{width}.png'),full_page=True)
    check('RU/UK Google/Outlook draft, Apple reminder data, signed installer, HTTP ICS, focus and responsive layout without launching apps',event_actions)
    def endpoint_errors():
        url=args.base_url.rstrip('/')+'/event.ics'
        for suffix in ['', '?event=broken', '?event=broken&event=duplicate', '?token=private']:
            response=context.request.get(url+suffix)
            assert response.status==400
            assert response.headers['cache-control']=='no-store'
            assert response.text()=='Calendar event unavailable.'
        assert context.request.post(url).status==405
        response=context.request.head(url+'?event='+quote(quote(json.dumps(payload),safe=''),safe=''))
        assert response.status==200 and response.body()==b''
        assert response.headers['content-type'].startswith('text/calendar')
    check('actual Pages calendar endpoint rejects malformed requests and supports bodyless HEAD',endpoint_errors)
    def invalid():
        for data in [None,{},[],{**payload,'v':2},{**payload,'token':'secret'},{**payload,'url':'javascript:alert(1)'},{**payload,'start':'2026-02-30T10:00:00Z'}]:
            visit(data);page.get_by_role('alert').wait_for();assert page.get_by_role('link').count()==0
        visit({**payload,'title':'<img src=x onerror=alert(1)> & "car"'})
        assert '<img src=x onerror=alert(1)>' in page.locator('h1').inner_text()
        assert page.locator('img').count()==0
    check('malformed/private payloads fail closed and untrusted titles stay plain text',invalid)
    def expires():
        visit(payload)
        page.clock.set_system_time(datetime(2099,10,21,13,20,tzinfo=timezone.utc));page.clock.run_for(1100)
        assert 'меньше 15 минут' in page.get_by_role('status').inner_text()
        assert page.locator('a[href^="shortcuts://"]').count()==0
        assert page.locator('.calendar-option[aria-disabled="true"]').count()==1
        assert page.get_by_role('link',name='Outlook · Windows',exact=False).count()==1
        page.clock.set_system_time(datetime(2099,10,21,13,31,tzinfo=timezone.utc));page.clock.run_for(1100)
        page.get_by_role('alert').wait_for();assert page.get_by_role('link').count()==0
        page.goto(args.base_url);page.get_by_role('alert').wait_for()
    check('late reminder and expiry update an open page without creating calendar events',expires)
    assert not errors,errors
    assert all(url.startswith(args.base_url) for url in requests),requests
    assert not any('/api/' in url for url in requests),requests
    context.close();browser.close()
(report/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(status='passed',checks=len(results),external_requests=0,calendar_writes=0,report=str(report)),ensure_ascii=False))
