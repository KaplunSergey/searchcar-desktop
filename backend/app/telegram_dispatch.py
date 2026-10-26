"""Local delivery for manual actions and opt-in Today; no server scheduler."""
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from threading import Event, Lock, Thread
import time
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from fastapi import HTTPException, Request

from . import telegram_pairing as pairing
from .telegram_connection import TelegramError
from .telegram_delivery import CHAT_TYPES, TelegramOutbox, _positive, _clock
from .telegram_message import auction_message, fingerprint
from .telegram_sender import send_once
from .auction_read import project_auction_view
from .models import Project, User
from .maintenance import maintenance_active

TERMINAL = {'SENT', 'FAILED', 'UNKNOWN', 'CANCELLED'}

class ManualDelivery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    car_ids: list[Annotated[StrictInt, Field(gt=0, lt=2**52)]] = Field(min_length=1, max_length=1000)
    collection: Literal['active','today','future','undated','archive']
    computer_timezone: str = Field(min_length=1, max_length=80)

async def delivery_body(request: Request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 65536:
            raise HTTPException(413, 'telegram_request_too_large')
    try:
        value = ManualDelivery.model_validate(json.loads(raw))
        if len(set(value.car_ids)) != len(value.car_ids):
            raise ValueError()
        return value.model_dump()
    except (ValueError, TypeError, ValidationError):
        raise HTTPException(422, 'telegram_request_invalid') from None

def enabled():
    return os.environ.get('SEARCHCAR_TELEGRAM_DELIVERY_ENABLED') == '1'

def configuration():
    if not enabled():
        raise TelegramError('telegram_delivery_not_configured')
    config, issue = pairing.configuration()
    if issue:
        raise TelegramError(issue)
    return config

def scope(project):
    return [project.source_key, project.created_at.isoformat()]

def selection_fingerprint(lot, project):
    return hashlib.sha256(json.dumps([fingerprint(lot), project.search_url, project.search_config,
        project.price_filter_revision], sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def selection_content_fingerprint(lot, project):
    details = {key:value for key,value in lot['details'].items() if key != 'schedule'}
    return selection_fingerprint({**lot,'schedule':{},'details':details},project)

def publication_key(lot):
    round_id = (lot.get('round') or {}).get('id')
    if not round_id:
        return None
    return hashlib.sha256(json.dumps([lot['source_key'], lot['source_listing_id'], round_id]).encode()).hexdigest()

class DeliveryBox(TelegramOutbox):
    def __init__(self, path):
        super().__init__(path)
        with self._db() as db:
            for sql in [
                '''CREATE TABLE IF NOT EXISTS delivery_batches(id TEXT PRIMARY KEY,owner_id INTEGER,
                    project_id INTEGER,source_key TEXT,scope TEXT,request_hash TEXT,recipient TEXT)''',
                '''CREATE TABLE IF NOT EXISTS delivery_items(batch_id TEXT,car_id INTEGER,job_id TEXT,previously_sent INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(batch_id,car_id))''',
                '''CREATE TABLE IF NOT EXISTS delivery_contexts(job_id TEXT,project_id INTEGER,scope TEXT,
                    car_id INTEGER,signature TEXT,locale TEXT,timezone TEXT,collection TEXT,
                    PRIMARY KEY(job_id,project_id))''',
                '''CREATE TABLE IF NOT EXISTS delivery_attempts(job_id TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL DEFAULT 0,force_text INTEGER NOT NULL DEFAULT 0)''',
            ]:
                db.execute(sql)
            if 'previously_sent' not in {row[1] for row in db.execute('PRAGMA table_info(delivery_items)')}:
                db.execute('ALTER TABLE delivery_items ADD COLUMN previously_sent INTEGER NOT NULL DEFAULT 0')
            columns = {row[1] for row in db.execute('PRAGMA table_info(delivery_contexts)')}
            for name, declaration in [('mode', "TEXT NOT NULL DEFAULT 'MANUAL'"), ('scan_id', 'INTEGER'), ('publication_key', 'TEXT'), ('auction_at', 'TEXT'), ('event_id', 'TEXT'), ('event_version', 'INTEGER'), ('content_signature','TEXT')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE delivery_contexts ADD COLUMN {name} {declaration}')
            if 'sent_at_ms' not in {row[1] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN sent_at_ms INTEGER')

    @staticmethod
    def _waiting(db):
        rows = list(TelegramOutbox._waiting(db))
        automatic = {row['id']: row for row in db.execute('''SELECT j.id,j.owner_id,j.bot_id,j.chat_id,
            min(coalesce(c.auction_at,'9999')) AS auction_at FROM jobs j JOIN delivery_contexts c ON c.job_id=j.id
            JOIN projects p ON p.job_id=c.job_id AND p.project_id=c.project_id
            WHERE j.state='QUEUED' AND c.mode='AUTO' AND p.active=1 GROUP BY j.id''')}
        groups = {}
        for index, row in enumerate(rows):
            if row['id'] in automatic:
                item = automatic[row['id']]
                groups.setdefault((item['owner_id'],item['bot_id'],item['chat_id']),[]).append(index)
        for positions in groups.values():
            ordered = sorted((rows[index] for index in positions),key=lambda row:(automatic[row['id']]['auction_at'],row['id']))
            for index,row in zip(positions,ordered):
                rows[index]=row
        # A later change of the same round cannot overtake its pending predecessor.
        changes = {row['id']: dict(row) for row in db.execute('''SELECT j.id,j.owner_id,j.bot_id,j.chat_id,
            c.publication_key,min(c.event_version) AS event_version FROM jobs j JOIN delivery_contexts c ON c.job_id=j.id
            JOIN projects p ON p.job_id=c.job_id AND p.project_id=c.project_id
            WHERE j.state IN ('QUEUED','IN_FLIGHT') AND c.mode='RESCHEDULE' AND p.active=1 GROUP BY j.id''')}
        first = {}
        for item in changes.values():
            key = tuple(item[k] for k in ('owner_id','bot_id','chat_id','publication_key'))
            first[key] = min(first.get(key, item['event_version']), item['event_version'])
        rows = [row for row in rows if row['id'] not in changes or changes[row['id']]['event_version'] ==
                first[tuple(changes[row['id']][k] for k in ('owner_id','bot_id','chat_id','publication_key'))]]
        return rows

    def confirmed_sent(self, job, *, message_id, now_ms):
        _positive(message_id); _clock(now_ms)
        with self._db() as local:
            self._attempt(local, job['id'], job['attempt_id'])
            local.execute("UPDATE jobs SET state='SENT',message_id=?,attempt_id=NULL,sent_at_ms=? WHERE id=?",
                          (message_id, now_ms, job['id']))

    def history(self, owner_id, project_id, session_scope):
        with self._db() as db:
            return [row[0] for row in db.execute('''SELECT DISTINCT c.car_id FROM delivery_contexts c
                JOIN jobs j ON j.id=c.job_id WHERE j.owner_id=? AND c.project_id=? AND c.scope=? AND j.state='SENT' ORDER BY c.car_id''',
                (owner_id, project_id, session_scope))]

    def batch(self, batch_id, owner_id, project_id, session_scope):
        with self._db() as db:
            row = db.execute('SELECT * FROM delivery_batches WHERE id=? AND owner_id=? AND project_id=? AND scope=?',
                             (batch_id, owner_id, project_id, session_scope)).fetchone()
            if not row:
                raise TelegramError('telegram_delivery_not_found', 404)
            jobs = []
            for item in db.execute('SELECT * FROM delivery_items WHERE batch_id=? ORDER BY car_id', (batch_id,)):
                job = self._job(db, item['job_id'])
                active = project_id in job['active_project_ids']
                jobs.append(dict(id=job['id'], car_id=item['car_id'], previously_sent=bool(item['previously_sent']), state=job['state'] if active else 'CANCELLED',
                    not_before_ms=job['not_before_ms'], message_id=job['message_id'], error_code=job['error_code']))
            return dict(id=batch_id, project_id=project_id, source_key=row['source_key'], recipient=json.loads(row['recipient']), jobs=jobs)

    def recover(self):
        # Only the holder of the dispatch file lock may do this at startup.
        with self._db() as db:
            db.execute("UPDATE jobs SET state='UNKNOWN',attempt_id=NULL,error_code='TELEGRAM_RESULT_UNKNOWN' WHERE state='IN_FLIGHT'")

    def finish(self, job, state, code):
        with self._db() as db:
            self._attempt(db, job['id'], job['attempt_id'])
            db.execute('UPDATE jobs SET state=?,error_code=?,attempt_id=NULL WHERE id=?', (state, code, job['id']))

def _session(config, owner_id, project):
    return pairing.PairingSession(config, owner_id, project.id, scope(project))

def enqueue(config, owner, project, db, body, *, now):
    from .auction_time import _timezone
    try:
        _timezone(body['computer_timezone'])
    except ValueError:
        raise TelegramError('telegram_timezone_invalid', 422) from None
    session = _session(config, owner.id, project)
    box = DeliveryBox(session.box.path)
    request_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    with box._db() as local:
        previous = local.execute('SELECT * FROM delivery_batches WHERE id=?', (body['intent_id'],)).fetchone()
    if previous:
        if (previous['owner_id'], previous['project_id'], previous['scope'], previous['request_hash']) != (owner.id, project.id, session.scope, request_hash):
            raise TelegramError('telegram_delivery_changed', 409)
        return box.batch(body['intent_id'], owner.id, project.id, session.scope)
    recipient = session.public()['recipient']
    if not recipient or recipient['chat_type'] not in CHAT_TYPES:
        raise TelegramError('telegram_recipient_required', 409)
    if recipient['chat_type'] != 'private' and os.environ.get('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED') != '1':
        raise TelegramError('telegram_chat_pairing_disabled')
    view = project_auction_view(db, project, now=now, computer_timezone=body['computer_timezone'], collection=body['collection'])
    selected = {lot['id']: lot for lot in view['lots'] if lot['id'] in body['car_ids']}
    if len(selected) != len(body['car_ids']):
        raise TelegramError('telegram_selection_changed', 409)
    try:
        for lot in selected.values():
            auction_message(lot, view, owner.preferred_locale)
    except ValueError:
        raise TelegramError('telegram_listing_link_unavailable', 409) from None
    with box._db() as local:
        # Atomic batch: no worker can send the first item of a partially saved selection.
        if local.execute('SELECT 1 FROM delivery_batches WHERE id=?', (body['intent_id'],)).fetchone():
            raise TelegramError('telegram_delivery_changed', 409)
        local.execute('INSERT INTO delivery_batches VALUES(?,?,?,?,?,?,?)', (body['intent_id'], owner.id, project.id, project.source_key,
            session.scope, request_hash, json.dumps({key: recipient[key] for key in ('title','chat_id','chat_type')})))
        for car_id, lot in selected.items():
            signature = selection_fingerprint(lot, project)
            previous_sent = local.execute('''SELECT 1 FROM delivery_contexts c JOIN jobs j ON j.id=c.job_id
                WHERE j.owner_id=? AND c.project_id=? AND c.scope=? AND c.car_id=? AND j.state='SENT' LIMIT 1''',
                (owner.id, project.id, session.scope, car_id)).fetchone() is not None
            job = box.enqueue(owner_id=owner.id, project_id=project.id, bot_id=recipient['bot_id'], destination=recipient,
                notification_key='manual:v2:'+body['intent_id']+':'+signature, now_ms=int(now.timestamp()*1000), _connection=local)
            # Same HTTP intent is idempotent; a new explicit user action intentionally sends again.
            local.execute('INSERT INTO delivery_items VALUES(?,?,?,?)', (body['intent_id'], car_id, job['id'], previous_sent))
            local.execute('''INSERT OR REPLACE INTO delivery_contexts
                (job_id,project_id,scope,car_id,signature,locale,timezone,collection,publication_key,content_signature) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (job['id'], project.id, session.scope, car_id, signature, owner.preferred_locale, body['computer_timezone'], body['collection'], publication_key(lot),selection_content_fingerprint(lot,project)))
            local.execute('INSERT OR IGNORE INTO delivery_attempts(job_id) VALUES(?)', (job['id'],))
    return box.batch(body['intent_id'], owner.id, project.id, session.scope)

def read_batch(config, owner_id, project, batch_id):
    session = _session(config, owner_id, project)
    return DeliveryBox(session.box.path).batch(batch_id, owner_id, project.id, session.scope)

def cancel_batch(config, owner_id, project, batch_id):
    session = _session(config, owner_id, project); box = DeliveryBox(session.box.path)
    box.batch(batch_id, owner_id, project.id, session.scope)
    with box._db() as local:
        for row in local.execute('SELECT job_id FROM delivery_items WHERE batch_id=?', (batch_id,)).fetchall():
            job = box._job(local, row['job_id'])
            if job['state'] != 'QUEUED':
                continue  # An in-flight HTTP request cannot be recalled.
            local.execute('UPDATE projects SET active=0 WHERE job_id=? AND project_id=?', (job['id'], project.id))
            if not local.execute('SELECT 1 FROM projects WHERE job_id=? AND active=1', (job['id'],)).fetchone():
                local.execute("UPDATE jobs SET state='CANCELLED',error_code='USER_CANCELLED' WHERE id=?", (job['id'],))
    return box.batch(batch_id, owner_id, project.id, session.scope)

def step(config, factory, *, now_ms, clock_ms=None):
    """One local admitted attempt, with a controlled clock for deterministic checks."""
    box = DeliveryBox(config[0] / 'notifications' / 'telegram-outbox.sqlite3')
    job = box.claim_ready(now_ms)
    if not job:
        return False
    try:
        selected = None
        def discard(project_id):
            with box._db() as local:
                local.execute('UPDATE projects SET active=0 WHERE job_id=? AND project_id=?', (job['id'], project_id))
        for project_id in job['active_project_ids']:
            with box._db() as local:
                context = local.execute('SELECT * FROM delivery_contexts WHERE job_id=? AND project_id=?', (job['id'], project_id)).fetchone()
            if not context:
                discard(project_id)
                continue
            with factory() as db:
                project = db.get(Project, project_id); owner = db.get(User, job['owner_id'])
                if not project or project.owner_id != job['owner_id'] or not owner or owner.status != 'ACTIVE':
                    discard(project_id)
                    continue
                session = _session(config, owner.id, project)
                recipient = session.public()['recipient']
                if (not recipient or session.scope != context['scope'] or recipient['bot_id'] != job['bot_id']
                        or recipient['chat_id'] != job['chat_id'] or recipient['chat_type'] != job['chat_type']
                        or recipient['chat_type'] not in CHAT_TYPES
                        or recipient['chat_type'] != 'private' and os.environ.get('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED') != '1'):
                    discard(project_id)
                    continue
                if context['mode'] == 'RESCHEDULE':
                    from .telegram_reschedules import prepare_message
                    message = prepare_message(config, box, db, owner, project, session, recipient, context, now_ms)
                    if not message:
                        discard(project_id)
                        continue
                    with box._db() as local:
                        attempt = local.execute('SELECT * FROM delivery_attempts WHERE job_id=?', (job['id'],)).fetchone()
                        local.execute('UPDATE delivery_attempts SET attempts=attempts+1 WHERE job_id=?', (job['id'],))
                    selected = (project.id, recipient['binding_id'], message, attempt['attempts'] + 1)
                    break
                if context['mode'] == 'AUTO':
                    from .telegram_automatic import validate_attempt
                    zone = validate_attempt(config, box, db, owner, project, session, recipient, context, now_ms)
                    if not zone:
                        discard(project_id)
                        continue
                else:
                    zone = context['timezone']
                view = project_auction_view(db, project, now=datetime.fromtimestamp(now_ms/1000, timezone.utc),
                    computer_timezone=zone, collection=context['collection'])
                lot = next((lot for lot in view['lots'] if lot['id'] == context['car_id']), None)
                if lot and context['mode'] == 'AUTO':
                    from .telegram_automatic import confirmed_lot
                    from .models import ScanRun
                    lot = confirmed_lot(db,db.get(ScanRun,context['scan_id']),project,lot,view)
                if not lot or selection_fingerprint(lot, project) != context['signature']:
                    discard(project_id)
                    continue
                if context['mode'] == 'AUTO':
                    from .telegram_automatic import eligible
                    if not eligible(lot):
                        discard(project_id)
                        continue
                with box._db() as local:
                    attempt = local.execute('SELECT * FROM delivery_attempts WHERE job_id=?', (job['id'],)).fetchone()
                    local.execute('UPDATE delivery_attempts SET attempts=attempts+1 WHERE job_id=?', (job['id'],))
                message = auction_message(lot, view, context['locale'], force_text=bool(attempt['force_text']))
                selected = (project.id, recipient['binding_id'], message, attempt['attempts'] + 1)
                break
        if not selected:
            box.finish(job, 'CANCELLED', 'TELEGRAM_CONTEXT_CHANGED'); return True
        project_id, binding_id, message, attempts = selected
        result = send_once(config, job, project_id, binding_id, message)
        state = result['state']
        if state == 'SENT':
            box.confirmed_sent(job, message_id=result['message_id'], now_ms=clock_ms() if clock_ms else now_ms)
        elif state in {'RATE_LIMITED','RETRY_LATER'}:
            if result['retry_after'] is None and attempts >= 5:
                box.finish(job, 'FAILED', 'TELEGRAM_RETRY_EXHAUSTED')
            else:
                box.rate_limited(job['id'], job['attempt_id'], retry_after=result['retry_after'] or min(900,30*2**(attempts-1)),
                    now_ms=clock_ms() if clock_ms else now_ms, error_code=result['error_code'])
        elif state == 'PHOTO_REJECTED':
            with box._db() as local:
                box._attempt(local, job['id'], job['attempt_id'])
                local.execute('UPDATE delivery_attempts SET force_text=1 WHERE job_id=?', (job['id'],))
                local.execute("UPDATE jobs SET state='QUEUED',attempt_id=NULL,error_code='TELEGRAM_PHOTO_REJECTED' WHERE id=?", (job['id'],))
        elif state == 'FAILED':
            box.finish(job, 'FAILED', result['error_code'])
        else:
            box.uncertain(job['id'], job['attempt_id'])
    except Exception:
        # Even a post-HTTP storage failure must not become permission to retry a send.
        logging.getLogger(__name__).warning('Telegram delivery step interrupted; outcome is unconfirmed')
        try:
            box.uncertain(job['id'], job['attempt_id'])
        except (OSError, ValueError, pairing.sqlite3.Error):
            pass  # Persisted IN_FLIGHT is recovered as UNKNOWN under the file lock.
    return True

class DeliveryService:
    def __init__(self):
        self.guard = Lock(); self.wake = Event(); self.stopping = Event(); self.thread = None

    def start(self):
        if not enabled():
            return
        with self.guard:
            if self.thread and self.thread.is_alive():
                return
            config = configuration()
            from .desktop_runtime import DesktopInstanceLock
            from .database import SessionLocal
            box = DeliveryBox(config[0] / 'notifications' / 'telegram-outbox.sqlite3')
            lock_path = box.path.parent / 'telegram-dispatch.lock'
            if lock_path.is_symlink():
                raise TelegramError('telegram_delivery_storage_failed')
            lease = DesktopInstanceLock(lock_path)
            lease.acquire()
            try:
                box.recover()
                self.stopping.clear(); self.wake.clear()
                def run():
                    try:
                        next_reconcile = 0
                        while not self.stopping.is_set():
                            try:
                                if enabled() and not maintenance_active():
                                    now_ms = int(time.time()*1000)
                                    if time.monotonic() >= next_reconcile:
                                        from .telegram_automatic import reconcile
                                        next_reconcile = time.monotonic() + 5
                                        try:
                                            reconcile(config, SessionLocal, now_ms=now_ms)
                                        except Exception:
                                            logging.getLogger(__name__).warning('Telegram automatic search checkpoint unavailable')
                                        try:
                                            from .telegram_reschedules import reconcile as reconcile_changes
                                            reconcile_changes(config, SessionLocal, now_ms=now_ms)
                                        except Exception:
                                            logging.getLogger(__name__).warning('Telegram reschedule checkpoint unavailable')
                                    if step(config, SessionLocal, now_ms=now_ms, clock_ms=lambda:int(time.time()*1000)):
                                        continue
                            except Exception:
                                logging.getLogger(__name__).warning('Telegram dispatcher unavailable')
                            self.wake.wait(1); self.wake.clear()
                    finally:
                        lease.release()
                self.thread = Thread(target=run, name='telegram-delivery', daemon=True)
                self.thread.start()
            except BaseException:
                lease.release(); raise

    def kick(self):
        self.start(); self.wake.set()

    def stop(self):
        self.stopping.set(); self.wake.set()
        if self.thread:
            self.thread.join(timeout=18)

service = DeliveryService()
