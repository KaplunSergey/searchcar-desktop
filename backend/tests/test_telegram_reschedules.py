"""Default-on reschedule notices; only disposable storage and mocked sends."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import telegram_dispatch as delivery
from app.auction_rounds import record_auction_observation
from app.models import Car, Project, ProjectCar, ScanRun, ProjectScanRun
from test_telegram_automatic import scan, policy as today_setting, reconcile as today_reconcile
from test_telegram_dispatch import ready_delivery, ready_chat_delivery, native_pairing, auction_api


def setting(f, enabled=None):
    path = '/api/projects/1/telegram-reschedules?source_key=copart'
    result = f.client.get(path) if enabled is None else f.client.post(path, json={
        'enabled': enabled, 'computer_timezone': 'Europe/Warsaw', 'binding_id': 'b'*32})
    assert result.status_code == 200, result.text
    return result.json()


def reconcile(f):
    from app.telegram_reschedules import reconcile
    # The real mounted desktop shell supplies this local heartbeat even with Today OFF.
    assert f.client.post('/api/telegram/automatic-clock', json={'computer_timezone':'Europe/Warsaw'}).status_code == 200
    reconcile(f.config, f.factory, now_ms=f.now())


def notices(f):
    box = delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    with box._db() as db:
        return [dict(row) for row in db.execute("SELECT DISTINCT j.* FROM jobs j JOIN delivery_contexts c "
                                              "ON c.job_id=j.id WHERE c.mode='RESCHEDULE' ORDER BY j.created_at_ms,j.id")]


def change(f, key='change-B', date='2026-10-12T04:00:00', **extra):
    f.clock[0] += timedelta(seconds=1)
    with f.factory() as db:
        result = record_auction_observation(db, db.get(Car, 1), observation_key=key, observed_at=f.clock[0],
            schedule={'local_datetime': date, 'source_timezone': 'UTC', 'event_kind': 'START'}, **extra)
        db.commit()
        return result.schedule_version_id


def published(f):
    setting(f)
    reconcile(f)  # Default preference starts before the next search, not from old history.
    assert f.post().status_code == 200
    assert f.step() and len(f.calls) == 1


def test_reschedules_default_on_is_independent_of_default_off_today(ready_delivery):
    f = ready_delivery
    assert setting(f)['enabled'] is True
    assert f.client.get('/api/projects/1/telegram-automatic').json()['enabled'] is False
    assert setting(f, False)['enabled'] is False
    assert setting(f)['enabled'] is False
    assert setting(f, True)['enabled'] is True
    assert not f.calls and not notices(f)


def test_sent_round_gets_one_short_notice_for_a_move_to_tomorrow(ready_delivery):
    f = ready_delivery; published(f)
    event = change(f); scan(f); reconcile(f)
    assert len(notices(f)) == 1 and notices(f)[0]['state'] == 'QUEUED'
    assert f.step() and len(f.calls) == 2
    message = f.calls[-1]['message']
    assert message['photo'] is None
    assert 'Было:' in message['text'] and 'Стало:' in message['text'] and 'TAHOE' in message['text']
    assert '12 октября' in message['text'] and '11 октября' in message['text']
    assert 'Повреждения:' not in message['text'] and 'Проект:' not in message['text']
    assert any(item.get('url') == 'https://www.copart.com/lot/65507986' for item in message['entities'])
    reconcile(f); scan(f, key=11); reconcile(f)
    assert len(notices(f)) == 1 and not f.step()


@pytest.mark.parametrize('condition', ['unsent', 'unknown', 'sent_after_change'])
def test_only_a_confirmed_publication_before_the_event_qualifies(ready_delivery, condition):
    f = ready_delivery; setting(f); reconcile(f)
    if condition == 'unknown':
        f.results.append(dict(state='UNKNOWN', message_id=None, retry_after=None, error_code='TELEGRAM_RESULT_UNKNOWN'))
        f.post(); f.step()
    change(f)
    if condition == 'sent_after_change':
        f.post(); f.step()
    scan(f); reconcile(f)
    assert not notices(f)


@pytest.mark.parametrize('status,other,page', [('PARTIAL',None,'ALL_PAGES'),
    ('SUCCEEDED','FAILED','ALL_PAGES'), ('CANCELLED',None,'ALL_PAGES'), ('SUCCEEDED',None,'FIRST_PAGE')])
def test_incomplete_run_never_creates_reschedule_notices(ready_delivery, status, other, page):
    f = ready_delivery; published(f); change(f)
    scan(f, status=status, other=other, page=page); reconcile(f)
    assert not notices(f)


def test_observed_a_b_c_and_return_to_a_are_distinct_events_even_before_dispatch(ready_delivery):
    f = ready_delivery; published(f)
    change(f, date='2026-10-12T04:00:00'); scan(f)
    change(f, key='change-C', date='2026-10-13T04:00:00'); scan(f, key=11)
    reconcile(f)  # Both successful runs happened between dispatcher polls.
    assert len(notices(f)) == 2
    for _ in range(2):
        assert f.step(); f.clock[0] += timedelta(seconds=2)
    assert '12 октября' in f.calls[1]['message']['text'] and '13 октября' not in f.calls[1]['message']['text']
    assert '13 октября' in f.calls[2]['message']['text']
    change(f, key='return-A', date='2026-10-11T01:00:00'); scan(f, key=12); reconcile(f)
    assert len(notices(f)) == 3 and f.step()
    assert 'Стало:' in f.calls[-1]['message']['text']


@pytest.mark.parametrize('action', ['disable', 'remove', 'filters', 'recipient', 'partial', 'stale', 'stale_clock', 'new_round', 'invalid_link'])
def test_pending_notice_is_revalidated_before_sending(ready_delivery, action):
    f = ready_delivery; published(f); change(f); scan(f); reconcile(f)
    assert len(notices(f)) == 1
    if action == 'disable': setting(f, False)
    elif action == 'recipient':
        assert f.command('DISCONNECT', binding_id='b'*32).status_code == 200
    elif action == 'partial': scan(f, key=11, status='PARTIAL')
    elif action == 'stale': f.clock[0] += timedelta(minutes=31)
    elif action == 'stale_clock': f.clock[0] += timedelta(minutes=6)
    else:
        with f.factory() as db:
            if action == 'remove': db.get(ProjectCar, (1,1)).tracking_enabled = False
            elif action == 'filters': db.get(Project,1).search_config = {'changed': True}
            elif action == 'invalid_link': db.get(Car,1).url = 'https://untrusted.example/lot/1'
            else:
                record_auction_observation(db, db.get(Car,1), observation_key='new-sale', observed_at=f.clock[0],
                    sale_id='sale-B', schedule={'local_datetime':'2026-10-14T04:00:00','source_timezone':'UTC','event_kind':'START'})
            db.commit()
    f.step()
    assert len(f.calls) == 1 and notices(f)[0]['state'] == 'CANCELLED'


def test_turning_on_never_replays_events_observed_while_off(ready_delivery):
    f = ready_delivery; published(f); setting(f, False)
    change(f); scan(f); reconcile(f)
    assert not notices(f)
    f.clock[0] += timedelta(seconds=1); setting(f, True)
    scan(f, key=11); reconcile(f)
    assert not notices(f)
    change(f, key='fresh-C', date='2026-10-13T04:00:00'); scan(f, key=12); reconcile(f)
    assert len(notices(f)) == 1 and f.step()


@pytest.mark.parametrize('changed_content',[False,True])
def test_before_the_first_send_refresh_only_the_date_of_the_waiting_post(ready_delivery, changed_content):
    f=ready_delivery;setting(f);reconcile(f)
    f.post()  # Still queued: there is no confirmed original publication.
    change(f)
    if changed_content:
        with f.factory() as db:
            car=db.get(Car,1);car.details={**car.details,'primary_damage':'CHANGED DAMAGE'};db.commit()
    scan(f);reconcile(f)
    assert not notices(f)
    assert f.step()
    if changed_content:
        assert not f.calls and f.get().json()['jobs'][0]['state']=='CANCELLED'
    else:
        assert len(f.calls)==1 and '12 октября' in f.calls[0]['message']['text']
        assert 'Повреждения:' in f.calls[0]['message']['text'] and 'Аукцион перенесён' not in f.calls[0]['message']['text']


def test_reschedule_setting_is_owned_source_scoped_and_strict(ready_delivery):
    f = ready_delivery
    for path in ['/api/projects/4/telegram-reschedules?source_key=copart', '/api/projects/1/telegram-reschedules?source_key=iaa']:
        assert f.client.get(path).status_code == 404
        assert f.client.post(path, json={'enabled':False,'computer_timezone':'UTC'}).status_code == 404
    for body in [{'enabled':'yes','computer_timezone':'UTC'}, {'enabled':True,'computer_timezone':'CST'},
                 {'enabled':True,'computer_timezone':'UTC','owner_id':2}]:
        assert f.client.post('/api/projects/1/telegram-reschedules', json=body).status_code == 422
    assert f.client.get('/api/projects/3/telegram-reschedules?source_key=encar').status_code == 400


def test_two_projects_in_one_chat_share_one_event_and_can_opt_out_independently(ready_delivery):
    f=ready_delivery;published(f)
    with f.factory() as db:
        p=db.get(Project,1)
        db.add(Project(id=6,owner_id=1,source_key='copart',name='Second',name_key='second',search_url=p.search_url+'?second=1',
                       search_page_mode='ALL_PAGES',created_at=p.created_at))
        db.flush();db.add(ProjectCar(project_id=6,car_id=1));db.commit()
    recipient=f.client.get('/api/telegram/recipients').json()['recipients'][0]
    assert f.client.post('/api/projects/6/telegram',json={'action':'USE_SAVED','recipient_id':recipient['id']}).status_code==200
    reconcile(f);change(f);scan(f)
    with f.factory() as db:
        run=db.get(ScanRun,10)
        run.payload={**run.payload,'project_ids':[1,6],'project_plans':[
            *run.payload['project_plans'],{**run.payload['project_plans'][0],'project_id':6,'search_url':db.get(Project,6).search_url}]}
        db.add(ProjectScanRun(scan_run_id=10,project_id=6,status='SUCCEEDED'));db.commit()
    reconcile(f)
    assert len(notices(f))==1
    box=delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    assert box.get(notices(f)[0]['id'],owner_id=1)['active_project_ids']==[1,6]
    setting(f,False)
    assert f.step() and f.calls[-1]['project_id']==6 and len(f.calls)==2


@pytest.mark.parametrize('state',['UNKNOWN','FAILED'])
def test_uncertain_or_failed_event_is_never_blindly_retried(ready_delivery,state):
    f=ready_delivery;published(f);change(f);scan(f);reconcile(f)
    f.results.append(dict(state=state,message_id=None,retry_after=None,
                         error_code='TELEGRAM_RESULT_UNKNOWN' if state=='UNKNOWN' else 'TELEGRAM_RELAY_REJECTED'))
    f.step();scan(f,key=11);reconcile(f)
    assert len(notices(f))==1 and notices(f)[0]['state']==state
    assert not f.step() and len(f.calls)==2


def test_reschedule_rate_limit_retries_through_the_existing_pacer(ready_delivery):
    f=ready_delivery;published(f);change(f);scan(f);reconcile(f)
    f.results.append(dict(state='RATE_LIMITED',message_id=None,retry_after=17,error_code='TELEGRAM_RATE_LIMIT'))
    assert f.step() and not f.step()
    f.clock[0]+=timedelta(seconds=19)
    assert f.step() and notices(f)[0]['state']=='SENT'
    assert len(f.calls)==3 and f.calls[1]['job']['id']==f.calls[2]['job']['id']
    assert f.calls[1]['job']['attempt_id']!=f.calls[2]['job']['attempt_id']


def test_restore_disables_reschedules_without_cancelling_manual_work(ready_delivery):
    from app.telegram_automatic import reset_after_restore
    f=ready_delivery;published(f);change(f);scan(f);reconcile(f)
    f.post('c'*32,[5]);reset_after_restore(f.root)
    assert setting(f)['enabled'] is False and setting(f)['issue']=='DATA_RESTORED'
    assert notices(f)[0]['state']=='CANCELLED'
    assert f.step() and len(f.calls)==2 and 'Second car' in f.calls[-1]['message']['text']


def test_legacy_receipts_are_anchored_now_not_backdated_before_enrollment(ready_delivery):
    f=ready_delivery;f.post();f.step()
    box=delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    with box._db() as local:local.execute('UPDATE jobs SET sent_at_ms=NULL')
    change(f);reconcile(f);scan(f);reconcile(f)
    assert not notices(f)
    change(f,key='future-C',date='2026-10-13T04:00:00');scan(f,key=11);reconcile(f)
    assert len(notices(f))==1 and f.step()


@pytest.mark.parametrize('schedule,expected',[
    ({'local_date':'2026-10-12','source_timezone':'America/Chicago','event_kind':'START'},1),
    ({'local_datetime':'2026-10-12T04:00:00','source_timezone':'CST','event_kind':'START'},0),
    ({'date_absent_confirmed':True,'event_kind':'START'},0),
    ({'local_datetime':'2026-10-12T04:00:00','source_timezone':'UTC','event_kind':'END'},0),
])
def test_only_confirmed_start_changes_are_notices_and_date_only_does_not_invent_time(ready_delivery,schedule,expected):
    f=ready_delivery;published(f);f.clock[0]+=timedelta(seconds=1)
    with f.factory() as db:
        record_auction_observation(db,db.get(Car,1),observation_key='precision-change',observed_at=f.clock[0],schedule=schedule)
        db.commit()
    scan(f);reconcile(f)
    assert len(notices(f))==expected
    if expected:
        assert f.step()
        assert 'без точного времени' in f.calls[-1]['message']['text'] and 'America/Chicago' in f.calls[-1]['message']['text']


def test_computer_timezone_changes_rendering_not_the_event_identity(ready_delivery):
    f=ready_delivery;published(f);change(f);scan(f);reconcile(f)
    assert f.client.post('/api/telegram/automatic-clock',json={'computer_timezone':'Asia/Tokyo'}).status_code==200
    assert f.step() and 'Asia/Tokyo' in f.calls[-1]['message']['text']
    scan(f,key=11);reconcile(f)
    assert len(notices(f))==1 and not f.step()


def test_today_opt_out_does_not_disable_reschedules_of_an_automatic_publication(ready_delivery):
    f=ready_delivery;setting(f);reconcile(f);today_setting(f,True)
    f.clock[0]+=timedelta(seconds=1);scan(f);today_reconcile(f);assert f.step()
    today_setting(f,False);change(f);scan(f,key=11);reconcile(f)
    assert f.step() and len(f.calls)==2 and 'Аукцион перенесён' in f.calls[-1]['message']['text']


def test_groups_and_channels_use_the_same_notification_queue(ready_chat_delivery):
    f=ready_chat_delivery;published(f);change(f);scan(f);reconcile(f)
    assert f.step() and len(f.calls)==2 and f.calls[-1]['job']['chat_type']==f.chat_type


def test_missing_csrf_never_changes_the_default_preference(ready_delivery):
    from app.auth import require_csrf
    from app.main import app
    f=ready_delivery;previous=app.dependency_overrides.pop(require_csrf)
    try:
        response=f.client.post('/api/projects/1/telegram-reschedules',json={'enabled':False,'computer_timezone':'UTC'})
        assert response.status_code==401
    finally:app.dependency_overrides[require_csrf]=previous
    assert setting(f)['enabled'] is True and not notices(f)


def test_dispatcher_observes_reschedules_without_an_open_settings_page(ready_delivery,monkeypatch):
    from app import database
    from threading import Event
    f=ready_delivery;published(f);change(f);scan(f)
    monkeypatch.setattr(database,'SessionLocal',f.factory)
    monkeypatch.setattr(delivery.time,'time',lambda:f.now()/1000)
    original=delivery.send_once;sent=Event()
    def send(*args):
        result=original(*args);sent.set();return result
    monkeypatch.setattr(delivery,'send_once',send)
    service=delivery.DeliveryService()
    try:service.start();assert sent.wait(3)
    finally:service.stop()
    assert len(f.calls)==2 and notices(f)[0]['state']=='SENT'


def test_new_destination_has_no_old_receipt_and_waits_for_its_own_initial_post(ready_delivery):
    f=ready_delivery;published(f)
    change(f,key='old-chat-B');scan(f);reconcile(f);assert f.step()
    pid=f.command('START').json()['session']['pairing_id']
    f.proposed(pid);f.counts['pairs'][pid]['recipient']['chat_id']=777
    assert f.command('CONFIRM',pairing_id=pid,chat_id=777).status_code==200
    assert setting(f)['counts'].get('SENT',0)==0
    reconcile(f);change(f,date='2026-10-13T04:00:00');scan(f,key=11);reconcile(f)
    assert all(job['chat_id']==555 for job in notices(f))
    assert f.post('d'*32).status_code==200 and f.step()
    change(f,key='next-C',date='2026-10-14T04:00:00');scan(f,key=12);reconcile(f)
    assert f.step() and notices(f)[-1]['chat_id']==777


def test_setting_can_be_disabled_before_a_recipient_is_connected(ready_delivery):
    f=ready_delivery
    assert f.command('DISCONNECT',binding_id='b'*32).status_code==200
    assert setting(f)['enabled'] is True and setting(f)['recipient'] is None
    assert setting(f,False)['enabled'] is False


def test_notice_entities_are_safe_utf16_ranges_not_html_markup(ready_delivery):
    f=ready_delivery;published(f)
    with f.factory() as db:
        db.get(Car,1).title='🚗 <b>Car & title</b>\n\u202e'+'X'*300;db.commit()
    change(f);scan(f);reconcile(f);assert f.step()
    message=f.calls[-1]['message'];raw=message['text'].encode('utf-16-le')
    assert len(raw)//2<=4096 and len(message['entities'])<=16 and '\u202e' not in message['text']
    end=0
    for entity in message['entities']:
        assert entity['offset']>=end and entity['length']>0
        end=entity['offset']+entity['length']
        assert raw[entity['offset']*2:end*2].decode('utf-16-le')


def test_delivery_schema_upgrade_preserves_old_receipts_and_is_idempotent(tmp_path):
    from app.telegram_delivery import TelegramOutbox
    path=tmp_path/'legacy-outbox.sqlite3'
    old=TelegramOutbox(path)
    job=old.enqueue(owner_id=1,project_id=1,bot_id=123456,
        destination=dict(chat_id=555,chat_type='private',title='fixture'),notification_key='legacy:v1',now_ms=1000)
    attempt=old.claim_ready(1000);old.sent(job['id'],attempt['attempt_id'],message_id=701)
    for _ in range(2):
        box=delivery.DeliveryBox(path);saved=box.get(job['id'],owner_id=1)
        assert saved['state']=='SENT' and saved['message_id']==701 and saved['sent_at_ms'] is None
        assert saved['active_project_ids']==[1]
