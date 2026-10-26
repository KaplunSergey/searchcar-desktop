"""Post-search notices for confirmed schedule events, not another scheduler."""
from datetime import datetime, timezone
import hashlib

from sqlalchemy import select

from . import telegram_automatic as automatic, telegram_dispatch as delivery, telegram_pairing as pairing
from .auction_rounds import _schedule_key
from .auction_time import normalize_schedule, schedule_view
from .auction_read import project_auction_view
from .models import Project, User, ScanRun, ProjectScanRun, AuctionObservation, AuctionScheduleVersion
from .sources.registry import is_auction_source, source_catalog
from .telegram_connection import TelegramError
from .telegram_message import clean, listing_link, utf16


def box_for(config):
    box = automatic.box_for(config)
    with box._db() as local:
        local.execute('''CREATE TABLE IF NOT EXISTS reschedule_policies(owner_id INTEGER,project_id INTEGER,
            scope TEXT,device_scope TEXT,binding_id TEXT,enabled INTEGER,enabled_at_ms INTEGER,
            last_scan_id INTEGER,issue TEXT,PRIMARY KEY(owner_id,project_id))''')
    return box


def saved(box, owner_id, project_id):
    with box._db() as local:
        row = local.execute('SELECT * FROM reschedule_policies WHERE owner_id=? AND project_id=?',
                            (owner_id,project_id)).fetchone()
    return dict(row) if row else None


def bound(policy, config, session, recipient):
    return bool(policy and recipient and policy['scope']==session.scope
        and policy['device_scope']==automatic.device_scope(config) and policy['binding_id']==recipient['binding_id'])


def retire(local, owner_id, project_id, reason):
    automatic.retire_pending(local,owner_id,project_id,reason,mode='RESCHEDULE')


def store_policy(box, config, owner_id, project, session, recipient, enabled, now_ms):
    with box._db() as local:
        retire(local,owner_id,project.id,'RESCHEDULE_DISABLED' if not enabled else 'RECIPIENT_CHANGED')
        local.execute('INSERT OR REPLACE INTO reschedule_policies VALUES(?,?,?,?,?,?,?,?,?)',
            (owner_id,project.id,session.scope,automatic.device_scope(config),recipient['binding_id'] if recipient else None,
             int(enabled),now_ms,None,None))


def status(config, owner_id, project, now_ms):
    if not is_auction_source(project.source_key):
        raise TelegramError('not_an_auction_source',400)
    box = box_for(config); session = delivery._session(config,owner_id,project)
    recipient = session.public()['recipient']; policy = saved(box,owner_id,project.id)
    enabled = bool(policy['enabled']) if policy else True
    issue = policy['issue'] if policy and bound(policy,config,session,recipient) else None
    if enabled and recipient and not automatic.current_zone(config,box,owner_id,now_ms):
        issue = 'CLOCK_REQUIRED'
    with box._db() as local:
        counts = {row[0]:row[1] for row in local.execute('''SELECT CASE WHEN p.active=0 THEN 'CANCELLED' ELSE j.state END,count(*)
            FROM jobs j JOIN delivery_contexts c ON c.job_id=j.id JOIN projects p ON p.job_id=c.job_id AND p.project_id=c.project_id
            WHERE j.owner_id=? AND c.project_id=? AND c.scope=? AND c.mode='RESCHEDULE'
            AND j.bot_id=? AND j.chat_id=? GROUP BY 1''',
            (owner_id,project.id,session.scope,recipient['bot_id'] if recipient else None,recipient['chat_id'] if recipient else None))}
    return dict(project_id=project.id,source_key=project.source_key,enabled=enabled,recipient=recipient,
                counts=counts,issue=issue,max_result_age_minutes=automatic.MAX_RESULT_AGE_MS//60000)


def configure(config, owner_id, project, body, now_ms):
    if not is_auction_source(project.source_key):
        raise TelegramError('not_an_auction_source',400)
    automatic.update_clock(config,owner_id,body.computer_timezone,now_ms)
    box = box_for(config); session = delivery._session(config,owner_id,project); recipient = session.public()['recipient']
    if body.enabled and body.binding_id != (recipient['binding_id'] if recipient else None):
        raise TelegramError('telegram_recipient_changed',409)
    previous = saved(box,owner_id,project.id)
    if not previous or bool(previous['enabled'])!=body.enabled or not bound(previous,config,session,recipient):
        store_policy(box,config,owner_id,project,session,recipient,body.enabled,now_ms)
    return status(config,owner_id,project,now_ms)


def run_issue(db, run, project, policy, now_ms):
    if not automatic.run_valid(db,run): return 'RUN_INCOMPLETE'
    if run.owner_id!=project.owner_id or not automatic.plan_matches(run,project): return 'SEARCH_CHANGED'
    if (int(run.started_at.timestamp()*1000)<policy['enabled_at_ms']
        or not 0<=now_ms-int(run.finished_at.timestamp()*1000)<=automatic.MAX_RESULT_AGE_MS): return 'RESULT_EXPIRED'
    return None


def publication_before(local, owner_id, recipient, key, event):
    return local.execute('''SELECT 1 FROM jobs j JOIN delivery_contexts c ON c.job_id=j.id
        WHERE j.owner_id=? AND j.bot_id=? AND j.chat_id=? AND j.state='SENT' AND c.mode!='RESCHEDULE'
        AND c.publication_key=? AND j.sent_at_ms<? LIMIT 1''',
        (owner_id,recipient['bot_id'],recipient['chat_id'],key,int(event.observed_at.timestamp()*1000))).fetchone() is not None


def refresh_waiting_post(local, project, session, recipient, lot, event):
    if lot['schedule'].get('id') != event.id:
        return  # A later, potentially incomplete observation is not this run's result.
    local.execute('''UPDATE delivery_contexts SET signature=? WHERE project_id=? AND scope=? AND publication_key=?
        AND mode='MANUAL' AND content_signature=? AND job_id IN
        (SELECT id FROM jobs WHERE owner_id=? AND bot_id=? AND chat_id=? AND state='QUEUED')''',
        (delivery.selection_fingerprint(lot,project),project.id,session.scope,delivery.publication_key(lot),
         delivery.selection_content_fingerprint(lot,project),project.owner_id,recipient['bot_id'],recipient['chat_id']))


def confirmed_event(db, run, lot, event_id, policy):
    event = db.get(AuctionScheduleVersion,event_id)
    if (not event or not lot.get('round') or event.round_id!=lot['round']['id'] or event.change_kind!='RESCHEDULED'
        or event.precision not in {'EXACT','DATE_ONLY'} or event.event_kind!='START'
        or int(event.observed_at.timestamp()*1000)<policy['enabled_at_ms']
        or lot['details'].get('auction_phase') in {'LIVE','CLOSED','CANCELLED','SOLD'}): return None
    observation = db.scalar(select(AuctionObservation).where(AuctionObservation.car_id==lot['id'],
        AuctionObservation.observation_key==f'scan:{run.id}:{lot["id"]}',AuctionObservation.applied.is_(True),
        AuctionObservation.schedule_version_id==event.id))
    if not observation or not run.started_at<=observation.observed_at<=run.finished_at: return None
    observed = normalize_schedule((observation.payload or {}).get('schedule'))
    if _schedule_key(observed)!=_schedule_key(event.evidence): return None
    previous = db.scalar(select(AuctionScheduleVersion).where(AuctionScheduleVersion.round_id==event.round_id,
        AuctionScheduleVersion.version==event.version-1))
    if not previous or previous.precision not in {'EXACT','DATE_ONLY'} or previous.event_kind!='START': return None
    return previous,event


def reschedule_message(lot, previous, event, zone, now, locale):
    link = listing_link(lot)
    if not link: raise ValueError('telegram_listing_link_unavailable')
    l = lambda ru,uk: uk if locale=='uk' else ru
    months = l('января февраля марта апреля мая июня июля августа сентября октября ноября декабря',
               'січня лютого березня квітня травня червня липня серпня вересня жовтня листопада грудня').split()
    def when(schedule):
        value = schedule_view(schedule,now=now,computer_timezone=zone)
        day = value['local_date'] if schedule['precision']=='EXACT' else schedule['local_date']
        date = f'{int(day[8:10])} {months[int(day[5:7])-1]} {day[:4]}'
        return (date+', '+value['local_datetime'][11:16]+' · '+zone if schedule['precision']=='EXACT'
                else date+' · '+l('без точного времени','без точного часу')+' · '+schedule['source_timezone'])
    text = ''; entities = []
    def add(value, kind=None, url=None):
        nonlocal text
        offset=utf16(text);text+=value
        if kind: entities.append(dict(type=kind,offset=offset,length=utf16(value),**({'url':url} if url else {})))
    add('🗓 '); add(l('Аукцион перенесён','Аукціон перенесено'),'bold'); add('\n\n🚙 ')
    add(clean(lot.get('title'),160) or l('Без названия','Без назви'),'bold'); add('\n')
    name=next(item['name'] for item in source_catalog() if item['key']==lot['source_key'])
    add(f'🏷 {name} · № {clean(lot["source_listing_id"],50)}\n\n')
    add(l('Было:','Було:'),'bold');add(' '+when(previous.evidence)+'\n')
    add(l('Стало:','Стало:'),'bold');add(' '+when(event.evidence)+'\n\n🔗 ')
    add(l('Открыть автомобиль','Відкрити автомобіль'),'text_link',link)
    return dict(text=text,photo=None,entities=entities)


def prepare_message(config, box, db, owner, project, session, recipient, context, now_ms):
    current,issue=pairing.configuration()
    policy=saved(box,owner.id,project.id)
    if issue or current!=config or not policy or not policy['enabled'] or not bound(policy,config,session,recipient): return None
    zone=automatic.current_zone(config,box,owner.id,now_ms)
    run=db.get(ScanRun,context['scan_id'])
    if not zone or run_issue(db,run,project,policy,now_ms): return None
    latest=db.scalar(select(ScanRun).join(ProjectScanRun,ProjectScanRun.scan_run_id==ScanRun.id).where(
        ScanRun.owner_id==owner.id,ProjectScanRun.project_id==project.id,ScanRun.finished_at.is_not(None))
        .order_by(ScanRun.id.desc()).limit(1))
    if run_issue(db,latest,project,policy,now_ms): return None
    now=datetime.fromtimestamp(now_ms/1000,timezone.utc)
    view=project_auction_view(db,project,now=now,computer_timezone=zone,collection='active')
    lot=next((lot for lot in view['lots'] if lot['id']==context['car_id']),None)
    if not lot or delivery.publication_key(lot)!=context['publication_key']: return None
    versions=confirmed_event(db,run,lot,context['event_id'],policy)
    if not versions or versions[1].version!=context['event_version']: return None
    with box._db() as local:
        if not publication_before(local,owner.id,recipient,context['publication_key'],versions[1]): return None
    try:
        return reschedule_message(lot,*versions,zone,now,context['locale'])
    except ValueError:
        return None  # No HTTP attempt occurred; an invalid listing is not an uncertain send.


def reconcile(config, factory, *, now_ms):
    if not delivery.enabled(): return
    current,issue=pairing.configuration()
    if issue or current!=config: return
    box=box_for(config)
    with factory() as db:
        projects=list(db.scalars(select(Project).join(User,User.id==Project.owner_id).where(User.status=='ACTIVE')))
        for project in projects:
            if not is_auction_source(project.source_key): continue
            with pairing.LOCK:
                session=delivery._session(config,project.owner_id,project);recipient=session.public()['recipient']
                if not recipient: continue
                policy=saved(box,project.owner_id,project.id)
                if not policy or not bound(policy,config,session,recipient):
                    store_policy(box,config,project.owner_id,project,session,recipient,
                                 bool(policy['enabled']) if policy else True,now_ms)
                    policy=saved(box,project.owner_id,project.id)
                # Old receipts prove only that the publication was known by enrollment,
                # never that it preceded an older schedule event.
                with box._db() as local:
                    local.execute("UPDATE jobs SET sent_at_ms=? WHERE owner_id=? AND state='SENT' AND sent_at_ms IS NULL",
                                  (now_ms,project.owner_id))
                if not policy['enabled']: continue
                zone=automatic.current_zone(config,box,project.owner_id,now_ms)
                if not zone: continue
                runs=list(db.scalars(select(ScanRun).join(ProjectScanRun,ProjectScanRun.scan_run_id==ScanRun.id).where(
                    ScanRun.owner_id==project.owner_id,ScanRun.kind=='PROJECTS',ProjectScanRun.project_id==project.id,
                    ScanRun.finished_at.is_not(None),ScanRun.id>(policy['last_scan_id'] or 0),
                    ScanRun.started_at>=datetime.fromtimestamp(policy['enabled_at_ms']/1000,timezone.utc)).order_by(ScanRun.id)))
                for run in runs:
                    issue=run_issue(db,run,project,policy,now_ms)
                    if issue:
                        with box._db() as local:
                            retire(local,project.owner_id,project.id,issue)
                    else:
                        now=datetime.fromtimestamp(now_ms/1000,timezone.utc)
                        lots=project_auction_view(db,project,now=now,computer_timezone=zone,collection='active')['lots']
                        for lot in lots:
                            observation=db.scalar(select(AuctionObservation).where(AuctionObservation.car_id==lot['id'],
                                AuctionObservation.observation_key==f'scan:{run.id}:{lot["id"]}',AuctionObservation.applied.is_(True)))
                            versions=confirmed_event(db,run,lot,observation.schedule_version_id,policy) if observation else None
                            if not versions: continue
                            previous,event=versions;key=delivery.publication_key(lot)
                            try:reschedule_message(lot,previous,event,zone,now,db.get(User,project.owner_id).preferred_locale)
                            except ValueError:continue
                            with box._db() as local:
                                refresh_waiting_post(local,project,session,recipient,lot,event)
                                if not publication_before(local,project.owner_id,recipient,key,event): continue
                                job=box.enqueue(owner_id=project.owner_id,project_id=project.id,bot_id=recipient['bot_id'],destination=recipient,
                                    notification_key='reschedule:v1:'+key+':'+event.id,now_ms=now_ms,_connection=local)
                                if job['state']=='CANCELLED' and job['error_code'] in {'RUN_INCOMPLETE','RESULT_EXPIRED','RESCHEDULE_DISABLED','TELEGRAM_CONTEXT_CHANGED'}:
                                    local.execute("UPDATE jobs SET state='QUEUED',attempt_id=NULL,error_code=NULL,not_before_ms=? WHERE id=?",(now_ms,job['id']))
                                local.execute('''INSERT OR REPLACE INTO delivery_contexts
                                    (job_id,project_id,scope,car_id,signature,locale,timezone,collection,mode,scan_id,publication_key,event_id,event_version)
                                    VALUES(?,?,?,?,?,?,?,'active','RESCHEDULE',?,?,?,?)''',
                                    (job['id'],project.id,session.scope,lot['id'],event.id,db.get(User,project.owner_id).preferred_locale,
                                     zone,run.id,key,event.id,event.version))
                                local.execute('INSERT OR IGNORE INTO delivery_attempts(job_id) VALUES(?)',(job['id'],))
                    with box._db() as local:
                        local.execute('UPDATE reschedule_policies SET last_scan_id=?,issue=? WHERE owner_id=? AND project_id=?',
                                      (run.id,issue,project.owner_id,project.id))
