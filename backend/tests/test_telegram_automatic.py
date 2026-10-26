"""Opt-in Today posting, controlled time and disposable storage; no Telegram traffic."""
from datetime import timedelta
import pytest
from app import telegram_dispatch as delivery
from app.models import Project, ProjectCar, ScanRun, ProjectScanRun, AuctionObservation, Car
from app.auction_rounds import record_auction_observation
from sqlalchemy import select
from test_telegram_dispatch import ready_delivery, native_pairing, auction_api


def policy(f, enabled=None):
    url='/api/projects/1/telegram-automatic?source_key=copart'
    return f.client.get(url) if enabled is None else f.client.post(url,json={'enabled':enabled,'computer_timezone':'Europe/Warsaw','binding_id':'b'*32})


def scan(f, *, status='SUCCEEDED', other=None, page='ALL_PAGES', key=10):
    with f.factory() as db:
        p=db.get(Project,1);p.search_page_mode=page
        fields=('source_key','search_url','search_config','scan_mode','search_page_mode','price_filter_mode',
                'price_min_krw','price_max_krw','price_filter_revision','price_filter_baseline_revision')
        plan={'project_id':1,**{field:getattr(p,field) for field in fields}}
        ids=[1,2] if other else [1]
        run=ScanRun(id=key,owner_id=1,kind='PROJECTS',status=status,started_at=f.clock[0],
            finished_at=f.clock[0]+timedelta(seconds=10),payload={'project_ids':ids,'project_plans':[plan]})
        db.add(run);db.flush()
        db.add(ProjectScanRun(scan_run_id=key,project_id=1,status='SUCCEEDED'))
        if other:db.add(ProjectScanRun(scan_run_id=key,project_id=2,status=other))
        relation=db.get(ProjectCar,(1,1));relation.last_found_at=f.clock[0]+timedelta(seconds=1)
        obs=db.scalar(select(AuctionObservation).where(AuctionObservation.car_id==1).order_by(AuctionObservation.observed_at.desc()).limit(1))
        db.add(AuctionObservation(id=f'obs-{key}',car_id=1,round_id=obs.round_id,
            schedule_version_id=obs.schedule_version_id,observation_key=f'scan:{key}:1',
            observed_at=relation.last_found_at,applied=True,payload=obs.payload))
        db.commit()
    f.clock[0]+=timedelta(seconds=10)


def reconcile(f):
    from app.telegram_automatic import reconcile
    reconcile(f.config,f.factory,now_ms=f.now())


def jobs(f):
    box=delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    with box._db() as db:return [dict(row) for row in db.execute('SELECT * FROM jobs')]


def test_automatic_default_off_and_enable_never_posts_existing_saved_cars(ready_delivery):
    f=ready_delivery
    response=policy(f)
    assert response.status_code==200,response.text
    assert response.json()['enabled'] is False
    scan(f);reconcile(f)
    assert not jobs(f)
    assert policy(f,True).json()['enabled'] is True
    reconcile(f)
    assert not jobs(f) and not f.calls


def test_next_full_success_posts_today_once_and_manual_repeat_still_works(ready_delivery):
    f=ready_delivery
    assert policy(f,True).status_code==200
    f.clock[0]+=timedelta(seconds=1);scan(f);reconcile(f)
    assert len(jobs(f))==1 and not f.calls
    assert f.step() and len(f.calls)==1
    assert policy(f).json()['batch']['jobs'][0]['state']=='SENT'
    scan(f,key=11);reconcile(f)
    assert len(jobs(f))==1 and not f.step()
    assert f.post('c'*32).status_code==200
    f.clock[0]+=timedelta(seconds=2)
    assert f.step() and len(f.calls)==2


@pytest.mark.parametrize('status,other,page',[
    ('RUNNING',None,'ALL_PAGES'),('PARTIAL','FAILED','ALL_PAGES'),
    ('CANCELLED','CANCELLED','ALL_PAGES'),('SUCCEEDED','PARTIAL','ALL_PAGES'),
    ('SUCCEEDED',None,'FIRST_PAGE'),
])
def test_incomplete_whole_run_or_single_page_never_auto_posts(ready_delivery,status,other,page):
    f=ready_delivery;assert policy(f,True).status_code==200
    f.clock[0]+=timedelta(seconds=1);scan(f,status=status,other=other,page=page);reconcile(f)
    assert not jobs(f) and not f.calls


@pytest.mark.parametrize('change',['off','filters','removed','midnight','stale_clock','stale_result','phase'])
def test_queued_automatic_is_revalidated_at_actual_send(ready_delivery,change):
    from app.models import Car
    f=ready_delivery;assert policy(f,True).status_code==200
    f.clock[0]+=timedelta(seconds=1);scan(f);reconcile(f)
    assert len(jobs(f))==1
    if change=='off':assert policy(f,False).status_code==200
    elif change in {'midnight','stale_result','stale_clock'}:
        f.clock[0]+=timedelta(days=1) if change=='midnight' else timedelta(minutes=31 if change=='stale_result' else 6)
        if change!='stale_clock':
            response=f.client.post('/api/telegram/automatic-clock',json={'computer_timezone':'Europe/Warsaw'})
            assert response.status_code==200,response.text
    else:
        with f.factory() as db:
            if change=='filters':db.get(Project,1).search_config={'changed':True}
            elif change=='removed':db.get(ProjectCar,(1,1)).tracking_enabled=False
            else:
                car=db.get(Car,1);car.details={**car.details,'auction_phase':'CLOSED'}
            db.commit()
    f.step()
    assert not f.calls and jobs(f)[0]['state']=='CANCELLED'


def test_automatic_policy_is_owned_source_scoped_strict_and_local(ready_delivery):
    f=ready_delivery
    for url in ['/api/projects/4/telegram-automatic?source_key=copart','/api/projects/1/telegram-automatic?source_key=iaa']:
        assert f.client.post(url,json={'enabled':True,'computer_timezone':'Europe/Warsaw'}).status_code==404
    for body in [{'enabled':'yes','computer_timezone':'Europe/Warsaw'},
                 {'enabled':True,'computer_timezone':'CST'},
                 {'enabled':True,'computer_timezone':'UTC','owner_id':2}]:
        assert f.client.post('/api/projects/1/telegram-automatic',json=body).status_code==422
    assert not jobs(f)


def test_manual_sent_or_unknown_round_suppresses_auto_but_manual_repeat_allowed(ready_delivery):
    f=ready_delivery;assert f.post().status_code==200;f.step()
    assert policy(f,True).status_code==200
    f.clock[0]+=timedelta(seconds=1);scan(f);reconcile(f)
    assert len(jobs(f))==1


def test_cancelling_queue_is_not_replayed_by_poll_but_next_full_search_can_send(ready_delivery):
    f=ready_delivery;policy(f,True);f.clock[0]+=timedelta(seconds=1);scan(f);reconcile(f)
    batch=policy(f).json()['batch']
    assert f.client.post(f'/api/projects/1/telegram-deliveries/{batch["id"]}/cancel?source_key=copart').status_code==200
    reconcile(f);assert not f.step() and not f.calls
    scan(f,key=11);reconcile(f)
    assert f.step() and len(f.calls)==1


def test_auto_is_deduplicated_across_two_projects_in_same_chat(ready_delivery):
    f=ready_delivery
    with f.factory() as db:
        p=db.get(Project,1)
        db.add(Project(id=6,owner_id=1,source_key='copart',name='Second',name_key='second',search_url=p.search_url+'?second=1',search_page_mode='ALL_PAGES',created_at=p.created_at))
        db.flush();db.add(ProjectCar(project_id=6,car_id=1));db.commit()
    saved=f.client.get('/api/telegram/recipients').json()['recipients'][0]
    assert f.client.post('/api/projects/6/telegram',json={'action':'USE_SAVED','recipient_id':saved['id']}).status_code==200
    policy(f,True)
    assert f.client.post('/api/projects/6/telegram-automatic',json={'enabled':True,'computer_timezone':'Europe/Warsaw','binding_id':'c'*32}).status_code==200
    f.clock[0]+=timedelta(seconds=1);scan(f)
    with f.factory() as db:
        run=db.get(ScanRun,10);plan={**run.payload['project_plans'][0],'project_id':6,'search_url':db.get(Project,6).search_url}
        run.payload={**run.payload,'project_ids':[1,6],'project_plans':[*run.payload['project_plans'],plan]}
        db.add(ProjectScanRun(scan_run_id=10,project_id=6,status='SUCCEEDED'))
        db.get(ProjectCar,(6,1)).last_found_at=f.clock[0]-timedelta(seconds=1);db.commit()
    reconcile(f)
    assert len(jobs(f))==1
    first=policy(f).json()['batch']
    second=f.client.get('/api/projects/6/telegram-automatic?source_key=copart')
    assert second.status_code==200,second.text
    assert second.json()['batch']['id']!=first['id']
    assert second.json()['batch']['jobs'][0]['id']==first['jobs'][0]['id']
    box=delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    assert box.get(jobs(f)[0]['id'],owner_id=1)['active_project_ids']==[1,6]
    # One project opting out does not cancel the other project's same-chat reference.
    policy(f,False)
    assert f.step() and len(f.calls)==1 and f.calls[0]['project_id']==6


def test_timezone_is_current_at_send_and_clock_has_no_external_calls(ready_delivery):
    f=ready_delivery;policy(f,True);f.clock[0]+=timedelta(seconds=1);scan(f);reconcile(f)
    count=len(f.counts['calls'])
    # At UTC-1 the computer is still on October 10, but the auction starts October 11.
    response=f.client.post('/api/telegram/automatic-clock',json={'computer_timezone':'Atlantic/Cape_Verde'})
    assert response.status_code==200 and len(f.counts['calls'])==count
    assert f.step() and not f.calls


@pytest.mark.parametrize('state',['UNKNOWN','FAILED'])
def test_terminal_auto_outcomes_never_retry_on_next_search(ready_delivery,state):
    f=ready_delivery;policy(f,True);f.clock[0]+=timedelta(seconds=1);scan(f);reconcile(f)
    f.results.append(dict(state=state,message_id=None,retry_after=None,error_code='TELEGRAM_RESULT_UNKNOWN' if state=='UNKNOWN' else 'TELEGRAM_RELAY_REJECTED'))
    f.step();scan(f,key=11);reconcile(f)
    assert len(jobs(f))==1 and not f.step() and len(f.calls)==1


def test_saved_cars_not_observed_in_this_scan_are_not_sent(ready_delivery):
    f=ready_delivery;policy(f,True);f.clock[0]+=timedelta(seconds=1);scan(f)
    with f.factory() as db:
        db.get(ProjectCar,(1,1)).last_found_at=f.clock[0]-timedelta(days=1);db.commit()
    reconcile(f);assert not jobs(f)


def test_enabling_requires_exact_recipient_shown_in_confirmation(ready_delivery):
    f=ready_delivery
    response=f.client.post('/api/projects/1/telegram-automatic',json={'enabled':True,'computer_timezone':'UTC','binding_id':'f'*32})
    assert response.status_code==409 and response.json()['detail']=='telegram_recipient_changed'
    assert policy(f).json()['enabled'] is False


def test_restore_attempt_disables_auto_and_cancels_only_automatic_queue(ready_delivery):
    from app.telegram_automatic import reset_after_restore
    f=ready_delivery;policy(f,True);f.clock[0]+=timedelta(seconds=1);scan(f);reconcile(f)
    assert f.post(ids=[5]).status_code==200
    reset_after_restore(f.root)
    assert not policy(f).json()['enabled']
    assert sorted(job['state'] for job in jobs(f))==['CANCELLED','QUEUED']
    assert f.step() and len(f.calls)==1 and 'Second car' in f.calls[0]['message']['text']


@pytest.mark.parametrize('schedule,expected',[
    ({'local_date':'2026-10-11','source_timezone':'Europe/Warsaw','event_kind':'START'},1),
    ({'local_date':'2026-10-11','source_timezone':'America/Chicago','event_kind':'START'},0),
    ({'local_datetime':'2026-10-11T04:00:00','source_timezone':'UTC','event_kind':'END'},0),
    ({'raw':'unconfirmed date'},0),
])
def test_automatic_needs_unambiguous_today_and_a_start_not_an_end(ready_delivery,schedule,expected):
    f=ready_delivery;policy(f,True);f.clock[0]+=timedelta(seconds=1)
    with f.factory() as db:
        record_auction_observation(db,db.get(Car,1),observation_key='changed-schedule',observed_at=f.clock[0],schedule=schedule)
        db.commit()
    scan(f);reconcile(f)
    assert len(jobs(f))==expected,f.client.get('/api/projects/1/auction-lots?computer_timezone=Europe/Warsaw').json()['lots'][0]['schedule']
    if expected:
        assert f.step() and 'точное время не указано' in f.calls[0]['message']['text']


def test_confirmed_new_round_can_post_but_a_time_change_is_not_a_new_initial_post(ready_delivery):
    f=ready_delivery;policy(f,True);f.clock[0]+=timedelta(seconds=1);scan(f);reconcile(f);f.step()
    def observe(key,**extra):
        with f.factory() as db:
            record_auction_observation(db,db.get(Car,1),observation_key=key,observed_at=f.clock[0],
                schedule={'local_datetime':'2026-10-11T04:00:00','source_timezone':'UTC','event_kind':'START'},**extra)
            db.commit()
    observe('time-change');scan(f,key=11);reconcile(f)
    assert len(jobs(f))==1 and not f.step()
    observe('new-sale',sale_id='sale-B');scan(f,key=12);reconcile(f)
    assert len(jobs(f))==2 and f.step() and len(f.calls)==2


def test_auto_queue_is_sorted_by_start_time_not_random_job_ids(ready_delivery):
    f=ready_delivery;policy(f,True);f.clock[0]+=timedelta(seconds=1);scan(f)
    with f.factory() as db:
        for car_id,hour in [(6,3),(7,2)]:
            car=Car(id=car_id,source_key='copart',source_listing_id=str(65507990+car_id),
                url=f'https://www.copart.com/lot/{65507990+car_id}',title=f'Car-{hour}')
            db.add(car);db.flush()
            db.add(ProjectCar(project_id=1,car_id=car_id,last_found_at=f.clock[0]))
            record_auction_observation(db,car,observation_key=f'scan:10:{car_id}',observed_at=f.clock[0],sale_id=f'sale-{car_id}',
                schedule={'local_datetime':f'2026-10-11T0{hour}:00:00','source_timezone':'UTC','event_kind':'START'})
        db.commit()
    reconcile(f)
    for _ in range(3):
        assert f.step();f.clock[0]+=timedelta(seconds=2)
    assert [call['message']['text'].split('\n')[0] for call in f.calls]==['🚙 🚘 2026 CHEVROLET TAHOE','🚙 Car-2','🚙 Car-3']


def test_dispatcher_thread_observes_committed_run_without_a_worker_or_page_trigger(ready_delivery,monkeypatch):
    from app import database
    from threading import Event
    f=ready_delivery;policy(f,True);f.clock[0]+=timedelta(seconds=1);scan(f)
    monkeypatch.setattr(database,'SessionLocal',f.factory)
    monkeypatch.setattr(delivery.time,'time',lambda:f.now()/1000)
    sent=Event();original=delivery.send_once
    def send(*args):
        result=original(*args);sent.set();return result
    monkeypatch.setattr(delivery,'send_once',send)
    service=delivery.DeliveryService()
    try:
        service.start();assert sent.wait(3)
    finally:service.stop()
    assert len(f.calls)==1 and jobs(f)[0]['state']=='SENT'


def test_newer_incomplete_search_retires_unsent_old_automatic_selection(ready_delivery):
    f=ready_delivery;policy(f,True);f.clock[0]+=timedelta(seconds=1);scan(f);reconcile(f)
    scan(f,key=11,status='PARTIAL');reconcile(f)
    assert not f.step() and not f.calls and jobs(f)[0]['state']=='CANCELLED'
    assert policy(f).json()['issue']=='RUN_INCOMPLETE'


def test_legacy_manual_history_counts_but_does_not_block_a_confirmed_later_round_forever(ready_delivery):
    f=ready_delivery;f.post();f.step()
    box=delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    with box._db() as local:local.execute('UPDATE delivery_contexts SET publication_key=NULL')
    policy(f,True);f.clock[0]+=timedelta(seconds=1);scan(f);reconcile(f)
    assert len(jobs(f))==1
    with f.factory() as db:
        record_auction_observation(db,db.get(Car,1),observation_key='confirmed-new-round',observed_at=f.clock[0],sale_id='sale-B',
            schedule={'local_datetime':'2026-10-11T04:00:00','source_timezone':'UTC','event_kind':'START'})
        db.commit()
    scan(f,key=11);reconcile(f)
    assert len(jobs(f))==2 and f.step() and len(f.calls)==2
