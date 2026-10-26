"""Local opt-in Today posting. Observes committed runs, never initiates a search."""
from datetime import date, datetime, timezone
import hashlib
import json
from pydantic import BaseModel, ConfigDict, StrictBool, Field
from sqlalchemy import select

from . import telegram_dispatch as delivery, telegram_pairing as pairing
from .telegram_connection import TelegramError
from .auction_time import _timezone, normalize_schedule, schedule_view, _day_bounds
from .auction_read import project_auction_view
from .models import Project, User, ScanRun, ProjectScanRun, ProjectCar, AuctionObservation, AuctionRound
from .sources.registry import is_auction_source

# An old result must not become a new automatic publication after downtime/429.
MAX_RESULT_AGE_MS = 30 * 60 * 1000
CLOCK_AGE_MS = 5 * 60 * 1000
PLAN_FIELDS = ('source_key','search_url','search_config','scan_mode','search_page_mode','price_filter_mode',
               'price_min_krw','price_max_krw','price_filter_revision','price_filter_baseline_revision')

class AutomaticPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool
    computer_timezone: str = Field(min_length=1, max_length=80)
    binding_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')

class AutomaticClock(BaseModel):
    model_config = ConfigDict(extra='forbid')
    computer_timezone: str = Field(min_length=1, max_length=80)

def box_for(config):
    box = delivery.DeliveryBox(config[0] / 'notifications' / 'telegram-outbox.sqlite3')
    with box._db() as local:
        local.execute('''CREATE TABLE IF NOT EXISTS automatic_policies(owner_id INTEGER,project_id INTEGER,
            scope TEXT,device_scope TEXT,binding_id TEXT,enabled INTEGER,enabled_at_ms INTEGER,
            last_scan_id INTEGER,last_batch_id TEXT,issue TEXT,PRIMARY KEY(owner_id,project_id))''')
        local.execute('''CREATE TABLE IF NOT EXISTS automatic_clocks(owner_id INTEGER,device_scope TEXT,
            timezone TEXT,updated_at_ms INTEGER,PRIMARY KEY(owner_id,device_scope))''')
    return box

def device_scope(config):
    return hashlib.sha256(json.dumps([config[1],config[2]],sort_keys=True).encode()).hexdigest()

def update_clock(config, owner_id, zone, now_ms):
    try:
        _timezone(zone)
    except ValueError:
        raise TelegramError('telegram_timezone_invalid',422) from None
    box = box_for(config)
    with box._db() as local:
        local.execute('INSERT OR REPLACE INTO automatic_clocks VALUES(?,?,?,?)',
            (owner_id,device_scope(config),zone,now_ms))

def current_zone(config, box, owner_id, now_ms):
    with box._db() as local:
        clock = local.execute('SELECT * FROM automatic_clocks WHERE owner_id=? AND device_scope=?',
            (owner_id,device_scope(config))).fetchone()
    return clock['timezone'] if clock and 0 <= now_ms-clock['updated_at_ms'] <= CLOCK_AGE_MS else None

def saved_policy(box, owner_id, project_id):
    with box._db() as local:
        row = local.execute('SELECT * FROM automatic_policies WHERE owner_id=? AND project_id=?',(owner_id,project_id)).fetchone()
    return dict(row) if row else None

def policy_matches(policy, config, session, recipient):
    return bool(policy and policy['enabled'] and policy['scope']==session.scope
        and policy['device_scope']==device_scope(config) and recipient
        and policy['binding_id']==recipient['binding_id'])

def retire_pending(local, owner_id, project_id, code, *, mode='AUTO'):
    rows=local.execute('''SELECT c.job_id FROM delivery_contexts c JOIN jobs j ON j.id=c.job_id
        WHERE j.owner_id=? AND c.project_id=? AND c.mode=? AND j.state='QUEUED' ''',(owner_id,project_id,mode)).fetchall()
    for row in rows:
        local.execute('UPDATE projects SET active=0 WHERE job_id=? AND project_id=?',(row[0],project_id))
        if not local.execute('SELECT 1 FROM projects WHERE job_id=? AND active=1',(row[0],)).fetchone():
            local.execute("UPDATE jobs SET state='CANCELLED',error_code=? WHERE id=?",(code,row[0]))

def reset_after_restore(root):
    path=root/'notifications'/'telegram-outbox.sqlite3'
    if not path.exists():
        return
    box=delivery.DeliveryBox(path)
    with box._db() as local:
        if local.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automatic_policies'").fetchone():
            local.execute("UPDATE automatic_policies SET enabled=0,issue='DATA_RESTORED'")
        if local.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reschedule_policies'").fetchone():
            local.execute("UPDATE reschedule_policies SET enabled=0,issue='DATA_RESTORED'")
        local.execute("""UPDATE projects SET active=0 WHERE job_id IN
            (SELECT job_id FROM delivery_contexts WHERE mode IN ('AUTO','RESCHEDULE'))""")
        local.execute("""UPDATE jobs SET state='CANCELLED',error_code='DATA_RESTORED'
            WHERE state='QUEUED' AND NOT EXISTS(SELECT 1 FROM projects p WHERE p.job_id=jobs.id AND p.active=1)
            AND id IN(SELECT job_id FROM delivery_contexts WHERE mode IN ('AUTO','RESCHEDULE'))""")

def status(config, owner_id, project, now_ms):
    if not is_auction_source(project.source_key):
        raise TelegramError('not_an_auction_source',400)
    box = box_for(config); session = delivery._session(config,owner_id,project)
    recipient = session.public()['recipient']; policy = saved_policy(box,owner_id,project.id)
    active = policy_matches(policy,config,session,recipient)
    batch = box.batch(policy['last_batch_id'],owner_id,project.id,session.scope) if active and policy['last_batch_id'] else None
    issue = policy['issue'] if active else 'RECIPIENT_CHANGED' if policy and policy['enabled'] else None
    if active and not current_zone(config,box,owner_id,now_ms):
        issue = 'CLOCK_REQUIRED'
    return dict(project_id=project.id,source_key=project.source_key,enabled=active,recipient=recipient,batch=batch,issue=issue,
        max_result_age_minutes=MAX_RESULT_AGE_MS//60000)

def configure(config, owner_id, project, body, now_ms):
    if not is_auction_source(project.source_key):
        raise TelegramError('not_an_auction_source',400)
    update_clock(config,owner_id,body.computer_timezone,now_ms)
    box=box_for(config); session=delivery._session(config,owner_id,project)
    recipient=session.public()['recipient']
    if body.enabled and (not recipient or not session.public()['delivery_enabled']):
        raise TelegramError('telegram_recipient_required',409)
    if body.enabled and body.binding_id != recipient['binding_id']:
        raise TelegramError('telegram_recipient_changed',409)
    previous=saved_policy(box,owner_id,project.id)
    with box._db() as local:
        if not body.enabled:
            # Only this project's automatic references; shared/manual work is not recalled.
            retire_pending(local,owner_id,project.id,'AUTO_DISABLED')
        if not body.enabled or not policy_matches(previous,config,session,recipient):
            local.execute('''INSERT OR REPLACE INTO automatic_policies VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (owner_id,project.id,session.scope,device_scope(config),recipient['binding_id'] if recipient else None,
                 int(body.enabled),now_ms,None,None,None))
    return status(config,owner_id,project,now_ms)

def run_valid(db, run):
    if not run or run.kind!='PROJECTS' or run.status!='SUCCEEDED' or not run.started_at or not run.finished_at:
        return False
    ids=(run.payload or {}).get('project_ids') or []
    results=list(db.scalars(select(ProjectScanRun).where(ProjectScanRun.scan_run_id==run.id)))
    plans=(run.payload or {}).get('project_plans') or []
    return bool(ids and len(results)==len(set(ids)) and set(ids)=={r.project_id for r in results}
        and len(plans)==len(ids) and {p.get('project_id') for p in plans}==set(ids)
        and all(p.get('search_page_mode')=='ALL_PAGES' for p in plans)
        and all(r.status=='SUCCEEDED' and not r.error_code for r in results))

def plan_matches(run, project):
    plan=next((p for p in (run.payload or {}).get('project_plans',[]) if p.get('project_id')==project.id),None)
    return bool(plan and plan.get('search_page_mode')=='ALL_PAGES'
        and all(key in plan and plan[key]==getattr(project,key) for key in PLAN_FIELDS))

def eligible(lot):
    return bool(delivery.publication_key(lot) and lot['time']['is_today'] is True
        and lot['time']['event_kind']=='START' and lot['time']['precision'] in {'EXACT','DATE_ONLY'}
        and lot['details'].get('auction_phase') not in {'LIVE','CLOSED','CANCELLED','SOLD'})

def manual_duplicate(local, owner_id, recipient, key, car_id, round_created_at):
    # Legacy jobs lack round keys. Only pre-round history can safely be ignored.
    return local.execute('''SELECT 1 FROM delivery_contexts c JOIN jobs j ON j.id=c.job_id
        WHERE j.owner_id=? AND j.bot_id=? AND j.chat_id=? AND c.mode='MANUAL'
        AND j.state IN ('SENT','UNKNOWN','IN_FLIGHT','QUEUED')
        AND (c.publication_key=? OR (c.publication_key IS NULL AND c.car_id=? AND j.created_at_ms>=?)) LIMIT 1''',
        (owner_id,recipient['bot_id'],recipient['chat_id'],key,car_id,int(round_created_at.timestamp()*1000))).fetchone()

def confirmed_lot(db, run, project, lot, view):
    relation=db.get(ProjectCar,(project.id,lot['id']))
    observation=db.scalar(select(AuctionObservation).where(AuctionObservation.car_id==lot['id'],
        AuctionObservation.round_id==lot['round']['id'],AuctionObservation.observation_key==f'scan:{run.id}:{lot["id"]}',
        AuctionObservation.applied.is_(True))) if lot.get('round') else None
    if (not observation or not relation or not relation.last_found_at
        or not run.started_at<=relation.last_found_at<=run.finished_at
        or not run.started_at<=observation.observed_at<=run.finished_at):
        return None
    observed=normalize_schedule((observation.payload or {}).get('schedule'))
    stored=lot['schedule']
    if observed['event_kind']!='START' or stored['event_kind']!='START':
        return None
    if observed['precision']=='EXACT':
        if stored['precision']!='EXACT' or datetime.fromisoformat(observed['utc_at'])!=datetime.fromisoformat(stored['utc_at']):
            return None
    elif observed['precision']=='DATE_ONLY':
        if stored['precision']=='EXACT':
            start,end=_day_bounds(date.fromisoformat(observed['local_date']),_timezone(observed['source_timezone']))
            if not start<=datetime.fromisoformat(stored['utc_at'])<end:
                return None
        elif stored['precision']!='DATE_ONLY' or any(observed[k]!=stored[k] for k in ('local_date','source_timezone')):
            return None
    else:
        return None
    # A freshly confirmed date must not borrow an exact time from an older observation.
    schedule={**stored,**observed}
    return {**lot,'schedule':schedule,'time':schedule_view(schedule,now=datetime.fromisoformat(view['generated_at']),computer_timezone=view['computer_timezone'])}

def validate_attempt(config, box, db, owner, project, session, recipient, context, now_ms):
    current,issue=pairing.configuration()
    if issue or current!=config:
        return None
    policy=saved_policy(box,owner.id,project.id)
    if not policy_matches(policy,config,session,recipient):
        return None
    zone=current_zone(config,box,owner.id,now_ms)
    run=db.get(ScanRun,context['scan_id'])
    if (not zone or not run_valid(db,run) or run.owner_id!=owner.id or not plan_matches(run,project)
        or int(run.started_at.timestamp()*1000)<policy['enabled_at_ms']
        or not 0<=now_ms-int(run.finished_at.timestamp()*1000)<=MAX_RESULT_AGE_MS):
        return None
    with box._db() as local:
        round_record=db.scalar(select(AuctionRound).where(AuctionRound.car_id==context['car_id'],AuctionRound.is_current.is_(True)))
        duplicate=not round_record or manual_duplicate(local,owner.id,recipient,context['publication_key'],context['car_id'],round_record.created_at)
    if duplicate:
        return None
    return zone

def reconcile(config, factory, *, now_ms):
    """Dispatcher-owned recovery checkpoint after the main database commit.

    No network calls: failure here cannot fail or roll back the completed search.
    """
    if not delivery.enabled():
        return
    current,issue=pairing.configuration()
    if issue or current!=config:
        return
    box=box_for(config)
    with box._db() as local:
        policies=[dict(row) for row in local.execute('SELECT * FROM automatic_policies WHERE enabled=1')]
    for policy in policies:
        with pairing.LOCK, factory() as db:
            project=db.get(Project,policy['project_id']);owner=db.get(User,policy['owner_id'])
            if not project or not owner or project.owner_id!=owner.id or owner.status!='ACTIVE' or not is_auction_source(project.source_key):
                continue
            session=delivery._session(config,owner.id,project);recipient=session.public()['recipient']
            if not policy_matches(policy,config,session,recipient):
                continue
            zone=current_zone(config,box,owner.id,now_ms)
            if not zone:
                continue
            # Latest completed run only: never send a backlog of superseded search results.
            run=db.scalar(select(ScanRun).join(ProjectScanRun,ProjectScanRun.scan_run_id==ScanRun.id)
                .where(ScanRun.owner_id==owner.id,ScanRun.kind=='PROJECTS',ProjectScanRun.project_id==project.id,
                    ScanRun.finished_at.is_not(None),ScanRun.started_at>=datetime.fromtimestamp(policy['enabled_at_ms']/1000,timezone.utc))
                .order_by(ScanRun.finished_at.desc(),ScanRun.id.desc()).limit(1))
            if not run or run.id==policy['last_scan_id']:
                continue
            issue=None; lots=[]
            if not run_valid(db,run):issue='RUN_INCOMPLETE'
            elif not plan_matches(run,project):issue='SEARCH_CHANGED'
            elif not 0<=now_ms-int(run.finished_at.timestamp()*1000)<=MAX_RESULT_AGE_MS:issue='RESULT_EXPIRED'
            else:
                view=project_auction_view(db,project,now=datetime.fromtimestamp(now_ms/1000,timezone.utc),computer_timezone=zone,collection='today')
                for lot in view['lots']:
                    lot=confirmed_lot(db,run,project,lot,view)
                    if not lot or not eligible(lot):
                        continue
                    try:delivery.auction_message(lot,view,owner.preferred_locale)
                    except ValueError:continue
                    lots.append(lot)
            batch_id=hashlib.sha256(f'auto:{owner.id}:{project.id}:{session.scope}:{run.id}:{policy["enabled_at_ms"]}'.encode()).hexdigest()[:32]
            with box._db() as local:
                # Do not race a user disabling or replacing the recipient during projection.
                current=local.execute('SELECT * FROM automatic_policies WHERE owner_id=? AND project_id=?',(owner.id,project.id)).fetchone()
                if not current or not current['enabled'] or current['enabled_at_ms']!=policy['enabled_at_ms']:
                    continue
                retire_pending(local,owner.id,project.id,'SUPERSEDED_SEARCH')
                local.execute('INSERT OR IGNORE INTO delivery_batches VALUES(?,?,?,?,?,?,?)',
                    (batch_id,owner.id,project.id,project.source_key,session.scope,batch_id,
                     json.dumps({key:recipient[key] for key in ('title','chat_id','chat_type')})))
                for lot in lots:
                    key=delivery.publication_key(lot)
                    # A known/uncertain manual publication counts too. Old history is conservative.
                    round_record=db.get(AuctionRound,lot['round']['id'])
                    prior=manual_duplicate(local,owner.id,recipient,key,lot['id'],round_record.created_at)
                    if prior:continue
                    job=box.enqueue(owner_id=owner.id,project_id=project.id,bot_id=recipient['bot_id'],destination=recipient,
                        notification_key='auto:v1:'+key,now_ms=now_ms,_connection=local)
                    if job['state']=='CANCELLED':
                        # New successful run only; never revive uncertain/failed/sent attempts.
                        local.execute("UPDATE jobs SET state='QUEUED',attempt_id=NULL,error_code=NULL,not_before_ms=? WHERE id=?",(now_ms,job['id']))
                        local.execute('UPDATE delivery_attempts SET attempts=0,force_text=0 WHERE job_id=?',(job['id'],))
                    local.execute('INSERT OR IGNORE INTO delivery_items VALUES(?,?,?,0)',(batch_id,lot['id'],job['id']))
                    local.execute('''INSERT OR REPLACE INTO delivery_contexts
                        (job_id,project_id,scope,car_id,signature,locale,timezone,collection,mode,scan_id,publication_key,auction_at,content_signature)
                        VALUES(?,?,?,?,?,?,?,'today','AUTO',?,?,?,?)''',
                        (job['id'],project.id,session.scope,lot['id'],delivery.selection_fingerprint(lot,project),owner.preferred_locale,zone,run.id,key,
                         lot['schedule'].get('utc_at'),delivery.selection_content_fingerprint(lot,project)))
                    local.execute('INSERT OR IGNORE INTO delivery_attempts(job_id) VALUES(?)',(job['id'],))
                local.execute('UPDATE automatic_policies SET last_scan_id=?,last_batch_id=?,issue=? WHERE owner_id=? AND project_id=?',
                    (run.id,batch_id,issue,owner.id,project.id))
