"""Manual delivery fixtures: no real Telegram traffic or user's database."""
import pytest
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace
from threading import Event
from app import telegram_dispatch as delivery, telegram_pairing
from app.models import Car, Project, ProjectCar, User
from test_telegram_pairing import native_pairing
from test_auction_api import auction_api

def test_manual_delivery_requires_explicit_runtime_enablement(native_pairing):
    f = native_pairing
    response = f.client.post('/api/projects/1/telegram-deliveries?source_key=copart', json={
        'intent_id': 'a' * 32, 'car_ids': [1], 'collection': 'active', 'computer_timezone': 'Europe/Warsaw'})
    assert response.status_code == 503, response.text
    assert response.json()['detail'] == 'telegram_delivery_not_configured'

@pytest.fixture
def ready_delivery(native_pairing, auction_api, monkeypatch):
    f = native_pairing; _, factory, clock = auction_api
    monkeypatch.setenv('SEARCHCAR_TELEGRAM_DELIVERY_ENABLED','1')
    monkeypatch.setattr(delivery.service,'kick',lambda:None)
    with factory() as db:
        car=db.get(Car,1);car.source_listing_id='65507986';car.url='https://www.copart.com/lot/65507986'
        car.title='🚘 2026 CHEVROLET TAHOE';car.details={**car.details,'main_image_source_url':'https://cs.copart.com/fixture.jpg','primary_damage':'REAR END'}
        db.add(Car(id=5,source_key='copart',source_listing_id='65507987',url='https://www.copart.com/lot/65507987',title='Second car',details={}))
        db.flush();db.add(ProjectCar(project_id=1,car_id=5));db.commit()
    pid=f.command('START').json()['session']['pairing_id'];f.proposed(pid)
    assert f.command('CONFIRM',pairing_id=pid,chat_id=555).status_code==200
    calls=[];results=[]
    def send(config,job,project_id,binding,message):
        calls.append(dict(job=deepcopy(job),project_id=project_id,binding=binding,message=deepcopy(message)))
        return results.pop(0) if results else dict(state='SENT',message_id=len(calls)+700,retry_after=None,error_code=None)
    monkeypatch.setattr(delivery,'send_once',send)
    config,_=telegram_pairing.configuration()
    f=SimpleNamespace(**vars(f),factory=factory,clock=clock,calls=calls,results=results,config=config)
    f.now=lambda:int(clock[0].timestamp()*1000)
    f.step=lambda **kw:delivery.step(config,factory,now_ms=f.now(),**kw)
    f.post=lambda intent='a'*32,ids=[1],**extra:f.client.post('/api/projects/1/telegram-deliveries?source_key=copart',json={
        'intent_id':intent,'car_ids':ids,'collection':'active','computer_timezone':'Europe/Warsaw',**extra})
    f.get=lambda intent='a'*32:f.client.get('/api/projects/1/telegram-deliveries/'+intent+'?source_key=copart')
    return f

@pytest.fixture(params=[('group','group'),('group','supergroup'),('channel','channel')])
def ready_chat_delivery(ready_delivery, monkeypatch, request):
    f=ready_delivery
    monkeypatch.setenv('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED','1')
    kind,chat_type=request.param
    started=f.command('START',target_kind=kind)
    assert started.status_code==200,started.text
    pid=started.json()['session']['pairing_id']
    f.counts['pairs'][pid].update(status='AWAITING_CONFIRMATION',recipient={
        'chat_id':-100555,'chat_type':chat_type,'title':'Test destination'})
    confirmed=f.command('CONFIRM',pairing_id=pid,chat_id=-100555)
    assert confirmed.status_code==200,confirmed.text
    f.chat_type=chat_type
    return f

def test_confirmed_chat_manual_batch_uses_the_local_queue_and_destination_pacing(ready_chat_delivery):
    f=ready_chat_delivery
    created=f.post(ids=[1,5]);assert created.status_code==200,created.text
    assert created.json()['recipient']==dict(title='Test destination',chat_id=-100555,chat_type=f.chat_type)
    assert [job['state'] for job in created.json()['jobs']]==['QUEUED','QUEUED'] and not f.calls
    assert f.step() and len(f.calls)==1 and not f.step()
    call=f.calls[0]
    assert call['job']['chat_id']==-100555 and call['job']['chat_type']==f.chat_type
    assert call['binding']=='b'*32 and call['project_id']==1
    interval=3200 if f.chat_type in {'group','supergroup'} else 1200
    f.clock[0]+=timedelta(milliseconds=interval-1)
    assert not f.step()
    f.clock[0]+=timedelta(milliseconds=1)
    assert f.step() and len(f.calls)==2
    scheduled=next(call['message'] for call in f.calls if 'TAHOE' in call['message']['text'])
    assert any(entity.get('url','').startswith('https://searchcar-auction-calendar-preview.pages.dev/#') for entity in scheduled['entities'])
    assert [job['state'] for job in f.get().json()['jobs']]==['SENT','SENT']
    assert f.post(ids=[1,5]).json()==f.get().json() and not f.step()

def test_chat_gate_rejects_new_delivery_without_mutating_the_queue(ready_chat_delivery,monkeypatch):
    f=ready_chat_delivery
    monkeypatch.delenv('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED')
    result=f.post()
    assert result.status_code==503 and result.json()['detail']=='telegram_chat_pairing_disabled'
    assert not f.step() and not f.calls

@pytest.mark.parametrize('change',['gate','disconnect','chat_type'])
def test_chat_is_revalidated_before_each_attempt(ready_chat_delivery,monkeypatch,change):
    f=ready_chat_delivery
    created=f.post();assert created.status_code==200,created.text
    if change=='gate':monkeypatch.delenv('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED')
    elif change=='disconnect':assert f.command('DISCONNECT',binding_id='b'*32).status_code==200
    else:
        box=delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
        with box._db() as db:
            db.execute("UPDATE project_recipients SET chat_type=? WHERE project_id=1",
                ('group' if f.chat_type!='group' else 'supergroup',))
    f.step()
    assert not f.calls and f.get().json()['jobs'][0]['state']=='CANCELLED'

def test_chat_rights_rejected_by_relay_are_terminal_and_not_retried(ready_chat_delivery):
    f=ready_chat_delivery
    created=f.post();assert created.status_code==200,created.text
    f.results.append(dict(state='FAILED',message_id=None,retry_after=None,error_code='TELEGRAM_RELAY_REJECTED'))
    assert f.step() and len(f.calls)==1
    f.clock[0]+=timedelta(days=1)
    assert not f.step() and f.get().json()['jobs'][0]['state']=='FAILED'

def test_manual_batch_is_queued_then_sent_and_same_http_intent_is_idempotent(ready_delivery):
    f=ready_delivery
    created=f.post();assert created.status_code==200,created.text
    assert created.json()['jobs'][0]['state']=='QUEUED' and f.calls==[]
    assert f.post().json()==created.json()
    assert f.step() and len(f.calls)==1
    result=f.get().json();assert result['jobs'][0]['state']=='SENT'
    assert result['jobs'][0]['message_id']==701
    assert f.post().json()==result and not f.step()
    message=f.calls[0]['message'];assert message['photo']=='https://cs.copart.com/fixture.jpg'
    assert 'Проект:' not in message['text'] and 'REAR END' in message['text']
    assert len(message['text'].encode('utf-16-le'))//2<=1024
    entity=next(entity for entity in message['entities'] if entity.get('url','').startswith('https://searchcar-auction-calendar-preview.pages.dev/#'))
    raw=message['text'].encode('utf-16-le');assert raw[entity['offset']*2:(entity['offset']+entity['length'])*2].decode('utf-16-le')=='Добавить в календарь'
    assert entity['url'].startswith('https://searchcar-auction-calendar-preview.pages.dev/#')
    assert f.client.get('/api/projects/1/telegram-history?source_key=copart').json()['sent_car_ids']==[1]

def test_new_explicit_single_or_bulk_intent_resends_an_already_sent_car_with_a_marker(ready_delivery):
    f=ready_delivery
    f.post();f.step()
    f.clock[0]+=timedelta(seconds=2)
    again=f.post('b'*32,[1,5]);assert again.status_code==200,again.text
    items=again.json()['jobs'];assert [job['state'] for job in items]==['QUEUED','QUEUED']
    assert items[0]['previously_sent'] and not items[1]['previously_sent']
    assert f.step();f.clock[0]+=timedelta(seconds=2);assert f.step()
    assert len(f.calls)==3 and f.calls[0]['job']['id']!=f.calls[1]['job']['id']
    assert [job['state'] for job in f.get('b'*32).json()['jobs']]==['SENT','SENT']

def test_selection_is_atomic_owned_scoped_and_revalidated_before_enqueue(ready_delivery):
    f=ready_delivery
    assert f.post(ids=[1,2]).status_code==409
    assert f.post(ids=[3]).status_code==409
    assert f.post(ids=[1],collection='archive').status_code==409
    assert f.client.post('/api/projects/4/telegram-deliveries?source_key=copart',json={}).status_code==404
    assert f.client.post('/api/projects/1/telegram-deliveries?source_key=iaa',json={}).status_code==404
    box=delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    with box._db() as db:assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0

@pytest.mark.parametrize('extra',[dict(car_ids=[True]),dict(car_ids=[1,1]),dict(car_ids=[]),dict(owner_id=999),dict(intent_id='bad'),dict(computer_timezone='CST')])
def test_manual_input_rejects_unknown_fields_and_ambiguous_identifiers(ready_delivery,extra):
    f=ready_delivery;assert f.post(**extra).status_code==422

@pytest.mark.parametrize('change',['damage','removed','inactive','filters'])
def test_changed_data_or_project_never_sends_the_old_selection(ready_delivery,change):
    f=ready_delivery;f.post()
    with f.factory() as db:
        if change=='damage':db.get(Car,1).details={'primary_damage':'NEW DAMAGE'}
        elif change=='removed':db.get(ProjectCar,(1,1)).tracking_enabled=False
        elif change=='inactive':db.get(User,1).status='SUSPENDED'
        else:db.get(Project,1).search_url='https://www.copart.com/lotSearchResults?different=1'
        db.commit()
    assert f.step() and f.calls==[]
    assert f.get().json()['jobs'][0]['state']=='CANCELLED'

def test_429_uses_local_bot_pause_from_response_time_and_missing_hint_has_bounded_backoff(ready_delivery):
    f=ready_delivery;f.post(ids=[1,5])
    f.results.append(dict(state='RATE_LIMITED',message_id=None,retry_after=17,error_code='TELEGRAM_RATE_LIMIT'))
    start=f.now();assert f.step(clock_ms=lambda:start+15000)
    assert not f.step()
    box=delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    assert box.next_wake_ms(start)==start+15000+17000+1000
    f.clock[0]+=timedelta(seconds=33);assert f.step()
    f.clock[0]+=timedelta(seconds=2)
    f.results.append(dict(state='RATE_LIMITED',message_id=None,retry_after=None,error_code='TELEGRAM_RATE_LIMIT'))
    assert f.step();assert not f.step()
    assert box.next_wake_ms(f.now())>=f.now()+30000

def test_photo_fallback_needs_another_local_admission_and_unknown_is_not_retried(ready_delivery):
    f=ready_delivery;f.post()
    f.results.append(dict(state='PHOTO_REJECTED',message_id=None,retry_after=None,error_code='TELEGRAM_PHOTO_REJECTED'))
    assert f.step() and not f.step()
    f.clock[0]+=timedelta(seconds=2)
    f.results.append(dict(state='UNKNOWN',message_id=None,retry_after=None,error_code='TELEGRAM_RESULT_UNKNOWN'))
    assert f.step() and f.calls[-1]['message']['photo'] is None
    assert f.calls[0]['job']['attempt_id']!=f.calls[1]['job']['attempt_id']
    f.clock[0]+=timedelta(days=1);assert not f.step()
    assert f.get().json()['jobs'][0]['state']=='UNKNOWN'
    assert f.post().json()['jobs'][0]['state']=='UNKNOWN'  # Retrying the lost HTTP intent is not a new user action.

def test_cancel_and_exclusive_restart_recovery_do_not_resend_unconfirmed_attempts(ready_delivery):
    f=ready_delivery;f.post(ids=[1,5])
    box=delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    job=box.claim_ready(f.now());assert job
    cancelled=f.client.post('/api/projects/1/telegram-deliveries/'+'a'*32+'/cancel?source_key=copart')
    assert cancelled.status_code==200,cancelled.text
    assert {job['state'] for job in cancelled.json()['jobs']}=={'IN_FLIGHT','CANCELLED'}
    box.recover();assert {job['state'] for job in f.get().json()['jobs']}=={'UNKNOWN','CANCELLED'}
    f.clock[0]+=timedelta(days=1);assert not f.step() and not f.calls

def test_only_the_exclusive_dispatcher_may_recover_and_the_thread_releases_its_lock(ready_delivery,monkeypatch):
    from app import database
    f=ready_delivery;f.post();monkeypatch.setattr(database,'SessionLocal',f.factory)
    entered,release=Event(),Event();original=delivery.send_once
    def send(*args):
        entered.set();assert release.wait(3);return original(*args)
    monkeypatch.setattr(delivery,'send_once',send)
    first,second=delivery.DeliveryService(),delivery.DeliveryService()
    try:
        first.start();assert entered.wait(3)
        with pytest.raises(RuntimeError,match='already_running'):second.start()
        assert f.get().json()['jobs'][0]['state']=='IN_FLIGHT'  # No other runner reclassified a live attempt.
    finally:
        release.set();first.stop();second.stop()
    assert f.get().json()['jobs'][0]['state']=='SENT'
    second.start();second.stop();assert not second.thread.is_alive()

def test_repeated_rate_limit_without_a_hint_stops_after_bounded_local_backoff(ready_delivery):
    f=ready_delivery;f.post();box=delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    for attempt in range(5):
        f.results.append(dict(state='RATE_LIMITED',message_id=None,retry_after=None,error_code='TELEGRAM_RATE_LIMIT'))
        assert f.step()
        wake=box.next_wake_ms(f.now())
        if attempt<4:
            assert wake>f.now();f.clock[0]+=timedelta(milliseconds=wake-f.now())
        else:assert wake is None
    assert f.get().json()['jobs'][0]['state']=='FAILED' and not f.step()

def test_manual_enqueue_never_bypasses_csrf_even_with_a_saved_recipient(ready_delivery):
    from app.main import app
    from app.auth import require_csrf
    f=ready_delivery
    previous=app.dependency_overrides.pop(require_csrf)
    try:
        assert f.post().status_code in (401,403)
        assert not f.step() and not f.calls
    finally:app.dependency_overrides[require_csrf]=previous

def test_a_today_selection_is_reconsidered_after_the_computer_day_changes(ready_delivery):
    f=ready_delivery
    result=f.post(collection='today');assert result.status_code==200,result.text
    f.clock[0]+=timedelta(days=1);assert f.step() and not f.calls
    assert f.get().json()['jobs'][0]['state']=='CANCELLED'

def test_delivery_status_and_history_never_leak_another_owner_or_source(ready_delivery):
    f=ready_delivery;f.post()
    for path in ['/api/projects/4/telegram-history?source_key=copart',
                 '/api/projects/1/telegram-history?source_key=iaa',
                 '/api/projects/4/telegram-deliveries/'+'a'*32+'?source_key=copart',
                 '/api/projects/1/telegram-deliveries/'+'a'*32+'?source_key=iaa']:
        assert f.client.get(path).status_code==404

def test_formatter_keeps_fractional_mileage_unknown_dates_and_safe_caption_entities(ready_delivery):
    from app.telegram_message import auction_message,utf16
    from app.auction_read import project_auction_view
    from urllib.parse import urlsplit,unquote
    f=ready_delivery
    with f.factory() as db:
        view=project_auction_view(db,db.get(Project,1),now=f.clock[0],computer_timezone='Europe/Warsaw')
    lot=view['lots'][0];lot['title']='🚙'*120;lot['details']['mileage']={'value':1234.5,'unit':'mi','accuracy':'ACTUAL'}
    lot['details']['primary_damage']='🚘'*500
    message=auction_message(lot,view,'uk');assert utf16(message['text'])<=1024
    assert '1 234,5 mi' in message['text'] and any(entity.get('url')==lot['url'] for entity in message['entities'])
    entity=next(entity for entity in message['entities'] if entity.get('url','').startswith('https://searchcar-auction-calendar-preview.pages.dev/#'))
    event=json.loads(unquote(urlsplit(entity['url']).fragment))
    assert event['id']=='65507986' and event['locale']=='uk' and event['start']=='2026-10-11T01:00:00.000Z'
    lot['time'].update(local_date=None,local_datetime=None,remaining_seconds=None,precision='UNKNOWN')
    unknown=auction_message(lot,view,'ru',force_text=True)
    assert 'Дата не назначена' in unknown['text'] and 'None' not in unknown['text']
    assert not any(entity.get('url','').startswith('https://searchcar-auction-calendar-preview.pages.dev/#') for entity in unknown['entities'])
