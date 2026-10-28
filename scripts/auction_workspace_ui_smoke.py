"""Real frontend interaction checks; all API and media are disposable fixtures."""
import argparse
import json
import os
import sys
import traceback
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url',default='http://127.0.0.1:1420/')
parser.add_argument('--report-dir',type=Path,default=root/'work/auction-ui-smoke')
parser.add_argument('--only',help='Run checks whose name contains this text')
args=parser.parse_args()
if urlsplit(args.base_url).hostname not in ('127.0.0.1','localhost'):
    parser.error('Use a local frontend; every API/media response is a fixture')
report=args.report_dir.resolve()
report.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(root/'backend'))
os.environ.update(STORAGE_ROOT=str(report/'ui-storage'),DATABASE_URL=f'sqlite+pysqlite:///{report/"ui.sqlite3"}')
from app.desktop_runtime import desktop_playwright,configure_playwright_environment,configure_playwright_driver,bundled_headless_chromium
configure_playwright_environment(root/'desktop/runtime/browsers')
configure_playwright_driver(root/'desktop/runtime/playwright-driver')
BASE=args.base_url.rstrip('/')+'/'
lot=dict(id=41,source_key='copart',source_listing_id='65507986',title='Audi fixture',url='https://www.copart.com/lot/65507986',image='/fixture-photo.svg',
    details={},money_values=[],round=None,schedule={},time=dict(event_kind='START',precision='UNKNOWN',local_date=None,local_datetime=None,is_today=None,remaining_seconds=None,time_state='UNKNOWN'))
project=dict(id=9,source_key='copart',name='Audi A4 · Очень длинное название проекта для проверки переносов',cars=2,
    created_at='2026-10-10T10:00:00Z',last_refreshed_at='2026-10-15T10:00:00Z',updated_at='2026-10-16T10:00:00Z',search_page_mode='ALL_PAGES')
results=[]
with desktop_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path=str(bundled_headless_chromium(root/'desktop/runtime/browsers')))
    context=browser.new_context(viewport={'width':1280,'height':900},locale='ru-RU',timezone_id='Europe/Warsaw')
    page=context.new_page()
    calls=[]
    held_selection=[]
    draft_selections={}
    draft_reads=[]
    removed_projects=set()
    deleted_projects=set()
    held_project_deletes=[]
    held_removals=[]
    held_bulk=[]
    held_edits=[]
    held_snapshots=[]
    held_telegram=[]
    held_telegram_checks=[]
    held_recipient_reuses=[]
    held_reschedules=[]
    telegram_checks=[]
    delivery_batches={}
    delivery_intents=[]
    telegram_reads=[]
    snapshot_reads=[]
    mode={'source':'copart','fail_launch':False,'can_search':True,'invalid_url':False,'created':False,'locale':'ru'}
    def selection():
        issue=mode.get('selection_issue')
        return dict(id='a'*32,source_key=mode['source'],project_id=mode.get('edit_project'),status=mode.get('selection_status','CLOSED'),issue=issue,can_use=not issue and not mode.get('empty_selection'),
            snapshot=None if mode.get('empty_selection') else dict(summary=['Make: Cadillac','Model: ESCALADE','Year: 2017–2027'],revision='fixture',
                creation_url='https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D' if mode['source']=='copart'
                else ('https://www.iaai.com/Search?url=public-native-uri' if mode.get('iaa_native_ready') else None)))
    def snapshot_response(req):
        query=parse_qs(urlsplit(req.url).query)
        source=query['source_key'][0]
        scan_id=int(urlsplit(req.url).path.split('/')[-2])
        run=next(run for run in mode['runs'] if run['id']==scan_id)
        items=[]
        for car_id in map(int,query['car_ids']):
            item=next(item for item in run['payload']['report'] if item['car_id']==car_id)
            view=dict(generated_at='2026-10-19T10:00:00Z',captured_round_id='saved-round',computer_timezone=query['computer_timezone'][0],local_date='2026-10-19',
                day_start_utc='2026-10-18T22:00:00Z',day_end_utc='2026-10-19T22:00:00Z',lot={**lot,'id':car_id,'source_key':source,
                'source_listing_id':str(65000000+car_id),'title':f'Saved snapshot {car_id}',
                'details':{'mileage':{'value':12345,'unit':'mi','accuracy':'ACTUAL'},'primary_damage':'FRONT END','keys':False},
                'money_values':[dict(amount_minor=12300,currency='USD',meaning='CURRENT_BID',state='KNOWN',auction_round='saved-round')],
                'schedule':dict(event_kind='START',precision='EXACT',utc_at='2026-10-19T18:30:00Z',local_date='2026-10-19',observed_at='2026-10-19T10:00:00Z'),
                'time':dict(event_kind='START',precision='EXACT',local_date='2026-10-19',local_datetime='2026-10-19T20:30:00+02:00',is_today=True,remaining_seconds=30600,time_state='UPCOMING_START')})
            missing=car_id in mode.get('snapshot_unavailable_ids',[])
            items.append(dict(car_id=car_id,snapshot_id=None if missing else item['after_snapshot_id'],recorded_at=None if missing else view['generated_at'],
                view=None if missing else view,issue='SNAPSHOT_UNAVAILABLE' if missing else None))
        return dict(scan_id=scan_id,source_key='other' if mode.get('snapshot_wrong_source') else source,computer_timezone=query['computer_timezone'][0],items=items)
    def api(route):
        req=route.request
        path=urlsplit(req.url).path.removeprefix('/api')
        calls.append((req.method,path,req.post_data))
        status=200
        user=dict(id=1,username='fixture',role='USER',preferred_locale=mode['locale'],must_change_password=False)
        if path=='/auth/me': value={'user':user,'csrf_token':'fixture'}
        elif path=='/auth/workspace': value={'saved':True}
        elif path=='/desktop/runtime':
            if mode.get('runtime_offline'):
                route.abort('connectionfailed');return
            value={'desktop':True,'source_test_mode':True,'updater':False,'telegram_delivery_enabled':bool(mode.get('delivery_enabled'))}
        elif path=='/settings': value={'enabled':False,'paused':False,'project_ids':[],'interval_minutes':180,'performance_mode':'FAST'}
        elif path=='/scans/launch-options':
            value={'sources':mode.get('bulk_sources') or [{'source_key':s,'family':'AUCTION','can_search':mode['can_search'],'reason':None} for s in ['copart','iaa']],
                'projects':mode.get('bulk_projects') if mode.get('bulk_projects') is not None else mode.get('progress_projects') or [{'id':9,'source_key':mode['source'],'name':project['name'],'can_search':mode['can_search'],'active_status':None,'reason':None}]}
            if mode.get('availability_error'): status=503;value={'detail':'fixture_unavailable'}
        elif path=='/scans/projects':
            if mode.get('hold_bulk'):
                held_bulk.append(route)
                return
            value={'detail':'source_access_denied'} if mode['fail_launch'] else {'id':101,'created':True,'status':'QUEUED','projects':[{'project_id':9,'status':'QUEUED','scan_run_id':101}]}
            if mode.get('bulk_projects') is not None:
                ids=json.loads(req.post_data)['project_ids']
                mode['runs']=[dict(id=101,kind='PROJECTS',status='QUEUED',progress=0,created_at='2026-10-19T10:00:00Z',payload=dict(
                    project_ids=ids,project_plans=[dict(project_id=p['id'],source_key=p['source_key'],name=p['name']) for p in mode['bulk_projects'] if p['id'] in ids],project_statuses={}))]
                value={**value,'projects':[dict(project_id=pid,status='ALREADY_ACTIVE' if mode.get('bulk_race') else 'QUEUED',scan_run_id=101) for pid in ids]}
            if mode.get('progress_projects'):
                mode['runs']=[dict(id=101,kind='PROJECTS',status='QUEUED',progress=0,created_at='2026-10-18T10:00:00Z',payload=dict(
                    project_ids=[9,10,11],project_plans=[dict(project_id=p['id'],source_key=p['source_key'],name=p['name']) for p in mode['progress_projects']],project_statuses={}))]
                value={**value,'projects':[dict(project_id=p['id'],status='QUEUED',scan_run_id=101) for p in mode['progress_projects']]}
            status=503 if mode['fail_launch'] else 200
        elif path=='/scans/active': value=[run for run in mode.get('runs',[]) if run['status'] in ('QUEUED','RUNNING','CANCEL_REQUESTED')]
        elif path=='/scans/101':
            value=mode.get('runs',[{}])[0]
            if mode.get('progress_error'): status=503;value={'detail':'fixture_read_failed'}
        elif path=='/scans/102': value=next((run for run in mode.get('runs',[]) if run['id']==102),{})
        elif path=='/scans':
            value=mode.get('runs',[])
            if mode.get('history_error'): status=503;value={'detail':'fixture_history_unavailable'}
        elif path.endswith('/cancel') and path.startswith('/scans/'):
            if mode.get('history_cancel_error'): status=503;value={'detail':'fixture_cancel_unavailable'}
            else:
                run=next(run for run in mode['runs'] if run['id']==int(path.split('/')[2]))
                run['payload']['project_statuses']['9']='CANCEL_REQUESTED'
                value=run
        elif path.endswith('/auction-report') and path.startswith('/scans/'):
            snapshot_reads.append(parse_qs(urlsplit(req.url).query))
            if mode.get('hold_snapshots'): held_snapshots.append(route);return
            if mode.get('snapshot_error'): status=503;value={'detail':'fixture_snapshot_failed'}
            else: value=snapshot_response(req)
        elif path.startswith('/scans'): value=[]
        elif path.startswith('/source-search/'):
            draft_id=(json.loads(req.post_data).get('draft_id') if req.method=='POST'
                      else parse_qs(urlsplit(req.url).query).get('draft_id',[None])[0])
            if req.method=='GET': draft_reads.append(draft_id)
            if req.method=='POST':
                body=json.loads(req.post_data)
                action=body['action']
                if mode.get('editing_fixture'): mode['edit_project']=body.get('project_id')
                if action=='OPEN' and mode.get('hold_selection'):
                    held_selection.append(route)
                    return
                mode['selection_status']='READY' if action=='OPEN' else 'CLOSED'
                if mode.get('scoped_drafts'): draft_selections[draft_id]=selection()
            value={'selection':draft_selections.get(draft_id) if mode.get('scoped_drafts') else selection()}
        elif path=='/projects/9/search-selection' and req.method=='POST':
            if mode.get('hold_edit'):
                held_edits.append(route);return
            if mode.get('edit_error'): status=409;value={'detail':'project_search_changed'}
            else: value={**project,'search_revision':2}
        elif path=='/projects/9' and req.method=='GET':
            value={**project,'source_key':mode['source'],'search_revision':1,'search_config':{'summary':['Make: Audi','Year: 2022–2027']}}
        elif path=='/telegram/recipients':
            value=dict(available=bool(mode.get('pairing_fixture')),recipients=mode.get('saved_recipients',[]))
        elif path=='/telegram/automatic-clock': value={'ok':True}
        elif path=='/projects/9/telegram-reschedules':
            if req.method=='POST':
                if mode.get('reschedule_hold'):
                    held_reschedules.append(route);return
                if mode.get('reschedule_write_error'):status=503
                else:mode['reschedule_enabled']=json.loads(req.post_data)['enabled']
            value=dict(project_id=9,source_key=mode['source'],enabled=mode.get('reschedule_enabled',True),
                recipient=mode.get('pairing_recipient'),counts=mode.get('reschedule_counts',{}),issue=mode.get('reschedule_issue'),max_result_age_minutes=30)
            if mode.get('reschedule_read_error') and req.method=='GET':status=503
        elif path=='/projects/9/telegram-automatic':
            if req.method=='POST':
                if mode.get('auto_write_error'):
                    status=503
                else:mode['auto_enabled']=json.loads(req.post_data)['enabled']
            value=dict(project_id=9,source_key=mode['source'],enabled=bool(mode.get('auto_enabled')),recipient=mode.get('pairing_recipient'),
                batch=mode.get('auto_batch'),issue=mode.get('auto_issue'),max_result_age_minutes=30)
            if mode.get('auto_read_error') and req.method=='GET':status=503
        elif path=='/projects/9/telegram':
            if req.method=='GET':
                telegram_checks.append(parse_qs(urlsplit(req.url).query))
                if mode.get('telegram_check_hold'):
                    held_telegram_checks.append(route);return
            value=dict(available=bool(mode.get('pairing_fixture')),issue=None if mode.get('pairing_fixture') else 'telegram_relay_not_configured',
                chat_pairing_enabled=bool(mode.get('chat_pairing_enabled')),
                recipient=mode.get('pairing_recipient'),session=mode.get('pairing_session'),delivery_enabled=bool(mode.get('delivery_enabled')))
            if req.method=='POST':
                command=json.loads(req.post_data)
                if mode.get('pairing_error'): status=503;value={'detail':mode.get('pairing_error_code','telegram_pairing_unavailable')}
                elif command['action']=='START':
                    mode['pairing_session']=dict(pairing_id='d'*32,project_id=9,status='WAITING_START',expires_at_ms=int(time.time()*1000)+600000,recipient=None,target_kind=command.get('target_kind','private'))
                    value={**value,'session':{**mode['pairing_session'],'start_url':'https://t.me/search_car_app_bot?start=p_'+'x'*32}}
                elif command['action']=='CHECK':
                    if mode.get('pairing_claimed'):
                        kind=mode['pairing_session'].get('target_kind','private')
                        recipient=dict(title='Verified team <script>',chat_id=-100555,chat_type='supergroup' if kind=='group' else 'channel') if kind!='private' else dict(title='Alex <script>',chat_id=555,chat_type='private')
                        mode['pairing_session']={**mode['pairing_session'],'status':'AWAITING_CONFIRMATION','recipient':recipient}
                    value={**value,'session':mode.get('pairing_session')}
                elif command['action']=='CONFIRM':
                    recipient=mode['pairing_session']['recipient']
                    assert command['chat_id']==recipient['chat_id'] and command['pairing_id']=='d'*32
                    mode['pairing_session']={**mode['pairing_session'],'status':'CONNECTED'}
                    mode['pairing_recipient']=dict(project_id=9,**recipient,binding_id='b'*32)
                    value={**value,'session':mode['pairing_session'],'recipient':mode['pairing_recipient']}
                    mode['saved_recipients']=[dict(id='a'*32,bot_id=123456,**recipient)]
                elif command['action']=='USE_SAVED':
                    assert command['recipient_id']=='a'*32
                    saved=next(item for item in mode['saved_recipients'] if item['id']==command['recipient_id'])
                    mode['pairing_recipient']=dict(project_id=9,**{key:saved[key] for key in ('title','chat_id','chat_type')},binding_id='c'*32)
                    kind='channel' if saved['chat_type']=='channel' else 'group' if saved['chat_type'] in ('group','supergroup') else 'private'
                    mode['pairing_session']=dict(pairing_id='e'*32,project_id=9,status='CONNECTED',expires_at_ms=0,recipient=None,target_kind=kind)
                    value={**value,'session':mode['pairing_session'],'recipient':mode['pairing_recipient']}
                    if mode.get('hold_recipient_reuse'):
                        held_recipient_reuses.append((route,value));return
                elif command['action']=='CANCEL':
                    mode['pairing_session']=None;value={**value,'session':None}
                elif command['action']=='DISCONNECT':
                    mode['pairing_recipient']=None;mode['pairing_session']=None;value={**value,'recipient':None,'session':None}
        elif path=='/projects/9/telegram-history':
            sent=set(mode.get('delivery_sent',[]))
            for batch in delivery_batches.values():
                if batch['source_key']==mode['source']:
                    sent.update(job['car_id'] for job in batch['jobs'] if job['state']=='SENT')
            value=dict(project_id=9,source_key=mode['source'],sent_car_ids=sorted(sent))
        elif path.startswith('/projects/9/telegram-deliveries'):
            if req.method=='POST' and not path.endswith('/cancel'):
                body=json.loads(req.post_data);delivery_intents.append(body)
                if mode.get('delivery_post_error'):
                    status=503;value={'detail':mode['delivery_post_error']}
                    route.fulfill(status=status,content_type='application/json',body=json.dumps(value));return
                sent=set(mode.get('delivery_sent',[]))
                for previous in delivery_batches.values():sent.update(job['car_id'] for job in previous['jobs'] if job['state']=='SENT')
                value=delivery_batches.setdefault(body['intent_id'],dict(id=body['intent_id'],project_id=9,source_key=mode['source'],
                    recipient={key:mode['pairing_recipient'][key] for key in ('title','chat_id','chat_type')},
                    jobs=[dict(id=f'{len(delivery_batches)*10000+car:032x}',car_id=car,state='QUEUED',message_id=None,
                        error_code=None,not_before_ms=0,previously_sent=car in sent) for car in body['car_ids']]))
                if mode.get('delivery_lose_next'):
                    mode['delivery_lose_next']=False;route.abort('failed');return
            else:
                key=path.split('/')[4];value=delivery_batches.get(key)
                if not value:status=404;value={'detail':'telegram_delivery_not_found'}
                elif path.endswith('/cancel'):
                    for job in value['jobs']:
                        if job['state']=='QUEUED':job['state']='CANCELLED'
                else:
                    if mode.get('delivery_read_error'):status=503;value={'detail':'fixture_read_failed'}
                    else:
                        for job in value['jobs']:
                            if job['state'] in ('QUEUED','IN_FLIGHT'):
                                job['state']=mode.get('delivery_state','QUEUED')
                                if job['state']=='SENT':job['message_id']=700+job['car_id']
        elif path=='/projects' and req.method=='POST':
            if mode['invalid_url']:
                value={'detail':[{'loc':['body','search_url'],'msg':'Value error, Expected a iaa search URL with filters','type':'value_error'}]}
                status=422
            else:
                mode['created']=True
                value={**project,'source_key':mode['source']}
                status=201
        elif req.method=='DELETE' and path.startswith('/projects/') and '/cars/' in path:
            if mode.get('hold_removal'):
                held_removals.append(route)
                return
            if mode.get('remove_error'): status=503;value={'detail':'fixture_remove_failed'}
            else: removed_projects.add(int(path.split('/')[2]));status=204;value=None
        elif req.method=='DELETE' and path.startswith('/projects/'):
            if mode.get('hold_project_delete'):
                held_project_deletes.append(route)
                return
            if mode.get('project_delete_error'): status=503;value={'detail':'fixture_delete_failed'}
            else: deleted_projects.add(int(path.split('/')[2]));status=204;value=None
        elif path=='/projects':
            value=mode.get('bulk_projects') if mode.get('bulk_projects') is not None else mode.get('progress_projects') or ([{**project,'id':pid,'source_key':mode['source'],
                'name':project['name'] if pid==9 else 'Другой проект'} for pid in [9,10]] if mode.get('removal_projects') else [{**project,'source_key':mode['source']}])
            value=[p for p in value if p['id'] not in deleted_projects]
        elif path.endswith('/auction-lots'):
            pid=int(path.split('/')[2])
            value=dict(project_id=pid,source_key=mode['source'],local_date='2026-10-16',computer_timezone='Europe/Warsaw',generated_at='2026-10-16T10:00:00Z',lots=[] if pid in removed_projects else [{**lot,'source_key':mode['source']}])
            if mode.get('collections_fixture'):
                collection=parse_qs(urlsplit(req.url).query).get('collection',['active'])[0]
                value['collection']=collection
                value['lots']=[{**lot,'id':pid*10,'source_key':mode['source'],'title':f'{collection} car {pid}',
                    'url':lot['url'] if mode['source']=='copart' else 'https://www.iaai.com/VehicleDetail/65507986~US',
                    'project_search_status':'NOT_FOUND_IN_SEARCH' if collection=='archive' else 'FOUND'}]
                if mode.get('collection_empty'): value['lots']=[]
                if mode.get('collection_error')==collection: status=503;value={'detail':'fixture_collection_failed'}
            if mode.get('telegram_fixture') and parse_qs(urlsplit(req.url).query).get('collection')==['today']:
                telegram_reads.append((pid,parse_qs(urlsplit(req.url).query)))
                if mode.get('telegram_hold'): held_telegram.append(route);return
                value['lots']=[{**lot,'id':i+40,'source_key':mode['source'],'title':f'Preview car {i+1}',
                    'source_listing_id':str(65507986+i),'url':f'https://www.copart.com/lot/{65507986+i}' if mode['source']=='copart' else f'https://www.iaai.com/VehicleDetail/{65507986+i}~US',
                    'image':'/storage/fixture-photo.svg' if i%2==0 else None,
                    'details':{'mileage':{'value':12345,'unit':'mi','accuracy':'ACTUAL'},'primary_damage':'FRONT END'},
                    'schedule':{'utc_at':'2026-10-16T13:30:00Z'},
                    'time':dict(event_kind='START',precision='EXACT',is_today=True,local_date='2026-10-16',local_datetime='2026-10-16T15:30:00+02:00',remaining_seconds=999999,time_state='UPCOMING_START')}
                    for i in range(mode.get('telegram_count',45))]
                if mode.get('telegram_wrong_source'): value['source_key']='foreign'
                if mode.get('telegram_wrong_project'): value['project_id']=10
                if mode.get('telegram_error'): status=503;value={'detail':'fixture_preview_failed'}
        elif path=='/desktop/open-external' and mode.get('calendar_open_error'): status=503;value={'detail':'fixture_browser_unavailable'}
        else: value={}
        route.fulfill(status=status,content_type='application/json',body='' if status==204 else json.dumps(value))
    page.route('**/api/**',api)
    page.route('**/fixture-photo.svg',lambda route:route.fulfill(status=404 if mode.get('photo_error') else 200,
        content_type='image/svg+xml',body='' if mode.get('photo_error') else '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><rect fill="#dbeafe" width="800" height="500"/><text x="80" y="260" font-size="50">Photo fixture</text></svg>'))
    def check(name,fn):
        if args.only and args.only not in name: return
        try:
            fn()
            results.append({'name':name,'status':'passed'})
        except Exception as error:
            results.append({'name':name,'status':'failed','error':str(error)[:400],'url':page.url,'traceback':traceback.format_exc()[-1800:]})
            page.screenshot(path=str(report/f'failure-{len(results)}.png'),full_page=True)
    def search_feedback():
        try:
            for locale in ['ru','uk']:
                for width in [1280,980,360]:
                    mode.update(locale=locale,source='copart',collections_fixture=True,collection_empty=True,runs=[dict(
                        id=101,kind='PROJECTS',status='SUCCEEDED',progress=100,payload=dict(project_ids=[9],
                        project_statuses={'9':'SUCCEEDED'},pagination={'9':dict(mode='ALL_PAGES',pages_visited=1,
                        total_pages=1,ads_seen=3,complete=True,read_summary=dict(dated=0,exact_time=0,without_date=3,
                        unrecognized_schedule=0,unsupported_market=0))}))])
                    page.set_viewport_size({'width':width,'height':900})
                    page.goto(BASE+'?source=copart&view=settings')
                    settings=page.locator('.source-nav button[aria-current="page"]')
                    settings.wait_for()
                    assert settings.inner_text()==('Настройки приложения' if locale=='ru' else 'Налаштування застосунку')
                    assert settings.evaluate('(e)=>["borderTopLeftRadius","borderTopRightRadius","borderBottomLeftRadius","borderBottomRightRadius"].every(k=>parseFloat(getComputedStyle(e)[k])>=7)'), 'Selected settings must have four rounded corners'
                    settings.screenshot(path=str(report/f'settings-selected-{locale}-{width}.png'))
                    page.goto(BASE+'?source=copart&view=project&project=9')
                    copy='Новые лоты без назначенной даты торгов не сохраняются' if locale=='ru' else 'Нові лоти без призначеної дати торгів не зберігаються'
                    page.get_by_text(copy,exact=False).wait_for()
                    page.get_by_text('Сохранённых лотов нет' if locale=='ru' else 'Збережених лотів немає',exact=True).wait_for()
                    assert not page.locator('.auction-card').count()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Feedback must fit the viewport'
                    page.screenshot(path=str(report/f'undated-results-{locale}-{width}.png'),full_page=True)
        finally:
            mode.update(locale='ru',collections_fixture=False,collection_empty=False,runs=[])
    check('search feedback: rounded settings selection and undated results RU/UK',search_feedback)

    def backend_disconnection():
        try:
            for locale in ['ru','uk']:
                for width in [980,1280]:
                    mode.update(locale=locale,source='copart',runtime_offline=False,runs=[dict(
                        id=101,kind='PROJECTS',status='RUNNING',progress=30,payload=dict(project_ids=[9],
                        project_plans=[dict(project_id=9,source_key='copart',name=project['name'])]))])
                    page.set_viewport_size({'width':width,'height':900})
                    page.goto(BASE+'?source=copart&view=projects')
                    worker=page.locator('.worker')
                    running='Выполняет сканирование' if locale=='ru' else 'Виконує сканування'
                    worker.get_by_text(running,exact=True).wait_for()
                    posts=len([call for call in calls if call[0]=='POST' and call[1]=='/scans/projects'])
                    mode['runtime_offline']=True
                    alert=page.locator('[data-backend-disconnected]')
                    alert.wait_for()
                    assert worker.locator('.dot.offline').count()==1
                    assert page.locator('.shell-refresh').is_disabled()
                    assert page.locator('.activity-trigger b').inner_text()=='?'
                    assert running not in worker.inner_text()
                    assert page.get_by_text(project['name'],exact=True).count()
                    assert page.locator('body').evaluate('(e)=>e.scrollWidth<=window.innerWidth')
                    page.screenshot(path=str(report/f'backend-disconnected-{locale}-{width}.png'))
                    mode['runtime_offline']=False
                    alert.get_by_role('button',name='Проверить связь' if locale=='ru' else 'Перевірити зв’язок').click()
                    alert.wait_for(state='detached')
                    worker.get_by_text(running,exact=True).wait_for()
                    assert len([call for call in calls if call[0]=='POST' and call[1]=='/scans/projects'])==posts
        finally:
            mode.update(locale='ru',runtime_offline=False,runs=[])
    check('backend disconnection: cached projects retained, worker offline, retry recovers without a new scan RU/UK',backend_disconnection)
    def telegram_reschedule_option():
        try:
            for locale,source,width in [('ru','copart',1280),('uk','iaa',360)]:
                ru=locale=='ru'
                mode.update(locale=locale,source=source,pairing_fixture=True,delivery_enabled=True,auto_enabled=False,
                    pairing_recipient=dict(project_id=9,title='Recipient with a long name <script>',chat_id=555,binding_id='b'*32),
                    pairing_session=None,reschedule_enabled=True,reschedule_read_error=False,reschedule_write_error=False,
                    reschedule_counts={},reschedule_issue=None)
                page.set_viewport_size({'width':width,'height':900})
                def settings():
                    page.goto(BASE+f'?source={source}&view=source-settings')
                    page.locator('.telegram-settings > label select').select_option(f'{source}:9')
                    panel=page.locator('.telegram-reschedule-settings')
                    page.wait_for_function('document.querySelector(".telegram-reschedule-settings input")?.disabled===false')
                    return panel
                panel=settings();toggle=panel.get_by_role('checkbox')
                assert toggle.is_checked(), 'New independent preference must default ON'
                assert not page.locator('.telegram-automatic-settings').get_by_role('checkbox').is_checked()
                posts=len([c for c in calls if c[0]=='POST' and c[1] in ['/scans/projects','/projects/9/telegram-deliveries']])
                toggle.uncheck()
                page.wait_for_function('document.querySelector(".telegram-reschedule-settings input")?.disabled===false')
                panel=settings();toggle=panel.get_by_role('checkbox')
                assert not toggle.is_checked(), 'OFF must survive reload'
                mode['reschedule_hold']=True
                toggle.check()
                assert toggle.is_disabled() and panel.get_attribute('aria-busy')=='true'
                page.wait_for_function('document.querySelector(".telegram-reschedule-settings input")?.disabled===true')
                assert len(held_reschedules)==1
                mode.update(reschedule_hold=False,reschedule_enabled=True)
                held_reschedules.pop().fulfill(status=200,content_type='application/json',body=json.dumps(dict(
                    project_id=9,source_key=source,enabled=True,recipient=mode['pairing_recipient'],counts={},issue=None,max_result_age_minutes=30)))
                page.wait_for_function('document.querySelector(".telegram-reschedule-settings input")?.disabled===false')
                assert toggle.is_checked()
                assert page.locator('body').evaluate('(e)=>e.scrollWidth<=window.innerWidth')
                panel.screenshot(path=str(report/f'telegram-reschedules-{locale}-{width}.png'))
                mode['reschedule_write_error']=True
                toggle.uncheck()
                panel.get_by_role('alert').wait_for()
                page.wait_for_function('document.querySelector(".telegram-reschedule-settings input")?.disabled===false')
                assert toggle.is_checked(), 'A failed save must not claim OFF'
                mode.update(reschedule_write_error=False,reschedule_read_error=True)
                page.evaluate('() => window.dispatchEvent(new Event("focus"))')
                page.wait_for_function('document.querySelector(".telegram-reschedule-settings input")?.disabled===true')
                mode['reschedule_read_error']=False
                panel.get_by_role('button',name='Повторить' if ru else 'Повторити',exact=True).click()
                page.wait_for_function('document.querySelector(".telegram-reschedule-settings input")?.disabled===false')
                assert len([c for c in calls if c[0]=='POST' and c[1] in ['/scans/projects','/projects/9/telegram-deliveries']])==posts
        finally:
            mode.update(locale='ru',source='copart',reschedule_hold=False,reschedule_read_error=False,reschedule_write_error=False,
                reschedule_counts={},reschedule_issue=None)
    check('Telegram reschedules: independent default-on, persisted OFF, pending/save/read failure, retry without search or messages RU/UK',telegram_reschedule_option)
    def telegram_automatic_opt_in():
        try:
            for locale,source,width in [('ru','copart',1280),('uk','iaa',360)]:
                ru=locale=='ru'
                mode.update(locale=locale,source=source,pairing_fixture=True,delivery_enabled=True,
                    pairing_recipient=dict(project_id=9,title='Our destination with a long name',chat_id=555,chat_type='private',binding_id='b'*32),
                    pairing_session=None,auto_enabled=False,auto_read_error=False,auto_write_error=False,auto_batch=None,auto_issue=None)
                page.set_viewport_size({'width':width,'height':900})
                page.goto(BASE+f'?source={source}&view=source-settings')
                page.locator('.telegram-settings > label select').select_option(f'{source}:9')
                panel=page.locator('.telegram-automatic-settings')
                toggle=panel.get_by_role('checkbox')
                assert not toggle.is_checked()
                toggle.check()
                panel.get_by_role('button',name='Отмена' if ru else 'Скасувати',exact=True).click()
                assert not toggle.is_checked()
                assert not [c for c in calls if c[0]=='POST' and c[1].endswith('telegram-automatic')]
                preview=panel.get_by_role('button',name='Посмотреть сообщения «Сегодня»' if ru else 'Переглянути повідомлення «Сьогодні»')
                preview.click();page.locator('.auction-telegram-preview').wait_for()
                page.keyboard.press('Escape');page.locator('.auction-telegram-preview').wait_for(state='detached')
                assert preview.evaluate('(e)=>e===document.activeElement')
                toggle.check()
                panel.get_by_role('button',name='Включить для следующего поиска' if ru else 'Увімкнути для наступного пошуку').click()
                panel.get_by_text('Автоотправка «Сегодня» включена' if ru else 'Автонадсилання «Сьогодні» ввімкнено',exact=False).wait_for()
                enabled=[json.loads(c[2]) for c in calls if c[0]=='POST' and c[1].endswith('telegram-automatic')]
                assert enabled[-1]==dict(enabled=True,computer_timezone='Europe/Warsaw',binding_id='b'*32)
                assert not [c for c in calls if c[0]=='POST' and c[1].endswith('telegram-deliveries')]
                panel.screenshot(path=str(report/f'auto-settings-{locale}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                batch=dict(id='e'*32,project_id=9,source_key=source,recipient=dict(title='Our destination',chat_id=555,chat_type='private'),
                    jobs=[dict(id=f'{i:032x}',car_id=i,state=state,previously_sent=False,message_id=None,error_code=None,not_before_ms=0)
                          for i,state in enumerate(['SENT','QUEUED','UNKNOWN','FAILED'],1)])
                mode['auto_batch']=batch;delivery_batches[batch['id']]=batch
                page.goto(BASE+f'?source={source}&view=project&project=9')
                progress=page.locator('.telegram-auto-progress')
                cancel=progress.get_by_role('button',name='Отменить оставшиеся автоотправки' if ru else 'Скасувати решту автонадсилань')
                cancel.click();cancel.wait_for(state='detached')
                assert batch['jobs'][1]['state']=='CANCELLED'
                progress.get_by_role('alert').wait_for()
                assert not progress.get_by_role('checkbox').count()
                progress.screenshot(path=str(report/f'auto-progress-{locale}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                mode['auto_batch']=None;delivery_batches.clear()
                page.goto(BASE+f'?source={source}&view=source-settings')
                page.reload();page.locator('.telegram-settings > label select').select_option(f'{source}:9')
                panel.get_by_role('checkbox').wait_for()
                assert panel.get_by_role('checkbox').is_checked()
                panel.get_by_role('checkbox').click()
                page.wait_for_function('!document.querySelector(".telegram-automatic-settings input").disabled && !document.querySelector(".telegram-automatic-settings input").checked')
                panel.get_by_role('checkbox').wait_for()
                assert not mode['auto_enabled']
                calls.clear()
            mode.update(auto_read_error=True)
            page.reload();page.locator('.telegram-settings > label select').select_option('iaa:9')
            page.locator('.telegram-automatic-settings').get_by_role('alert').wait_for()
            assert page.locator('.telegram-automatic-settings').get_by_role('checkbox').is_disabled()
        finally:
            mode.update(locale='ru',source='copart',pairing_fixture=False,pairing_recipient=None,delivery_enabled=False,
                auto_enabled=False,auto_read_error=False,auto_write_error=False,auto_batch=None,auto_issue=None)
            calls.clear();page.set_viewport_size({'width':1280,'height':900})
    check('Telegram automatic Today: default-off, opt-in, preview/focus, no sends on enable, progress/cancel/UNKNOWN, reload, off, read failure RU/UK mobile',telegram_automatic_opt_in)
    def auction_creation_recipient_modal():
        saved=[dict(id='a'*32,bot_id=123456,title='Alex fixture',chat_id=555,chat_type='private')]
        try:
            for locale,source,width in [('ru','copart',1280),('uk','iaa',360)]:
                ru=locale=='ru'
                mode.update(locale=locale,source=source,pairing_fixture=True,saved_recipients=saved,pairing_session=None,
                    pairing_recipient=None,pairing_error=False,iaa_native_ready=True,created=False)
                page.set_viewport_size({'width':width,'height':1000});calls.clear()
                page.goto(BASE+f'?source={source}&view=projects')
                origin=page.get_by_role('button',name='Создать проект' if ru else 'Створити проєкт',exact=True)
                origin.click()
                dialog=page.get_by_role('dialog',name='Создать проект' if ru else 'Створити проєкт',exact=True)
                dialog.wait_for(timeout=2500)
                dialog.get_by_label('Название проекта '+('Copart' if source=='copart' else 'IAA') if ru else 'Назва проєкту IAA').fill('Recipient creation fixture')
                dialog.get_by_role('button',name='Создать и настроить Telegram' if ru else 'Створити й налаштувати Telegram',exact=True).click()
                panel=dialog.locator('.telegram-project')
                choice=panel.get_by_label('Получатель Telegram' if ru else 'Одержувач Telegram')
                choice.select_option('new')
                panel.get_by_role('button',name='Создать ссылку подключения' if ru else 'Створити посилання підключення',exact=True).click()
                panel.get_by_role('link',name='Открыть бота для привязки' if ru else 'Відкрити бота для прив’язки').wait_for()
                panel.get_by_role('button',name='Отменить новый выбор' if ru else 'Скасувати новий вибір',exact=True).click()
                mode['pairing_error']=True
                choice.select_option('a'*32)
                assert len([c for c in calls if c[0]=='POST' and c[1]=='/projects'])==1
                assert not [c for c in calls if c[0]=='POST' and c[1]=='/scans/projects']
                panel.get_by_role('alert').wait_for()
                assert len([c for c in calls if c[0]=='POST' and c[1]=='/projects'])==1
                mode['pairing_error']=False
                panel.get_by_role('button',name='Повторить подключение' if ru else 'Повторити підключення',exact=True).click()
                panel.locator('.telegram-recipient').wait_for()
                dialog.screenshot(path=str(report/f'creation-recipient-{locale}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                dialog.get_by_role('button',name='Готово — начать поиск' if ru else 'Готово — почати пошук',exact=True).click()
                dialog.wait_for(state='detached')
                assert len([c for c in calls if c[0]=='POST' and c[1]=='/scans/projects'])==1
                assert len([c for c in calls if c[0]=='POST' and c[1]=='/projects'])==1
                assert not page.locator('.telegram-project').count()
                page.goto(BASE+f'?source={source}&view=source-settings')
                page.locator('.telegram-settings select').select_option(f'{source}:9')
                page.locator('.telegram-recipient').wait_for()
                assert page.locator('.telegram-connection').count()==1
                assert not [c for c in calls if c[0]=='POST' and 'telegram-deliveries' in c[1]]
        finally:
            mode.update(locale='ru',source='copart',pairing_fixture=False,pairing_session=None,pairing_recipient=None,pairing_error=False,created=False,iaa_native_ready=False)
            page.set_viewport_size({'width':1280,'height':900})
    check('auction recipient modal: create once, confirm before search, recover pairing failure, source settings RU/UK',auction_creation_recipient_modal)
    def auction_creation_cancel():
        try:
            mode.update(locale='ru',source='copart',pairing_fixture=False,created=False,can_search=True)
            page.goto(BASE+'?source=copart&view=projects');calls.clear()
            trigger=page.get_by_role('button',name='Создать проект',exact=True)
            trigger.click();page.keyboard.press('Escape')
            page.locator('.auction-create-dialog').wait_for(state='detached')
            assert trigger.evaluate('(e)=>e===document.activeElement')
            assert not [c for c in calls if c[0]=='POST' and c[1] in ('/projects','/scans/projects')]
            trigger.click();page.get_by_label('Название проекта Copart',exact=True).fill('Keep without queue')
            page.get_by_role('button',name='Создать и настроить Telegram',exact=True).click()
            page.locator('.auction-create-dialog .telegram-project').wait_for()
            page.get_by_role('button',name='Закрыть без запуска',exact=True).click()
            page.locator('.auction-create-dialog').wait_for(state='detached')
            assert mode['created'] and len([c for c in calls if c[0]=='POST' and c[1]=='/projects'])==1
            assert not [c for c in calls if c[0]=='POST' and c[1]=='/scans/projects']
            assert 'Проект сохранён. Поиск пока не запущен' in page.locator('main').inner_text()
        finally: mode.update(created=False,pairing_fixture=False,source='copart',locale='ru')
    check('auction creation cancellation: Escape restores focus, saved second step survives without search or Telegram',auction_creation_cancel)
    def auction_action_visuals():
        try:
            for locale,source,width in [('ru','copart',1280),('uk','iaa',360)]:
                ru=locale=='ru'
                mode.update(locale=locale,source=source,pairing_fixture=True,collections_fixture=True,can_search=True,
                    telegram_fixture=True,telegram_count=1,pairing_recipient=None,pairing_session=None)
                page.set_viewport_size({'width':width,'height':1000});calls.clear()
                page.goto(BASE+f'?source={source}&view=projects')
                refresh=page.locator('.auction-project-refresh svg path').first
                refresh.wait_for()
                bounds=refresh.evaluate('(e)=>{const b=e.getBBox();return [b.x,b.y,b.x+b.width,b.y+b.height]}')
                assert min(bounds[:2])>=2 and max(bounds[2:])<=22
                page.goto(BASE+f'?source={source}&view=project&project=9')
                card=page.locator('.auction-card').first
                actions=card.locator('.auction-car-actions');actions.wait_for()
                buttons=[actions.locator('.auction-listing-button'),actions.locator('.telegram-icon-button'),actions.locator('.auction-remove-button')]
                boxes=[button.bounding_box() for button in buttons]
                assert all(box and box['width']==44 and box['height']==44 for box in boxes)
                assert len({round(box['y']) for box in boxes})==1 and boxes[0]['x']<boxes[1]['x']<boxes[2]['x']
                assert all(abs(boxes[i+1]['x']-boxes[i]['x']-54)<1 for i in range(2)), 'Horizontal action gaps must be equal, with no overlap'
                for button,color in zip(buttons,['rgb(21, 128, 61)','rgb(8, 126, 181)','rgb(220, 38, 38)']):
                    assert not button.inner_text().strip() and button.get_attribute('aria-label') and button.get_attribute('title')
                    assert button.evaluate('(e)=>getComputedStyle(e).backgroundColor')==color
                    assert button.evaluate('(e)=>getComputedStyle(e).color')=='rgb(255, 255, 255)'
                buttons[0].click()
                assert [json.loads(c[2])['url'] for c in calls if c[0]=='POST' and c[1]=='/desktop/open-external'][-1].startswith('https://www.copart.com/' if ru else 'https://www.iaai.com/')
                buttons[2].click();page.locator('.auction-removal-dialog').wait_for()
                page.keyboard.press('Escape');page.locator('.auction-removal-dialog').wait_for(state='detached')
                assert not page.locator('.telegram-project').count()
                controls=page.locator('.auction-project-controls')
                icons=controls.locator('button');assert icons.count()==3
                header_boxes=[icons.nth(index).bounding_box() for index in range(3)]
                assert len({round(box['y']) for box in header_boxes})==1
                assert all(box['width']==44 and box['height']==44 for box in header_boxes)
                for index,color in enumerate(['rgb(71, 85, 105)','rgb(37, 99, 235)','rgb(8, 126, 181)']):
                    icon=icons.nth(index)
                    assert not icon.inner_text().strip() and icon.get_attribute('aria-label') and icon.get_attribute('title')
                    assert icon.evaluate('(e)=>getComputedStyle(e).backgroundColor')==color
                assert controls.locator('.auction-project-edit-button svg circle').count()==1
                controls.screenshot(path=str(report/f'project-actions-{locale}-{width}.png'))
                card.screenshot(path=str(report/f'auction-actions-{locale}-{width}.png'))
                page.locator('.auction-tabs').get_by_role('button',name='Сегодня' if ru else 'Сьогодні',exact=True).click()
                page.get_by_role('button',name='Telegram',exact=True).click()
                preview=page.locator('.telegram-preview-message').first;preview.wait_for()
                preview.locator('input[type=checkbox]').check();preview.focus()
                assert preview.evaluate('(e)=>getComputedStyle(e).outlineOffset')=='-2px'
                assert preview.evaluate('(e)=>getComputedStyle(e).borderLeftColor')=='rgb(37, 99, 235)'
                page.locator('dialog.auction-telegram-preview').screenshot(path=str(report/f'preview-selection-{locale}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not [c for c in calls if c[0]=='DELETE' or c[0]=='POST' and 'telegram-deliveries' in c[1]]
                page.keyboard.press('Escape')
        finally:
            mode.update(locale='ru',source='copart',pairing_fixture=False,collections_fixture=False,telegram_fixture=False,can_search=True)
            page.set_viewport_size({'width':1280,'height':900})
    check('auction actions visuals: horizontal icon groups, recipient removed, listing/delete and inset selection RU/UK',auction_action_visuals)
    def create_form(submit=True):
        page.goto(BASE+'?source='+mode['source'])
        page.get_by_role('button',name='Создать проект',exact=True).click()
        page.get_by_label('Название проекта '+('IAA' if mode['source']=='iaa' else 'Copart')).fill('Ionic 6' if mode['source']=='iaa' else 'Audi fixture')
        page.locator('.search-selection').wait_for(timeout=2500)
        if submit:
            page.get_by_role('button',name='Создать и настроить Telegram',exact=True).click(timeout=2500)
            page.wait_for_timeout(200)
            finish=page.get_by_role('button',name='Готово — начать поиск',exact=True)
            if finish.count(): finish.click()
        page.wait_for_timeout(600)
    def telegram_link_actions():
        try:
            for locale,width in [('ru',1280),('uk',360)]:
                ru=locale=='ru'
                mode.update(locale=locale,source='copart',pairing_fixture=True,pairing_session=None,pairing_recipient=None,pairing_claimed=False)
                page.set_viewport_size({'width':width,'height':1000})
                page.goto(BASE+'?source=copart&view=source-settings')
                page.evaluate('''() => {
                    window.fixtureCopies=[]; window.fixtureCopyFails=false;
                    Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{
                        writeText:async text => {
                            if(window.fixtureCopyFails) throw Error('fixture denied');
                            if(window.fixtureCopyHold) await new Promise(resolve => window.fixtureCopyResume=resolve);
                            window.fixtureCopies.push(text);
                        }
                    }});
                }''')
                shared=page.locator('.telegram-connection');shared.wait_for()
                copy_name='Скопировать ссылку' if ru else 'Скопіювати посилання'
                shared.get_by_role('button',name=copy_name,exact=True).click(timeout=2000)
                assert page.evaluate('window.fixtureCopies')==['https://t.me/search_car_app_bot']
                page.locator('.telegram-settings select').select_option('copart:9')
                panel=page.locator('.telegram-project');panel.wait_for()
                panel.get_by_text('Группы и каналы' if ru else 'Групи та канали',exact=False).wait_for()
                panel.get_by_role('button',name='Создать ссылку подключения' if ru else 'Створити посилання підключення',exact=True).click()
                link=panel.get_by_role('link');link.wait_for()
                target=link.get_attribute('href')
                copy=panel.get_by_role('button',name=copy_name,exact=True)
                copy.focus();page.keyboard.press('Enter')
                panel.get_by_text('Ссылка скопирована' if ru else 'Посилання скопійовано',exact=False).wait_for()
                assert page.evaluate('window.fixtureCopies')[-1]==target
                assert target not in page.evaluate('JSON.stringify(localStorage)+JSON.stringify(sessionStorage)')
                actions=panel.locator('.telegram-link-actions')
                boxes=[item.bounding_box() for item in actions.locator('.button').all()]
                if width==1280:
                    assert len(boxes)==3 and max(b['y'] for b in boxes)-min(b['y'] for b in boxes)<1
                    assert max(b['height'] for b in boxes)-min(b['height'] for b in boxes)<1
                    assert all(b['x']>=a['x']+a['width']+7 for a,b in zip(boxes,boxes[1:]))
                else:
                    assert all(b['width']<=width for b in boxes)
                page.evaluate('window.fixtureCopyFails=true')
                copy.click();fallback=panel.get_by_label('Ссылка для копирования' if ru else 'Посилання для копіювання',exact=True)
                fallback.wait_for();assert fallback.input_value()==target
                fallback.focus();assert fallback.evaluate('(el)=>el.selectionEnd-el.selectionStart')==len(target)
                page.evaluate('window.fixtureCopyFails=false');copy.click();fallback.wait_for(state='detached')
                page.evaluate('window.fixtureCopyHold=true');copy.click()
                busy_copy=panel.get_by_role('button',name='Копируем…' if ru else 'Копіюємо…',exact=True)
                assert busy_copy.is_disabled()
                page.evaluate('window.fixtureCopyHold=false;window.fixtureCopyResume()')
                copy.wait_for();assert not copy.is_disabled()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                panel.screenshot(path=str(report/f'telegram-link-actions-{locale}-{width}.png'))
                panel.get_by_role('button',name='Отменить новый выбор' if ru else 'Скасувати новий вибір',exact=True).click()
                copy.wait_for(state='detached')
                assert not panel.get_by_text('Ссылка скопирована' if ru else 'Посилання скопійовано',exact=False).count()
                panel.get_by_role('button',name='Создать ссылку подключения' if ru else 'Створити посилання підключення',exact=True).click()
                copy.wait_for()
                mode['pairing_session']['expires_at_ms']=1
                panel.get_by_text('Срок ссылки истёк' if ru else 'Строк посилання минув',exact=False).wait_for(timeout=8500)
                copy.wait_for(state='detached')
                assert not panel.get_by_role('link').count(), 'An expired pairing URL remains actionable'
                assert not [c for c in calls if c[0]=='POST' and 'telegram-deliveries' in c[1]]
        finally:
            mode.update(locale='ru',source='copart',pairing_fixture=False,pairing_session=None,pairing_recipient=None)
    check('Telegram link actions: copy public/pairing URL, keyboard, denied clipboard fallback, aligned wrapping and cancel RU/UK',telegram_link_actions)

    def telegram_connection_settings():
        for locale,width in [('ru',1280),('uk',360)]:
            mode.update(locale=locale)
            page.set_viewport_size({'width':width,'height':1000})
            page.goto(BASE+'?source=copart&view=source-settings')
            panel=page.locator('.telegram-connection')
            panel.wait_for()
            assert not panel.locator('input').count()
            panel.get_by_text('Выберите проект ниже' if locale=='ru' else 'Виберіть проєкт нижче',exact=False).wait_for()
            bot=panel.get_by_role('link',name='@search_car_app_bot',exact=False)
            assert bot.get_attribute('href')=='https://t.me/search_car_app_bot'
            calls.clear()
            mode['calendar_open_error']=True
            bot.click()
            panel.get_by_role('alert').wait_for()
            mode['calendar_open_error']=False
            bot.click()
            panel.get_by_role('alert').wait_for(state='detached')
            assert [json.loads(c[2]) for c in calls if c[0]=='POST' and c[1]=='/desktop/open-external']==[{'url':'https://t.me/search_car_app_bot'}]*2
            assert not panel.get_by_role('button',name='Подключить Telegram' if locale=='ru' else 'Підключити Telegram').count()
            panel.get_by_role('heading',name='Личные сообщения' if locale=='ru' else 'Особисті повідомлення',exact=True).wait_for()
            panel.get_by_text('Получатель нажимает Start' if locale=='ru' else 'Одержувач натискає Start',exact=False).wait_for()
            assert ('ссылку подключения' if locale=='ru' else 'посилання підключення') in panel.inner_text()
            assert '@BotFather' not in panel.inner_text()
            panel.get_by_role('heading',name='Группы и каналы' if locale=='ru' else 'Групи та канали',exact=True).wait_for()
            panel.get_by_text('подключение групп и каналов ещё не включено' if locale=='ru' else 'підключення груп і каналів ще не ввімкнено',exact=False).wait_for()
            panel.scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(report/f'telegram-shared-{locale}-{width}.png'),full_page=True)
            panel.screenshot(path=str(report/f'telegram-shared-panel-{locale}-{width}.png'))
        assert not any(c[1].startswith('/desktop/telegram') for c in calls)
        mode.update(locale='ru')
    check('Telegram shared setup: no user token, delay warning, private/group instructions, unconfigured gate, RU/UK mobile, no send',telegram_connection_settings)
    def telegram_chat_kinds():
        try:
            for locale,source,width in [('ru','copart',1280),('uk','iaa',360)]:
                ru=locale=='ru'
                for kind in ['group','channel']:
                    mode.update(locale=locale,source=source,pairing_fixture=True,chat_pairing_enabled=True,
                        pairing_session=None,pairing_recipient=None,pairing_claimed=False,pairing_error=False,saved_recipients=[])
                    page.set_viewport_size({'width':width,'height':1000})
                    page.goto(BASE+f'?source={source}&view=source-settings')
                    page.locator('.telegram-settings select').select_option(f'{source}:9')
                    panel=page.locator('.telegram-project');panel.wait_for()
                    selector=panel.get_by_label('Тип получателя' if ru else 'Тип одержувача',exact=True)
                    selector.select_option(kind);calls.clear()
                    panel.get_by_role('button',name='Создать ссылку подключения' if ru else 'Створити посилання підключення',exact=True).click()
                    panel.get_by_role('link').wait_for()
                    assert selector.is_disabled() and selector.input_value()==kind
                    assert any(c[0]=='POST' and c[1].endswith('/telegram') and json.loads(c[2])=={'action':'START','target_kind':kind} for c in calls)
                    panel.get_by_text('Ожидаем выбор чата' if ru else 'Очікуємо вибір чату',exact=False).wait_for()
                    mode['pairing_claimed']=True
                    check=panel.get_by_role('button',name='Проверить состояние' if ru else 'Перевірити стан',exact=True)
                    mode.update(pairing_error=True,pairing_error_code='telegram_chat_permission_required')
                    panel.get_by_role('alert').wait_for(timeout=8500)
                    assert not panel.locator('.telegram-recipient').count()
                    mode.update(pairing_error=False)
                    check.click();candidate=panel.locator('.telegram-candidate');candidate.wait_for()
                    assert 'Verified team <script>' in candidate.inner_text() and not candidate.locator('script').count()
                    assert '-100555' in candidate.inner_text()
                    panel.locator('.telegram-recipient').wait_for(timeout=8500)
                    expected_type=('Группа' if ru else 'Група') if kind=='group' else 'Канал'
                    assert expected_type in panel.locator('.telegram-recipient').inner_text()
                    assert not panel.get_by_role('link').count()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    panel.screenshot(path=str(report/f'telegram-{kind}-{locale}-{width}.png'))
                    assert not [c for c in calls if c[0]=='POST' and 'telegram-deliveries' in c[1]]
            mode.update(chat_pairing_enabled=False,pairing_session=None,pairing_recipient=None)
            page.reload();page.locator('.telegram-settings select').select_option(f'{source}:9')
            selector=page.get_by_label('Тип одержувача',exact=True)
            page.wait_for_function("Array.from(document.querySelectorAll('.telegram-kind-choice option')).filter(o=>o.value!=='private').length===2 && Array.from(document.querySelectorAll('.telegram-kind-choice option')).filter(o=>o.value!=='private').every(o=>o.disabled)")
            gate_state=selector.evaluate('(el)=>Array.from(el.options).map(o=>({value:o.value,disabled:o.disabled}))')
            assert gate_state==[{'value':'private','disabled':False},{'value':'group','disabled':True},{'value':'channel','disabled':True}],gate_state
            selector.focus();page.keyboard.press('ArrowDown');page.keyboard.press('ArrowDown')
            assert selector.input_value()=='private','Disabled recipient kinds can be chosen with the keyboard'
        finally:
            mode.update(locale='ru',source='copart',pairing_fixture=False,chat_pairing_enabled=False,pairing_session=None,
                pairing_recipient=None,pairing_claimed=False,pairing_error=False,pairing_error_code='telegram_pairing_unavailable',saved_recipients=[])
    check('Telegram chat types: explicit group/channel selection, auto-save, trusted negative ID, permission failure/retry, gated off and no sends RU/UK',telegram_chat_kinds)

    def telegram_automatic_pairing():
        try:
            for locale,kind,width in [('ru','group',1280),('uk','private',360),('ru','channel',800)]:
                ru=locale=='ru'
                mode.update(locale=locale,source='copart',pairing_fixture=True,chat_pairing_enabled=True,
                    pairing_session=None,pairing_recipient=None,pairing_claimed=False,pairing_error=False,saved_recipients=[])
                page.set_viewport_size({'width':width,'height':1000})
                page.goto(BASE+'?source=copart&view=source-settings')
                page.locator('.telegram-settings select').select_option('copart:9')
                panel=page.locator('.telegram-project');panel.wait_for()
                panel.get_by_label('Тип получателя' if ru else 'Тип одержувача',exact=True).select_option(kind)
                calls.clear()
                panel.get_by_role('button',name='Создать ссылку подключения' if ru else 'Створити посилання підключення',exact=True).click()
                panel.get_by_role('link').wait_for()
                assert not panel.get_by_role('button',name='Проверить состояние' if ru else 'Перевірити стан',exact=True).count()
                mode['pairing_claimed']=True
                panel.locator('.telegram-recipient').wait_for(timeout=8500)
                assert not panel.get_by_role('link').count(),'Completed pairing still exposes its transient link'
                commands=[json.loads(c[2]) for c in calls if c[0]=='POST' and c[1].endswith('/telegram')]
                assert [c['action'] for c in commands]==['START','CHECK','CONFIRM'],commands
                assert not any(c[0]=='POST' and ('telegram-deliveries' in c[1] or c[1]=='/scans/projects') for c in calls)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                panel.screenshot(path=str(report/f'telegram-auto-{kind}-{locale}-{width}.png'))
                before=len(calls);page.wait_for_timeout(5500)
                assert not [c for c in calls[before:] if c[0]=='POST' and c[1].endswith('/telegram')],'Connected recipient keeps polling'
            mode.update(locale='ru',source='copart',pairing_fixture=True,pairing_session=None,pairing_recipient=None,
                pairing_claimed=False,pairing_error=False,chat_pairing_enabled=True)
            page.goto(BASE+'?source=copart&view=source-settings');page.locator('.telegram-settings select').select_option('copart:9')
            panel=page.locator('.telegram-project');panel.wait_for();calls.clear()
            panel.get_by_role('button',name='Создать ссылку подключения',exact=True).click();panel.get_by_role('link').wait_for()
            mode.update(pairing_error=True,pairing_error_code='telegram_chat_permission_required')
            panel.get_by_role('alert').wait_for(timeout=8500)
            count=len(calls);page.wait_for_timeout(5500)
            assert not [c for c in calls[count:] if c[0]=='POST' and c[1].endswith('/telegram')],'Errors cause an endless retry loop'
            assert 'Проверить состояние' in panel.get_by_role('alert').inner_text()
            mode.update(pairing_error=False,pairing_claimed=True)
            panel.get_by_role('button',name='Проверить состояние',exact=True).click()
            panel.locator('.telegram-recipient').wait_for(timeout=8500)
            panel.get_by_role('button',name='Изменить получателя',exact=True).click();panel.get_by_role('link').wait_for()
            panel.get_by_role('button',name='Отменить новый выбор',exact=True).click();panel.get_by_role('link').wait_for(state='detached')
            count=len(calls);page.wait_for_timeout(5500)
            assert not [c for c in calls[count:] if c[0]=='POST' and c[1].endswith('/telegram')],'Cancellation keeps polling'
        finally:
            mode.update(locale='ru',source='copart',pairing_fixture=False,chat_pairing_enabled=False,
                pairing_session=None,pairing_recipient=None,pairing_claimed=False,pairing_error=False,saved_recipients=[])
    check('Telegram automatic pairing: no manual check/confirm, stop after success and no sends RU/UK',telegram_automatic_pairing)

    def telegram_project_pairing():
        try:
            for locale,source,width in [('ru','copart',1280),('uk','iaa',360)]:
                mode.update(locale=locale,source=source,pairing_fixture=True,pairing_session=None,pairing_recipient=None,pairing_claimed=False,pairing_error=False)
                page.set_viewport_size({'width':width,'height':1000})
                calls.clear();page.goto(BASE+f'?source={source}&view=source-settings')
                page.locator('.telegram-settings select').select_option(f'{source}:9')
                panel=page.locator('.telegram-project');panel.wait_for()
                assert not any(c[0]=='POST' and c[1].endswith('/telegram') for c in calls)
                begin=panel.get_by_role('button',name='Создать ссылку подключения' if locale=='ru' else 'Створити посилання підключення',exact=True)
                begin.wait_for();begin.click()
                link=panel.get_by_role('link',name='Открыть бота для привязки' if locale=='ru' else 'Відкрити бота для прив’язки')
                link.wait_for();assert link.get_attribute('href')=='https://t.me/search_car_app_bot?start=p_'+'x'*32
                link.click()
                assert any(c[1]=='/desktop/open-external' and json.loads(c[2])=={'url':link.get_attribute('href')} for c in calls)
                check_button=panel.get_by_role('button',name='Проверить состояние' if locale=='ru' else 'Перевірити стан',exact=True)
                mode['pairing_error']=True;panel.get_by_role('alert').wait_for(timeout=8500)
                assert not panel.get_by_role('button',name='Подтвердить получателя' if locale=='ru' else 'Підтвердити одержувача',exact=True).count()
                mode.update(pairing_error=False,pairing_claimed=True);check_button.click()
                candidate=panel.locator('.telegram-candidate');candidate.wait_for();assert 'Alex <script>' in candidate.inner_text() and not candidate.locator('script').count()
                assert '555' in candidate.inner_text()
                panel.screenshot(path=str(report/f'project-pairing-candidate-{locale}-{width}.png'))
                panel.locator('.telegram-recipient').wait_for(timeout=8500);assert not panel.get_by_role('link').count()
                page.reload();page.locator('.telegram-settings select').select_option(f'{source}:9');panel.locator('.telegram-recipient').wait_for()
                assert not panel.get_by_role('link').count(), 'A deep-link secret survived page reload'
                panel.get_by_role('button',name='Изменить получателя' if locale=='ru' else 'Змінити одержувача',exact=True).click()
                panel.get_by_role('link').wait_for()
                panel.get_by_role('button',name='Отменить новый выбор' if locale=='ru' else 'Скасувати новий вибір',exact=True).click()
                panel.get_by_role('link').wait_for(state='detached');assert '555' in panel.locator('.telegram-recipient').inner_text()
                panel.get_by_role('button',name='Отключить получателя' if locale=='ru' else 'Відключити одержувача',exact=True).click()
                panel.get_by_role('button',name='Отмена' if locale=='ru' else 'Скасувати',exact=True).click()
                assert panel.locator('.telegram-recipient').count()==1
                panel.screenshot(path=str(report/f'project-pairing-saved-{locale}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                panel.get_by_role('button',name='Отключить получателя' if locale=='ru' else 'Відключити одержувача',exact=True).click()
                panel.get_by_role('button',name='Да, отключить' if locale=='ru' else 'Так, відключити',exact=True).click()
                panel.locator('.telegram-recipient').wait_for(state='detached')
                commands=[json.loads(c[2]) for c in calls if c[0]=='POST' and c[1].endswith('/telegram')]
                actions=[c['action'] for c in commands]
                collapsed=[v for i,v in enumerate(actions) if i==0 or v!='CHECK' or actions[i-1]!='CHECK']
                assert collapsed==['START','CHECK','CONFIRM','START','CANCEL','DISCONNECT'],actions
                assert not any('owner_id' in c or 'token' in c for c in commands)
                assert not any(c[0]=='POST' and c[1]=='/scans/projects' for c in calls)
        finally:
            mode.update(locale='ru',source='copart',pairing_fixture=False,pairing_session=None,pairing_recipient=None,pairing_error=False)
            page.set_viewport_size({'width':1280,'height':900})
    check('Telegram project pairing: auto confirmation with manual error recovery, no token/sends, timeout, reload, cancel and disconnect RU/UK',telegram_project_pairing)
    def telegram_saved_recipient_creation():
        try:
            saved=[dict(id='a'*32,bot_id=123456,title='Alex <script>',chat_id=555,chat_type='private')]
            for locale,source,width in [('ru','copart',1280),('uk','iaa',360)]:
                mode.update(locale=locale,source=source,pairing_fixture=True,saved_recipients=saved,pairing_session=None,
                    pairing_recipient=None,pairing_error=False,iaa_native_ready=True,created=False)
                page.set_viewport_size({'width':width,'height':1000});calls.clear()
                page.goto(BASE+f'?source={source}&view=projects')
                page.get_by_role('button',name='Создать проект' if locale=='ru' else 'Створити проєкт',exact=True).click()
                form=page.locator('.auction-project-form')
                form.locator('input').first.fill('Saved recipient fixture')
                assert not form.locator('.telegram-recipient-choice').count()
                page.get_by_role('button',name='Создать и настроить Telegram' if locale=='ru' else 'Створити й налаштувати Telegram',exact=True).click()
                page.get_by_role('button',name='Готово — начать поиск' if locale=='ru' else 'Готово — почати пошук',exact=True).click()
                page.get_by_role('button',name='Telegram',exact=True).wait_for()
                assert not page.locator('.telegram-project').count()
                page.goto(BASE+f'?source={source}&view=source-settings')
                page.locator('.telegram-settings select').select_option(f'{source}:9')
                panel=page.locator('.telegram-project')
                choice=panel.get_by_label('Получатель Telegram' if locale=='ru' else 'Одержувач Telegram')
                choice.locator('option[value="'+'a'*32+'"]').wait_for(state='attached')
                choice.select_option('a'*32)
                assert '555' in choice.inner_text() and '<script>' in choice.inner_text()
                assert not panel.locator('script').count()
                panel.screenshot(path=str(report/f'recipient-settings-{locale}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                panel.locator('.telegram-recipient').wait_for(timeout=2500)
                commands=[json.loads(c[2]) for c in calls if c[0]=='POST' and c[1].endswith('/telegram')]
                assert commands==[dict(action='USE_SAVED',recipient_id='a'*32)]
                assert len([c for c in calls if c[0]=='POST' and c[1]=='/projects'])==1
                assert len([c for c in calls if c[0]=='POST' and c[1]=='/scans/projects'])==1
                panel.screenshot(path=str(report/f'recipient-assigned-{locale}-{width}.png'))
            mode.update(locale='ru',source='copart',pairing_recipient=None,pairing_session=None,pairing_error=True,created=False)
            page.goto(BASE+'?source=copart&view=source-settings')
            page.locator('.telegram-settings select').select_option('copart:9')
            panel=page.locator('.telegram-project')
            panel.get_by_label('Получатель Telegram').select_option('a'*32)
            panel.get_by_role('alert').wait_for()
            assert not page.locator('.telegram-recipient').count()
            count=len([c for c in calls if c[0]=='POST' and c[1]=='/projects'])
            mode['pairing_error']=False
            panel=page.locator('.telegram-project')
            panel.get_by_role('button',name='Повторить подключение',exact=True).click()
            panel.locator('.telegram-recipient').wait_for()
            assert len([c for c in calls if c[0]=='POST' and c[1]=='/projects'])==count
            mode.update(pairing_recipient=None,pairing_session=None)
            page.reload();page.locator('.telegram-settings select').select_option('copart:9')
            panel=page.locator('.telegram-project');panel.get_by_label('Получатель Telegram').select_option('new')
            calls.clear();panel.get_by_role('button',name='Создать ссылку подключения',exact=True).wait_for()
            assert panel.get_attribute('open') is not None
            assert not any(c[0]=='POST' and c[1].endswith('/telegram') for c in calls)
            panel.get_by_role('button',name='Создать ссылку подключения',exact=True).click()
            panel.get_by_role('link',name='Открыть бота для привязки').wait_for()
            # Encar shares Settings, while its legacy channel link remains editable there.
            mode.update(source='encar',pairing_session=None,pairing_recipient=None)
            page.goto(BASE+'?source=encar&view=settings')
            page.locator('.telegram-settings select').select_option('encar:9')
            panel=page.locator('.telegram-project')
            panel.get_by_label('Получатель Telegram').locator('option[value="'+'a'*32+'"]').wait_for(state='attached')
            assert page.get_by_label('Ссылка на Telegram-канал Encar').count()==1
            page.get_by_label('Ссылка на Telegram-канал Encar').fill('https://t.me/searchcar_fixture')
            page.get_by_role('button',name='Сохранить ссылку',exact=True).click()
            page.get_by_text('Ссылка сохранена',exact=True).wait_for()
            assert any(c[0]=='PATCH' and c[1]=='/projects/9' and json.loads(c[2])=={'telegram_url':'https://t.me/searchcar_fixture'} for c in calls)
            assert not panel.locator('input[type="password"]').count()
            page.locator('.telegram-settings').screenshot(path=str(report/'recipient-settings-encar-1280.png'))
        finally:
            mode.update(locale='ru',source='copart',pairing_fixture=False,pairing_session=None,pairing_recipient=None,pairing_error=False,saved_recipients=[],iaa_native_ready=False)
            page.set_viewport_size({'width':1280,'height':900})
    check('Telegram saved recipients: Settings across sites, clean creation, add new, failure/retry, RU/UK mobile',telegram_saved_recipient_creation)
    def telegram_saved_chat_one_step():
        try:
            for locale,source,width,chat_type in [('ru','copart',1280,'group'),('uk','iaa',360,'channel')]:
                ru=locale=='ru'
                delivery_batches.clear();delivery_intents.clear();calls.clear()
                mode.update(locale=locale,source=source,pairing_fixture=True,chat_pairing_enabled=True,
                    saved_recipients=[dict(id='a'*32,bot_id=123456,title='Saved team',chat_id=-100555,chat_type=chat_type)],
                    pairing_session=None,pairing_recipient=None,pairing_error=False,hold_recipient_reuse=True,
                    telegram_fixture=True,telegram_count=2,delivery_enabled=True,delivery_state='QUEUED',
                    iaa_native_ready=True,created=False,can_search=False)
                page.set_viewport_size({'width':width,'height':1000})
                page.goto(BASE+f'?source={source}&view=projects')
                page.get_by_role('button',name='Создать проект' if ru else 'Створити проєкт',exact=True).click()
                dialog=page.locator('.auction-create-dialog')
                dialog.locator('.auction-project-form input').first.fill('Saved chat one step')
                dialog.get_by_role('button',name='Создать и настроить Telegram' if ru else 'Створити й налаштувати Telegram',exact=True).click()
                panel=dialog.locator('.telegram-project')
                panel.get_by_label('Получатель Telegram' if ru else 'Одержувач Telegram').select_option('a'*32)
                panel.get_by_text('Проверяем и подключаем выбранный чат…' if ru else 'Перевіряємо й підключаємо вибраний чат…',exact=True).wait_for(timeout=2500)
                finish=dialog.get_by_role('button',name='Готово — начать поиск' if ru else 'Готово — почати пошук',exact=True)
                assert finish.is_disabled() and not panel.get_by_role('button',name='Использовать выбранного' if ru else 'Використати вибраного').count()
                assert len(held_recipient_reuses)==1
                route,value=held_recipient_reuses.pop();route.fulfill(status=200,content_type='application/json',body=json.dumps(value))
                mode['hold_recipient_reuse']=False
                panel.get_by_role('status').filter(has_text='Подключено: Saved team' if ru else 'Підключено: Saved team').wait_for()
                page.wait_for_function("!document.querySelector('.auction-create-dialog .launch-actions .primary').disabled")
                dialog.get_by_role('button',name='Закрыть без запуска' if ru else 'Закрити без запуску',exact=True).click()
                page.locator('.auction-tabs').get_by_role('button',name='Сегодня' if ru else 'Сьогодні',exact=True).click()
                page.get_by_role('button',name='Telegram',exact=True).click()
                preview=page.locator('dialog.auction-telegram-preview')
                preview.locator('.telegram-select-all input').check()
                preview.get_by_role('button',name='Отправить выбранные' if ru else 'Надіслати вибрані',exact=False).click()
                preview.get_by_role('status').filter(has_text='0 / 2').wait_for()
                assert delivery_batches[delivery_intents[-1]['intent_id']]['recipient']['chat_type']==chat_type
                assert [json.loads(c[2])['action'] for c in calls if c[0]=='POST' and c[1].endswith('/telegram')]==['USE_SAVED']
                page.screenshot(path=str(report/f'saved-chat-send-{source}-{width}.png'))
                page.keyboard.press('Escape')
                mode['telegram_count']=0
                page.get_by_role('button',name='Telegram',exact=True).click()
                preview.get_by_text('В этом разделе пока нет автомобилей. Обновите данные проекта или выберите другой раздел. Подключать Telegram повторно не нужно.' if ru else 'У цьому розділі поки немає автомобілів. Оновіть дані проєкту або виберіть інший розділ. Підключати Telegram повторно не потрібно.',exact=True).wait_for()
                assert preview.get_by_role('button',name='Отправить выбранные' if ru else 'Надіслати вибрані',exact=False).is_disabled()
                page.keyboard.press('Escape')
        finally:
            for route,_ in held_recipient_reuses:route.abort()
            held_recipient_reuses.clear();delivery_batches.clear()
            mode.update(source='copart',locale='ru',pairing_fixture=False,pairing_recipient=None,pairing_session=None,
                hold_recipient_reuse=False,telegram_fixture=False,delivery_enabled=False,created=False,can_search=True)
            page.set_viewport_size({'width':1280,'height':900})
    check('Telegram saved chat one-step connection: creation pending, auto-save, direct bulk send and clear empty state RU/UK',telegram_saved_chat_one_step)
    def telegram_project_settings_context():
        try:
            for source,locale,width in [('copart','ru',1280),('iaa','uk',360)]:
                ru=locale=='ru'
                delivery_batches.clear();delivery_intents.clear();calls.clear()
                mode.update(source=source,locale=locale,pairing_fixture=True,chat_pairing_enabled=True,
                    pairing_recipient=None,pairing_session=None,pairing_error=False,delivery_enabled=True,
                    telegram_fixture=True,telegram_count=2,delivery_state='QUEUED',
                    saved_recipients=[dict(id='a'*32,bot_id=123456,title='Existing team',chat_id=-100555,chat_type='supergroup')])
                page.set_viewport_size({'width':width,'height':1000})
                page.goto(BASE+f'?source={source}&view=project&project=9')
                page.locator('.auction-tabs').get_by_role('button',name='Сегодня' if ru else 'Сьогодні',exact=True).click()
                page.get_by_role('button',name='Telegram',exact=True).click()
                preview=page.locator('dialog.auction-telegram-preview')
                preview.locator('.telegram-select-all input').check()
                preview.get_by_role('button',name='Отправить выбранные' if ru else 'Надіслати вибрані',exact=False).click()
                preview.get_by_role('alert').filter(has_text='Для проекта не выбран получатель' if ru else 'Для проєкту не вибрано одержувача').wait_for()
                preview.get_by_role('button',name='Настройки' if ru else 'Налаштування',exact=True).click()
                selector=page.locator('.telegram-settings > label > select')
                selector.wait_for()
                assert selector.input_value()==f'{source}:9','Settings lost the project that requested delivery'
                panel=page.locator('.telegram-project')
                panel.get_by_label('Получатель Telegram' if ru else 'Одержувач Telegram').select_option('a'*32)
                panel.locator('.telegram-recipient').filter(has_text='Existing team').wait_for()
                assert [json.loads(c[2]) for c in calls if c[0]=='POST' and c[1].endswith('/telegram')]==[dict(action='USE_SAVED',recipient_id='a'*32)]
                page.goto(BASE+f'?source={source}&view=project&project=9')
                page.locator('.auction-tabs').get_by_role('button',name='Сегодня' if ru else 'Сьогодні',exact=True).click()
                page.get_by_role('button',name='Telegram',exact=True).click()
                preview.locator('.telegram-select-all input').check()
                preview.get_by_role('button',name='Отправить выбранные' if ru else 'Надіслати вибрані',exact=False).click()
                preview.get_by_role('status').filter(has_text='0 / 2').wait_for()
                assert len(delivery_intents)==1
                page.screenshot(path=str(report/f'project-context-send-{source}-{width}.png'))
                page.keyboard.press('Escape')
        finally:
            delivery_batches.clear();mode.update(source='copart',locale='ru',pairing_fixture=False,
                pairing_recipient=None,pairing_session=None,delivery_enabled=False,telegram_fixture=False)
            page.set_viewport_size({'width':1280,'height':900})
    check('Telegram project settings context: missing recipient opens the exact originating project, one choice connects and bulk send queues RU/UK',telegram_project_settings_context)
    def auto_search():
        calls.clear()
        create_form()
        assert len([c for c in calls if c[0]=='POST' and c[1]=='/projects'])==1
        assert json.loads(next(c[2] for c in calls if c[0]=='POST' and c[1]=='/projects'))['search_selection_id']=='a'*32
        searches=[c for c in calls if c[0]=='POST' and c[1]=='/scans/projects']
        assert len(searches)==1, f'Expected one automatic queue command, got {len(searches)}'
        assert json.loads(searches[0][2])=={'project_ids':[9]}
    check('create automatically queues exactly its own project',auto_search)
    def isolated_new_project():
        mode.update(source='copart',scoped_drafts=True,selection_issue=None,selection_status='CLOSED',can_search=True)
        calls.clear();draft_reads.clear()
        try:
            create_form(False)
            assert not page.locator('.search-selection-summary').count(), 'A new project inherited another project filters'
            submit=page.get_by_role('button',name='Создать и настроить Telegram',exact=True)
            assert submit.is_disabled()
            first=draft_reads[-1]
            assert first and len(first)==32
            page.get_by_role('button',name='Выбрать авто для поиска',exact=True).click()
            page.get_by_role('button',name='Использовать выбранные фильтры',exact=True).click()
            submit.click()
            page.wait_for_timeout(400)
            page.get_by_role('button',name='Готово — начать поиск',exact=True).click()
            created=json.loads(next(c[2] for c in calls if c[0]=='POST' and c[1]=='/projects'))
            assert created['search_draft_id']==first
            page.get_by_role('button',name='← Назад к проектам',exact=True).click()
            page.get_by_role('button',name='Создать проект',exact=True).click()
            page.wait_for_timeout(300)
            assert draft_reads[-1]!=first
            assert not page.locator('.search-selection-summary').count()
            assert page.get_by_role('button',name='Создать и настроить Telegram',exact=True).is_disabled()
            page.screenshot(path=str(report/'new-project-empty-filters.png'),full_page=True)
        finally:
            mode['scoped_drafts']=False
    check('each new project gets an empty independent filter draft',isolated_new_project)
    def denied():
        calls.clear();mode['can_search']=False
        create_form()
        assert mode['created']
        assert not [c for c in calls if c[0]=='POST' and c[1]=='/scans/projects']
        assert 'Поиск недоступен' in page.locator('main').inner_text()
    check('creation without search permission is retained without a queue command',denied)
    mode['can_search']=True
    def launch_failure():
        mode['fail_launch']=True;calls.clear()
        create_form()
        assert 'Проект создан, но поиск не запущен' in page.locator('main').inner_text()
        assert not page.get_by_role('button',name='Создать и настроить Telegram',exact=True).count()
        mode['fail_launch']=False
    check('queue failure does not become a project creation failure',launch_failure)
    mode['fail_launch']=False
    def preview():
        page.goto(BASE+'?source=copart&view=project&project=9')
        photo=page.get_by_role('button',name='Посмотреть фото: Audi fixture',exact=True)
        photo.click(timeout=3000)
        dialog=page.get_by_role('dialog',name='Фото автомобиля')
        dialog.wait_for(state='visible')
        assert dialog.locator('img').get_attribute('src').endswith('/fixture-photo.svg')
        page.screenshot(path=str(report/'photo-preview.png'))
        page.keyboard.press('Escape')
        dialog.wait_for(state='detached')
        assert photo.evaluate('(e)=>e===document.activeElement'),'Photo trigger did not regain focus'
    check('photo opens in modal, Escape closes it and restores focus',preview)
    def unfinished():
        mode.update(source='copart',selection_issue='LAST_CHANGE_UNCONFIRMED');calls.clear()
        create_form(False)
        assert 'Последнее изменение не подтверждено' in page.locator('.search-selection').inner_text()
        assert page.get_by_role('button',name='Создать и настроить Telegram',exact=True).is_disabled()
        assert 'Year: 2017–2027' in page.locator('.search-selection').inner_text()
        assert not [c for c in calls if c[0]=='POST' and c[1]=='/projects']
        mode['selection_issue']=None
    check('closing during an edit retains the previous summary but blocks silent reuse',unfinished)
    def captured_iaa():
        mode.update(source='iaa',selection_issue=None,iaa_native_ready=False);calls.clear()
        create_form(False)
        assert 'нет ссылки для их восстановления' in page.locator('.search-selection').inner_text()
        assert page.get_by_role('button',name='Создать и настроить Telegram',exact=True).is_disabled()
        assert not page.get_by_label('Ссылка поиска IAA').count()
        assert not [c for c in calls if c[0]=='POST' and c[1]=='/projects']
        page.screenshot(path=str(report/'iaa-captured-filters.png'))
    check('IAA captured conditions are shown without substituting the general search URL',captured_iaa)
    def confirmed_iaa():
        mode.update(source='iaa',selection_status='CLOSED',selection_issue=None,iaa_native_ready=True,invalid_url=False);calls.clear()
        create_form()
        creates=[json.loads(c[2]) for c in calls if c[0]=='POST' and c[1]=='/projects']
        assert len(creates)==1 and creates[0]['search_url']=='https://www.iaai.com/Search?url=public-native-uri'
        assert creates[0]['search_selection_id']=='a'*32 and creates[0]['source_key']=='iaa'
        searches=[json.loads(c[2]) for c in calls if c[0]=='POST' and c[1]=='/scans/projects']
        assert searches==[{'project_ids':[9]}]
        mode['iaa_native_ready']=False
    check('confirmed native IAA preset creates and queues exactly its own project',confirmed_iaa)
    def waiting_for_capture():
        mode.update(source='iaa',selection_status='SELECTING',selection_issue='NO_SEARCH_CAPTURED',empty_selection=True);calls.clear()
        create_form(False)
        confirm=page.get_by_role('button',name='Использовать выбранные фильтры',exact=True)
        assert confirm.is_visible() and confirm.is_disabled()
        assert 'Условия ещё не получены' in page.locator('.search-selection').inner_text()
        assert page.get_by_role('button',name='Создать и настроить Telegram',exact=True).is_disabled()
        assert not [c for c in calls if c[0]=='POST' and c[1] in ('/projects','/scans/projects')]
        page.screenshot(path=str(report/'iaa-waiting-for-capture.png'))
        mode.update(selection_status='CLOSED',selection_issue=None,empty_selection=False)
    check('pending capture has a visible disabled confirmation and actionable explanation',waiting_for_capture)
    def explicit_confirmation():
        mode.update(source='copart',selection_issue=None,selection_status='CLOSED',hold_selection=True);calls.clear()
        create_form(False)
        page.get_by_role('button',name='Изменить фильтры',exact=True).click()
        page.wait_for_timeout(100)
        assert held_selection
        assert page.get_by_role('button',name='Создать и настроить Telegram',exact=True).is_disabled()
        mode.update(hold_selection=False,selection_status='READY')
        held_selection.pop().fulfill(status=200,content_type='application/json',body=json.dumps({'selection':selection()}))
        confirm=page.get_by_role('button',name='Использовать выбранные фильтры',exact=True)
        confirm.wait_for()
        assert page.get_by_role('button',name='Создать и настроить Telegram',exact=True).is_disabled()
        confirm.click()
        page.wait_for_timeout(350)
        assert page.get_by_role('button',name='Создать и настроить Telegram',exact=True).is_enabled()
        assert not [c for c in calls if c[0]=='POST' and c[1] in ('/projects','/scans/projects')]
        assert [json.loads(c[2])['action'] for c in calls if c[0]=='POST' and c[1].startswith('/source-search/')]==['OPEN','CONFIRM']
        assert page.get_by_label('Название проекта Copart').input_value()=='Audi fixture'
        for width in [1280,800,360]:
            page.set_viewport_size({'width':width,'height':1000})
            form=page.locator('.auction-project-form')
            assert form.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
            assert form.locator('.search-selection').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
            assert form.evaluate('(e)=>e.getBoundingClientRect().bottom-e.lastElementChild.getBoundingClientRect().bottom<40'), 'Inline form inherited modal fullscreen height'
            page.screenshot(path=str(report/f'selection-{width}.png'),full_page=True)
    check('explicit selection confirmation never creates or queues a project',explicit_confirmation)
    def layout():
        mode.update(source='copart',invalid_url=False)
        for width in [1280,800,360]:
            page.set_viewport_size({'width':width,'height':900})
            page.goto(BASE+'?source=copart')
            card=page.locator('.auction-project').first
            card.wait_for()
            assert 'Создан' not in card.inner_text() and 'Обновлён' in card.inner_text()
            assert card.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
            name=card.locator('.auction-project-name')
            action=card.locator('.auction-project-refresh')
            assert name.count()==1
            assert action.get_attribute('aria-label')=='Обновить данные: '+project['name']
            assert action.locator('svg').count()==1
            page.screenshot(path=str(report/f'projects-{width}.png'),full_page=True)
    check('project metadata, long names and action layout at 1280/800/360',layout)
    def ukrainian():
        mode.update(source='iaa',locale='uk')
        page.set_viewport_size({'width':1280,'height':900})
        page.goto(BASE+'?source=iaa&view=project&project=9')
        page.get_by_role('button',name='Переглянути фото: Audi fixture',exact=True).click()
        dialog=page.get_by_role('dialog',name='Фото автомобіля')
        dialog.wait_for(state='visible')
        dialog.get_by_role('button',name='Закрити ×').click()
        dialog.wait_for(state='detached')
        page.goto(BASE+'?source=iaa')
        card=page.locator('.auction-project').first
        card.wait_for()
        assert 'Створено' not in card.inner_text() and 'Оновлено' in card.inner_text()
        page.screenshot(path=str(report/'projects-uk.png'))
    check('IAA shared preview and project metadata work in Ukrainian',ukrainian)
    def broken_photo():
        mode.update(source='copart',locale='ru',photo_error=True)
        page.goto(BASE+'?source=copart&view=project&project=9')
        page.get_by_role('button',name='Посмотреть фото: Audi fixture',exact=True).click()
        dialog=page.get_by_role('dialog',name='Фото автомобиля')
        dialog.get_by_role('alert').wait_for()
        assert 'Не удалось загрузить фото' in dialog.inner_text()
        dialog.get_by_role('button',name='Закрыть ×').click()
        dialog.wait_for(state='detached')
    check('unavailable image is explained and the preview remains closable',broken_photo)
    def remove_lot():
        for source in ['copart','iaa']:
            removed_projects.clear();calls.clear()
            mode.update(source=source,locale='ru',photo_error=False,removal_projects=True,can_search=False,remove_error=False)
            page.set_viewport_size({'width':1280,'height':900})
            page.goto(BASE+f'?source={source}&view=project&project=9')
            trigger=page.get_by_role('button',name='Удалить из проекта: Audi fixture',exact=True)
            trigger.wait_for()
            page.wait_for_function("document.querySelector('.auction-project-toolbar .auction-project-refresh')?.disabled===true")
            assert page.get_by_role('button',name='Обновить данные',exact=True).last.is_disabled()
            trigger.click()
            dialog=page.get_by_role('dialog',name='Удалить автомобиль из проекта?',exact=True)
            assert 'больше не будет добавляться' in dialog.inner_text()
            assert project['name'] in dialog.inner_text()
            page.keyboard.press('Escape')
            dialog.wait_for(state='detached')
            assert trigger.evaluate('(e)=>e===document.activeElement')
            assert not [c for c in calls if c[0]=='DELETE']
            trigger.click();dialog.get_by_role('button',name='Отмена',exact=True).click()
            assert not [c for c in calls if c[0]=='DELETE']
            # Failure leaves the card and a closable, retryable confirmation.
            mode['remove_error']=True
            trigger.click();dialog.get_by_role('button',name='Удалить из проекта',exact=True).click()
            dialog.get_by_role('alert').wait_for()
            assert page.locator('.auction-card').count()==1
            assert 'Не удалось подтвердить удаление' in dialog.get_by_role('alert').inner_text()
            mode.update(remove_error=False,hold_removal=True)
            dialog.get_by_role('button',name='Удалить из проекта',exact=True).click()
            page.wait_for_timeout(150)
            assert len(held_removals)==1
            assert dialog.get_by_role('button',name='Удаление…',exact=True).is_disabled()
            assert dialog.get_by_role('button',name='Отмена',exact=True).is_disabled()
            page.keyboard.press('Escape')
            assert dialog.is_visible()
            assert len([c for c in calls if c[0]=='DELETE'])==2
            removed_projects.add(9);mode['hold_removal']=False
            held_removals.pop().fulfill(status=204,body='')
            dialog.wait_for(state='detached')
            page.get_by_text('Сохранённых лотов нет',exact=True).wait_for()
            assert not page.locator('.auction-card').count()
            assert not page.locator('.auction-removal-message').count()
            assert not page.get_by_text('Автомобиль удалён из проекта',exact=False).count()
            assert page.locator('h1').evaluate('(e)=>e===document.activeElement')
            assert all(c[1]=='/projects/9/cars/41' for c in calls if c[0]=='DELETE')
            page.reload()
            page.get_by_text('Сохранённых лотов нет',exact=True).wait_for()
            page.goto(BASE+f'?source={source}&view=project&project=10')
            page.get_by_role('button',name='Удалить из проекта: Audi fixture',exact=True).wait_for()
            assert page.locator('.auction-card').count()==1,'Removal leaked to another project'
            # Today cards keep the exact project context, including Ukrainian and narrow windows.
            mode['locale']='uk'
            page.goto(BASE+f'?source={source}')
            page.get_by_role('button',name='Сьогодні',exact=True).click()
            trigger=page.get_by_role('button',name='Видалити з проєкту: Audi fixture',exact=True)
            trigger.wait_for();trigger.click()
            dialog=page.get_by_role('dialog',name='Видалити автомобіль із проєкту?',exact=True)
            assert 'Другой проект' in dialog.inner_text() and 'В інших проєктах він залишиться' in dialog.inner_text()
            for width in [1280,800,360]:
                page.set_viewport_size({'width':width,'height':900})
                assert dialog.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                page.screenshot(path=str(report/f'{source}-remove-confirm-{width}.png'))
            dialog.get_by_role('button',name='Видалити з проєкту',exact=True).click()
            dialog.wait_for(state='detached')
            page.wait_for_function("document.querySelectorAll('.auction-card').length===0")
            assert [c[1] for c in calls if c[0]=='DELETE']==['/projects/9/cars/41','/projects/9/cars/41','/projects/10/cars/41']
            assert not [c for c in calls if c[0]=='POST' and c[1]=='/scans/projects']
        removed_projects.clear();mode.update(removal_projects=False,can_search=True,locale='ru')
    check('project-scoped removal: cancel, failure, pending, success, reload, other project and Today in both auctions',remove_lot)
    def live_progress():
        mode.update(source='copart',locale='ru',photo_error=False,can_search=True,progress_projects=[
            dict(project,id=9,can_search=True,active_status=None,reason=None),
            dict(project,id=10,name='Второй проект',can_search=True,active_status=None,reason=None),
            dict(project,id=11,source_key='iaa',name='Проект IAA',can_search=True,active_status=None,reason=None)])
        page.set_viewport_size({'width':1280,'height':900})
        page.goto(BASE+'?source=copart')
        page.locator('.shell-refresh').click()
        dialog=page.get_by_role('dialog',name='Обновить данные')
        dialog.get_by_role('button',name='Выбрать все доступные',exact=True).click()
        dialog.get_by_role('button',name='Запустить обновление (3)',exact=True).click()
        page.wait_for_function("document.querySelectorAll('.launch-progress [data-status=QUEUED]').length===3")
        run=mode['runs'][0]
        run.update(status='RUNNING',progress=35)
        run['payload'].update(current_project_id=10,project_statuses={'9':'SUCCEEDED','10':'RUNNING','11':'QUEUED'})
        page.wait_for_function("!!document.querySelector('.launch-progress [data-project-id=\"10\"][data-status=RUNNING]')")
        assert dialog.locator('[data-project-id="9"]').inner_text()=='Завершено'
        page.wait_for_function("!!document.querySelector('[aria-label=\"Copart: Обновляется\"].source-running')")
        assert page.locator('.source-waiting[aria-label="IAA: В очереди"]').count()==1
        indicator=page.locator('.source-running[aria-label="Copart: Обновляется"]')
        assert indicator.evaluate('(e)=>getComputedStyle(e).animationName')=='source-update-pulse'
        page.emulate_media(reduced_motion='reduce')
        assert indicator.evaluate('(e)=>getComputedStyle(e).animationName')=='none'
        page.emulate_media(reduced_motion='no-preference')
        page.screenshot(path=str(report/'live-update-dialog.png'))
        # Waiting is orange, not a successful completion or a blue active read.
        run['payload']['pagination']={'10':{'interaction':{'state':'WAITING_FOR_USER','reason':'SITE_VERIFICATION_REQUIRED'}}}
        page.wait_for_function("!!document.querySelector('.launch-progress [data-status=WAITING_FOR_USER]')")
        page.wait_for_function("!!document.querySelector('.source-waiting[aria-label=\"Copart: Ожидается проверка сайта\"]')")
        del run['payload']['pagination']
        mode['progress_error']=True
        dialog.get_by_role('alert').wait_for(timeout=6000)
        assert 'не означает завершения' in dialog.get_by_role('alert').inner_text()
        mode['progress_error']=False
        dialog.get_by_role('button',name='Повторить',exact=True).click()
        page.wait_for_function("!!document.querySelector('.launch-progress [data-status=RUNNING]')")
        # Completion persists even after the active-only endpoint drops the run.
        run.update(status='SUCCEEDED',progress=100)
        run['payload'].update(current_project_id=None,project_statuses={'9':'SUCCEEDED','10':'SUCCEEDED','11':'FAILED'})
        page.wait_for_function("document.querySelectorAll('.launch-progress [data-status=SUCCEEDED]').length===2")
        assert dialog.locator('[data-project-id="11"]').inner_text()=='Ошибка'
        assert 'Завершено: 2' in dialog.inner_text() and 'С ошибкой: 1' in dialog.inner_text()
        page.screenshot(path=str(report/'completed-update-dialog.png'))
        page.set_viewport_size({'width':360,'height':900})
        assert dialog.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        page.screenshot(path=str(report/'completed-update-dialog-360.png'))
        page.set_viewport_size({'width':1280,'height':900})
        dialog.get_by_role('button',name='Готово',exact=True).click()
        page.wait_for_function("document.querySelectorAll('.source-running,.source-waiting').length===0")
        assert not page.locator('.auction-project [data-status=SUCCEEDED]').count()
        for width in [1280,800,360]:
            page.set_viewport_size({'width':width,'height':900})
            assert page.locator('main').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
            page.screenshot(path=str(report/f'project-progress-{width}.png'),full_page=True)
        # Completed state survives navigation and does not start another scan.
        posts=len([c for c in calls if c[0]=='POST' and c[1]=='/scans/projects'])
        page.goto(BASE+'?source=copart&view=scans')
        page.locator('.auction-history-run').get_by_role('button',name='Подробнее',exact=True).click()
        page.get_by_role('heading',name='Второй проект',exact=True).wait_for()
        assert page.locator('.auction-run-project').count()==2
        assert len([c for c in calls if c[0]=='POST' and c[1]=='/scans/projects'])==posts
        mode.update(progress_projects=None,runs=[])
    check('live queue tracks individual completion, waiting, failure, sidebar and project states',live_progress)
    def history_fixture():
        plans=[dict(project_id=9,source_key='copart',name='Audi · длинное название проекта для проверки переноса текста'),
            dict(project_id=10,source_key='copart',name='Удалённый BMW'),dict(project_id=11,source_key='iaa',name='Foreign IAA')]
        def run(rid,status,ids,statuses,pagination=None,**payload):
            return dict(id=rid,kind='PROJECTS',status=status,progress=87,created_at='2026-10-19T10:00:00Z',
                payload=dict(project_ids=ids,project_plans=[p for p in plans if p['project_id'] in ids],project_statuses=statuses,
                    pagination=pagination or {},trigger='MANUAL',**payload))
        mode.update(source='copart',locale='ru',can_search=True,runs=[
            run(101,'PARTIAL',[9,11],{'9':'SUCCEEDED','11':'FAILED'},
                {'9':dict(mode='FIRST_PAGE',pages_visited=1,total_pages=9,ads_seen=20,complete=True)},report=[
                    dict(car_id=41,project_id=9,source_key='copart',source_listing_id='12345678',title='<script>New Audi</script>',change='NEW'),
                    dict(car_id=42,project_id=9,source_key='copart',title='Changed Audi',change='UPDATED',changes=[dict(field='schedule',old={'raw':'Today'},new={'raw':'Tomorrow'})]),
                    dict(car_id=43,project_id=11,source_key='iaa',title='Foreign car',change='NEW')]),
            run(102,'RUNNING',[9,11],{'9':'RUNNING','11':'QUEUED'},{'9':dict(mode='ALL_PAGES',pages_visited=2,ads_seen=40)},current_project_id=9),
            run(103,'QUEUED',[10],{'10':'QUEUED'}),run(104,'FAILED',[10],{'10':'FAILED'}),run(105,'CANCELLED',[10],{'10':'CANCELLED'}),
            run(106,'SUCCEEDED',[11],{'11':'SUCCEEDED'},{'11':dict(mode='ALL_PAGES',pages_visited=2,total_pages=2,ads_seen=99,complete=True)})],
            bulk_projects=None,progress_projects=None,history_error=False,history_cancel_error=False)
        calls.clear()
        page.set_viewport_size({'width':1280,'height':900})
        page.goto(BASE+'?source=copart&view=scans')
        page.locator('.auction-history-run').first.wait_for()
        page.wait_for_function("document.querySelectorAll('.auction-history-run').length===5")
    def history_filters():
        history_fixture()
        assert page.locator('.auction-history-run').count()==5
        own=page.locator('.auction-history')
        assert 'Foreign IAA' not in own.inner_text()
        assert '1 из 1 · выбранный охват' in own.inner_text()
        assert page.locator('[data-run-id="102"] progress').get_attribute('value') is None
        assert '87%' not in own.inner_text()
        page.get_by_label('Состояние поиска',exact=True).select_option('completed')
        page.wait_for_function("document.querySelectorAll('.auction-history-run').length===1")
        assert page.locator('.auction-history-run').count()==1
        page.get_by_label('Проект',exact=True).select_option('10')
        page.get_by_text('Нет запусков по выбранным фильтрам.',exact=True).wait_for()
        page.get_by_role('button',name='Сбросить фильтры',exact=True).first.click()
        page.wait_for_function("document.querySelectorAll('.auction-history-run').length===5")
        assert page.locator('.auction-history-run').count()==5
        row=page.locator('[data-run-id="101"]')
        row.get_by_role('button',name='Подробнее',exact=True).focus()
        page.keyboard.press('Enter')
        row.get_by_role('heading',name='Изменения: 2',exact=True).wait_for()
        assert row.locator('.auction-history-change').count()==2
        assert not row.locator('script').count()
        assert 'Foreign car' not in row.inner_text()
        row.get_by_label('Фильтр изменений',exact=True).select_option('schedule')
        page.wait_for_function("document.querySelectorAll('[data-run-id=\"101\"] .auction-history-change').length===1")
        assert row.locator('.auction-history-change').count()==1
        assert 'Today → Tomorrow' in row.inner_text()
        row.get_by_role('button',name='Telegram',exact=True).click()
        row.get_by_text('Telegram выключен в тестовом профиле. Отправка сообщений не выполнялась.',exact=True).wait_for()
        row.get_by_role('button',name='Изменения (2)',exact=True).click()
        row.get_by_label('Фильтр изменений',exact=True).wait_for()
        assert row.locator('.auction-history-change').count()==1
        # Sidebar navigation opens base screens instead of the last report/filter.
        page.get_by_label('Состояние поиска',exact=True).select_option('attention')
        page.locator('.source-button[aria-label^="IAA:"]').click()
        page.get_by_role('button',name='Запуски',exact=True).click()
        page.get_by_label('Состояние поиска',exact=True).wait_for()
        assert page.get_by_label('Состояние поиска',exact=True).input_value()=='all'
        page.wait_for_function("document.querySelectorAll('.auction-history-run').length===3")
        page.get_by_label('Проект',exact=True).select_option('11')
        page.locator('.source-button[aria-label^="Copart:"]').click()
        page.get_by_role('heading',name='Проекты · Copart',exact=True).wait_for()
        page.get_by_role('button',name='Запуски',exact=True).click()
        page.get_by_label('Состояние поиска',exact=True).wait_for()
        assert page.get_by_label('Состояние поиска',exact=True).input_value()=='all'
        assert page.get_by_label('Проект',exact=True).input_value()=='all'
        page.wait_for_function("document.querySelectorAll('.auction-history-run').length===5")
        assert page.locator('.auction-history-run [aria-expanded=true]').count()==0
        assert not [c for c in calls if c[0] in ('POST','DELETE','PATCH') and c[1]!='/auth/workspace']
        assert not [c for c in calls if c[1].startswith('/cars/')]
    check('auction history scopes filters, frozen projects, stored changes and source state without starting scans',history_filters)
    def history_cancel():
        history_fixture()
        row=page.locator('[data-run-id="102"]')
        cancel=row.get_by_role('button',name='Отменить поиск',exact=True)
        cancel.click()
        dialog=page.get_by_role('dialog')
        assert dialog.get_by_role('button',name='Не отменять',exact=True).evaluate('(e)=>e===document.activeElement')
        page.keyboard.press('Escape')
        assert cancel.evaluate('(e)=>e===document.activeElement')
        assert not [c for c in calls if c[1].endswith('/cancel')]
        cancel.click();mode['history_cancel_error']=True
        dialog.get_by_role('button',name='Подтвердить отмену',exact=True).click()
        dialog.get_by_role('alert').wait_for()
        assert dialog.is_visible()
        mode['history_cancel_error']=False
        dialog.get_by_role('button',name='Подтвердить отмену',exact=True).click()
        dialog.wait_for(state='hidden')
        assert len([c for c in calls if c[1].endswith('/cancel')])==2
        assert all(json.loads(c[2])=={'source_keys':['copart']} for c in calls if c[1].endswith('/cancel'))
        assert mode['runs'][1]['payload']['project_statuses']['11']=='QUEUED'
        page.wait_for_function("!!document.querySelector('[data-run-id=\"102\"] [data-status=CANCEL_REQUESTED]')")
        assert not row.get_by_role('button',name='Отменить поиск',exact=True).count()
        assert not [c for c in calls if c[0]=='POST' and c[1]=='/scans/projects']
    check('history cancellation is confirmed, recoverable, source-scoped and never cancels other sites',history_cancel)
    def history_visual():
        for source,locale in [('copart','ru'),('iaa','uk')]:
            history_fixture();mode.update(source=source,locale=locale)
            page.goto(BASE+f'?source={source}&view=scans')
            page.locator('.auction-history-run').first.wait_for()
            page.locator(f'[data-run-id="{106 if source=="iaa" else 101}"]').get_by_role('button',name='Докладніше' if locale=='uk' else 'Подробнее',exact=True).click()
            page.get_by_label('Стан пошуку' if locale=='uk' else 'Состояние поиска',exact=True).select_option('completed')
            page.wait_for_function("document.querySelectorAll('.auction-history-run').length===1")
            for width in [1280,800,360]:
                page.set_viewport_size({'width':width,'height':1000})
                assert page.locator('main').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                page.screenshot(path=str(report/f'history-{source}-{locale}-{width}.png'),full_page=True)
        mode.update(source='copart',locale='ru',history_error=True)
        page.goto(BASE+'?source=copart&view=scans')
        page.get_by_text('Не удалось обновить историю. Действия недоступны до повторной проверки.',exact=True).wait_for()
        assert not page.locator('.auction-history-run').count()
        mode['history_error']=False
        page.locator('.auction-history').get_by_role('button',name='Повторить',exact=True).click()
        page.locator('.auction-history-run').first.wait_for()
        mode['runs']=[]
        page.reload()
        page.get_by_text('Сохранённых запусков нет',exact=True).wait_for()
        mode.update(runs=[],history_error=False)
    check('history RU/UK responsive reports, read failure retry and empty states',history_visual)
    def report_cards():
        history_fixture()
        for item in mode['runs'][0]['payload']['report']: item['after_snapshot_id']=item['car_id']+500
        mode.update(snapshot_error=False,snapshot_wrong_source=False,snapshot_unavailable_ids=[],hold_snapshots=True)
        snapshot_reads.clear()
        page.reload()
        page.get_by_label('Состояние поиска',exact=True).select_option('completed')
        page.wait_for_function("document.querySelectorAll('.auction-history-run').length===1")
        assert not snapshot_reads
        row=page.locator('[data-run-id="101"]')
        row.get_by_role('button',name='Подробнее',exact=True).click()
        row.get_by_text('Загрузка сохранённой карточки…',exact=True).first.wait_for()
        assert len(snapshot_reads)==1 and snapshot_reads[0]['car_ids']==['41','42']
        assert snapshot_reads[0]['source_key']==['copart'] and snapshot_reads[0]['computer_timezone']==['Europe/Warsaw']
        mode['hold_snapshots']=False
        route=held_snapshots.pop();route.fulfill(status=200,content_type='application/json',body=json.dumps(snapshot_response(route.request)))
        row.get_by_role('heading',name='Saved snapshot 41',exact=True).wait_for()
        assert row.locator('.auction-card').count()==2
        assert 'Ставка на момент проверки' in row.inner_text() and '123 USD' in row.inner_text()
        assert not row.locator('.auction-countdown,.auction-remove-button,.auction-info h2 button,.auction-time a').count()
        assert 'Сегодня' not in row.inner_text()
        assert 'Фото по сохранённой ссылке' in row.inner_text()
        photo=row.get_by_role('button',name='Посмотреть фото: Saved snapshot 41',exact=True)
        photo.click();page.get_by_role('dialog',name='Фото автомобиля',exact=True).wait_for()
        page.keyboard.press('Escape');assert photo.evaluate('(e)=>e===document.activeElement')
        for width in [1280,800,360]:
            page.set_viewport_size({'width':width,'height':1000})
            assert page.locator('main').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
            row.locator('.auction-history-body').screenshot(path=str(report/f'historical-cards-ru-{width}.png'))
        # Filtered batches have independent failure/retry and reject another source.
        mode['snapshot_error']=True
        row.get_by_label('Фильтр изменений',exact=True).focus()
        row.get_by_label('Фильтр изменений',exact=True).select_option('new')
        row.get_by_role('button',name='Повторить загрузку карточек',exact=True).wait_for()
        assert row.get_by_label('Фильтр изменений',exact=True).evaluate('(e)=>e===document.activeElement'), 'Filter change lost keyboard focus'
        assert '<script>New Audi</script>' in row.inner_text() and not row.locator('.auction-card').count()
        mode['snapshot_error']=False
        row.get_by_role('button',name='Повторить загрузку карточек',exact=True).click()
        row.get_by_role('heading',name='Saved snapshot 41',exact=True).wait_for()
        mode['snapshot_wrong_source']=True
        row.get_by_label('Фильтр изменений',exact=True).select_option('updated')
        row.get_by_role('button',name='Повторить загрузку карточек',exact=True).wait_for()
        assert not row.locator('.auction-card').count()
        mode['snapshot_wrong_source']=False
        row.get_by_role('button',name='Повторить загрузку карточек',exact=True).click()
        row.get_by_role('heading',name='Saved snapshot 42',exact=True).wait_for()
        mode['snapshot_unavailable_ids']=[42]
        page.reload()
        row=page.locator('[data-run-id="101"]')
        row.get_by_role('button',name='Подробнее',exact=True).click()
        row.get_by_text('Исторический снимок недоступен. Актуальные данные не подставляются.',exact=True).wait_for()
        assert 'Today → Tomorrow' in row.inner_text()
        assert not [c for c in calls if c[1].startswith('/cars/') or c[0]=='POST' and c[1]=='/scans/projects']
        mode.update(snapshot_unavailable_ids=[],snapshot_error=False,snapshot_wrong_source=False)
    check('historical report cards: lazy snapshots, frozen money/time, preview, scoped failure/retry and missing evidence',report_cards)
    def report_batches():
        history_fixture();mode.update(source='iaa',locale='uk',hold_snapshots=False,snapshot_error=False)
        mode['runs'][-1]['payload']['report']=[dict(car_id=i,project_id=11,source_key='iaa',source_listing_id=str(65000000+i),
            after_snapshot_id=i+500,title=f'Report IAA {i}',change='NEW') for i in range(200,245)]
        page.set_viewport_size({'width':360,'height':1000})
        snapshot_reads.clear()
        page.goto(BASE+'?source=iaa&view=scans')
        row=page.locator('[data-run-id="106"]')
        row.get_by_role('button',name='Докладніше',exact=True).click()
        row.get_by_role('heading',name='Saved snapshot 200',exact=True).wait_for()
        assert row.locator('.auction-card').count()==20
        assert len(snapshot_reads)==1 and len(snapshot_reads[0]['car_ids'])==20
        row.get_by_role('button',name='Показати ще · 20 / 45',exact=True).focus();page.keyboard.press('Enter')
        row.get_by_role('heading',name='Saved snapshot 220',exact=True).wait_for()
        assert row.locator('.auction-card').count()==40
        assert row.locator('[data-report-item="220"]').evaluate('(e)=>e===document.activeElement')
        row.get_by_role('button',name='Показати ще · 40 / 45',exact=True).click()
        row.get_by_role('heading',name='Saved snapshot 244',exact=True).wait_for()
        assert row.locator('.auction-card').count()==45
        assert [len(q['car_ids']) for q in snapshot_reads]==[20,20,5]
        assert all(q['source_key']==['iaa'] for q in snapshot_reads)
        assert 'Ставка на момент перевірки' in row.inner_text()
        assert page.locator('main').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        row.get_by_label('Фільтр змін',exact=True).select_option('updated')
        row.get_by_text('Немає змін за цим фільтром.',exact=True).wait_for()
        assert len(snapshot_reads)==3
        row.get_by_label('Фільтр змін',exact=True).focus()
        row.get_by_label('Фільтр змін',exact=True).select_option('all')
        page.wait_for_function("document.querySelectorAll('[data-run-id=\"106\"] .auction-card').length===20")
        assert row.get_by_label('Фільтр змін',exact=True).evaluate('(e)=>e===document.activeElement')
        assert len(snapshot_reads)==3
        assert not [c for c in calls if c[1].startswith('/cars/') or c[0]=='POST' and c[1]=='/scans/projects']
    check('historical report cards: IAA Ukrainian bounded batches, load more, keyboard focus and filter reset',report_batches)
    def bulk_project(pid,source='copart',**extra):
        return {**project,'id':pid,'source_key':source,'name':f'{source} project {pid}','can_search':True,
            'reason':None,'active_status':None,'scan_run_id':None,**extra}
    def queue_posts():
        return [json.loads(c[2]) for c in calls if c[0]=='POST' and c[1]=='/scans/projects']
    def reset_bulk():
        mode.update(bulk_projects=None,bulk_sources=None,bulk_race=False,hold_bulk=False,availability_error=False,
            fail_launch=False,source='copart',locale='ru',runs=[],progress_error=False,progress_projects=None,removal_projects=False)
        for route in held_bulk:
            route.fulfill(status=503,content_type='application/json',body='{}')
        held_bulk.clear()
    def bulk_refresh():
        mode.update(source='copart',locale='ru',can_search=True,hold_bulk=True,progress_projects=None,removal_projects=False,bulk_projects=[
            bulk_project(9),bulk_project(10,active_status='RUNNING',scan_run_id=102),
            bulk_project(12,can_search=False,reason='INVALID_SOURCE_URL'),
            bulk_project(13,can_search=False,reason='LICENSE_SOURCE_NOT_ALLOWED'),bulk_project(11,'iaa')])
        mode['runs']=[dict(id=102,kind='PROJECTS',status='RUNNING',progress=30,created_at='2026-10-19T09:00:00Z',
            payload=dict(project_ids=[10],project_plans=[dict(project_id=10,source_key='copart',name='copart project 10')],project_statuses={'10':'RUNNING'}))]
        try:
            page.set_viewport_size({'width':1280,'height':900})
            page.goto(BASE+'?source=copart')
            button=page.get_by_role('button',name='Обновить проекты',exact=True)
            button.wait_for()
            page.wait_for_function("document.querySelector('.auction-project-toolbar>.button')?.disabled===false")
            calls.clear()
            # Two same-tick clicks must produce one preview and one queue command.
            button.evaluate('(button)=>{button.click();button.click()}')
            page.wait_for_timeout(250)
            assert queue_posts()==[{'project_ids':[9]}],queue_posts()
            assert len(held_bulk)==1 and page.get_by_role('button',name='Добавляем в очередь…',exact=True).is_disabled()
            assert len([c for c in calls if c[1]=='/scans/launch-options'])==1,calls
            mode['hold_bulk']=False
            mode['runs'].insert(0,dict(id=101,kind='PROJECTS',status='QUEUED',progress=0,created_at='2026-10-19T10:00:00Z',
                payload=dict(project_ids=[9],project_plans=[dict(project_id=9,source_key='copart',name='copart project 9')],project_statuses={'9':'QUEUED'})))
            held_bulk.pop().fulfill(status=200,content_type='application/json',body=json.dumps(dict(id=101,status='QUEUED',created=True,
                projects=[dict(project_id=9,status='QUEUED',scan_run_id=101)])))
            result=page.get_by_role('region',name='Результат обновления проектов',exact=True)
            result.wait_for()
            assert 'Добавлено в очередь: 1 · Уже активны: 1 · Недоступно: 2' in result.inner_text()
            assert 'Проверьте ссылку' in result.inner_text() and 'Сайт недоступен по лицензии' in result.inner_text()
            assert 'iaa project' not in result.inner_text()
            page.wait_for_function("!!document.querySelector('.auction-refresh-result [data-project-id=\"10\"][data-status=RUNNING]')")
            mode['runs'][0].update(status='SUCCEEDED',progress=100)
            mode['runs'][0]['payload']['project_statuses']={'9':'SUCCEEDED'}
            mode['runs'][1].update(status='FAILED',progress=100)
            mode['runs'][1]['payload']['project_statuses']={'10':'FAILED'}
            mode['bulk_projects'][1].update(active_status=None,scan_run_id=None)
            page.wait_for_function("!!document.querySelector('.auction-refresh-result [data-status=SUCCEEDED]') && !!document.querySelector('.auction-refresh-result [data-status=FAILED]')")
            page.get_by_role('button',name='Сегодня',exact=True).click()
            assert 'Завершено: 1' in result.inner_text() and 'С ошибкой: 1' in result.inner_text()
            assert page.get_by_role('button',name='Обновить проекты',exact=True).is_enabled()
            page.locator('.auction-tabs').get_by_role('button',name='Проекты',exact=True).click()
            page.wait_for_function("!!document.querySelector('.auction-project [data-project-id=\"10\"][data-status=FAILED]')")
            assert not page.locator('.auction-project [data-status=SUCCEEDED]').count()
            page.wait_for_function("document.querySelectorAll('.source-running,.source-waiting').length===0")
            for width in [1280,800,360]:
                page.set_viewport_size({'width':width,'height':900})
                assert page.locator('main').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                toolbar=page.locator('.auction-project-toolbar')
                assert toolbar.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                if width==1280:
                    bulk_box=button.bounding_box();row_box=page.locator('.auction-project-actions').first.bounding_box()
                    collapse_box=page.locator('.auction-projects-panel .projects-collapse-toggle').bounding_box()
                    assert bulk_box['x']+bulk_box['width']<=collapse_box['x']
                    assert abs(collapse_box['x']+collapse_box['width']-row_box['x']-row_box['width'])<20
                page.screenshot(path=str(report/f'bulk-refresh-copart-{width}.png'),full_page=True)
            assert queue_posts()==[{'project_ids':[9]}]
        finally: reset_bulk()
    check('site bulk update: single command, exclusions, active runs, final results, Today and responsive layout',bulk_refresh)
    def refresh_dismissal():
        mode.update(source='copart',locale='ru',can_search=True,bulk_projects=[bulk_project(9)],runs=[])
        try:
            page.goto(BASE+'?source=copart')
            button=page.get_by_role('button',name='Обновить проекты',exact=True)
            button.click()
            result=page.get_by_role('region',name='Результат обновления проектов',exact=True)
            result.wait_for()
            page.wait_for_timeout(4800)
            assert result.is_visible(), 'Queued work must not be treated as complete'
            mode['runs'][0].update(status='SUCCEEDED',progress=100)
            mode['runs'][0]['payload']['project_statuses']={'9':'SUCCEEDED'}
            page.wait_for_function("!!document.querySelector('.auction-refresh-result [data-status=SUCCEEDED]')")
            result.wait_for(state='hidden',timeout=7000)
            assert page.locator('.auction-project [data-status=SUCCEEDED]').count()==0
            button.click();result.wait_for()
            mode['runs'][0].update(status='FAILED',progress=100)
            mode['runs'][0]['payload']['project_statuses']={'9':'FAILED'}
            page.wait_for_function("!!document.querySelector('.auction-refresh-result [data-status=FAILED]')")
            page.wait_for_timeout(4800)
            assert result.is_visible(), 'Failure must stay visible until leaving the screen'
            assert result.get_by_role('button',name='Скрыть результат обновления',exact=True).count()==0
            calls.clear()
            page.locator('.source-sections').get_by_role('button',name='Проекты',exact=True).click()
            assert result.count()==0
            assert not [c for c in calls if c[0]=='POST' and c[1].startswith('/scans')], calls
        finally: reset_bulk()
    check('refresh dismissal: queued stays, success clears, no close cross, failure clears on navigation without cancelling',refresh_dismissal)
    def base_navigation():
        mode.update(source='copart',locale='ru',can_search=True,bulk_projects=[bulk_project(9)],runs=[])
        try:
            page.goto(BASE+'?source=copart&view=project&project=9')
            calls.clear()
            page.locator('.auction-tabs').get_by_role('button',name='Сегодня',exact=True).click()
            page.locator('.source-sections').get_by_role('button',name='Проекты',exact=True).click()
            page.wait_for_url('**/?source=copart')
            assert page.locator('.auction-tabs').get_by_role('button',name='Проекты',exact=True).get_attribute('aria-pressed')=='true'
            page.get_by_role('button',name='Создать проект',exact=True).click()
            assert page.get_by_label('Название проекта Copart',exact=True).is_visible()
            assert page.locator('.auction-create-dialog').evaluate('(e)=>e.matches(":modal")')
            page.keyboard.press('Escape')
            page.locator('.auction-create-dialog').wait_for(state='detached')
            assert page.get_by_role('button',name='Создать проект',exact=True).evaluate('(e)=>e===document.activeElement')
            page.locator('.source-button[aria-label^="Copart:"]').click()
            assert page.get_by_label('Название проекта Copart',exact=True).count()==0
            page.locator('.auction-tabs').get_by_role('button',name='Сегодня',exact=True).click()
            page.locator('.source-button[aria-label^="IAA:"]').click()
            page.locator('.source-sections').get_by_role('button',name='Запуски',exact=True).click()
            page.locator('.source-button[aria-label^="Copart:"]').click()
            page.wait_for_url('**/?source=copart')
            assert page.locator('.auction-tabs').get_by_role('button',name='Проекты',exact=True).get_attribute('aria-pressed')=='true'
            page.locator('.source-button[aria-label^="IAA:"]').click()
            page.wait_for_url('**/?source=iaa')
            assert page.get_by_role('heading',name='Проекты · IAA',exact=True).is_visible()
            assert not [c for c in calls if c[0]=='POST' and c[1].startswith('/scans')], calls
        finally: reset_bulk()
    check('base navigation: sidebar projects and source clicks reset nested routes, Today and open forms',base_navigation)
    def saved_collections():
        try:
            for source,locale,labels in [('copart','ru',['Будущие','Без даты','Архив']),('iaa','uk',['Майбутні','Без дати','Архів'])]:
                mode.update(source=source,locale=locale,can_search=False,collections_fixture=True,runs=[],collection_error=None,
                    bulk_projects=[bulk_project(9,source),bulk_project(10,source),bulk_project(11,'iaa' if source=='copart' else 'copart')])
                page.goto(BASE+f'?source={source}&view=project&project=9');calls.clear()
                for key,label in zip(['future','undated','archive'],labels):
                    tab=page.locator('.auction-tabs').get_by_role('button',name=label,exact=True)
                    tab.focus();page.keyboard.press('Enter')
                    page.get_by_role('heading',name=f'{key} car 9',exact=True).wait_for()
                    assert tab.get_attribute('aria-pressed')=='true'
                    assert not page.get_by_role('heading',name='active car 9',exact=True).count()
                assert not page.locator('.auction-countdown').count()
                assert not page.locator('.auction-remove-button').count()
                assert not page.locator('.auction-date').filter(has_text='Сегодня').count()
                for width in [1280,800,360]:
                    page.set_viewport_size({'width':width,'height':900})
                    assert page.locator('main').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                    page.screenshot(path=str(report/f'{source}-saved-archive-{width}.png'),full_page=True)
                page.locator('.source-sections').get_by_role('button',name='Проекты' if locale=='ru' else 'Проєкти',exact=True).click()
                calls.clear()
                page.locator('.auction-tabs').get_by_role('button',name=labels[2],exact=True).click()
                page.get_by_role('heading',name='archive car 9',exact=True).wait_for()
                page.get_by_role('heading',name='archive car 10',exact=True).wait_for()
                assert not page.get_by_role('heading',name='archive car 11',exact=True).count()
                assert not [c for c in calls if c[0]=='POST' and c[1].startswith('/scans')], calls
                # A failed new collection never substitutes cards from another query key.
                mode['collection_error']='undated'
                page.locator('.auction-tabs').get_by_role('button',name=labels[1],exact=True).click()
                page.get_by_role('button',name='Повторить' if locale=='ru' else 'Повторити',exact=True).first.wait_for()
                assert not page.get_by_role('heading',name='archive car 9',exact=True).count()
                assert not page.get_by_role('heading',name='archive car 10',exact=True).count()
                mode['collection_error']=None
                page.get_by_role('button',name='Повторить' if locale=='ru' else 'Повторити',exact=True).first.click()
                page.get_by_role('heading',name='undated car 9',exact=True).wait_for()
        finally:
            mode.update(collections_fixture=False,collection_error=None,collection_empty=False)
            reset_bulk()
    check('saved collections: local project/site filters, archive warning, keyboard/mobile, error isolation and retry without scans',saved_collections)
    def telegram_preview():
        try:
            for source,locale in [('copart','ru'),('iaa','uk')]:
                mode.update(source=source,locale=locale,can_search=False,telegram_fixture=True,telegram_count=45,telegram_error=False,
                    telegram_wrong_source=False,telegram_wrong_project=False,runs=[],bulk_projects=None,progress_projects=None)
                page.set_viewport_size({'width':1280,'height':900})
                page.goto(BASE+f'?source={source}&view=project&project=9')
                page.locator('.auction-tabs').get_by_role('button',name='Сегодня' if locale=='ru' else 'Сьогодні',exact=True).click()
                action=page.get_by_role('button',name='Telegram',exact=True)
                action.wait_for();telegram_reads.clear();calls.clear()
                assert action.is_enabled()
                assert action.locator('svg.telegram-logo[aria-hidden="true"][focusable="false"]').count()==1
                assert action.evaluate('(e)=>getComputedStyle(e).backgroundColor')=='rgb(8, 126, 181)'
                action.focus();page.keyboard.press('Enter')
                dialog=page.locator('dialog.auction-telegram-preview')
                dialog.locator('[data-preview-message]').first.wait_for()
                assert dialog.locator('[data-preview-message]').count()==20
                assert all(pid==9 and query['computer_timezone']==['Europe/Warsaw'] for pid,query in telegram_reads)
                text=dialog.inner_text()
                assert not dialog.locator('.telegram-preview-text').filter(has_text='Проект:').count()
                assert not dialog.locator('.telegram-preview-text').filter(has_text='Проєкт:').count()
                assert ('Выберите автомобили' if locale=='ru' else 'Виберіть автомобілі') in text
                send=dialog.get_by_role('button',name='Отправить выбранные' if locale=='ru' else 'Надіслати вибрані',exact=False)
                assert send.is_disabled()
                dialog.locator('.telegram-select-all input').check()
                assert '(45)' in send.inner_text() and send.is_enabled()
                send.click()
                dialog.get_by_role('alert').filter(has_text='не подключена' if locale=='ru' else 'не підключено').wait_for()
                assert not [c for c in calls if c[0]=='POST']
                assert '3 ч 30 мин' in text if locale=='ru' else '3 год 30 хв' in text
                assert '2026-10-16T10:00:00Z' in text
                assert ('Без фото · текстовое сообщение' if locale=='ru' else 'Без фото · текстове повідомлення') in text
                # A nested photo dialog must close independently and restore its own trigger.
                photo=dialog.locator('.auction-photo-button').first
                photo.click();page.locator('dialog.auction-image-preview').wait_for(state='visible')
                page.keyboard.press('Escape');page.locator('dialog.auction-image-preview').wait_for(state='detached')
                assert dialog.is_visible() and photo.evaluate('(e)=>e===document.activeElement')
                more=dialog.get_by_role('button',name='Показать ещё' if locale=='ru' else 'Показати ще',exact=True)
                more.focus();page.keyboard.press('Enter')
                assert dialog.locator('[data-preview-message]').count()==40
                assert dialog.locator('[data-preview-message]').nth(20).evaluate('(e)=>e===document.activeElement')
                more.click();assert dialog.locator('[data-preview-message]').count()==45
                assert not more.count()
                for width in [1280,800,360]:
                    page.set_viewport_size({'width':width,'height':900})
                    assert dialog.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                    close=dialog.get_by_role('button',name='Закрыть' if locale=='ru' else 'Закрити',exact=True)
                    assert close.is_visible()
                    dialog.locator('.launch-content').evaluate('(e)=>e.scrollTop=0')
                    page.screenshot(path=str(report/f'{source}-telegram-preview-{width}.png'))
                page.keyboard.press('Escape');dialog.wait_for(state='detached')
                assert action.evaluate('(e)=>e===document.activeElement')
                assert action.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                page.screenshot(path=str(report/f'{source}-telegram-button-mobile.png'))
                # Explicit refresh is only another local GET; errors hide stale messages.
                action.click();dialog.locator('[data-preview-message]').first.wait_for()
                mode['telegram_error']=True
                dialog.get_by_role('button',name='Обновить предпросмотр' if locale=='ru' else 'Оновити попередній перегляд',exact=True).click()
                dialog.get_by_role('alert').wait_for();assert not dialog.locator('[data-preview-message]').count()
                mode['telegram_error']=False
                dialog.get_by_role('button',name='Повторить' if locale=='ru' else 'Повторити',exact=True).click()
                dialog.locator('[data-preview-message]').first.wait_for()
                mode['telegram_wrong_project']=True
                dialog.get_by_role('button',name='Обновить предпросмотр' if locale=='ru' else 'Оновити попередній перегляд',exact=True).click()
                dialog.get_by_role('alert').wait_for();assert not dialog.locator('[data-preview-message]').count()
                mode.update(telegram_wrong_project=False,telegram_wrong_source=True)
                dialog.get_by_role('button',name='Повторить' if locale=='ru' else 'Повторити',exact=True).click()
                dialog.get_by_role('alert').wait_for();assert not dialog.locator('[data-preview-message]').count()
                mode.update(telegram_wrong_source=False,telegram_count=0)
                dialog.get_by_role('button',name='Повторить' if locale=='ru' else 'Повторити',exact=True).click()
                dialog.get_by_text('В этом разделе пока нет автомобилей. Обновите данные проекта или выберите другой раздел. Подключать Telegram повторно не нужно.' if locale=='ru' else 'У цьому розділі поки немає автомобілів. Оновіть дані проєкту або виберіть інший розділ. Підключати Telegram повторно не потрібно.',exact=True).wait_for()
                page.keyboard.press('Escape');dialog.wait_for(state='detached')
                assert not [c for c in calls if c[0]!='GET' and c[1]!='/auth/workspace'], calls
            # Loading is cancellable, and a late response never reopens the modal.
            page.reload();mode.update(telegram_hold=True);action.wait_for();action.click()
            dialog.get_by_role('status').wait_for();page.keyboard.press('Escape');dialog.wait_for(state='detached')
            assert action.evaluate('(e)=>e===document.activeElement')
            mode['telegram_hold']=False
            while held_telegram: held_telegram.pop().fulfill(status=503,content_type='application/json',body='{}')
            page.wait_for_timeout(100);assert not dialog.count()
        finally:
            mode.update(telegram_fixture=False,telegram_error=False,telegram_wrong_project=False,telegram_wrong_source=False,telegram_hold=False,can_search=True)
            page.set_viewport_size({'width':1280,'height':900})
            reset_bulk()
    check('Telegram preview: local-only scope, safe content, photos, bounded batches, focus, failure/retry, empty and mobile RU/UK',telegram_preview)
    def telegram_manual_collections():
        try:
            for source,locale in [('copart','ru'),('iaa','uk')]:
                mode.update(source=source,locale=locale,collections_fixture=True,telegram_fixture=False,pairing_fixture=True,
                    pairing_session=None,pairing_recipient=None,can_search=False,telegram_check_hold=False)
                page.goto(BASE+f'?source={source}&view=project&project=9')
                page.get_by_role('heading',name='active car 9',exact=True).wait_for()
                assert not page.locator('.telegram-project').count()
                button=page.get_by_role('button',name=('Отправить в Telegram' if locale=='ru' else 'Надіслати в Telegram')+': active car 9',exact=True)
                assert button.inner_text()=='' and button.locator('svg').count()==1
                calls.clear();telegram_checks.clear();mode['telegram_check_hold']=True
                button.click();page.locator('.telegram-car-action [role=status]').wait_for()
                assert button.is_disabled() and button.get_attribute('aria-busy')=='true'
                assert len(held_telegram_checks)==1 and telegram_checks==[{'source_key':[source]}]
                mode['telegram_check_hold']=False
                held_telegram_checks.pop().fulfill(status=200,content_type='application/json',body=json.dumps(dict(available=True,recipient=None)))
                page.locator('.telegram-car-action [role=alert]').wait_for()
                assert button.is_enabled()
                assert not [c for c in calls if c[0]=='POST']
                tabs=page.locator('.auction-tabs')
                action=page.get_by_role('button',name='Telegram',exact=True)
                labels=[('future','Будущие' if locale=='ru' else 'Майбутні'),('undated','Без даты' if locale=='ru' else 'Без дати'),
                    ('archive','Архив' if locale=='ru' else 'Архів'),('active','Все сохранённые' if locale=='ru' else 'Усі збережені')]
                for collection,label in labels:
                    tabs.get_by_role('button',name=label,exact=True).click()
                    page.get_by_role('heading',name=f'{collection} car 9',exact=True).wait_for()
                    action.click();dialog=page.locator('dialog.auction-telegram-preview')
                    checkbox=dialog.locator('.telegram-select-car input');checkbox.wait_for()
                    assert dialog.locator('[data-preview-message]').count()==1
                    assert f'{collection} car 9' in dialog.locator('.telegram-select-car').inner_text()
                    send=dialog.get_by_role('button',name='Отправить выбранные' if locale=='ru' else 'Надіслати вибрані',exact=False)
                    assert send.is_disabled() and not checkbox.is_checked()
                    checkbox.focus();page.keyboard.press('Space');assert checkbox.is_checked() and '(1)' in send.inner_text()
                    dialog.get_by_role('button',name='Обновить предпросмотр' if locale=='ru' else 'Оновити попередній перегляд',exact=True).click()
                    checkbox.wait_for();assert not checkbox.is_checked() and send.is_disabled()
                    checkbox.check();page.keyboard.press('Escape');dialog.wait_for(state='detached')
                    action.click();checkbox.wait_for();assert not checkbox.is_checked()
                    if collection=='archive': assert 'последние известные' in dialog.inner_text() if locale=='ru' else 'останні відомі' in dialog.inner_text()
                    for width in [1280,360]:
                        page.set_viewport_size({'width':width,'height':900})
                        assert dialog.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                        if collection=='future': page.screenshot(path=str(report/f'telegram-manual-{source}-{width}.png'))
                    page.keyboard.press('Escape');dialog.wait_for(state='detached')
                page.get_by_role('button',name=('Отправить в Telegram' if locale=='ru' else 'Надіслати в Telegram')+': active car 9',exact=True).click()
                page.locator('.telegram-car-action [role=alert]').wait_for()
                page.locator('.telegram-car-action').get_by_role('button',name='Настройки' if locale=='ru' else 'Налаштування',exact=True).click()
                page.locator('.telegram-settings').wait_for()
        finally:
            mode.update(locale='ru',source='copart',collections_fixture=False,pairing_fixture=False,telegram_check_hold=False)
            while held_telegram_checks: held_telegram_checks.pop().fulfill(status=503,content_type='application/json',body='{}')
            page.set_viewport_size({'width':1280,'height':900})
    check('Telegram manual selection: current tab, reset, checkbox keyboard/mobile, single-car progress, no fake delivery, Settings',telegram_manual_collections)
    def telegram_delivery_queue():
        try:
            for source,locale,width in [('copart','ru',1280),('iaa','uk',360)]:
                delivery_batches.clear();delivery_intents.clear()
                mode.update(source=source,locale=locale,telegram_fixture=True,telegram_count=3,pairing_fixture=True,
                    pairing_session=None,pairing_recipient=dict(project_id=9,title='Team fixture',chat_id=-100555,chat_type='supergroup' if source=='copart' else 'channel',binding_id='b'*32),
                    delivery_enabled=True,delivery_state='QUEUED',delivery_sent=[40],can_search=False)
                page.set_viewport_size({'width':width,'height':1000})
                page.goto(BASE+f'?source={source}&view=project&project=9')
                page.locator('.auction-tabs').get_by_role('button',name='Сегодня' if locale=='ru' else 'Сьогодні',exact=True).click()
                first=page.locator('.auction-card').filter(has=page.get_by_role('heading',name='Preview car 1',exact=True))
                repeat=first.get_by_role('button',name=('Отправить ещё раз в Telegram' if locale=='ru' else 'Надіслати ще раз у Telegram')+': Preview car 1',exact=True)
                repeat.wait_for();assert first.locator('.telegram-sent-before').count()==1
                mode['delivery_post_error']='telegram_chat_pairing_disabled'
                repeat.click()
                first.get_by_role('alert').filter(has_text='Отправка ещё не подключена' if locale=='ru' else 'Надсилання ще не підключено').wait_for()
                assert not first.get_by_text('Постановка не подтверждена',exact=False).count()
                assert not first.get_by_text('Постановка не підтверджена',exact=False).count()
                mode['delivery_post_error']=None
                repeat.click()
                first.locator('[role=status]').filter(has_text='0 / 1').wait_for()
                assert repeat.is_disabled() and delivery_intents[-1]['car_ids']==[40]
                assert delivery_intents[-1]['collection']=='today' and delivery_intents[-1]['computer_timezone']=='Europe/Warsaw'
                first_id=delivery_intents[-1]['intent_id']
                assert delivery_batches[first_id]['recipient']['chat_type']==('supergroup' if source=='copart' else 'channel')
                first.get_by_text(('Получатель' if locale=='ru' else 'Одержувач')+': Team fixture',exact=True).wait_for()
                mode['delivery_state']='IN_FLIGHT'
                first.locator('[role=status]').filter(has_text='1').filter(has_text='Отправляется: 1' if locale=='ru' else 'Надсилається: 1').wait_for()
                mode['delivery_state']='SENT'
                first.locator('[role=status]').filter(has_text='1 / 1').wait_for();page.wait_for_timeout(50)
                assert repeat.is_enabled()
                mode['delivery_state']='QUEUED';repeat.click();first.locator('[role=status]').filter(has_text='0 / 1').wait_for()
                assert delivery_intents[-1]['intent_id']!=first_id
                mode['delivery_state']='SENT';first.locator('[role=status]').filter(has_text='1 / 1').wait_for()
                action=page.get_by_role('button',name='Telegram',exact=True);action.click()
                dialog=page.locator('dialog.auction-telegram-preview');dialog.locator('.telegram-select-car input').first.wait_for()
                assert dialog.locator('.telegram-sent-before').count()==1
                dialog.locator('.telegram-select-car input').nth(0).check();dialog.locator('.telegram-select-car input').nth(1).check()
                mode['delivery_state']='QUEUED'
                dialog.get_by_role('button',name='Отправить выбранные' if locale=='ru' else 'Надіслати вибрані',exact=False).click()
                dialog.locator('[role=status]').filter(has_text='0 / 2').wait_for()
                assert delivery_intents[-1]['car_ids']==[40,41],delivery_intents[-1]
                group_id=delivery_intents[-1]['intent_id'];assert delivery_batches[group_id]['jobs'][0]['previously_sent']
                count=len(delivery_intents)
                page.keyboard.press('Escape');dialog.wait_for(state='detached');action.click()
                dialog.locator('[role=status]').filter(has_text='0 / 2').wait_for()
                assert len(delivery_intents)==count,'Reopening progress submitted a new send'
                dialog.get_by_role('button',name='Отменить оставшиеся' if locale=='ru' else 'Скасувати решту',exact=True).click()
                dialog.get_by_text('Отменено или условия изменились: 2' if locale=='ru' else 'Скасовано або умови змінилися: 2',exact=True).wait_for()
                dialog.locator('.telegram-select-car input').nth(2).check()
                mode['delivery_lose_next']=True
                dialog.get_by_role('button',name='Отправить выбранные' if locale=='ru' else 'Надіслати вибрані',exact=False).click()
                dialog.get_by_role('alert').wait_for();lost_group=delivery_intents[-1]['intent_id'];count=len(delivery_batches)
                page.keyboard.press('Escape');dialog.wait_for(state='detached');action.click()
                retry_group=dialog.get_by_role('button',name='Повторить запрос' if locale=='ru' else 'Повторити запит',exact=False)
                retry_group.wait_for();assert '(1)' in retry_group.inner_text()
                assert dialog.locator('.telegram-select-car input').first.is_disabled()
                retry_group.click();dialog.locator('[role=status]').filter(has_text='0 / 1').wait_for()
                assert delivery_intents[-1]['intent_id']==lost_group and delivery_intents[-1]['car_ids']==[42]
                assert len(delivery_batches)==count
                dialog.get_by_role('button',name='Отменить оставшиеся' if locale=='ru' else 'Скасувати решту',exact=True).click()
                dialog.get_by_text('Отменено или условия изменились: 1' if locale=='ru' else 'Скасовано або умови змінилися: 1',exact=True).wait_for()
                page.keyboard.press('Escape');dialog.wait_for(state='detached')
                # Lost POST reply: repeat the very same intent, including after a card remount.
                second=page.locator('.auction-card').filter(has=page.get_by_role('heading',name='Preview car 2',exact=True))
                single=second.get_by_role('button',name=('Отправить в Telegram' if locale=='ru' else 'Надіслати в Telegram')+': Preview car 2',exact=True)
                mode['delivery_lose_next']=True;single.click()
                second.get_by_role('alert').wait_for();lost_id=delivery_intents[-1]['intent_id'];count=len(delivery_batches)
                single.click();second.locator('[role=status]').filter(has_text='0 / 1').wait_for()
                assert delivery_intents[-1]['intent_id']==lost_id and len(delivery_batches)==count
                mode['delivery_state']='UNKNOWN'
                consent=second.get_by_role('checkbox');consent.wait_for();assert single.is_disabled()
                count=len(delivery_intents);page.wait_for_timeout(1200);assert len(delivery_intents)==count
                consent.check();assert single.is_enabled()
                mode['delivery_state']='QUEUED';single.click();second.locator('[role=status]').filter(has_text='0 / 1').wait_for()
                assert delivery_intents[-1]['intent_id']!=lost_id
                second.get_by_role('button',name='Отменить оставшиеся' if locale=='ru' else 'Скасувати решту',exact=True).click()
                second.get_by_text('Отменено или условия изменились: 1' if locale=='ru' else 'Скасовано або умови змінилися: 1',exact=True).wait_for()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                first.screenshot(path=str(report/f'telegram-queue-car-{source}-{width}.png'))
                action.click();dialog.locator('[data-preview-message]').first.wait_for()
                assert dialog.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                page.screenshot(path=str(report/f'telegram-queue-group-{source}-{width}.png'))
                page.keyboard.press('Escape');dialog.wait_for(state='detached')
        finally:
            mode.update(locale='ru',source='copart',telegram_fixture=False,pairing_fixture=False,pairing_recipient=None,
                delivery_enabled=False,delivery_lose_next=False,delivery_sent=[],delivery_post_error=None)
            delivery_batches.clear();page.set_viewport_size({'width':1280,'height':900})
    check('Telegram delivery queue: single/group progress, explicit resend, past-send badge, cancel, lost POST idempotency and UNKNOWN consent RU/UK',telegram_delivery_queue)
    def calendar_actions():
        from datetime import datetime,timezone
        isolated=browser.new_context(viewport={'width':1280,'height':900},locale='ru-RU',timezone_id='Europe/Warsaw')
        test_page=isolated.new_page()
        duplicate_keys=[]
        test_page.on('console',lambda message:duplicate_keys.append(message.text) if 'Encountered two children with the same key' in message.text else None)
        test_page.route('**/api/**',api)
        test_page.route('**/fixture-photo.svg',lambda route:route.fulfill(status=200,content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><rect fill="#dbeafe" width="800" height="500"/></svg>'))
        test_page.add_init_script("Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>{if(window.fixtureCopyFail)throw new Error('denied');window.fixtureCalendarCopied=value}}})")
        test_page.clock.install(time=datetime(2026,10,16,10,0,tzinfo=timezone.utc))
        try:
            for source,locale in [('copart','ru'),('iaa','uk')]:
                mode.update(source=source,locale=locale,can_search=False,telegram_fixture=True,telegram_count=1,runs=[],bulk_projects=None,
                    progress_projects=None,telegram_error=False,telegram_wrong_source=False,telegram_wrong_project=False)
                test_page.clock.set_system_time(datetime(2026,10,16,10,0,tzinfo=timezone.utc))
                test_page.goto(BASE+f'?source={source}&view=project&project=9')
                test_page.locator('.auction-tabs').get_by_role('button',name='Сегодня' if locale=='ru' else 'Сьогодні',exact=True).click()
                test_page.get_by_role('button',name='Telegram',exact=True).click()
                dialog=test_page.locator('dialog.auction-telegram-preview')
                link=dialog.locator('.auction-calendar-buttons').get_by_role('link',name='Добавить в календарь' if locale=='ru' else 'Додати до календаря',exact=True)
                link.wait_for()
                in_message=dialog.locator('.telegram-preview-text').get_by_role('link',name='Добавить в календарь' if locale=='ru' else 'Додати до календаря',exact=True)
                in_message.wait_for()
                url=link.get_attribute('href');event=json.loads(unquote(urlsplit(url).fragment))
                assert in_message.get_attribute('href')==url
                caption=dialog.locator('.telegram-preview-text')
                assert caption.locator('strong').first.inner_text()=='Preview car 1'
                assert ('Аукцион:' if locale=='ru' else 'Аукціон:') in caption.locator('strong').all_inner_texts()
                listing=caption.get_by_role('link',name=('Открыть автомобиль на ' if locale=='ru' else 'Відкрити автомобіль на ')+('Copart' if source=='copart' else 'IAA'),exact=True)
                assert listing.get_attribute('href')==event['url']
                assert 'https://' not in caption.inner_text()
                assert urlsplit(url).netloc=='searchcar-auction-calendar-preview.pages.dev'
                assert not urlsplit(url).query and len(url)<=2048
                assert event['start']=='2026-10-16T13:30:00.000Z' and event['locale']==locale and event['source']==source
                assert 'Preview car 1' in event['title']
                assert ('copart.com/lot/' if source=='copart' else 'iaai.com/VehicleDetail/') in event['url']
                assert 'localhost' not in url and '127.0.0.1' not in url and 'token=' not in url
                calls.clear();listing.focus();test_page.keyboard.press('Enter');test_page.wait_for_timeout(50)
                assert [json.loads(c[2]) for c in calls if c[0]=='POST' and c[1]=='/desktop/open-external']==[{'url':event['url']}]
                assert dialog.is_visible()
                calls.clear();link.focus();test_page.keyboard.press('Enter')
                test_page.wait_for_timeout(50)
                opened=[json.loads(c[2]) for c in calls if c[0]=='POST' and c[1]=='/desktop/open-external']
                assert opened==[{'url':url}],opened
                assert dialog.is_visible(),'Calendar opening must not replace the app webview'
                calls.clear();in_message.focus();test_page.keyboard.press('Enter');test_page.wait_for_timeout(50)
                assert [json.loads(c[2]) for c in calls if c[0]=='POST' and c[1]=='/desktop/open-external']==[{'url':url}]
                mode['calendar_open_error']=True;in_message.click()
                dialog.locator('.telegram-preview-text').get_by_role('alert').wait_for()
                mode['calendar_open_error']=False;in_message.click()
                dialog.locator('.telegram-preview-text').get_by_role('alert').wait_for(state='detached')
                mode['calendar_open_error']=True;link.click()
                dialog.locator('.auction-calendar-actions').get_by_role('alert').wait_for()
                mode['calendar_open_error']=False;link.click()
                dialog.locator('.auction-calendar-actions').get_by_role('alert').wait_for(state='detached')
                # Direct Google is an explicit fallback, never an in-webview navigation.
                direct=dialog.get_by_role('link',name='Google Calendar напрямую' if locale=='ru' else 'Google Calendar напряму',exact=True)
                google=direct.get_attribute('href');query=parse_qs(urlsplit(google).query)
                assert urlsplit(google).netloc=='calendar.google.com'
                assert query['dates']==['20261016T133000Z/20261016T133000Z']
                calls.clear();direct.click();test_page.wait_for_timeout(50)
                assert [json.loads(c[2]) for c in calls if c[0]=='POST' and c[1]=='/desktop/open-external']==[{'url':google}]
                copy=dialog.get_by_role('button',name='Скопировать ссылку' if locale=='ru' else 'Скопіювати посилання',exact=True)
                copy.click();test_page.wait_for_function('window.fixtureCalendarCopied!==undefined')
                assert test_page.evaluate('window.fixtureCalendarCopied')==url
                test_page.evaluate('window.fixtureCopyFail=true');copy.click()
                field=dialog.locator('.auction-calendar-actions input')
                field.wait_for();assert field.input_value()==url
                with test_page.expect_download() as downloaded:
                    dialog.get_by_role('link',name='Календарный файл (.ics)' if locale=='ru' else 'Файл календаря (.ics)',exact=True).click()
                exported=downloaded.value
                assert exported.suggested_filename==f'SearchCar-{source}-65507986.ics'
                content=Path(exported.path()).read_text(encoding='utf-8').replace('\n ','')
                assert 'BEGIN:VCALENDAR' in content and 'TRIGGER:-PT15M' in content
                assert 'DTSTART:20261016T133000Z' in content and 'Preview car 1' in content
                assert 'token=' not in content and 'DTEND:' not in content
                for width in [1280,800,360]:
                    test_page.set_viewport_size({'width':width,'height':900})
                    assert dialog.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                    assert link.is_visible() and copy.is_visible()
                    assert in_message.is_visible()
                    link.scroll_into_view_if_needed()
                    test_page.screenshot(path=str(report/f'{source}-calendar-actions-{width}.png'))
                # Advancing the real UI clock hides actions from an already open preview.
                test_page.clock.set_system_time(datetime(2026,10,17,10,0,tzinfo=timezone.utc))
                test_page.clock.run_for(1100)
                assert not link.count()
                assert not in_message.count()
                assert not [c for c in calls if c[0]=='POST' and c[1].startswith('/scans')]
            assert not duplicate_keys,duplicate_keys
        except Exception:
            test_page.screenshot(path=str(report/'calendar-actions-failure.png'))
            raise
        finally:
            isolated.close();mode.update(telegram_fixture=False,telegram_count=45,calendar_open_error=False,can_search=True);reset_bulk()
    check('calendar message actions: public prefill, scoped external open, clipboard fallback, ICS download, expiry and RU/UK mobile',calendar_actions)
    def bulk_failures():
        mode.update(source='copart',locale='ru',can_search=True,bulk_projects=[bulk_project(9),bulk_project(11,'iaa')])
        try:
            page.goto(BASE+'?source=copart')
            button=page.get_by_role('button',name='Обновить проекты',exact=True);button.wait_for();calls.clear()
            mode['availability_error']=True
            button.click()
            page.get_by_role('alert').filter(has_text='Обновление не запускалось').wait_for()
            assert not queue_posts()
            mode.update(availability_error=False,fail_launch=True)
            button.click()
            page.get_by_role('alert').filter(has_text='Не удалось подтвердить добавление').wait_for()
            assert queue_posts()==[{'project_ids':[9]}]
            # Queue authority can report a race: an eligible project became active after preview.
            mode.update(fail_launch=False,bulk_race=True)
            button.click()
            result=page.get_by_role('region',name='Результат обновления проектов',exact=True);result.wait_for()
            assert 'Добавлено в очередь: 0 · Уже активны: 1 · Недоступно: 0' in result.inner_text()
            assert not page.locator('.auction-project-toolbar + [role="alert"]').count()
            mode['bulk_sources']=[dict(source_key='copart',family='AUCTION',can_search=False,reason='LICENSE_SOURCE_NOT_ALLOWED')]
            before=len(queue_posts());button.click()
            page.wait_for_function("document.querySelector('.auction-refresh-result')?.textContent.includes('Недоступно: 1')")
            assert len(queue_posts())==before
            assert 'Сайт недоступен по лицензии' in result.inner_text()
            mode['bulk_sources']=None
            mode['bulk_projects'][0].update(active_status='QUEUED',scan_run_id=101)
            button.click()
            page.wait_for_function("document.querySelector('.auction-refresh-result')?.textContent.includes('Уже активны: 1')")
            assert len(queue_posts())==before
            mode['bulk_projects']=[]
            page.reload()
            button.wait_for();assert button.is_disabled()
            assert len(queue_posts())==before
        finally: reset_bulk()
    check('site bulk failures: availability, queue, race, restricted site, all active and empty list',bulk_failures)
    def bulk_iaa_uk():
        mode.update(source='iaa',locale='uk',can_search=True,bulk_projects=[bulk_project(9),bulk_project(11,'iaa')])
        try:
            page.goto(BASE+'?source=iaa');calls.clear()
            button=page.get_by_role('button',name='Оновити проєкти',exact=True)
            button.focus();page.keyboard.press('Enter')
            result=page.get_by_role('region',name='Результат оновлення проєктів',exact=True);result.wait_for()
            assert queue_posts()==[{'project_ids':[11]}]
            assert 'Додано до черги: 1 · Уже активні: 0 · Недоступно: 0' in result.inner_text()
            assert 'copart project' not in result.inner_text()
            page.get_by_role('button',name='Сьогодні',exact=True).click()
            for width in [1280,360]:
                page.set_viewport_size({'width':width,'height':900})
                assert page.locator('main').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                page.screenshot(path=str(report/f'bulk-refresh-iaa-uk-{width}.png'),full_page=True)
        finally: reset_bulk()
    check('IAA bulk update is keyboard-operable, Ukrainian, responsive and never includes Copart',bulk_iaa_uk)
    def project_header_layout():
        try:
            for source,locale,refresh_label,today_label in [('copart','ru','Обновить данные','Сегодня'),('iaa','uk','Оновити дані','Сьогодні')]:
                mode.update(source=source,locale=locale,can_search=True,bulk_projects=[bulk_project(9,source,name=project['name'])],
                    runs=[dict(id=101,kind='PROJECTS',status='FAILED',progress=100,created_at='2026-10-19T10:00:00Z',
                        payload=dict(project_ids=[9],project_plans=[dict(project_id=9,source_key=source,name=project['name'])],project_statuses={'9':'FAILED'}))])
                page.goto(BASE+f'?source={source}&view=project&project=9')
                status=page.locator('.auction-heading-status [data-project-id="9"][data-status="FAILED"]')
                status.wait_for()
                toolbar=page.locator('.auction-project-toolbar')
                refresh=toolbar.get_by_role('button',name=refresh_label,exact=True)
                assert refresh.count()==1
                for width in [1280,800,360]:
                    page.set_viewport_size({'width':width,'height':900})
                    assert page.locator('main').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                    assert toolbar.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                    line=page.locator('.auction-heading-line').bounding_box()
                    badge=page.locator('.auction-heading-status').bounding_box()
                    assert abs(line['x']+line['width']-badge['x']-badge['width'])<2
                    box=toolbar.bounding_box();button=toolbar.locator('.auction-project-controls').bounding_box()
                    assert abs(box['x']+box['width']-button['x']-button['width'])<2
                    if width==1280:
                        title=page.locator('h1').bounding_box()
                        assert abs(title['y']-badge['y'])<2
                        tabs=toolbar.locator('.auction-tabs').bounding_box();button=refresh.bounding_box()
                        assert abs(tabs['y']-button['y'])<2
                    page.screenshot(path=str(report/f'{source}-project-header-{width}.png'),full_page=True)
                toolbar.get_by_role('button',name=today_label,exact=True).click()
                mode['runs'][0].update(status='SUCCEEDED')
                mode['runs'][0]['payload']['project_statuses']={'9':'SUCCEEDED'}
                page.reload()
                refresh.wait_for()
                page.wait_for_function("!document.querySelector('.auction-heading-status [data-status]')")
                assert refresh.is_enabled()
                calls.clear();refresh.click()
                page.wait_for_function("document.querySelector('.auction-heading-status [data-status=QUEUED]')!==null")
                assert queue_posts()==[{'project_ids':[9]}]
                assert toolbar.locator('.auction-project-refresh').is_disabled(), 'Moved button lost active-project gating'
                assert toolbar.locator('.auction-project-edit-button').is_disabled()
                assert toolbar.get_by_role('button',name='Telegram',exact=True).is_enabled()
        finally: reset_bulk()
    check('project header: right-aligned status and tabs refresh preserve scope, busy gating, both locales and narrow layout',project_header_layout)
    def project_panel_controls():
        try:
            page.set_viewport_size({'width':1280,'height':900})
            # View preferences belong to their own auction, not Encar or another auction.
            page.goto(BASE+'?source=copart')
            page.evaluate("() => {localStorage.setItem('encar-projects-view','grid');localStorage.setItem('encar-project-list-sort','name');for(const source of ['copart','iaa']){localStorage.removeItem(source+'-auction-projects-view');localStorage.removeItem(source+'-auction-projects-sort')}}")
            for source,locale,grid_label,sort_label,collapse_label,expand_label,today_label in [
                ('copart','ru','Плитка','Сортировка проектов','Свернуть проекты','Развернуть проекты','Сегодня'),
                ('iaa','uk','Плитка','Сортування проєктів','Згорнути проєкти','Розгорнути проєкти','Сьогодні')]:
                mode.update(source=source,locale=locale,can_search=True,bulk_projects=[
                    bulk_project(9,source,name='BMW 10',cars=2,last_refreshed_at=None,search_page_mode='FIRST_PAGE'),
                    bulk_project(10,source,name=project['name'],cars=5,last_refreshed_at='2026-10-16T10:00:00Z'),
                    bulk_project(12,source,name='BMW 2',cars=0,last_refreshed_at='2026-10-18T10:00:00Z'),
                    bulk_project(11,'iaa' if source=='copart' else 'copart',name='Foreign project',cars=99)])
                mode['runs']=[dict(id=101,kind='PROJECTS',status='RUNNING',progress=50,created_at='2026-10-19T10:00:00Z',
                    payload=dict(project_ids=[9,10,12],project_plans=[dict(project_id=p['id'],source_key=source,name=p['name']) for p in mode['bulk_projects'] if p['source_key']==source],
                        project_statuses={'9':'SUCCEEDED','10':'RUNNING','12':'QUEUED'}))]
                page.goto(BASE+f'?source={source}');calls.clear()
                panel=page.locator('.auction-projects-panel')
                panel.locator('.auction-project').first.wait_for()
                assert panel.locator('.project-list').count()==1,'Another site leaked its tile preference'
                assert ('Проекты: 3 · Авто: 7' if locale=='ru' else 'Проєкти: 3 · Авто: 7') in panel.inner_text()
                def names(): return panel.locator('.auction-project-name').all_text_contents()
                assert names()==['BMW 2',project['name'],'BMW 10'],names()
                assert 'Foreign project' not in panel.inner_text()
                assert not panel.locator('time[datetime="2026-10-10T10:00:00Z"]').count()
                assert not panel.locator('[title*="ссылк"],[aria-label*="ссылк"]').count()
                sorter=panel.get_by_label(sort_label)
                sorter.select_option('name')
                page.wait_for_function("() => document.querySelectorAll('.auction-project-name')[1]?.textContent==='BMW 2'")
                assert names()==[project['name'],'BMW 2','BMW 10']
                sorter.select_option('updated')
                # Unknown last refresh stays after real dates, independently of created/updated metadata.
                page.wait_for_function("() => document.querySelector('.auction-project-name')?.textContent==='BMW 2'")
                assert names()==['BMW 2',project['name'],'BMW 10']
                tile=panel.get_by_role('button',name=grid_label,exact=True)
                tile.focus();page.keyboard.press('Enter')
                panel.locator('.project-grid').wait_for()
                assert tile.get_attribute('aria-pressed')=='true'
                for layout_name in ['grid','list']:
                    if layout_name=='list': panel.get_by_role('button',name='Список',exact=True).click()
                    for width in [1280,800,360]:
                        page.set_viewport_size({'width':width,'height':900})
                        assert panel.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                        assert page.locator('main').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                        for row in panel.locator('.auction-project').all():
                            assert row.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                            assert row.locator('.auction-project-date').is_visible()
                            has_state=bool(row.locator('.auction-project-state').inner_text().strip())
                            assert has_state==(row.locator('.auction-project-name').inner_text()!='BMW 10')
                            if has_state: assert row.locator('.auction-project-state').is_visible()
                            else: assert not row.locator('.project-update').count()
                            assert row.locator('.auction-project-refresh').is_visible()
                            if layout_name=='grid':
                                badges=row.locator('.project-search-badges').bounding_box()
                                state=row.locator('.auction-project-state').bounding_box()
                                date=row.locator('.auction-project-date').bounding_box()
                                actions=row.locator('.project-actions').bounding_box()
                                if has_state: assert abs((badges['y']+badges['height']/2)-(state['y']+state['height']/2))<2
                                assert date['y']>max(badges['y']+badges['height'],state['y']+state['height'])
                                assert abs((date['y']+date['height']/2)-(actions['y']+actions['height']/2))<2
                                assert row.evaluate("e=>getComputedStyle(e,'::after').display")=='block'
                        page.screenshot(path=str(report/f'{source}-panel-{layout_name}-{width}.png'),full_page=True)
                panel.get_by_role('button',name=grid_label,exact=True).click()
                sorter.select_option('name')
                page.reload();panel.locator('.project-grid').wait_for()
                assert panel.get_by_label(sort_label).input_value()=='name'
                toggle=panel.get_by_role('button',name=collapse_label,exact=True)
                toggle.click()
                assert panel.get_by_role('button',name=expand_label,exact=True).get_attribute('aria-expanded')=='false'
                assert panel.locator('.projects-collapsible-content').get_attribute('inert') is not None
                page.keyboard.press('Tab')
                assert not panel.locator('.projects-collapsible-content').evaluate('(e)=>e.contains(document.activeElement)')
                assert panel.get_by_role('button',name='Обновить проекты' if locale=='ru' else 'Оновити проєкти',exact=True).is_visible()
                panel.get_by_role('button',name=expand_label,exact=True).click()
                page.locator('.auction-tabs').get_by_role('button',name=today_label,exact=True).click()
                assert not panel.get_by_role('button',name=grid_label,exact=True).count()
                page.locator('.auction-tabs').get_by_role('button',name='Проекты' if locale=='ru' else 'Проєкти',exact=True).click()
                assert panel.locator('.project-grid').count()==1
                assert not queue_posts(),'Layout/sorting/collapse must never start a search'
                # The compact row action keeps exact project scope.
                calls.clear()
                panel.locator('.auction-project').filter(has=page.get_by_role('button',name='BMW 10',exact=True)).locator('.auction-project-refresh').click()
                page.wait_for_timeout(250)
                assert queue_posts()==[{'project_ids':[9]}]
                panel.get_by_role('button',name=project['name'],exact=True).click()
                page.wait_for_function("new URL(window.location.href).searchParams.get('project')==='10'")
                assert page.locator('h1').inner_text()==project['name']
                assert page.evaluate("() => localStorage.getItem('encar-projects-view')")=='grid'
                assert page.evaluate("() => localStorage.getItem('encar-project-list-sort')")=='name'
        finally:
            reset_bulk()
            page.evaluate("() => {for(const source of ['copart','iaa']){localStorage.removeItem(source+'-auction-projects-view');localStorage.removeItem(source+'-auction-projects-sort')}}")
    check('auction panel: list/tile/sort/collapse, per-site preferences, row actions, actual dates, status and mobile visibility',project_panel_controls)
    def delete_project():
        try:
            for source,locale in [('copart','ru'),('iaa','uk')]:
                deleted_projects.clear();calls.clear()
                mode.update(source=source,locale=locale,can_search=False,bulk_projects=None,progress_projects=None,runs=[],removal_projects=True,project_delete_error=False)
                if source=='iaa':
                    mode['runs']=[dict(id=101,kind='PROJECTS',status='RUNNING',progress=20,created_at='2026-10-19T10:00:00Z',payload=dict(project_ids=[9,10],current_project_id=9))]
                page.set_viewport_size({'width':1280,'height':900})
                page.goto(BASE+'?source='+source)
                panel=page.locator('.auction-projects-panel')
                delete_label=('Удалить проект: ' if locale=='ru' else 'Видалити проєкт: ')+project['name']
                action=panel.get_by_role('button',name=delete_label,exact=True)
                action.wait_for()
                assert action.is_enabled(),'Deleting saved data must not require search entitlement'
                count=panel.locator('.auction-project').first.locator('.count')
                assert count.evaluate('(e)=>parseFloat(getComputedStyle(e).paddingLeft)>=20'), 'Car count needs breathing room after divider'
                action.click()
                dialog=page.get_by_role('dialog')
                assert project['name'] in dialog.inner_text()
                if source=='iaa': assert 'Пов’язаний запуск оновлення може бути зупинено' in dialog.inner_text()
                page.screenshot(path=str(report/f'{source}-delete-project-confirm.png'),full_page=True)
                assert not [c for c in calls if c[0]=='DELETE']
                page.keyboard.press('Escape')
                dialog.wait_for(state='detached')
                assert action.evaluate('(e)=>e===document.activeElement')
                action.click()
                cancel='Отмена' if locale=='ru' else 'Скасувати'
                confirm='Удалить навсегда' if locale=='ru' else 'Видалити назавжди'
                dialog.get_by_role('button',name=cancel,exact=True).click()
                assert not [c for c in calls if c[0]=='DELETE']
                action.click();mode['project_delete_error']=True
                dialog.get_by_role('button',name=confirm,exact=True).click()
                dialog.get_by_role('alert').wait_for()
                assert panel.locator('.auction-project').count()==2
                mode.update(project_delete_error=False,hold_project_delete=True)
                dialog.get_by_role('button',name=confirm,exact=True).click()
                page.wait_for_timeout(200)
                assert len(held_project_deletes)==1
                assert dialog.get_by_role('button',name=cancel,exact=True).is_disabled()
                assert dialog.get_by_role('button',name=confirm,exact=True).is_disabled()
                page.keyboard.press('Escape');assert dialog.is_visible()
                assert panel.locator('.auction-project').count()==2
                deleted_projects.add(9);mode['hold_project_delete']=False
                held_project_deletes.pop().fulfill(status=204,body='')
                dialog.wait_for(state='detached')
                page.wait_for_function("document.querySelectorAll('.auction-project').length===1")
                assert panel.get_by_role('button',name='Другой проект',exact=True).is_visible()
                assert [c[1] for c in calls if c[0]=='DELETE']==['/projects/9','/projects/9']
                page.reload()
                panel.get_by_role('button',name='Другой проект',exact=True).wait_for()
                assert panel.locator('.auction-project').count()==1
                for width in [1280,360]:
                    page.set_viewport_size({'width':width,'height':900})
                    assert panel.locator('.auction-project-delete').is_visible()
                    assert panel.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                    page.screenshot(path=str(report/f'{source}-project-delete-{width}.png'),full_page=True)
                panel.locator('.auction-project-delete').click()
                dialog.get_by_role('button',name=confirm,exact=True).click()
                page.get_by_text('Сохранённых проектов нет' if locale=='ru' else 'Збережених проєктів немає',exact=True).wait_for()
                assert panel.locator('.auction-project').count()==0
                assert not [c for c in calls if c[0]=='POST' and c[1]=='/scans/projects']
        finally:
            deleted_projects.clear()
            mode.update(can_search=True,removal_projects=False,project_delete_error=False,hold_project_delete=False)
    check('delete project: exact scope, no search entitlement, cancel/Escape/focus, failure, pending, success, reload and mobile',delete_project)
    def edit_filters():
        for source,locale in [('copart','ru'),('iaa','uk')]:
            mode.update(source=source,locale=locale,editing_fixture=True,edit_project=None,selection_status='CLOSED',
                selection_issue=None,empty_selection=False,can_search=True,iaa_native_ready=True,scoped_drafts=False,
                runs=[],bulk_projects=None,progress_projects=None,edit_error=False,hold_edit=False,fail_launch=False)
            page.set_viewport_size({'width':360,'height':900})
            page.goto(BASE+'?source='+source)
            action=page.locator('.auction-project-edit-button').first
            action.click()
            dialog=page.locator('dialog.auction-project-edit')
            dialog.get_by_text('Make: Audi',exact=True).wait_for()
            save=dialog.locator('.launch-actions .primary')
            assert save.is_disabled()
            page.keyboard.press('Escape');dialog.wait_for(state='detached')
            assert action.evaluate('(e)=>e===document.activeElement')
            action.click();dialog.get_by_text('Make: Audi',exact=True).wait_for()
            mode['hold_selection']=True
            dialog.get_by_role('button',name='Изменить фильтры' if locale=='ru' else 'Змінити фільтри',exact=True).click()
            cancel=dialog.get_by_role('button',name='Отмена' if locale=='ru' else 'Скасувати',exact=True)
            page.wait_for_function("document.querySelector('dialog.auction-project-edit .launch-actions button').disabled")
            assert cancel.is_disabled(), 'Do not orphan an OPEN request by closing its form'
            page.keyboard.press('Escape');assert dialog.is_visible()
            mode.update(hold_selection=False,selection_status='READY')
            held_selection.pop().fulfill(status=200,content_type='application/json',body=json.dumps({'selection':selection()}))
            page.wait_for_function("!document.querySelector('dialog.auction-project-edit .launch-actions button').disabled")
            cancel.click();dialog.wait_for(state='detached')
            assert any(c[0]=='POST' and c[1].startswith('/source-search/') and json.loads(c[2])['action']=='CANCEL' for c in calls)
            mode.update(edit_project=None,selection_status='CLOSED')
            action.click();dialog.get_by_text('Make: Audi',exact=True).wait_for()
            calls.clear()
            dialog.get_by_role('button',name='Изменить фильтры' if locale=='ru' else 'Змінити фільтри',exact=True).click()
            confirm=dialog.get_by_role('button',name='Использовать выбранные фильтры' if locale=='ru' else 'Використати вибрані фільтри',exact=True)
            confirm.click();save.wait_for(state='visible')
            page.wait_for_function("!document.querySelector('dialog.auction-project-edit .launch-actions .primary').disabled")
            opens=[json.loads(c[2]) for c in calls if c[0]=='POST' and c[1].startswith('/source-search/')]
            assert opens[0]['project_id']==9 and len(opens[0]['draft_id'])==32
            mode['edit_error']=True;save.click()
            dialog.get_by_role('alert').wait_for()
            assert not [c for c in calls if c[0]=='POST' and c[1]=='/scans/projects']
            mode.update(edit_error=False,hold_edit=True)
            save.click();page.wait_for_timeout(100)
            assert len(held_edits)==1 and save.is_disabled()
            page.keyboard.press('Escape');assert dialog.is_visible()
            assert dialog.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
            page.screenshot(path=str(report/f'{source}-edit-filters-mobile.png'),full_page=True)
            held_edits.pop().fulfill(status=200,content_type='application/json',body=json.dumps({**project,'search_revision':2}))
            mode['hold_edit']=False
            dialog.wait_for(state='detached');page.wait_for_timeout(250)
            saves=[json.loads(c[2]) for c in calls if c[0]=='POST' and c[1]=='/projects/9/search-selection']
            assert len(saves)==2 and saves[-1]['expected_revision']==1
            queued=[json.loads(c[2]) for c in calls if c[0]=='POST' and c[1]=='/scans/projects']
            assert queued==[{'project_ids':[9]}]
            # A saved edit with failed enqueue stays saved and offers an explicit retry.
            mode.update(edit_project=None,selection_status='CLOSED',fail_launch=True)
            action.click();dialog.get_by_text('Make: Audi',exact=True).wait_for()
            dialog.get_by_role('button',name='Изменить фильтры' if locale=='ru' else 'Змінити фільтри',exact=True).click()
            confirm.click()
            page.wait_for_function("!document.querySelector('dialog.auction-project-edit .launch-actions .primary').disabled")
            save.click();dialog.wait_for(state='detached')
            page.get_by_text('Фильтры сохранены, но запуск поиска не подтверждён.',exact=False).wait_for() if locale=='ru' else page.get_by_text('Фільтри збережено, але запуск пошуку не підтверджений.',exact=False).wait_for()
        mode.update(editing_fixture=False,edit_project=None,fail_launch=False,can_search=True)
    check('edit project filters: scoped draft, cancel/focus, revision conflict, pending/mobile, one enqueue and saved-but-queue-failed',edit_filters)
    def encar_ui_regressions():
        sample_project={**project,'source_key':'encar','search_url':'https://car.encar.com/list/car?page=1',
            'scan_mode':'ACCURATE','price_filter_mode':'LINK','auto_update':True}
        sample_car=dict(id=41,source_key='encar',project_id=9,encar_id='42112995',
            url='https://fem.encar.com/cars/detail/42112995',title='Hyundai Palisade 2.2 Diesel AWD Prestige 2023',
            price=38900000,status='NEW',details={},favorite=True,viewed=False,missing_scans=0,image='/fixture-photo.svg')
        pending_saves=[]
        submissions=[]
        save_state={'hold':False}
        def encar_api(route):
            path=urlsplit(route.request.url).path.removeprefix('/api')
            if path=='/projects/9' and route.request.method=='PATCH':
                submissions.append(json.loads(route.request.post_data))
                if save_state['hold']:
                    pending_saves.append(route)
                    return
            if path=='/projects': value=[sample_project]
            elif path=='/projects/9': value=sample_project
            elif path=='/projects/9/cars': value=[sample_car]
            elif path=='/favorites': value=[sample_car]
            else: return api(route)
            route.fulfill(status=200,content_type='application/json',body=json.dumps(value))
        page.route('**/api/**',encar_api)
        try:
            for locale in ['ru','uk']:
                ru=locale=='ru'
                mode.update(source='encar',locale=locale,runs=[],bulk_projects=None,progress_projects=None,
                    bulk_sources=[dict(source_key='encar',family='MARKETPLACE',can_search=True,reason=None)])
                for width in [980,1280]:
                    page.set_viewport_size({'width':width,'height':680 if width==980 else 820})
                    for edit in [False,True]:
                        page.goto(BASE+('?source=encar&view=project&project=9' if edit else '?source=encar&view=projects'))
                        trigger=page.get_by_role('button',name=('Редактировать' if ru else 'Редагувати') if edit
                            else ('＋ Создать проект' if ru else '＋ Створити проєкт'),exact=True)
                        trigger.click()
                        dialog=page.get_by_role('dialog')
                        assert dialog.count()==1, 'Encar form must be an accessible modal dialog'
                        assert dialog.evaluate('(e)=>e.contains(document.activeElement)'), 'Focus must enter the form'
                        # Include the dynamically revealed price fields in the focus cycle.
                        dialog.locator('select').first.select_option('CUSTOM')
                        for _ in range(22):
                            page.keyboard.press('Tab')
                            assert dialog.evaluate('(e)=>e.contains(document.activeElement)'), 'Tab escaped to the page'
                        for _ in range(22):
                            page.keyboard.press('Shift+Tab')
                            assert dialog.evaluate('(e)=>e.contains(document.activeElement)'), 'Shift+Tab escaped to the page'
                        page.screenshot(path=str(report/f'encar-dialog-{locale}-{width}-{edit}.png'))
                        page.keyboard.press('Escape')
                        dialog.wait_for(state='detached')
                        assert trigger.evaluate('(e)=>e===document.activeElement'), 'Focus must return to the opener'
                        trigger.click();page.get_by_role('dialog').locator('.modal-actions').get_by_role('button',name='Отмена' if ru else 'Скасувати',exact=True).click()
                        page.get_by_role('dialog').wait_for(state='detached')
                        assert trigger.evaluate('(e)=>e===document.activeElement')
            mode['locale']='ru'
            page.goto(BASE+'?source=encar&view=project&project=9')
            trigger=page.get_by_role('button',name='Редактировать',exact=True)
            trigger.click()
            dialog=page.get_by_role('dialog')
            dialog.get_by_label('Название проекта',exact=True).fill('Проверка сохранения')
            save_state['hold']=True
            submit=dialog.locator('button[type=submit]')
            submit.click()
            page.wait_for_function("document.querySelector('dialog button[type=submit]')?.disabled")
            page.keyboard.press('Escape')
            assert dialog.is_visible(), 'A pending save must keep the form available'
            assert dialog.locator('.modal-actions').get_by_role('button',name='Отмена',exact=True).is_disabled()
            assert len(pending_saves)==1
            pending_saves.pop().fulfill(status=503,content_type='application/json',body='{"detail":"fixture_save_failed"}')
            page.wait_for_function("!document.querySelector('dialog button[type=submit]')?.disabled")
            assert dialog.get_by_label('Название проекта',exact=True).input_value()=='Проверка сохранения'
            assert submissions[-1]['scan_mode']=='ACCURATE' and submissions[-1]['search_page_mode']=='ALL_PAGES'
            save_state['hold']=False
            submit.click();dialog.wait_for(state='detached')
            assert len(submissions)==2 and submissions[0]==submissions[1], 'Retry must keep the submitted settings'
            assert trigger.evaluate('(e)=>e===document.activeElement')
        finally:
            page.unroute('**/api/**',encar_api)
            mode.update(source='copart',locale='ru',bulk_sources=None)
    check('Encar UI: create/edit modal keyboard isolation, Escape and focus restoration RU/UK',encar_ui_regressions)

    def countdown():
        from datetime import datetime,timezone,timedelta
        mode.update(source='copart',locale='ru',progress_projects=None,runs=[],progress_error=False)
        start=datetime(2026,10,18,16,27,56,tzinfo=timezone.utc)
        target=start+timedelta(seconds=124)
        page.clock.install(time=start)
        lot['schedule']={'utc_at':target.isoformat()}
        lot['time'].update(precision='EXACT',event_kind='START',time_state='UPCOMING_START',remaining_seconds=99999,
            local_date=target.date().isoformat(),local_datetime=target.isoformat(),is_today=False)
        page.set_viewport_size({'width':1280,'height':900})
        page.goto(BASE+'?source=copart&view=project&project=9')
        page.get_by_text('Через 0 ч 3 мин',exact=True).wait_for(timeout=3000)
        posts=len([c for c in calls if c[0]=='POST' and c[1]=='/scans/projects'])
        page.clock.run_for(5000)
        page.wait_for_function("document.querySelector('.auction-card').textContent.includes('Через 0 ч 2 мин')")
        # Simulated wake: derive from the current wall clock, not tick counters.
        page.clock.set_system_time(target+timedelta(minutes=1))
        page.evaluate("() => window.dispatchEvent(new Event('pageshow'))")
        page.get_by_text('Время по расписанию наступило',exact=True).wait_for(timeout=3000)
        assert not page.get_by_text('Аукцион идёт',exact=True).count()
        assert len([c for c in calls if c[0]=='POST' and c[1]=='/scans/projects'])==posts
        page.screenshot(path=str(report/'countdown-after-wake.png'))
    check('local countdown ticks across a minute, wakes without a source scan, never implies LIVE',countdown)
    browser.close()
(report/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
sys.exit(1 if any(r['status']=='failed' for r in results) else 0)
